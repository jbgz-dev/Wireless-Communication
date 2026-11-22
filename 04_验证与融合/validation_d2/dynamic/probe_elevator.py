"""看电梯 session 在 move_start..move_end 附近的竖直加速度脉冲，找出漏检原因。"""
import sys
from pathlib import Path
import numpy as np
from dynlib import load_session, label
from detect import prep, _runs, HZ, PULSE_A, PULSE_MIN_S, HAND_STD_MAX

for d in sys.argv[1:]:
    meta, s, ev, wifi = load_session(d)
    lab = label(meta, ev)
    sig, steps, a, rs = prep(s)
    t = sig.t.values
    print(f"== {Path(d).name}  {lab['start']}->{lab['end']}  move {lab['move_start']:.1f}..{lab['move_end']:.1f}s")
    for sign in (+1, -1):
        # 不加手晃门限，列出所有达到幅度的脉冲
        for i, j in _runs(sign * a > PULSE_A):
            dur = (j - i) / HZ
            if dur < 0.3:
                continue
            med = np.median(rs[i:j])
            peak = (sign * a[i:j]).max()
            ok = dur >= PULSE_MIN_S and med < HAND_STD_MAX
            print(f"  {'+' if sign > 0 else '-'} {t[i]:6.1f}-{t[j-1]:6.1f}s dur {dur:.2f}s peak {peak:.2f} "
                  f"hand_std {med:.2f} {'通过' if ok else '被过滤'}")
    m = (t > lab['move_start'] - 2) & (t < lab['move_end'] + 2)
    print(f"  移动期间 raw_std 中位数 {np.median(rs[m]):.2f}，最大平滑加速度 +{a[m].max():.2f} / {a[m].min():.2f}")
