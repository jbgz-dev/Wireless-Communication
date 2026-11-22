"""逐条列出当前检测规则（App 用的陀螺仪版）在每个动态 session 上的结果，和真值对比。

只看传感器检测，不涉及 Wi-Fi 模型。运行：python audit.py [数据目录...]
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from dynlib import load_session, label
from detect import prep, elevator_rides, walking_bouts, yaw_rate, stairs_by_turns

HERE = Path(__file__).resolve().parent
FLOOR_H = 4.0


def detect(s):
    sig, steps, a, rs = prep(s)
    ev = []
    for r in elevator_rides(sig, a, rs):
        d = int(round(r['disp_m'] / FLOOR_H))
        if d:
            ev.append(dict(kind='elevator', delta=d, t0=r['t0'], t1=r['t1'], disp=r['disp_m']))
    yr = yaw_rate(s, sig.t.values)
    for b in stairs_by_turns(sig, steps, rs, yr):
        ev.append(dict(kind='stairs', delta=b['delta'], t0=b['t0'], t1=b['t1'], net=b['net']))
    # MotionDetector.kt：按 t1 排序，t0 落在上一个已输出事件内的丢弃
    out, until = [], -np.inf
    for e in sorted(ev, key=lambda e: e['t1']):
        if e['t0'] <= until:
            continue
        out.append(e)
        until = e['t1']
    bouts = walking_bouts(sig, steps, rs)
    return out, bouts, sig, yr


def main(roots):
    rows = []
    for root in roots:
        for d in sorted(p for p in Path(root).iterdir() if (p / 'sensors.csv').exists()):
            meta, s, ev, wifi = load_session(d)
            lab = label(meta, ev)
            det, bouts, sig, yr = detect(s)
            truth = lab['end'] - lab['start']
            got = sum(e['delta'] for e in det)
            kinds = {e['kind'] for e in det}
            want = lab['kind'] if truth else 'walk'
            if want == 'walk':
                verdict = 'OK' if not det else '误报'
            elif not det:
                verdict = '漏检'
            elif got != truth:
                verdict = '层数错'
            elif kinds != {'elevator' if want == 'elevator' else 'stairs'}:
                verdict = '类型错'
            else:
                verdict = 'OK'
            # 到达后多久确认（检测在线延迟另加约 1.5 s）
            arrive = lab.get('move_end')
            lag = (max(e['t1'] for e in det) - arrive) if det and arrive is not None else None
            dur = sig.t.values[-1]
            after = dur - arrive if arrive is not None else None
            mv = (lab['move_end'] - lab['move_start']) if arrive is not None else None
            rows.append(dict(
                set=Path(root).name[:14], session=d.name[:15], kind=want, truth=truth, got=got,
                verdict=verdict,
                det=';'.join(f"{'E' if e['kind'] == 'elevator' else 'S'}{e['delta']:+d}"
                             + (f"({e['disp']:.1f}m)" if 'disp' in e else f"({e['net']:.0f}°)") for e in det) or '-',
                move_s=None if mv is None else round(mv, 1),
                after_s=None if after is None else round(after, 1),
                lag_s=None if lag is None else round(lag, 1),
                bouts=';'.join(f"{b['dur']:.0f}s" for b in bouts),
            ))
    df = pd.DataFrame(rows)
    pd.set_option('display.width', 250, 'display.max_rows', 200, 'display.max_colwidth', 60)
    print(df.to_string(index=False))
    print()
    print(df.groupby(['set', 'verdict']).size().unstack(fill_value=0))
    return df


if __name__ == '__main__':
    roots = sys.argv[1:] or sorted(str(p) for p in (HERE / '数据').iterdir() if p.is_dir())
    main(roots)
