"""Reproducible Wi-Fi-only baselines. No validation data enter AP vocabulary fitting."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.dummy import DummyClassifier
from sklearn.model_selection import StratifiedKFold, StratifiedGroupKFold
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

BASE = Path(__file__).resolve().parent
MISSING = -100.0
MAX_AGE_MS = 10000


def encode_transform(fingerprints, vocab):
    lookup = {b: i for i, b in enumerate(vocab)}
    x = np.full((len(fingerprints), len(vocab)), MISSING, dtype=np.float32)
    for i, fp in enumerate(fingerprints):
        for b, rssi in fp.items():
            if b in lookup:
                x[i, lookup[b]] = np.clip(rssi, -100, -20)
    return x


def encode_fit(fingerprints):
    vocab = sorted({b for fp in fingerprints for b in fp})
    if not vocab:
        raise ValueError('No usable access points')
    return vocab, encode_transform(fingerprints, vocab)


def connected_groups(rows):
    """Keep points sharing identical model inputs in the same CV partition."""
    parent = {r['point_id']: r['point_id'] for r in rows}
    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    seen = {}
    for r in rows:
        p, fp = r['point_id'], r['fingerprint']
        if fp in seen:
            a, b = find(p), find(seen[fp])
            parent[max(a, b)] = min(a, b)
        else:
            seen[fp] = p
    return {p: find(p) for p in parent}


def load_data(data_dir, labels_path):
    labels = json.loads(labels_path.read_text())
    records, quality = [], defaultdict(int)
    for meta_path in sorted(data_dir.glob('*/meta.json')):
        meta = json.loads(meta_path.read_text())
        lab = meta['labels']
        if lab['split'].lower() != 'train' or lab['mode'] != 'static':
            raise ValueError(f'Unexpected split/mode: {meta_path}')
        point = lab['point_id']
        plan = labels[point]
        assert int(lab['floor']) == plan['floor'] and lab['zone'] == plan['anchor_zone']
        assert plan['split'] == 'Train'
        scans = defaultdict(list)
        with (meta_path.parent/'wifi.csv').open(newline='') as f:
            for row in csv.DictReader(f):
                scans[int(row['scan_id'])].append(row)
                quality['raw_ap_rows'] += 1
        quality['sessions'] += 1
        for scan_id, rows in sorted(scans.items()):
            quality['raw_scans'] += 1
            fresh = all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows)
            if not fresh:
                quality['excluded_not_fresh_scans'] += 1
                continue
            values = defaultdict(list)
            for r in rows:
                rssi = float(r['rssi'])
                if not np.isfinite(rssi) or not -127 <= rssi < 0 or not r['bssid'].strip():
                    quality['invalid_ap_rows'] += 1
                    continue
                if float(r['age_ms']) < 0 or float(r['age_ms']) > MAX_AGE_MS:
                    quality['stale_ap_rows'] += 1
                    continue
                values[r['bssid'].lower()].append(float(np.clip(rssi, -100, -20)))
            quality['duplicate_bssid_rows'] += sum(len(v)-1 for v in values.values())
            fp = {b: float(np.median(v)) for b, v in sorted(values.items())}
            if not fp:
                quality['excluded_empty_scans'] += 1
                continue
            fingerprint = hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()
            records.append(dict(sample_id=f"{meta['session_id']}:{scan_id}", session_id=meta['session_id'],
                                point_id=point, floor=plan['floor'], zone=plan['zone'],
                                orientation=lab['orientation'], date=meta['start_local'][:10],
                                fingerprint=fingerprint, n_ap=len(fp), features=fp))
    raw = pd.DataFrame(records)
    groups = connected_groups(records)
    raw['cv_group'] = raw.point_id.map(groups)
    clean = raw.drop_duplicates(['point_id', 'fingerprint']).reset_index(drop=True)
    quality.update(eligible_scans=len(raw), clean_scans=len(clean), points=raw.point_id.nunique(),
                   removed_within_point_duplicates=len(raw)-len(clean),
                   global_unique_fingerprints=raw.fingerprint.nunique(), cv_groups=len(set(groups.values())),
                   bssids=len({b for fp in raw.features for b in fp}),
                   cross_point_fingerprint_clusters=int((raw.groupby('fingerprint').point_id.nunique()>1).sum()))
    return raw, clean, dict(quality)


def point_folds(df, seed):
    points = df[['point_id','floor','cv_group']].drop_duplicates().sort_values('point_id').reset_index(drop=True)
    if points.cv_group.nunique() == len(points):
        splitter = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        splits = splitter.split(points, points.floor)
    else:
        splitter = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
        splits = splitter.split(points, points.floor, points.cv_group)
    mapping = {}
    for fold, (_, test) in enumerate(splits):
        mapping.update({p: fold for p in points.iloc[test].point_id})
    return mapping


def make_model(name):
    if name == 'KNN':
        return KNeighborsClassifier(n_neighbors=5, weights='distance', metric='euclidean')
    if name == 'RandomForest':
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=1, max_features='sqrt',
                                      class_weight='balanced_subsample', random_state=42, n_jobs=2)
    return DummyClassifier(strategy='most_frequent')


def evaluate(df, mapping, seed, dataset):
    predictions, diagnostics = [], []
    for fold in range(5):
        test_mask = df.point_id.map(mapping).eq(fold)
        train, test = df[~test_mask], df[test_mask]
        assert set(train.point_id).isdisjoint(test.point_id)
        assert set(train.cv_group).isdisjoint(test.cv_group)
        assert set(train.fingerprint).isdisjoint(test.fingerprint)
        assert set(train.floor) == {2,3,4,5,6}
        assert set(train.zone) == {'West','Middle','East'}
        vocab, x_train = encode_fit(train.features.tolist())
        x_test = encode_transform(test.features.tolist(), vocab)
        vocab_set = set(vocab)
        unseen = [sum(b not in vocab_set for b in fp)/len(fp) for fp in test.features]
        diagnostics.append(dict(dataset=dataset,seed=seed,fold=fold,train_points=train.point_id.nunique(),
                                test_points=test.point_id.nunique(),train_samples=len(train),test_samples=len(test),
                                n_features=len(vocab),test_mean_unseen_ap_fraction=float(np.mean(unseen)),
                                train_test_point_overlap=0,train_test_fingerprint_overlap=0))
        for name in ('Dummy','KNN','RandomForest'):
            result = test.drop(columns='features').copy()
            result['dataset'], result['seed'], result['fold'], result['model'] = dataset,seed,fold,name
            for target in ('floor','zone'):
                model = make_model(name).fit(x_train, train[target])
                result[f'{target}_pred'] = model.predict(x_test)
            predictions.append(result)
    pred = pd.concat(predictions,ignore_index=True)
    assert pred.groupby('model').sample_id.nunique().eq(len(df)).all()
    return pred, pd.DataFrame(diagnostics)


def metric_rows(pred, by):
    rows = []
    for keys, part in pred.groupby(by):
        if not isinstance(keys,tuple): keys = (keys,)
        row = dict(zip(by, keys))
        for target in ('floor','zone'):
            ok = part[target].eq(part[f'{target}_pred'])
            row[f'{target}_accuracy'] = float(ok.mean())
            labels = [2,3,4,5,6] if target == 'floor' else ['West','Middle','East']
            row[f'{target}_macro_f1'] = f1_score(part[target],part[f'{target}_pred'],labels=labels,average='macro',zero_division=0)
            row[f'{target}_point_balanced_accuracy'] = float(ok.groupby(part.point_id).mean().mean())
        row['joint_accuracy'] = float((part.floor.eq(part.floor_pred)&part.zone.eq(part.zone_pred)).mean())
        row['n_samples'] = len(part)
        rows.append(row)
    return pd.DataFrame(rows)


def bootstrap_point_ci(pred):
    """Descriptive point-cluster bootstrap, not independent CV retraining uncertainty."""
    rows = []
    for model, part in pred.groupby('model'):
        for target in ('floor','zone'):
            point_acc = part[target].eq(part[f'{target}_pred']).groupby(part.point_id).mean().to_numpy()
            rng = np.random.default_rng(2026)
            samples = rng.choice(point_acc, size=(10000,len(point_acc)), replace=True).mean(axis=1)
            lo, hi = np.quantile(samples,[.025,.975])
            rows.append(dict(model=model,target=target,point_balanced_accuracy=float(point_acc.mean()),
                             descriptive_ci_low=lo,descriptive_ci_high=hi))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=BASE/'data/static/sessions')
    parser.add_argument('--labels',type=Path,default=BASE/'point_labels.json')
    parser.add_argument('--out',type=Path,default=BASE/'results')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    raw, clean, quality = load_data(args.data,args.labels)
    print('QUALITY',json.dumps(quality),flush=True)
    (args.out/'data_quality.json').write_text(json.dumps(quality,indent=2))
    clean.drop(columns='features').to_csv(args.out/'clean_samples.csv',index=False)
    preds, diags, folds = [], [], []
    for seed in range(42,47):
        mapping = point_folds(clean,seed)
        folds.extend(dict(seed=seed,point_id=p,fold=f) for p,f in mapping.items())
        pred,diag = evaluate(clean,mapping,seed,'deduplicated')
        preds.append(pred);diags.append(diag)
        print(f'Completed clean seed {seed}',flush=True)
        if seed == 42:
            pred,diag = evaluate(raw,mapping,seed,'with_duplicates')
            preds.append(pred);diags.append(diag)
    all_pred = pd.concat(preds,ignore_index=True)
    all_pred.to_csv(args.out/'predictions.csv',index=False)
    pd.concat(diags,ignore_index=True).to_csv(args.out/'fold_diagnostics.csv',index=False)
    pd.DataFrame(folds).to_csv(args.out/'point_folds.csv',index=False)
    metrics = metric_rows(all_pred,['dataset','seed','model'])
    metrics.to_csv(args.out/'metrics.csv',index=False)
    metric_rows(all_pred,['dataset','seed','fold','model']).to_csv(args.out/'fold_metrics.csv',index=False)
    primary = all_pred[(all_pred.dataset=='deduplicated') & (all_pred.seed==42)]
    bootstrap_point_ci(primary).to_csv(args.out/'point_bootstrap_ci.csv',index=False)
    metric_rows(primary,['model','point_id']).to_csv(args.out/'point_metrics.csv',index=False)
    for model, part in primary.groupby('model'):
        for target, labels in [('floor',[2,3,4,5,6]),('zone',['West','Middle','East'])]:
            cm = confusion_matrix(part[target],part[f'{target}_pred'],labels=labels)
            pd.DataFrame(cm,index=labels,columns=labels).to_csv(args.out/f'confusion_{model}_{target}.csv')
    # Refit fixed baselines on all cleaned training data; these fits are NOT evaluated as test results.
    model_dir = args.out/'models';model_dir.mkdir(exist_ok=True)
    vocab,x = encode_fit(clean.features.tolist())
    config = dict(bssids=vocab,missing_rssi=MISSING,clip_rssi=[-100,-20],duplicate_bssid_aggregation='median',
                  max_age_ms=MAX_AGE_MS,targets=['floor','zone'],score_is_calibrated_probability=False)
    (model_dir/'feature_config.json').write_text(json.dumps(config,indent=2))
    for name in ('KNN','RandomForest'):
        models = {target:make_model(name).fit(x,clean[target]) for target in ('floor','zone')}
        joblib.dump(dict(config=config,models=models),model_dir/f'{name}.joblib',compress=3)
        restored = joblib.load(model_dir/f'{name}.joblib')
        for target in models:
            assert np.array_equal(models[target].predict(x),restored['models'][target].predict(x))
    # Portable KNN reference data for Android implementation (not an Android binary).
    (model_dir/'knn_reference.json').write_text(json.dumps(dict(config=config,k=5,weights='distance',
        metric='euclidean',x=x.tolist(),floor=clean.floor.tolist(),zone=clean.zone.tolist()),separators=(',',':')))
    (args.out/'example_scan.json').write_text(json.dumps(clean.iloc[0].features,indent=2))
    import sklearn, scipy, matplotlib
    versions = dict(numpy=np.__version__,pandas=pd.__version__,scikit_learn=sklearn.__version__,
                    scipy=scipy.__version__,joblib=joblib.__version__,matplotlib=matplotlib.__version__)
    (args.out/'versions.json').write_text(json.dumps(versions,indent=2))
    print(metrics.to_string(index=False),flush=True)


if __name__ == '__main__':
    main()
