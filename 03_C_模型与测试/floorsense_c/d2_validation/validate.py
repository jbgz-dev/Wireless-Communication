"""Score frozen D1 bundles on the separate D2 Static Validation archive."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from zipfile import ZipFile

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent
sys.path.insert(0, str(ROOT))
from pipeline import MAX_AGE_MS, encode_transform, load_data

LABELS = {'floor': [2, 3, 4, 5, 6], 'zone': ['West', 'Middle', 'East']}
ABSTAIN = {'floor': 0, 'zone': 'Unknown'}


def effective_label(meta_labels, plan):
    point = meta_labels['point_id']
    planned = plan.get(point)
    if (not planned or planned['split'] != 'Validation' or not re.fullmatch(r'T8-F[2-6]-[A-H]-VA\d+', point)
            or meta_labels['building'] != 'T8' or meta_labels['mode'] != 'static'
            or int(meta_labels['floor']) != planned['floor'] or meta_labels['zone'] != planned['anchor_zone']
            or meta_labels['orientation'] not in 'NESW' or len(meta_labels['orientation']) != 1):
        raise ValueError(f'Unexpected Validation identity: {point}')
    return dict(point_id=point, floor=planned['floor'], zone=planned['zone'], anchor_zone=planned['anchor_zone'],
                orientation=meta_labels['orientation'], original_split=meta_labels['split'], split='validation')


def scan_fingerprints(wifi_csv):
    """Mirror D1 scan eligibility, clip-before-median, and BSSID normalization."""
    quality = Counter()
    scans = defaultdict(list)
    for row in csv.DictReader(wifi_csv.splitlines()):
        scans[int(row['scan_id'])].append(row)
        quality['raw_ap_rows'] += 1
    fingerprints = []
    for scan, rows in sorted(scans.items()):
        quality['raw_scans'] += 1
        if not all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows):
            quality['excluded_not_fresh_scans'] += 1
            continue
        values = defaultdict(list)
        for row in rows:
            rssi, age, bssid = float(row['rssi']), float(row['age_ms']), row['bssid'].lower()
            if not np.isfinite(rssi) or not -127 <= rssi < 0 or not bssid.strip():
                quality['invalid_ap_rows'] += 1
                continue
            if not np.isfinite(age) or not 0 <= age <= MAX_AGE_MS:
                quality['stale_ap_rows'] += 1
                continue
            values[bssid].append(float(np.clip(rssi, -100, -20)))
        quality['duplicate_bssid_rows'] += sum(len(v)-1 for v in values.values())
        if not values:
            quality['excluded_empty_scans'] += 1
            continue
        fingerprints.append((scan, {b: float(np.median(v)) for b, v in sorted(values.items())}))
    return fingerprints, quality


def load_validation(archive, plan, train_points):
    records, manifest, quality = [], [], Counter()
    with ZipFile(archive) as z:
        metas = sorted(n for n in z.namelist() if n.endswith('/meta.json') and '__MACOSX' not in PurePosixPath(n).parts)
        for name in metas:
            meta = json.loads(z.read(name))
            label = effective_label(meta['labels'], plan)
            if label['point_id'] in train_points or meta['status'] != 'complete':
                raise ValueError(f'Unexpected training overlap or incomplete session: {name}')
            wifi = z.read(str(PurePosixPath(name).with_name('wifi.csv'))).decode('utf-8-sig')
            scans, q = scan_fingerprints(wifi)
            quality.update(q)
            sid = meta['session_id']
            manifest.append(dict(**label, session_id=sid, meta_entry=name, start_local=meta['start_local'],
                                 collector=meta['labels']['collector'], device=meta['device']['model'],
                                 raw_scans=q['raw_scans'], eligible_scans=len(scans)))
            for scan, fp in scans:
                digest = hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()
                records.append(dict(**label, session_id=sid, sample_id=f'{sid}:{scan}', scan_id=scan,
                                    n_ap=len(fp), fingerprint=digest, features=fp))
    sessions = pd.DataFrame(manifest)
    planned_points = {p for p, v in plan.items() if v['split'] == 'Validation'}
    if (set(sessions.point_id) != planned_points or len(sessions) != len(planned_points)*4
            or sessions.session_id.duplicated().any() or sessions.duplicated(['point_id', 'orientation']).any()
            or sessions.eligible_scans.eq(0).any()):
        raise ValueError('Validation must cover each planned point and four orientations with usable scans')
    df = pd.DataFrame(records)
    clean = df.drop_duplicates(['point_id', 'fingerprint'])
    quality.update(sessions=len(sessions), points=len(planned_points), eligible_scans=len(df),
                   point_dedup_scans=len(clean), session_dedup_scans=len(df.drop_duplicates(['session_id','fingerprint'])),
                   removed_point_duplicates=len(df)-len(clean))
    return df, sessions, dict(quality)


def predict_bundle(df, name, bundle):
    """Return point-dedup scan predictions, direction-balanced point predictions and session predictions."""
    config = bundle['config']
    if (config['missing_rssi'], config['clip_rssi'], config['max_age_ms'], config['duplicate_bssid_aggregation']) != (-100., [-100,-20], MAX_AGE_MS, 'median'):
        raise ValueError(f'Unexpected preprocessing config for {name}')
    vocab = config['bssids']
    vocab_set = set(vocab)
    direction = df.drop_duplicates(['session_id','fingerprint']).copy()
    direction['known_aps'] = [len(set(fp)&vocab_set) for fp in direction.features]
    x = encode_transform(direction.features.tolist(), vocab)
    prob = {}
    usable = direction.known_aps.to_numpy() > 0
    for target, model in bundle['models'].items():
        if set(model.classes_) != set(LABELS[target]):
            raise ValueError(f'Unexpected classes in {name}/{target}')
        values = np.zeros((len(x), len(model.classes_)))
        if usable.any():
            values[usable] = model.predict_proba(x[usable])
        prob[target] = pd.DataFrame(values, index=direction.index, columns=model.classes_)
        direction[f'{target}_pred'] = class_decision(prob[target], target)
    direction['model'] = name
    scan = direction.drop_duplicates(['point_id','fingerprint']).drop(columns='features').copy()
    session = direction.drop_duplicates('session_id')[['point_id','session_id','orientation','floor','zone','model','split']].copy().set_index('session_id')
    point = session.drop_duplicates('point_id')[['point_id','floor','zone','model','split']].copy().set_index('point_id')
    for target in LABELS:
        by_dir = prob[target].groupby(direction.session_id).mean().reindex(session.index)
        by_point = by_dir.groupby(session.point_id).mean().reindex(point.index)
        session[f'{target}_pred'] = class_decision(by_dir,target)
        point[f'{target}_pred'] = class_decision(by_point,target)
    for frame in (scan, session, point):
        frame['floor_ok'] = frame.floor.eq(frame.floor_pred)
        frame['zone_ok'] = frame.zone.eq(frame.zone_pred)
        frame['joint_ok'] = frame.floor_ok & frame.zone_ok
    return scan, point.reset_index(), session.reset_index()


def class_decision(probability, target):
    decision = probability.columns.to_numpy()[probability.to_numpy().argmax(axis=1)].astype(object)
    decision[probability.to_numpy().sum(axis=1) == 0] = ABSTAIN[target]
    return decision


def summarize(frame, unit):
    rows = []
    for name, group in frame.groupby('model'):
        for area, part in [('2–6F',group),('2–4F',group[group.floor.le(4)])]:
            row = dict(model=name, unit=unit, scope=area, n=len(part), points=part.point_id.nunique())
            for key in ['floor','zone','joint']:
                row[f'{key}_correct'] = int(part[f'{key}_ok'].sum())
                row[f'{key}_accuracy'] = float(part[f'{key}_ok'].mean())
            for target in LABELS:
                y_pred = part[f'{target}_pred'].astype(int if target=='floor' else str)
                row[f'{target}_macro_f1'] = f1_score(part[target],y_pred,labels=LABELS[target] if area=='2–6F' or target=='zone' else [2,3,4],average='macro',zero_division=0)
            west = part[part.zone.eq('West')]
            row.update(west_n=len(west),west_correct=int(west.zone_ok.sum()),west_to_middle=int(west.zone_pred.eq('Middle').sum()))
            rows.append(row)
    return pd.DataFrame(rows)


def save_results(out, frames, plan):
    out.mkdir(parents=True,exist_ok=True)
    summary = []
    for unit, frame in frames.items():
        frame.to_csv(out/f'{unit}_predictions.csv',index=False)
        summary.append(summarize(frame,unit))
        for name, part in frame.groupby('model'):
            for target, classes in LABELS.items():
                labels = classes + ([ABSTAIN[target]] if part[f'{target}_pred'].eq(ABSTAIN[target]).any() else [])
                y_pred = part[f'{target}_pred'].astype(int if target=='floor' else str)
                cm = confusion_matrix(part[target],y_pred,labels=labels)
                pd.DataFrame(cm,index=labels,columns=labels).rename_axis('actual').to_csv(out/f'cm_{unit}_{name}_{target}.csv')
    stats = pd.concat(summary,ignore_index=True)
    stats.to_csv(out/'metrics.csv',index=False)
    per_floor = []
    for unit, frame in frames.items():
        grouped = frame.groupby(['model','floor']).agg(n=('floor_ok','size'),floor_correct=('floor_ok','sum'),
                zone_correct=('zone_ok','sum'),joint_correct=('joint_ok','sum')).reset_index()
        for target in ('floor','zone','joint'):
            grouped[f'{target}_accuracy'] = grouped[f'{target}_correct']/grouped.n
        grouped['unit'] = unit
        per_floor.append(grouped)
    pd.concat(per_floor,ignore_index=True).to_csv(out/'per_floor.csv',index=False)
    point = frames['point'].copy()
    point['planned_location'] = point.point_id.map(lambda p:plan[p]['planned_location'])
    point.loc[~point.joint_ok].to_csv(out/'point_errors.csv',index=False)
    point.loc[point.zone.eq('West')].to_csv(out/'west_points.csv',index=False)
    print(stats.loc[stats.scope.eq('2–6F'),['model','unit','n','floor_accuracy','zone_accuracy','joint_accuracy','west_correct','west_to_middle']].to_string(index=False))
    return stats


def run(archive, output, run_candidates):
    labels_path = ROOT/'point_labels.json'
    plan = json.loads(labels_path.read_text())
    train_points = {p for p,v in plan.items() if v['split']=='Train'}
    val, manifest, q = load_validation(archive,plan,train_points)
    output.mkdir(parents=True,exist_ok=True)
    manifest.to_csv(output/'static_validation_manifest.csv',index=False)
    (output/'static_quality.json').write_text(json.dumps(q,indent=2))
    print('QUALITY',q,flush=True)
    models = {f'D1_{name}':joblib.load(ROOT/f'results/models/{name}.joblib') for name in ('KNN','RandomForest')}
    if run_candidates:
        from candidates import train_candidates
        _,train,_ = load_data(ROOT/'data/static/sessions',labels_path)
        if set(train.point_id) != train_points or set(train.point_id)&set(val.point_id):
            raise ValueError('Fit inputs must contain only the expected D1 training identities')
        q['d1_d2_exact_fingerprint_overlap'] = len(set(train.fingerprint)&set(val.fingerprint))
        (output/'static_quality.json').write_text(json.dumps(q,indent=2))
        # The candidate training function receives only D1 data, never Validation rows or labels.
        candidates = train_candidates(train)
        models.update(candidates)
        models_dir = output/'models';models_dir.mkdir(exist_ok=True)
        for name,bundle in candidates.items():
            joblib.dump(bundle,models_dir/f'{name}.joblib',compress=3)
    preds = defaultdict(list)
    for name,bundle in models.items():
        for unit,frame in zip(('scan','point','session'),predict_bundle(val,name,bundle)):
            preds[unit].append(frame)
    return save_results(output,{unit:pd.concat(parts,ignore_index=True) for unit,parts in preds.items()},plan)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive',type=Path)
    parser.add_argument('--out',type=Path,default=STAGE/'baseline')
    parser.add_argument('--candidates',action='store_true')
    args = parser.parse_args()
    run(args.archive,args.out,args.candidates)
