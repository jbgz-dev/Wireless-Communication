"""E: vertical-transition detector from phone sensors (no barometer).

Elevator: start/stop acceleration pulses on the world-vertical axis (gravity sensor gives the axis),
displacement by double integration between a start pulse and the opposite stop pulse.
Stairs: walking bout (step detector + acceleration energy) whose vertical-acceleration features look
like stairs; direction and floor are decided together with Wi-Fi (see fuse.py).
Thresholds were chosen on dynamic_train only.
"""
import numpy as np, pandas as pd
from dynlib import signals

HZ = 25
PULSE_A = 0.25      # m/s^2, smoothed vertical accel during elevator start/stop
PULSE_MIN_S = 0.6
HAND_STD_MAX = 0.35  # 1-s std of raw vertical accel; walking is ~1.0, elevator ~0.1
FLOOR_H = None       # metres per floor, calibrated in calibrate()

def _runs(mask):
    """(start, end) index pairs of consecutive True."""
    m = np.r_[False, mask, False].astype(int); d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))

def prep(s):
    sig, steps = signals(s, HZ)
    raw_std = sig.a_v.rolling(HZ, center=True, min_periods=1).std().fillna(0).values
    quiet = raw_std < 0.15
    bias = np.median(sig.a_v.values[quiet]) if quiet.sum() > HZ else np.median(sig.a_v.values)
    a = pd.Series(sig.a_v.values - bias).rolling(int(HZ / 2), center=True, min_periods=1).mean().values
    return sig, steps, a, raw_std

def elevator_rides(sig, a, raw_std):
    """List of rides: dict(t0, t1, direction, disp_m)."""
    t = sig.t.values
    pulses = []
    for sign in (+1, -1):
        for i, j in _runs((sign * a > PULSE_A) & (raw_std < HAND_STD_MAX + 0.25)):
            if (j - i) / HZ >= PULSE_MIN_S and np.median(raw_std[i:j]) < HAND_STD_MAX:
                pulses.append((i, j, sign))
    pulses.sort()
    rides, k = [], 0
    while k < len(pulses) - 1:
        i0, j0, s0 = pulses[k]; i1, j1, s1 = pulses[k + 1]
        gap = (i1 - j0) / HZ
        if s1 == -s0 and gap <= 60:
            seg = slice(max(i0 - HZ // 2, 0), min(j1 + HZ // 2, len(a)))
            v = np.cumsum(a[seg]) / HZ
            v -= np.linspace(0, v[-1], len(v))        # car is at rest at both ends
            disp = np.sum(v) / HZ
            rides.append(dict(t0=t[i0], t1=t[j1], direction=s0, disp_m=disp))
            k += 2
        else:
            k += 1
    return rides

def walking_bouts(sig, steps, raw_std, min_s=6):
    t = sig.t.values
    walk = pd.Series(raw_std > 0.5).rolling(2 * HZ, center=True, min_periods=1).mean().values > 0.5
    out = []
    for i, j in _runs(walk):
        if (j - i) / HZ < min_s: continue
        a = sig.a_v.values[i:j]
        n_steps = int(((steps >= t[i]) & (steps <= t[j - 1])).sum())
        out.append(dict(t0=t[i], t1=t[j - 1], dur=(j - i) / HZ, steps=n_steps, std=a.std(),
                        p95=np.percentile(a, 95), p5=np.percentile(a, 5)))
    return out
