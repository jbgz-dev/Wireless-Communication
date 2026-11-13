"""Compare Wi-Fi-only vs Wi-Fi + phone sensors on dynamic runs.

Wi-Fi model: RandomForest fit on static Train only (scans with >=10 APs), the variant chosen on D2.
Fusion: discrete Bayes filter over floors 2..6.
  - between detected vertical events the floor is assumed constant (identity transition);
  - elevator ride: shift by round(disp/FLOOR_H); stairs bout: shift by +-1 (direction from
    vertical-accel std), with leftover probability on 0 and the opposite direction;
  - each new distinct Wi-Fi fingerprint multiplies in (proba + eps)^BETA.
Ground truth: start floor before move_start, end floor after move_end; scans during motion not scored.
"""
import csv, glob, json, sys
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; C = HERE.parents[1]/'floorsense_c'
sys.path.insert(0, str(C)); sys.path.insert(0, str(HERE.parent))
from pipeline import load_data, encode_fit, encode_transform
from variants import rf
from dynlib import load_session, label
from detect import prep, elevator_rides, walking_bouts

FLOORS = np.array([2, 3, 4, 5, 6])
FLOOR_H = 4.0          # m/floor: mean |disp|/|dF| over dynamic_train elevator rides (see report)
STAIRS_DOWN_STD = 1.45 # train: up 1.18-1.30, down 1.66-1.82
STAIRS_MIN_S, STAIRS_MAX_S = 5, 30
BETA, EPS, LEAK = 0.5, 0.02, 0.02  # BETA<1: consecutive scans are correlated, count each less

def wifi_scans(wifi):
    out = []
    for sid, g in wifi.groupby('scan_id', sort=True):
        if not ((g.fresh == 1) & (g.results_updated == 1)).all(): continue
        vals = defaultdict(list)
        for b, r, age in zip(g.bssid, g.rssi, g.age_ms):
            if not isinstance(b, str) or not b.strip() or not np.isfinite(r) or not -127 <= r < 0 or not 0 <= age <= 10000: continue
            vals[b.lower()].append(float(np.clip(r, -100, -20)))
        out.append((g.t.iloc[0], {b: float(np.median(v)) for b, v in vals.items()}))
    return out

def shift(p, d, spread):
    q = np.zeros_like(p)
    for delta, w in spread(d):
        for i in range(len(p)):
            j = i + delta
            if 0 <= j < len(p): q[j] += w * p[i]
    return q / q.sum() if q.sum() else np.full_like(p, 1 / len(p))

def events(s):
    sig, steps, a, rs = prep(s)
    ev = [dict(t=r['t1'], kind='elevator', delta=int(round(r['disp_m'] / FLOOR_H)), t0=r['t0'], disp=r['disp_m'])
          for r in elevator_rides(sig, a, rs)]
    ev = [e for e in ev if e['delta'] != 0]
    for b in walking_bouts(sig, steps, rs):
        if STAIRS_MIN_S <= b['dur'] <= STAIRS_MAX_S:
            ev.append(dict(t=b['t1'], kind='stairs', delta=-1 if b['std'] > STAIRS_DOWN_STD else +1, t0=b['t0'], std=b['std']))
    return sorted(ev, key=lambda e: e['t'])

def run(d, fm, vocab, init_mode='uniform'):
    meta, s, ev_lab, wifi = load_session(d); lab = label(meta, ev_lab)
    det = events(s)
    init = np.full(5, 0.2)
    if init_mode == 'known_start':   # user opened the app on a known floor (or got a good fix in the corridor)
        init = np.full(5, 0.01); init[list(FLOORS).index(lab['start'])] = 0.96
    scans = wifi_scans(wifi); vs = set(vocab)
    p = init.copy(); last_fp = None; hist = []; ei = 0; rows = []
    # A detected transition covers [t0, t]. Wi-Fi seen inside it belongs to neither floor, so the
    # filter keeps a checkpoint at t0 and, when the transition is confirmed, restarts from it.
    starts = sorted(e['t0'] for e in det); si = 0; checkpoint = {}
    for t, fp in scans:
        while si < len(starts) and starts[si] <= t:
            checkpoint[starts[si]] = p.copy(); si += 1
        while ei < len(det) and det[ei]['t'] <= t:
            e = det[ei]; ei += 1
            base = checkpoint.get(e['t0'], p)
            if e['kind'] == 'elevator':
                p = shift(base, e['delta'], lambda d: [(d, .9), (d - 1, .05), (d + 1, .05)])
            else:
                p = shift(base, e['delta'], lambda d: [(d, .7), (0, .2), (-d, .1)])
            hist.clear(); last_fp = None   # tracker reset on transition (FloorTracker.onTransitionEnd)
        in_motion = any(e['t0'] <= t <= e['t'] for e in det)
        known = sum(b in vs for b in fp)
        raw = smooth = None
        if known >= 10:
            pr = fm.predict_proba(encode_transform([fp], vocab))[0]
            raw = FLOORS[pr.argmax()]
            if fp != last_fp:
                if not in_motion:
                    p = p * (pr + EPS) ** BETA; p /= p.sum()
                    p = (1 - LEAK) * p + LEAK / len(p)     # never fully certain: lets Wi-Fi recover a wrong lock
                hist.append(pr); hist[:] = hist[-5:]
            last_fp = fp
        if hist: smooth = FLOORS[np.mean(hist, 0).argmax()]
        if 'move_start' in lab:
            truth = lab['start'] if t < lab['move_start'] else lab['end'] if t > lab['move_end'] else None
        else: truth = lab['start']
        rows.append(dict(t=t, truth=truth, known=known, raw=raw, smooth=smooth, fused=FLOORS[p.argmax()], fused_p=p.max()))
    return lab, det, pd.DataFrame(rows)

def settle(df, lab, col):
    if 'move_end' not in lab: return None
    after = df[df.t > lab['move_end']]
    ok = (after[col] == lab['end']).values
    for i in range(len(ok)):
        if ok[i:].all(): return round(after.t.iloc[i] - lab['move_end'], 1)
    return np.inf

def main():
    _, train, _ = load_data(Path('/tmp/fs_static/sessions'), C/'point_labels.json')
    tr = train[train.n_ap >= 10]; vocab, x = encode_fit(tr.features.tolist()); fm = rf().fit(x, tr.floor)
    out, evrows, traces = [], [], []
    for split, root in (('train', '/tmp/fs_dyn/dynamic_train'), ('validation', str(HERE.parents[1]/'dynamic_validation(1)'))):
        for d in sorted(glob.glob(root + '/*/')):
            for init_mode in ('uniform', 'known_start'):
                lab, det, df = run(d, fm, vocab, init_mode)
                name = lab['run'] or Path(d).name.split('_')[-2]
                df = df.assign(split=split, run=name, kind=lab['kind'], init=init_mode,
                               phase=np.where(df.truth.isna(), 'moving', np.where(('move_end' in lab) & (df.t > lab.get('move_end', 1e9)), 'after', 'before')))
                traces.append(df)
                def changes(c): v = df[c].dropna().values; return int((v[1:] != v[:-1]).sum())
                dsum = sum(e['delta'] for e in det)
                out.append(dict(split=split, init=init_mode, run=name, kind=lab['kind'], move=f"{lab['start']}->{lab['end']}",
                                detected=';'.join(f"{e['kind']}{e['delta']:+d}" for e in det) or '-',
                                det_ok=bool(dsum == lab['end'] - lab['start'] and (lab['kind'] == 'walk' or all(e['kind'] == lab['kind'] for e in det))),
                                settle_smooth=settle(df, lab, 'smooth'), settle_fused=settle(df, lab, 'fused'),
                                flips_raw=changes('raw'), flips_smooth=changes('smooth'), flips_fused=changes('fused'),
                                low_ap=int((df.known < 10).sum()), scans=len(df)))
                if init_mode == 'uniform':
                    for e in det: evrows.append(dict(split=split, run=name, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in e.items()}))
    r = pd.DataFrame(out); r.to_csv(HERE/'fusion_results.csv', index=False)
    pd.DataFrame(evrows).to_csv(HERE/'detected_events.csv', index=False)
    T = pd.concat(traces); T.to_csv(HERE/'fusion_traces.csv', index=False)
    sc = T[T.truth.notna()]
    def pooled(g):
        res = {}
        for c in ('raw', 'smooth', 'fused'):
            # a method that shows nothing counts as wrong (the screen has no floor)
            res[c] = (g[c] == g.truth).mean()
        res['n'] = len(g); return pd.Series(res)
    summ = sc.groupby(['split', 'init', 'phase']).apply(pooled, include_groups=False)
    summ_all = sc.groupby(['split', 'init']).apply(pooled, include_groups=False)
    summ.to_csv(HERE/'fusion_summary_by_phase.csv'); summ_all.to_csv(HERE/'fusion_summary.csv')
    pd.set_option('display.width', 250)
    print(r[r.init == 'uniform'].drop(columns='init').round(2).to_string(index=False))
    print('\npooled accuracy over scored scans (no floor shown = wrong):'); print(summ_all.round(3)); print(summ.round(3))


if __name__ == '__main__':
    main()
