"""Shared loading for dynamic sessions: sensors on the elapsed-realtime clock, events, labels."""
import csv, json
from pathlib import Path
import numpy as np, pandas as pd

def load_session(d):
    d = Path(d)
    meta = json.loads((d/'meta.json').read_text())
    s = pd.read_csv(d/'sensors.csv', usecols=['t_ns', 'sensor', 'v0', 'v1', 'v2', 'v3'])
    ev = pd.read_csv(d/'events.csv')
    t0 = ev.t_elapsed_ms.iloc[0] / 1000.0  # session_start on the same elapsedRealtime clock
    s['t'] = s.t_ns / 1e9 - t0
    ev['t'] = ev.t_elapsed_ms / 1000.0 - t0
    wifi = pd.read_csv(d/'wifi.csv')
    wifi['t'] = wifi.t_elapsed_ms / 1000.0 - t0
    return meta, s, ev, wifi

def series(s, name, cols=('v0', 'v1', 'v2')):
    x = s[s.sensor == name].sort_values('t')
    return x.t.values, x[list(cols)].values.astype(float)

def resample(t, v, grid):
    return np.column_stack([np.interp(grid, t, v[:, i]) for i in range(v.shape[1])])

def signals(s, hz=25):
    """Vertical linear acceleration (world frame via gravity), |mag|, heading, steps on a uniform grid."""
    ta, acc = series(s, 'acc'); tg, grav = series(s, 'gravity'); tm, mag = series(s, 'mag')
    tr, rv = series(s, 'game_rot_vec', ('v0', 'v1', 'v2', 'v3'))
    grid = np.arange(max(ta[0], tg[0]), min(ta[-1], tg[-1]), 1 / hz)
    A, G, M = resample(ta, acc, grid), resample(tg, grav, grid), resample(tm, mag, grid)
    g = np.linalg.norm(G, axis=1, keepdims=True); gu = G / g
    a_v = (A * gu).sum(1) - g[:, 0]                           # + = upward acceleration
    # heading (yaw) from game rotation vector quaternion (x,y,z,w); unwrap for turn accumulation
    R = resample(tr, rv, grid); x, y, z, w = R.T
    yaw = np.unwrap(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    steps = np.sort(s[s.sensor == 'step_detector'].t.values)
    return pd.DataFrame(dict(t=grid, a_v=a_v, mag=np.linalg.norm(M, axis=1), yaw=np.degrees(yaw))), steps

def label(meta, ev):
    l = meta['labels']
    m = l['mode']
    if m == 'walk':
        return dict(kind='walk', start=int(l['floor']), end=int(l['floor']), run=l['notes'])
    return dict(kind=l['method'], start=int(l['floor']), end=int(l['to_floor']), run=l['notes'],
                move_start=ev[ev.event == 'move_start'].t.iloc[0], move_end=ev[ev.event == 'move_end'].t.iloc[0])
