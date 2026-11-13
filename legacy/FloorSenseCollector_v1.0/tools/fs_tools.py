"""FloorSense 数据集工具（只用 Python 标准库）。

用法：
    python fs_tools.py check     <数据目录> [<数据目录> ...]
    python fs_tools.py merge     <数据目录> ... -o wifi_scans.csv [--include-incomplete] [--modes static,walk]
    python fs_tools.py anonymize <数据目录> -o <输出目录> --salt <只有组内知道的字符串>

<数据目录> 可以是解压后的导出包，也可以是多台手机导出包放在一起的上级目录；
脚本会递归查找所有 meta.json。同一个 session_id 出现多次只算一次，
优先采用在手机上最后修改过标签（labels_modified_ms 最大）的那一份。
"""

import argparse
import csv
import hashlib
import hmac
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 课程要求的阈值
MIN_TEST_POSITIONS = 50
MIN_FLOORS = 3
BONUS_FLOORS = 5
MIN_TRANSITIONS_PER_METHOD = 5


# ---------------------------------------------------------------- 读取

class Session:
    def __init__(self, path: Path, meta: dict):
        self.dir = path
        self.meta = meta
        self.labels = meta.get("labels", {})
        self.id = meta.get("session_id", path.name)
        self.status = meta.get("status", "")
        self.mode = self.labels.get("mode", "")
        self.split = self.labels.get("split", "")
        self.building = self.labels.get("building", "")
        self.floor = self.labels.get("floor")
        self.point = self.labels.get("point_id", "")
        self.device = meta.get("device", {}).get("model", "")
        self.collector = self.labels.get("collector", "")
        self.start_local = meta.get("start_local", "")
        self.stats = meta.get("stats", {})
        self.modified_ms = meta.get("labels_modified_ms") or 0

    @property
    def date(self):
        return self.start_local[:10]

    @property
    def time_bucket(self):
        try:
            h = int(self.start_local[11:13])
        except ValueError:
            return "?"
        return "morning" if h < 12 else "afternoon" if h < 18 else "evening"

    @property
    def point_key(self):
        if self.mode == "transition":
            return None
        return (self.building, self.floor, self.point)


def load_sessions(roots):
    seen = {}
    for root in roots:
        root = Path(root)
        if not root.exists():
            sys.exit(f"找不到目录：{root}")
        for meta_path in sorted(root.rglob("meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"[WARN] 无法读取 {meta_path}: {e}")
                continue
            s = Session(meta_path.parent, meta)
            old = seen.get(s.id)
            if old is None or s.modified_ms > old.modified_ms:
                seen[s.id] = s
    return list(seen.values())


def read_csv(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- check

def consecutive_runs(floors):
    """返回最长连续楼层段的长度。"""
    fl = sorted(set(floors))
    best = run = 1 if fl else 0
    for a, b in zip(fl, fl[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


def cmd_check(args):
    sessions = load_sessions(args.dirs)
    if not sessions:
        sys.exit("没有找到任何 meta.json")
    errors, warns = [], []

    status = Counter(s.status for s in sessions)
    print(f"会话总数 {len(sessions)}：" + "，".join(f"{k} {v}" for k, v in status.items()))
    done = [s for s in sessions if s.status == "complete"]
    if status.get("recording"):
        warns.append(f"{status['recording']} 个会话状态是 recording（App 异常退出），已忽略")

    # ---- 数据泄漏：同一 (楼, 层, 点位) 同时出现在 train 和 test
    splits_by_key = defaultdict(set)
    for s in done:
        if s.point_key:
            splits_by_key[s.point_key].add(s.split)
    leaks = [k for k, v in splits_by_key.items() if len(v) > 1]
    for b, f, p in leaks:
        errors.append(f"数据泄漏：{b} {f}F 点位 {p} 同时出现在 train 和 test")
    for s in done:
        if s.mode == "static" and s.point:
            want = {"train": "T", "test": "X"}.get(s.split)
            if want and not s.point.startswith(want):
                warns.append(f"命名不一致：{s.id} 是 {s.split}，但点位编号 {s.point} 不以 {want} 开头")

    # ---- 每层覆盖情况
    print("\n== 每层覆盖 ==")
    header = ["楼", "层", "训练点", "测试点", "训练会话", "测试会话", "行走", "朝向", "持机", "机型", "采集人", "日期", "时段"]
    rows = []
    by_floor = defaultdict(list)
    for s in done:
        if s.mode != "transition":
            by_floor[(s.building, s.floor)].append(s)
    for (b, f), items in sorted(by_floor.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        st = [s for s in items if s.mode == "static"]
        tr = [s for s in st if s.split == "train"]
        te = [s for s in st if s.split == "test"]
        rows.append([
            b, f,
            len({s.point for s in tr}), len({s.point for s in te}),
            len(tr), len(te),
            sum(s.mode == "walk" for s in items),
            "".join(sorted({s.labels.get("orientation", "") for s in st})),
            len({s.labels.get("posture") for s in items}),
            len({s.device for s in items}),
            len({s.collector for s in items}),
            len({s.date for s in items}),
            ",".join(sorted({s.time_bucket for s in items})),
        ])
    print_table(header, rows)

    # ---- 每栋楼的测试点数和连续楼层
    print("\n== 每栋楼 ==")
    floors_by_building = defaultdict(set)
    test_points_by_building = defaultdict(set)
    for (b, f), items in by_floor.items():
        has_train = any(s.split == "train" and s.mode == "static" for s in items)
        has_test = any(s.split == "test" and s.mode == "static" for s in items)
        if has_train and has_test:
            floors_by_building[b].add(f)
        for s in items:
            if s.mode == "static" and s.split == "test":
                test_points_by_building[b].add((f, s.point))
    for b in sorted(set(k[0] for k in by_floor)):
        n_test = len(test_points_by_building[b])
        run = consecutive_runs(floors_by_building[b])
        print(f"{b}：训练+测试都有的楼层 {sorted(floors_by_building[b])}，最长连续 {run} 层，测试点 {n_test} 个")
        if run < MIN_FLOORS:
            warns.append(f"{b} 连续楼层只有 {run} 层（基本要求 ≥{MIN_FLOORS}）")
        if n_test < MIN_TEST_POSITIONS:
            warns.append(f"{b} 测试点只有 {n_test} 个（基本要求 ≥{MIN_TEST_POSITIONS}）")

    # ---- 跨层
    trans = [s for s in done if s.mode == "transition"]
    print("\n== 跨层记录 ==")
    if not trans:
        print("（无）")
    trans_count = Counter()
    for s in trans:
        to = s.labels.get("to_floor")
        direction = "up" if to is not None and s.floor is not None and to > s.floor else "down"
        trans_count[(s.labels.get("method"), direction)] += 1
        events = {r["event"] for r in read_csv(s.dir / "events.csv")}
        if not {"move_start", "move_end"} <= events:
            warns.append(f"{s.id} 缺少 move_start / move_end 标记")
    for (m, d), n in sorted(trans_count.items()):
        print(f"{m} {d}: {n}")

    # ---- 数据质量
    for s in done:
        if s.mode == "static":
            tgt = s.labels.get("target_scans", 0)
            if s.stats.get("wifi_scans_fresh", 0) < tgt:
                warns.append(f"{s.id} 新扫描 {s.stats.get('wifi_scans_fresh')} < 目标 {tgt}")
        if s.meta.get("env_at_start", {}).get("wifi_throttled") is True:
            warns.append(f"{s.id} 采集时 Wi-Fi 扫描节流是开启的")
        iv = s.stats.get("wifi_mean_fresh_interval_s")
        if isinstance(iv, (int, float)) and iv > 12:
            warns.append(f"{s.id} 新扫描平均间隔 {iv:.1f} 秒（正常 3–8 秒），系统在限制扫描")
        scans = read_csv(s.dir / "wifi.csv")
        fresh_ids = {r["scan_id"] for r in scans if r["fresh"] == "1"}
        empty = {r["scan_id"] for r in scans if r["fresh"] == "1" and not r["bssid"]}
        if fresh_ids and len(empty) == len(fresh_ids):
            errors.append(f"{s.id} 所有扫描都是 0 个 AP（定位权限或定位开关有问题）")
    if any(_has_pressure(s) for s in done):
        errors.append("sensors.csv 里出现了气压计数据，课程不允许使用")

    # ---- 加分项准备情况
    print("\n== 加分项数据准备 ==")
    models_full = _models_covering_all_floors(done, by_floor)
    collectors = {s.collector for s in done}
    dates = sorted({s.date for s in done})
    test_dates = {s.date for s in done if s.split == "test"}
    train_dates = {s.date for s in done if s.split == "train"}
    good_buildings = [b for b, fl in floors_by_building.items() if consecutive_runs(fl) >= MIN_FLOORS]
    mag_ok = sum(1 for s in done if s.mode == "static" and "mag" in _sensor_names(s))
    zone_ok = sum(1 for s in done if s.labels.get("zone"))
    later_test = bool(test_dates and train_dates and max(test_dates) > min(train_dates))
    checks = [
        ("A1 五层以上", any(consecutive_runs(fl) >= BONUS_FLOORS for fl in floors_by_building.values()),
         f"最长连续 {max((consecutive_runs(fl) for fl in floors_by_building.values()), default=0)} 层"),
        ("A2 楼梯/电梯检测",
         all(sum(n for (m, _), n in trans_count.items() if m == meth) >= MIN_TRANSITIONS_PER_METHOD
             for meth in ("stairs", "elevator")),
         ", ".join(f"{m} {d} {n}" for (m, d), n in sorted(trans_count.items())) or "无"),
        ("A3 传感器融合", mag_ok > 0, f"{mag_ok} 个静态会话有磁力计数据"),
        ("A4 多用户多机型", len(models_full) >= 2 and len(collectors) >= 2,
         f"覆盖所有楼层的机型 {sorted(models_full)}，采集人 {len(collectors)} 个"),
        ("A5 随时间变化", len(dates) >= 2 and later_test, f"采集日期 {dates}"),
        ("A6 多栋楼", len(good_buildings) >= 2, f"满足 ≥{MIN_FLOORS} 层的楼：{sorted(good_buildings)}"),
        ("A7 区域识别", zone_ok >= len(done) * 0.8 if done else False, f"{zone_ok}/{len(done)} 个会话填了区域"),
    ]
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else '..'}] {name}：{detail}")

    print()
    for w in warns:
        print(f"[WARN] {w}")
    for e in errors:
        print(f"[ERROR] {e}")
    if not warns and not errors:
        print("没有发现问题。")
    return 1 if errors else 0


def _sensor_names(s):
    p = s.dir / "sensors.json"
    if not p.exists():
        return set()
    try:
        return {x.get("name") for x in json.loads(p.read_text(encoding="utf-8"))}
    except json.JSONDecodeError:
        return set()


def _has_pressure(s):
    return any("press" in (n or "").lower() for n in _sensor_names(s))


def _models_covering_all_floors(done, by_floor):
    floors = set(by_floor)
    models = defaultdict(set)
    for s in done:
        if s.mode == "static":
            models[s.device].add((s.building, s.floor))
    return {m for m, fl in models.items() if fl >= floors}


def print_table(header, rows):
    if not rows:
        print("（无）")
        return
    cells = [header] + [[str(c) for c in r] for r in rows]
    widths = [max(_width(r[i]) for r in cells) for i in range(len(header))]
    for r in cells:
        print("  ".join(c + " " * (w - _width(c)) for c, w in zip(r, widths)))


def _width(s):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


# ---------------------------------------------------------------- merge

MERGE_FIELDS = [
    "session_id", "building", "floor", "zone", "point_id", "split", "mode", "orientation", "posture",
    "device_model", "collector", "start_local", "scan_id", "t_wall_ms", "n_ap", "bssid", "rssi", "freq_mhz",
]


def cmd_merge(args):
    modes = set(args.modes.split(","))
    sessions = [s for s in load_sessions(args.dirs)
                if s.mode in modes and (s.status == "complete" or args.include_incomplete)]
    n_rows = n_scans = 0
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MERGE_FIELDS)
        w.writeheader()
        for s in sorted(sessions, key=lambda s: s.id):
            base = {
                "session_id": s.id, "building": s.building, "floor": s.floor,
                "zone": s.labels.get("zone", ""), "point_id": s.point, "split": s.split,
                "mode": s.mode, "orientation": s.labels.get("orientation", ""),
                "posture": s.labels.get("posture", ""), "device_model": s.device,
                "collector": s.collector, "start_local": s.start_local,
            }
            scan_ids = set()
            for r in read_csv(s.dir / "wifi.csv"):
                if r["fresh"] != "1":
                    continue
                scan_ids.add(r["scan_id"])
                w.writerow({**base, "scan_id": r["scan_id"], "t_wall_ms": r["t_wall_ms"], "n_ap": r["n_ap"],
                            "bssid": r["bssid"], "rssi": r["rssi"], "freq_mhz": r["freq_mhz"]})
                n_rows += 1
            n_scans += len(scan_ids)
    print(f"写入 {args.output}：{len(sessions)} 个会话，{n_scans} 次扫描，{n_rows} 行")
    print("每行是一次扫描里的一个 AP；bssid 为空表示这次扫描一个 AP 都没扫到。")
    print("训练时按 (session_id, scan_id) 透视成向量，没扫到的 AP 填 -100。")
    return 0


# ---------------------------------------------------------------- anonymize

HASH_COLUMNS = {
    "wifi.csv": ["bssid", "ssid"],
    "ble.csv": ["address", "ibeacon_uuid"],
    "cell.csv": ["area", "cid"],
}


def cmd_anonymize(args):
    src, dst = Path(args.dir), Path(args.output)
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"输出目录 {dst} 已存在且不为空，请换一个目录")
    key = args.salt.encode("utf-8")
    sessions = load_sessions([src])
    for s in sessions:
        out = dst / s.dir.relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        meta = json.loads(json.dumps(s.meta))
        meta.get("labels", {}).pop("notes", None)
        for h in meta.get("label_history", []):
            h.get("before", {}).pop("notes", None)
            h.get("after", {}).pop("notes", None)
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        for f in s.dir.iterdir():
            if f.name == "meta.json" or not f.is_file():
                continue
            cols = HASH_COLUMNS.get(f.name)
            if not cols:
                shutil.copy2(f, out / f.name)
                continue
            with f.open(encoding="utf-8", newline="") as fi, (out / f.name).open("w", encoding="utf-8", newline="") as fo:
                reader = csv.DictReader(fi)
                writer = csv.DictWriter(fo, fieldnames=reader.fieldnames)
                writer.writeheader()
                for r in reader:
                    for c in cols:
                        if r.get(c):
                            r[c] = _pseudo(key, c, r[c])
                    writer.writerow(r)
    print(f"已匿名化 {len(sessions)} 个会话 → {dst}")
    print("同一个 salt 下，同一个 AP 总是映射到同一个代号，所以匿名数据仍然可以用来训练。")
    return 0


def _pseudo(key, column, value):
    norm = value.lower() if column in ("bssid", "address", "ibeacon_uuid") else value
    return hmac.new(key, f"{column}:{norm}".encode("utf-8"), hashlib.sha256).hexdigest()[:12]


# ---------------------------------------------------------------- main

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="FloorSense 数据集工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="检查覆盖情况、数据泄漏和数据质量")
    c.add_argument("dirs", nargs="+")
    c.set_defaults(func=cmd_check)

    m = sub.add_parser("merge", help="把 Wi-Fi 扫描合并成一张训练用的长表")
    m.add_argument("dirs", nargs="+")
    m.add_argument("-o", "--output", default="wifi_scans.csv")
    m.add_argument("--modes", default="static,walk")
    m.add_argument("--include-incomplete", action="store_true")
    m.set_defaults(func=cmd_merge)

    a = sub.add_parser("anonymize", help="生成可以提交的匿名数据集")
    a.add_argument("dir")
    a.add_argument("-o", "--output", required=True)
    a.add_argument("--salt", required=True)
    a.set_defaults(func=cmd_anonymize)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
