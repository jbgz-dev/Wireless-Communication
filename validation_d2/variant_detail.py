"""Detail for the chosen variant (RF, Train scans with >=10 APs) + per-floor zone models."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; C = HERE.parent/'floorsense_c'
sys.path.insert(0, str(C)); sys.path.insert(0, str(HERE))
from pipeline import load_data, encode_fit, encode_transform, point_folds
from static_validation import load
from variants import rf
labels = json.loads((C/'point_labels.json').read_text())
_, train, _ = load_data(Path('/tmp/fs_static/sessions'), C/'point_labels.json')
va = load(HERE.parent/'static_validation', labels)[0].drop_duplicates(['point_id', 'fp_hash']).reset_index(drop=True)
tr = train[train.n_ap >= 10]
print('train rows kept', len(tr), 'of', len(train), '; dropped:', train[train.n_ap < 10][['point_id', 'n_ap']].values.tolist())
vocab, xtr = encode_fit(tr.features.tolist()); xva = encode_transform(va.features.tolist(), vocab)
vs = set(vocab); va['known'] = [sum(b in vs for b in f) for f in va.features]
fm = rf().fit(xtr, tr.floor); zm = rf().fit(xtr, tr.zone)
va['floor_pred'] = fm.predict(xva); va['floor_score'] = fm.predict_proba(xva).max(1); va['zone_pred'] = zm.predict(xva)
# hierarchical: zone model trained per floor, applied on predicted floor
hz = {f: rf().fit(xtr[(tr.floor == f).values], tr.zone[tr.floor == f]) for f in sorted(tr.floor.unique())}
va['zone_pred_h'] = [hz[f].predict(x[None])[0] for f, x in zip(va.floor_pred, xva)]
print('\nfloor errors:'); print(va[va.floor != va.floor_pred][['point_id', 'orientation', 'n_ap', 'known', 'floor_pred', 'floor_score']].to_string(index=False))
for col in ('zone_pred', 'zone_pred_h'):
    zo = va[col] == va.zone; print(f'\n{col}: zone {zo.mean():.3f} joint {((va.floor == va.floor_pred) & zo).mean():.3f}',
        'recall', {z: round(zo[va.zone == z].mean(), 3) for z in ('West', 'Middle', 'East')})
    print(pd.crosstab(va.zone, va[col]).reindex(index=['West', 'Middle', 'East'], columns=['West', 'Middle', 'East'], fill_value=0))
print('\nper point (flat zone model):')
g = va.assign(fo=va.floor == va.floor_pred, zo=va.zone_pred == va.zone, zh=va.zone_pred_h == va.zone).groupby('point_id')
print(g.agg(n=('fo', 'size'), floor_ok=('fo', 'sum'), zone_ok=('zo', 'sum'), zone_h_ok=('zh', 'sum'), med_known=('known', 'median')).to_string())
print('\nfloor confusion'); print(pd.crosstab(va.floor, va.floor_pred))
# CV check of hierarchical zone on Train (same folds)
folds = point_folds(train, 42); flat, hier = [], []
for k in range(5):
    te = train[train.point_id.map(folds) == k]; t2 = train[(train.point_id.map(folds) != k) & (train.n_ap >= 10)]
    v, x = encode_fit(t2.features.tolist()); xt = encode_transform(te.features.tolist(), v)
    fp = rf().fit(x, t2.floor).predict(xt); zf = rf().fit(x, t2.zone).predict(xt)
    h = {f: rf().fit(x[(t2.floor == f).values], t2.zone[t2.floor == f]) for f in sorted(t2.floor.unique())}
    zh = [h[f].predict(r[None])[0] for f, r in zip(fp, xt)]
    flat += list(zf == te.zone.values); hier += list(np.array(zh) == te.zone.values)
print(f'\nCV zone: flat {np.mean(flat):.3f}  hierarchical {np.mean(hier):.3f}')
va.drop(columns='features').to_csv(HERE/'results/predictions_rf_drop_sparse.csv', index=False)
