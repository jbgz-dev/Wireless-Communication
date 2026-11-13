"""Model-selection experiments on D2 Validation. All fits use Train data only.
Validation is used to choose between a few pre-listed variants; with 15 points the
differences of 1 point (6.7 pp point-level) are within noise, so only large effects count."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
HERE = Path(__file__).resolve().parent; C = HERE.parent/'floorsense_c'
sys.path.insert(0, str(C))
from pipeline import load_data, encode_fit, encode_transform, point_folds
from static_validation import load

labels = json.loads((C/'point_labels.json').read_text())
_, train, _ = load_data(Path('/tmp/fs_static/sessions'), C/'point_labels.json')
va_raw, _ = load(HERE.parent/'static_validation', labels)
va = va_raw.drop_duplicates(['point_id', 'fp_hash']).reset_index(drop=True)

def rf(): return RandomForestClassifier(n_estimators=300, max_features='sqrt', class_weight='balanced_subsample', random_state=42, n_jobs=-1)
def knn(): return KNeighborsClassifier(n_neighbors=5, weights='distance')

def positive(x): return (x + 100) / 80          # 0 = missing, 1 = -20 dBm
def exp_(x):   return np.exp((x + 100) / 20) - 1 # strong APs dominate, missing = 0
def binar(x):  return (x > -100).astype(np.float32)

VARIANTS = {
    'base': dict(),
    'drop_sparse_train(<10AP)': dict(min_train_ap=10),
    'exp_features': dict(tf=exp_),
    'drop_sparse+exp': dict(min_train_ap=10, tf=exp_),
    'drop_sparse+presence+rssi': dict(min_train_ap=10, tf=lambda x: np.hstack([positive(x), binar(x)])),
}

def fit_predict(tr, te, min_train_ap=0, tf=lambda x: x):
    tr = tr[tr.n_ap >= min_train_ap]
    vocab, xtr = encode_fit(tr.features.tolist()); xte = encode_transform(te.features.tolist(), vocab)
    xtr, xte = tf(xtr), tf(xte)
    out = {}
    for name, mk in (('RF', rf), ('KNN', knn)):
        for t in ('floor', 'zone'):
            out[(name, t)] = mk().fit(xtr, tr[t]).predict(xte)
    return out

def score(df, pred, name):
    fo, zo = pred[(name, 'floor')] == df.floor.values, pred[(name, 'zone')] == df.zone.values
    w = df.zone.values == 'West'
    pts = pd.DataFrame(dict(p=df.point_id, fo=fo)).groupby('p').fo.mean()
    return dict(floor=fo.mean(), zone=zo.mean(), joint=(fo & zo).mean(), west=zo[w].mean(), worst_point_floor=pts.min())

if __name__ == '__main__':
    rows = []
    for vname, kw in VARIANTS.items():
        # 5-fold Point-grouped CV on Train (seed 42), same folds as C's main result
        folds = point_folds(train, 42); cv = {}
        for k in sorted(set(folds.values())):
            te = train[train.point_id.map(folds) == k]; tr = train[train.point_id.map(folds) != k]
            p = fit_predict(tr, te, **kw)
            for key, v in p.items(): cv.setdefault(key, pd.Series(index=train.index, dtype=object)).loc[te.index] = v
        cvp = {k: v.values for k, v in cv.items()}
        vp = fit_predict(train, va, **kw)
        for m in ('RF', 'KNN'):
            rows.append(dict(variant=vname, model=m, **{f'cv_{k}': v for k, v in score(train, cvp, m).items()},
                             **{f'va_{k}': v for k, v in score(va, vp, m).items()}))
    r = pd.DataFrame(rows); r.to_csv(HERE/'results/variants.csv', index=False)
    pd.set_option('display.width', 250)
    print(r.round(3).to_string(index=False))
