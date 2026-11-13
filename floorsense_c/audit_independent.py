#!/usr/bin/env python3
"""Read-only independent audit of FloorSense static data; stdout is JSON.

Uses only stdlib plus openpyxl to read the planning workbook. No model imports,
no training mutations, and no raw-data writes. Run with the bundled Python.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl


ROOT = Path(__file__).resolve().parent
SESSIONS = ROOT / "data/static/sessions"
PLAN = ROOT / "data/T8_FloorSense_Integrated_Sampling_v2.xlsx"


def quantiles(values):
    v = sorted(values)
    def p(frac):
        at = (len(v) - 1) * frac
        a = int(at)
        b = min(a + 1, len(v) - 1)
        return round(v[a] + (v[b] - v[a]) * (at - a), 3)
    return {"n": len(v), "min": v[0], "p25": p(.25), "median": p(.5),
            "p75": p(.75), "p95": p(.95), "max": v[-1]}


def local(ms):
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def main():
    book = openpyxl.load_workbook(PLAN, read_only=True, data_only=True)
    plan_rows = list(book["Point Register"].iter_rows(values_only=True))
    plan = {r[0]: dict(zip(plan_rows[0], r)) for r in plan_rows[1:] if r and r[0]}
    mapping_rows = list(book["Zone Mapping"].iter_rows(values_only=True))
    mapping = {(int(r[0][:-1]), r[1]): r[2] for r in mapping_rows[1:] if r and r[0]}
    book.close()

    metas = {}
    scans = []
    schemas = defaultdict(Counter)
    issues = []
    files = Counter()
    raw_ages = []
    rssi_values = []
    unique_bssid = set()
    observations = 0
    consecutive_intervals = []
    adjacent_repeat_pairs = 0
    adjacent_repeat_preserved_observation_time = 0
    preserved_observation_time_flags = Counter()
    duplicate_bssid_details = []
    consecutive_updated_intervals = []
    within_session_unique = {}
    for folder in sorted(p for p in SESSIONS.iterdir() if p.is_dir()):
        files.update(p.name for p in folder.iterdir() if p.is_file())
        m = json.loads((folder / "meta.json").read_text())
        metas[folder.name] = m
        for key in ("labels", "device", "stats", "env_at_start"):
            schemas[key][tuple(sorted(m[key]))] += 1
        schemas["meta"][tuple(sorted(m))] += 1
        if m["session_id"] != folder.name:
            issues.append([folder.name, "session_id != folder"])
        label = m["labels"]
        point = label["point_id"]
        if point not in plan:
            issues.append([folder.name, "point missing in plan"])
        else:
            pr = plan[point]
            for raw, planned in (("floor", "Floor"), ("zone", "Zone"), ("building", "Building")):
                if label[raw] != pr[planned]:
                    issues.append([folder.name, f"label {raw} differs from plan"])
            if label["split"].lower() != pr["Split"].lower():
                issues.append([folder.name, "split differs from plan"])
            if mapping[label["floor"], label["zone"]] != pr["Zone (coarse)"]:
                issues.append([folder.name, "zone mapping differs from Point Register"])

        grouped = defaultdict(list)
        with (folder / "wifi.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            schemas["wifi"][tuple(reader.fieldnames)] += 1
            for r in reader:
                grouped[int(r["scan_id"])].append(r)
        if sorted(grouped) != list(range(len(grouped))):
            issues.append([folder.name, "nonsequential scan_id"])
        ss = []
        for scan_id, rows in sorted(grouped.items()):
            base = rows[0]
            for key in ("t_wall_ms", "t_elapsed_ms", "fresh", "n_ap", "results_updated"):
                if len({r[key] for r in rows}) != 1:
                    issues.append([folder.name, scan_id, f"inconsistent {key}"])
            bssids = [r["bssid"] for r in rows]
            if len(set(bssids)) != len(bssids):
                issues.append([folder.name, scan_id, "duplicate BSSID in scan"])
                duplicate_bssid_details.append(dict(session=folder.name, scan_id=scan_id,
                    duplicate_keys={b:[dict(rssi=int(r["rssi"]),freq_mhz=int(r["freq_mhz"])) for r in rows if r["bssid"] == b]
                                    for b,n in Counter(bssids).items() if n>1}))
            if len(rows) != int(base["n_ap"]):
                issues.append([folder.name, scan_id, "n_ap mismatch"])
            signature = tuple(sorted((r["bssid"].lower(), int(r["rssi"]), int(r["freq_mhz"])) for r in rows))
            age = [int(r["age_ms"]) for r in rows]
            wall = int(base["t_wall_ms"])
            elapsed = int(base["t_elapsed_ms"])
            if not (m["start_wall_ms"] <= wall <= m["end_wall_ms"]):
                issues.append([folder.name, scan_id, "scan outside session timestamps"])
            s = dict(session=folder.name, scan_id=scan_id, point=point,
                     floor=label["floor"], region=label["zone"],
                     zone=mapping[label["floor"], label["zone"]],
                     orientation=label["orientation"], wall=wall, elapsed=elapsed,
                     fresh=int(base["fresh"]), updated=int(base["results_updated"]),
                     n_ap=len(rows), signature=signature,
                     ap_set=tuple(sorted(bssids)), age=age,
                     observation_time={r["bssid"]: elapsed - int(r["age_ms"]) for r in rows})
            scans.append(s)
            ss.append(s)
            raw_ages.extend(age)
            rssi_values.extend(int(r["rssi"]) for r in rows)
            unique_bssid.update(bssids)
            observations += len(rows)
        within_session_unique[folder.name] = len({s["signature"] for s in ss})
        for a, b in zip(ss, ss[1:]):
            if b["wall"] < a["wall"] or b["elapsed"] < a["elapsed"]:
                issues.append([folder.name, "nonmonotonic scan timestamp"])
            consecutive_intervals.append((b["wall"] - a["wall"]) / 1000)
            if a["signature"] == b["signature"]:
                adjacent_repeat_pairs += 1
                if max(abs(a["observation_time"][ap] - b["observation_time"][ap]) for ap in a["observation_time"]) <= 5:
                    adjacent_repeat_preserved_observation_time += 1
                    preserved_observation_time_flags[str((a["fresh"],b["fresh"]))] += 1
        freshss = [s for s in ss if s["fresh"] == 1]
        for a, b in zip(freshss, freshss[1:]):
            consecutive_updated_intervals.append((b["wall"] - a["wall"]) / 1000)
        for key, actual in (("wifi_scans", len(ss)), ("wifi_scans_fresh", sum(s["fresh"] for s in ss)),
                            ("wifi_results_not_updated", sum(not s["updated"] for s in ss))):
            if actual != m["stats"][key]:
                issues.append([folder.name, f"meta stats {key}={m['stats'][key]} actual={actual}"])

    all_points = sorted({m["labels"]["point_id"] for m in metas.values()})
    by_point = {p: [m for m in metas.values() if m["labels"]["point_id"] == p] for p in all_points}
    signatures = defaultdict(list)
    point_signatures = defaultdict(set)
    fresh_signatures = set()
    for s in scans:
        signatures[s["signature"]].append(s)
        point_signatures[s["point"]].add(s["signature"])
        if s["fresh"] and s["updated"]:
            fresh_signatures.add(s["signature"])
    cross_session = [v for v in signatures.values() if len({s["session"] for s in v}) > 1]
    cross_point = [v for v in signatures.values() if len({s["point"] for s in v}) > 1]
    by_floor = []
    for floor in range(2, 7):
        mm = [m for m in metas.values() if m["labels"]["floor"] == floor]
        ss = [s for s in scans if s["floor"] == floor]
        by_floor.append(dict(floor=floor, points=len({s["point"] for s in ss}), sessions=len(mm), scans=len(ss),
                             fresh=sum(s["fresh"] for s in ss), unique_signatures=len({s["signature"] for s in ss}),
                             start=local(min(m["start_wall_ms"] for m in mm)),
                             end=local(max(m["end_wall_ms"] for m in mm)),
                             zone_points={z:len({s["point"] for s in ss if s["zone"] == z}) for z in ["West", "Middle", "East"]}))

    result = dict(
        totals=dict(sessions=len(metas), points=len(all_points), scans=len(scans), ap_rows=observations,
                    unique_bssid=len(unique_bssid), fresh_scans=sum(s["fresh"] for s in scans),
                    results_updated_scans=sum(s["updated"] for s in scans),
                    unique_signatures=len(signatures), unique_fresh_updated_signatures=len(fresh_signatures)),
        schema={k:[dict(fields=list(fields),sessions=count) for fields,count in v.items()] for k,v in schemas.items()},
        file_presence=files,
        session_status=Counter(m["status"] for m in metas.values()),
        label_counts={key:Counter(m["labels"][key] for m in metas.values()) for key in ["floor", "zone", "orientation", "collector", "posture", "split", "mode", "target_scans"]},
        device_counts=Counter(json.dumps(m["device"],sort_keys=True) for m in metas.values()),
        env_counts={key:Counter(str(m["env_at_start"][key]) for m in metas.values()) for key in next(iter(metas.values()))["env_at_start"]},
        raw_stat_counts={key:Counter(m["stats"][key] for m in metas.values()) for key in ["wifi_scans", "wifi_scans_fresh", "wifi_scan_requests", "wifi_results_not_updated", "wifi_scan_rejected"]},
        issues=issues,
        duplicate_bssid_details=duplicate_bssid_details,
        plan=dict(total_points=len(plan),split_points=Counter(r["Split"] for r in plan.values()),
                  actual_missing_train_points=sorted(p for p,r in plan.items() if r["Split"] == "Train" and p not in by_point),
                  actual_unplanned_points=sorted(p for p in by_point if p not in plan),
                  recorded_val_test_points=sum(plan[p]["Split"] != "Train" for p in all_points),
                  train_missing_physical_description=sum(plan[p]["现场具体位置描述（待填）"] in (None, "") for p in all_points),
                  train_missing_distance=sum(plan[p]["距西端约多少米（待填）"] in (None, "") for p in all_points)),
        point_orientation_issues={p:dict(Counter(m["labels"]["orientation"] for m in mm)) for p,mm in by_point.items() if Counter(m["labels"]["orientation"] for m in mm) != Counter(N=1,E=1,S=1,W=1)},
        by_floor=by_floor,
        coarse_zone_counts={z:dict(points=len({s["point"] for s in scans if s["zone"] == z}),sessions=sum(mapping[m["labels"]["floor"],m["labels"]["zone"]] == z for m in metas.values()), scans=sum(s["zone"] == z for s in scans)) for z in ["West", "Middle", "East"]},
        quality=dict(scan_ap_counts=quantiles([s["n_ap"] for s in scans]), rssi=quantiles(rssi_values),
                     ages_ms=quantiles(raw_ages),
                     fresh_ages_ms=quantiles([a for s in scans if s["fresh"] for a in s["age"]]),
                     fresh_rows_age_exceedance={str(t):sum(a > t for s in scans if s["fresh"] for a in s["age"]) for t in [1000,2000,5000]},
                     row_age_exceedance={str(t):sum(a > t for a in raw_ages) for t in [1000,2000,5000,10000,30000]},
                     scans_any_age_exceedance={str(t):sum(max(s["age"]) > t for s in scans) for t in [1000,2000,5000,10000,30000]},
                     scans_all_age_exceedance={str(t):sum(min(s["age"]) > t for s in scans) for t in [1000,2000,5000,10000,30000]},
                     negative_age_rows=sum(a<0 for a in raw_ages),
                     scan_intervals_s=quantiles(consecutive_intervals),fresh_intervals_s=quantiles(consecutive_updated_intervals),
                     duration_s=quantiles([m["stats"]["duration_s"] for m in metas.values()]),
                     fresh_updated_cross=Counter(str((s["fresh"],s["updated"])) for s in scans),
                     unique_signatures_per_session=Counter(within_session_unique.values()),
                     unique_signatures_per_point=quantiles([len(v) for v in point_signatures.values()]),
                     within_session_redundant_scans=len(scans)-sum(within_session_unique.values()),
                     repeated_signature_clusters=sum(len(v)>1 for v in signatures.values()),
                     scans_in_repeated_clusters=sum(len(v) for v in signatures.values() if len(v)>1),
                     adjacent_repeat_pairs=adjacent_repeat_pairs,
                     adjacent_repeat_preserved_observation_time=adjacent_repeat_preserved_observation_time,
                     preserved_observation_time_flags=preserved_observation_time_flags,
                     small_ap_scans=[{k:s[k] for k in ["session","scan_id","point","orientation","fresh","n_ap"]} for s in scans if s["n_ap"] < 10],
                     orientation_max_error_deg={o:round(max(abs((m["stats"]["mean_azimuth_deg"]-target+180)%360-180) for m in metas.values() if m["labels"]["orientation"]==o),3) for o,target in dict(N=0,E=90,S=180,W=270).items()},
                     cross_session_clusters=len(cross_session),cross_point_clusters=len(cross_point),
                     cross_session_details=[dict(scans=len(v), points=sorted({s["point"] for s in v}),
                                                 sessions=sorted({s["session"] for s in v}),
                                                 floor=sorted({s["floor"] for s in v}), zone=sorted({s["zone"] for s in v})) for v in cross_session],
                     scan_signatures_per_point={p:len(v) for p,v in sorted(point_signatures.items())}),
    )
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
