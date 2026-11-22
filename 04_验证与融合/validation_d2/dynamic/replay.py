"""按 App 的逻辑（FusionEngine：V1 随机森林 + 实时门限 + 贝叶斯融合 + 跨层检测）重放动态 session，
统计“到达后多少秒界面显示正确楼层”。

和 App 的差别：跨层事件用离线检测结果，在 t1 + CONFIRM_S 秒生效（App 在线确认约 1.5–1.75 s）。
起点楼层已知（相当于在走廊里先识别好了再出发）。
"""
import gzip
import json
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import sweep

HERE = Path(__file__).resolve().parent
MODEL = HERE.parent / 'floorsense' / 'src' / 'main' / 'assets' / 'wifi_model.json.gz'
FLOORS = [2, 3, 4, 5, 6]
CONFIRM_S = 1.75
BETA, EPS, LEAK, SHOW_MIN = 0.5, 0.02, 0.02, 0.5


class Forest:
    def __init__(self, m):
        self.classes = m['classes']
        self.trees = []
        for t in m['trees']:
            left = np.array(t['left']); right = np.array(t['right'])
            feat = np.array(t['feature']); th = np.array(t['threshold'], dtype=float)
            leaves = {int(k): np.array(v, float) / np.sum(v) for k, v in t['leaves'].items()}
            self.trees.append((left, right, feat, th, leaves))

    def proba(self, x):
        s = np.zeros(len(self.classes))
        for left, right, feat, th, leaves in self.trees:
            n = 0
            while left[n] != -1:
                n = left[n] if x[feat[n]] <= th[n] else right[n]
            s += leaves[n]
        return s / len(self.trees)


class Model:
    def __init__(self, path=MODEL):
        m = json.load(gzip.open(path))
        self.bssids = m['config']['bssids']
        self.index = {b: i for i, b in enumerate(self.bssids)}
        self.floor = Forest(m['models']['floor'])
        p = m['realtime_policy']
        self.min_known, self.min_vote = p['min_known_aps'], p['min_floor_vote_score']

    def fingerprint(self, g):
        vals = defaultdict(list)
        for b, r, age in zip(g.bssid, g.rssi, g.age_ms):
            if not isinstance(b, str) or not b.strip() or not np.isfinite(r) or not -127 <= r < 0:
                continue
            if np.isfinite(age) and not 0 <= age <= 10000:
                continue
            vals[b.lower()].append(float(np.clip(r, -100, -20)))
        return {b: float(np.median(v)) for b, v in vals.items()}

    def predict(self, fp):
        x = np.full(len(self.bssids), -100.0)
        for b, r in fp.items():
            if b in self.index:
                x[self.index[b]] = r
        return self.floor.proba(x)


def scans(wifi, model):
    out = []
    for sid, g in wifi.groupby('scan_id', sort=True):
        if not ((g.fresh == 1) & (g.results_updated == 1)).all():
            continue
        fp = model.fingerprint(g)
        known = sum(b in model.index for b in fp)
        out.append((g.t.iloc[0], fp, known))
    return out


def update(p, proba):
    q = p * (proba + EPS) ** BETA
    return (1 - LEAK) * q / q.sum() + LEAK / len(q)


def shift(p, spread):
    q = np.zeros_like(p)
    for d, w in spread:
        for i in range(len(p)):
            if 0 <= i + d < len(p):
                q[i + d] += w * p[i]
    return q / q.sum()


def run(x, model, events, wifi):
    """events: [{kind:'E'/'S', delta, t0, t1}]。返回逐次扫描的 (t, 显示楼层, 纯 Wi-Fi 楼层, 真值)。"""
    lab = x['lab']
    p = np.full(5, 0.01); p[FLOORS.index(lab['start'])] = 0.96
    log = []          # (t, proba)：确认跨层时从 t0 之前的状态重算
    base_p = p.copy()
    last_fp = None
    wifi_floor = lab['start']
    pending = sorted(events, key=lambda e: e['t1'])
    rows = []
    for t, fp, known in scans(wifi, model):
        while pending and pending[0]['t1'] + CONFIRM_S <= t:
            e = pending.pop(0)
            q = base_p.copy()
            for tt, pr in log:
                if tt < e['t0']:
                    q = update(q, pr)
            spread = ([(e['delta'], .9), (e['delta'] - 1, .05), (e['delta'] + 1, .05)] if e['kind'] == 'E'
                      else [(e['delta'], .7), (0, .2), (-e['delta'], .1)])
            base_p = shift(q, spread)
            log = [(tt, pr) for tt, pr in log if tt > e['t1']]
            p = base_p.copy()
            for tt, pr in log:
                p = update(p, pr)
            last_fp = None
        used = False
        if known >= model.min_known:
            pr = model.predict(fp)
            if pr.max() >= model.min_vote and fp != last_fp:
                last_fp = fp
                log.append((t, pr))
                p = update(p, pr)
                used = True
            if pr.max() >= model.min_vote:
                wifi_floor = FLOORS[int(np.argmax(pr))]
        shown = FLOORS[int(np.argmax(p))] if p.max() >= SHOW_MIN else None
        rows.append(dict(t=t, shown=shown, wifi=wifi_floor, known=known, used=used))
    return pd.DataFrame(rows)


def first_correct_after(df, t_arrive, truth):
    """到达后第一次显示正确、并且之后一直正确的时间（秒）；到结束都不对返回 None。"""
    after = df[df.t > t_arrive]
    ok = (after.shown == truth).values
    for i in range(len(ok)):
        if ok[i:].all():
            return after.t.values[i] - t_arrive
    return None


if __name__ == '__main__':
    model = Model()
    data = sweep.load_all(sorted(str(p) for p in (HERE / '数据').iterdir() if p.is_dir()))
    from dynlib import load_session
    out = []
    for cfg_name, cfg in [('现在', {}), ('改阈值', dict(hand=1.0, turn_rate=15, turn_min=60))]:
        det = sweep.evaluate(data, **cfg)
        for x, (_, r) in zip(data, det.iterrows()):
            if x['kind'] == 'walk':
                continue
            d = next(p for p in (HERE / '数据').rglob(x['name'] + '*') if p.is_dir())
            _, s, ev, wifi = load_session(d)
            events = []
            # 重新跑一次拿到 t0/t1（evaluate 只返回汇总）
            ev_list = [dict(kind='E', delta=int(round(rr['disp'] / 4.0)), t0=rr['t0'], t1=rr['t1'])
                       for rr in sweep.elevator_rides(x['sig'], x['a'], x['rs'], cfg.get('hand', 0.35))]
            ev_list = [e for e in ev_list if e['delta']]
            ev_list += [dict(kind='S', **b) for b in sweep.stairs(
                x['sig'], x['steps'], x['rs'], x['yr'], cfg.get('turn_rate', 30), cfg.get('turn_min', 90), 150, 60)]
            until = -np.inf
            for e in sorted(ev_list, key=lambda e: e['t1']):
                if e['t0'] > until:
                    events.append(e); until = e['t1']
            df = run(x, model, events, wifi)
            arrive = x['lab']['move_end']
            truth = x['lab']['end']
            fused = first_correct_after(df, arrive, truth)
            wifi_only = df[df.t > arrive]
            w_ok = next((t - arrive for t, w in zip(wifi_only.t, wifi_only.wifi) if w == truth), None)
            used_after = int(df[(df.t > arrive) & df.used].shape[0])
            n_after = int((df.t > arrive).sum())
            out.append(dict(cfg=cfg_name, set=x['set'], name=x['name'], kind=x['kind'], truth=f"{x['lab']['start']}->{truth}",
                            det=r['det'], fused_s=fused, wifi_s=w_ok, wifi_used=f"{used_after}/{n_after}",
                            after_s=round(df.t.values[-1] - arrive, 1)))
    df = pd.DataFrame(out)
    pd.set_option('display.width', 220, 'display.max_rows', 200)
    print(df.to_string(index=False, float_format=lambda v: f'{v:.1f}'))
    for c, g in df.groupby('cfg'):
        f = g.fused_s
        print(f"{c}: 到达后最终正确 {f.notna().sum()}/{len(g)}；正确时间中位数 {f.median():.1f}s；"
              f"≤3 s 正确 {(f <= 3).sum()}；纯 Wi-Fi 首次正确中位数 {g.wifi_s.median():.1f}s")
