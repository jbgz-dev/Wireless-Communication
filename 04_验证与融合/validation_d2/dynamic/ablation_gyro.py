"""Advanced 3 ablation: accelerometer only vs accelerometer + gyroscope, on dynamic Train and Validation.

Detection is correct when the detected floor changes sum to the true change and every event has the
right kind (walks must give no event). Fusion accuracy uses the same RF and Bayes filter as fuse.py.
Thresholds were set on Train only. Dynamic Test (2026-11-16) is NOT used here: it was inspected
before this change, so scoring on it would not be blind.
"""
import glob, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fuse
from fuse import run, load_data, encode_fit, rf, C, train_data

def main():
    _, train, _ = load_data(train_data.static_sessions(), C/'point_labels.json')
    tr = train[train.n_ap >= 10]; vocab, x = encode_fit(tr.features.tolist()); fm = rf().fit(x, tr.floor)
    rows, traces = [], []
    for split, root in (('train', str(train_data.dynamic_sessions())), ('validation', str(fuse.ROOT/'02_数据'/'Validation'/'dynamic_validation'))):
        for d in sorted(glob.glob(root + '/*/')):
            for gyro in (False, True):
                lab, det, df = run(d, fm, vocab, 'known_start', gyro=gyro)
                ok = sum(e['delta'] for e in det) == lab['end'] - lab['start'] and all(
                    e['kind'] == lab['kind'] for e in det) and (lab['kind'] != 'walk' or not det)
                sc = df[df.truth.notna()]
                after = sc[sc.t > lab['move_end']] if 'move_end' in lab else sc.iloc[:0]
                rows.append(dict(split=split, sensors='acc+gyro' if gyro else 'acc', run=lab['run'] or Path(d).name.split('_')[-2],
                                 kind=lab['kind'], move=f"{lab['start']}->{lab['end']}",
                                 detected=';'.join(f"{e['kind'][:2]}{e['delta']:+d}" for e in det) or '-', det_ok=ok,
                                 n=len(sc), fused_ok=int((sc.fused == sc.truth).sum()),
                                 after_n=len(after), after_ok=int((after.fused == after.truth).sum())))
    r = pd.DataFrame(rows); r.to_csv(HERE/'ablation_gyro_runs.csv', index=False)
    g = r.groupby(['split', 'kind', 'sensors']).agg(runs=('det_ok', 'size'), det_ok=('det_ok', 'sum')).reset_index()
    f = r.groupby(['split', 'sensors'])[['n', 'fused_ok', 'after_n', 'after_ok']].sum()
    f['fused_acc'] = f.fused_ok / f.n; f['after_acc'] = f.after_ok / f.after_n
    g.to_csv(HERE/'ablation_gyro_summary.csv', index=False); f.to_csv(HERE/'ablation_gyro_fusion.csv')
    pd.set_option('display.width', 200)
    print(r[r.kind != 'elevator'].to_string(index=False)); print(); print(g.to_string(index=False)); print(); print(f.round(3))

if __name__ == '__main__':
    main()
