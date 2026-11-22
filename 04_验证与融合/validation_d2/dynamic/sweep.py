"""在全部动态 session 上试不同阈值，统计漏检 / 误报 / 层数错。

1116 那批的 EL01–EL06 在 meta.json 里写的是 stairs，但每层只用 3–4 s（楼梯每层约 20–38 s），
路线编号也是 EL，这里按电梯计。原始文件不改。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import detect
from dynlib import load_session, label
from detect import prep, _runs, HZ, walking_bouts, yaw_rate

HERE = Path(__file__).resolve().parent
FLOOR_H = 4.0


def true_kind(meta, lab):
    if lab['kind'] == 'walk':
        return 'walk'
    if str(meta['labels'].get('zone', '')).startswith('EL'):
        return 'elevator'
    return lab['kind']


def elevator_rides(sig, a, rs, hand):
    t = sig.t.values
    pulses = []
    for sign in (+1, -1):
        for i, j in _runs((sign * a > detect.PULSE_A) & (rs < hand + 0.25)):
            if (j - i) / HZ >= detect.PULSE_MIN_S and np.median(rs[i:j]) < hand:
                pulses.append((i, j, sign))
    pulses.sort()
    rides, k = [], 0
    while k < len(pulses) - 1:
        i0, j0, s0 = pulses[k]
        i1, j1, s1 = pulses[k + 1]
        if s1 == -s0 and (i1 - j0) / HZ <= 60:
            seg = slice(max(i0 - HZ // 2, 0), min(j1 + HZ // 2, len(a)))
            v = np.cumsum(a[seg]) / HZ
            v -= np.linspace(0, v[-1], len(v))
            rides.append(dict(t0=t[i0], t1=t[j1], disp=np.sum(v) / HZ))
            k += 2
        else:
            k += 1
    return rides


def stairs(sig, steps, rs, yr, turn_rate, turn_min, net_min, max_s):
    t = sig.t.values
    r = pd.Series(yr).rolling(HZ, center=True, min_periods=1).mean().values
    out = []
    for b in walking_bouts(sig, steps, rs):
        if not 5 <= b['dur'] <= max_s:
            continue
        m = (t >= b['t0']) & (t <= b['t1'])
        rb = r[m]
        turns = [rb[i:j].sum() / HZ for i, j in _runs(np.abs(rb) > turn_rate)]
        net = yr[m].sum() / HZ
        if max((abs(x) for x in turns), default=0) < turn_min or abs(net) < net_min:
            continue
        out.append(dict(t0=b['t0'], t1=b['t1'], delta=1 if net > 0 else -1, net=net))
    return out


def load_all(roots):
    data = []
    for root in roots:
        for d in sorted(p for p in Path(root).iterdir() if (p / 'sensors.csv').exists()):
            meta, s, ev, wifi = load_session(d)
            lab = label(meta, ev)
            sig, steps, a, rs = prep(s)
            yr = yaw_rate(s, sig.t.values)
            data.append(dict(set=Path(root).name[:6], name=d.name[:15], kind=true_kind(meta, lab),
                             truth=lab['end'] - lab['start'], lab=lab, sig=sig, steps=steps, a=a, rs=rs, yr=yr,
                             who=meta['labels']['collector']))
    return data


def evaluate(data, hand=0.35, turn_rate=30.0, turn_min=90.0, net_min=150.0, max_s=60, verbose=False):
    rows = []
    for x in data:
        ev = [dict(kind='E', delta=int(round(r['disp'] / FLOOR_H)), t0=r['t0'], t1=r['t1'])
              for r in elevator_rides(x['sig'], x['a'], x['rs'], hand)]
        ev = [e for e in ev if e['delta']]
        ev += [dict(kind='S', **b) for b in stairs(x['sig'], x['steps'], x['rs'], x['yr'],
                                                     turn_rate, turn_min, net_min, max_s)]
        out, until = [], -np.inf
        for e in sorted(ev, key=lambda e: e['t1']):
            if e['t0'] <= until:
                continue
            out.append(e)
            until = e['t1']
        got = sum(e['delta'] for e in out)
        kinds = {e['kind'] for e in out}
        if x['kind'] == 'walk':
            v = 'OK' if not out else '误报'
        elif not out:
            v = '漏检'
        elif got != x['truth']:
            v = '层数错'
        elif kinds != {'E' if x['kind'] == 'elevator' else 'S'}:
            v = '类型错'
        else:
            v = 'OK'
        arrive = x['lab'].get('move_end')
        lag = max(e['t1'] for e in out) - arrive if out and arrive is not None else np.nan
        rows.append(dict(set=x['set'], who=x['who'], name=x['name'], kind=x['kind'], truth=x['truth'], got=got,
                         v=v, det=';'.join(f"{e['kind']}{e['delta']:+d}" for e in out) or '-', lag=lag))
    df = pd.DataFrame(rows)
    if verbose:
        print(df.to_string(index=False))
    return df


def summary(df):
    s = df.groupby('kind').v.value_counts().unstack(fill_value=0)
    return s


if __name__ == '__main__':
    roots = sorted(str(p) for p in (HERE / '数据').iterdir() if p.is_dir())
    data = load_all(roots)
    pd.set_option('display.width', 200)
    print('== 现在的规则（标签修正后）')
    base = evaluate(data, verbose=True)
    print(summary(base))
    print('\n== 电梯手晃门限 HAND_STD_MAX')
    for hand in (0.35, 0.5, 0.6, 0.7, 0.8, 1.0):
        print(hand, summary(evaluate(data, hand=hand)).to_dict('index'))
    print('\n== 楼梯转身：TURN_RATE / TURN_MIN')
    for tr, tm in ((30, 90), (20, 90), (15, 90), (20, 60), (15, 60), (10, 60)):
        print(tr, tm, summary(evaluate(data, hand=0.7, turn_rate=tr, turn_min=tm)).to_dict('index'))
