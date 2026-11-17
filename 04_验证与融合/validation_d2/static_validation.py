"""D2 static validation: score the frozen D1 models on 15 independent VA points.

Models are loaded from floorsense_c/results/models (fit on all 50 Train points only).
Nothing from the validation set is used for fitting or for the AP vocabulary.
meta.json says split=train because the collector app has no Validation option;
points are relabelled from point_labels.json (split=Validation) and checked.
"""
import argparse, csv, hashlib, json, sys
from collections import defaultdict
from pathlib import Path
import joblib, numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # project_wireless
C_DIR = ROOT/'03_C_模型与测试'/'floorsense_c_D1版'
sys.path.insert(0, str(C_DIR))
from pipeline import encode_transform, MAX_AGE_MS  # same feature rules as training

FLOORS, ZONES = [2, 3, 4, 5, 6], ['West', 'Middle', 'East']


def load(data_dir, labels):
    recs, q = [], defaultdict(int)
    for mp in sorted(data_dir.glob('*/meta.json')):
        meta = json.loads(mp.read_text()); lab = meta['labels']; pid = lab['point_id']
        plan = labels[pid]
        assert plan['split'] == 'Validation', pid
        assert int(lab['floor']) == plan['floor'] and lab['zone'] == plan['anchor_zone'] and lab['mode'] == 'static'
        scans = defaultdict(list)
        with (mp.parent/'wifi.csv').open(newline='') as f:
            for r in csv.DictReader(f): scans[int(r['scan_id'])].append(r)
        q['sessions'] += 1
        for sid, rows in sorted(scans.items()):
            q['raw_scans'] += 1
            if not all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows):
                q['excluded_not_fresh'] += 1; continue
            vals = defaultdict(list)
            for r in rows:
                if not r['bssid'].strip(): continue
                rssi = float(r['rssi'])
                if not np.isfinite(rssi) or not -127 <= rssi < 0: continue
                if not 0 <= float(r['age_ms']) <= MAX_AGE_MS: q['stale_ap_rows'] += 1; continue
                vals[r['bssid'].lower()].append(float(np.clip(rssi, -100, -20)))
            fp = {b: float(np.median(v)) for b, v in sorted(vals.items())}
            if not fp: q['empty_scans'] += 1; continue
            recs.append(dict(session_id=meta['session_id'], scan_id=sid, point_id=pid, floor=plan['floor'],
                             zone=plan['zone'], orientation=lab['orientation'], t=meta['start_local'],
                             fp_hash=hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest(),
                             n_ap=len(fp), features=fp))
    return pd.DataFrame(recs), dict(q)


def predict(df, bundle):
    vocab = bundle['config']['bssids']; vs = set(vocab)
    x = encode_transform(df.features.tolist(), vocab)
    out = df.drop(columns='features').copy()
    out['known_aps'] = [sum(b in vs for b in fp) for fp in df.features]
    out['unknown_ap_frac'] = 1 - out.known_aps/out.n_ap
    for t, m in bundle['models'].items():
        out[f'{t}_pred'] = m.predict(x)
        out[f'{t}_score'] = m.predict_proba(x).max(1)
    return out


def summarize(p):
    fo, zo = p.floor_pred == p.floor, p.zone_pred == p.zone
    return dict(n=len(p), floor_acc=fo.mean(), zone_acc=zo.mean(), joint_acc=(fo & zo).mean(),
                west_recall=zo[p.zone == 'West'].mean(), middle_recall=zo[p.zone == 'Middle'].mean(),
                east_recall=zo[p.zone == 'East'].mean())


def point_vote(p):
    """One vote per point (all 4 orientations): what a smoothed app would show if you stood there."""
    g = p.groupby('point_id')
    return pd.DataFrame(dict(floor=g.floor.first(), zone=g.zone.first(),
                             floor_pred=g.floor_pred.agg(lambda s: s.value_counts().sort_index().idxmax()),
                             zone_pred=g.zone_pred.agg(lambda s: s.value_counts().sort_index().idxmax()))).reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', type=Path, default=ROOT/'02_数据'/'Validation'/'static_validation')
    ap.add_argument('--out', type=Path, default=HERE/'results')
    a = ap.parse_args(); a.out.mkdir(exist_ok=True)
    labels = json.loads((C_DIR/'point_labels.json').read_text())
    raw, q = load(a.data, labels)
    dedup = raw.drop_duplicates(['point_id', 'fp_hash']).reset_index(drop=True)
    # leakage check: no VA fingerprint identical to any training fingerprint
    train_hashes = set(pd.read_csv(C_DIR/'results/clean_samples.csv').fingerprint)
    q.update(fresh_scans=len(raw), dedup_scans=len(dedup), points=raw.point_id.nunique(),
             va_fp_equal_to_train=int(raw.fp_hash.isin(train_hashes).sum()),
             dates=sorted(raw.t.str[:10].unique().tolist()))
    rows, allpred = [], []
    for name in ('KNN', 'RandomForest'):
        b = joblib.load(C_DIR/f'results/models/{name}.joblib')
        for ds, df in (('deduplicated', dedup), ('all_fresh', raw)):
            p = predict(df, b); p.insert(0, 'model', name); p.insert(1, 'dataset', ds)
            allpred.append(p); rows.append(dict(model=name, dataset=ds, **summarize(p)))
        pv = point_vote(allpred[-2])
        rows.append(dict(model=name, dataset='point_vote_15', n=len(pv), floor_acc=(pv.floor == pv.floor_pred).mean(),
                         zone_acc=(pv.zone == pv.zone_pred).mean(),
                         joint_acc=((pv.floor == pv.floor_pred) & (pv.zone == pv.zone_pred)).mean()))
        pv.to_csv(a.out/f'point_vote_{name}.csv', index=False)
    pred = pd.concat(allpred); pred.to_csv(a.out/'predictions.csv', index=False)
    m = pd.DataFrame(rows); m.to_csv(a.out/'metrics.csv', index=False)
    for name in ('KNN', 'RandomForest'):
        p = pred[(pred.model == name) & (pred.dataset == 'deduplicated')]
        pd.crosstab(p.floor, p.floor_pred).reindex(index=FLOORS, columns=FLOORS, fill_value=0).to_csv(a.out/f'confusion_{name}_floor.csv')
        pd.crosstab(p.zone, p.zone_pred).reindex(index=ZONES, columns=ZONES, fill_value=0).to_csv(a.out/f'confusion_{name}_zone.csv')
        pp = p.assign(fo=p.floor == p.floor_pred, zo=p.zone == p.zone_pred).groupby('point_id').agg(
            floor=('floor', 'first'), zone=('zone', 'first'), n=('fo', 'size'), floor_ok=('fo', 'sum'), zone_ok=('zo', 'sum'),
            floor_preds=('floor_pred', lambda s: dict(s.value_counts())), zone_preds=('zone_pred', lambda s: dict(s.value_counts())),
            min_known=('known_aps', 'min'), med_known=('known_aps', 'median'), unk_frac=('unknown_ap_frac', 'mean'))
        pp.to_csv(a.out/f'per_point_{name}.csv')
        pf = p.assign(fo=p.floor == p.floor_pred, zo=p.zone == p.zone_pred).groupby('floor')[['fo', 'zo']].mean()
        pf.to_csv(a.out/f'per_floor_{name}.csv')
    (a.out/'data_quality.json').write_text(json.dumps(q, indent=2, ensure_ascii=False))
    print(json.dumps(q, ensure_ascii=False)); print(m.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
