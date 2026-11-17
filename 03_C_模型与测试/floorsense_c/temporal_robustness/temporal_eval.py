"""Freeze the deployed model's temporal replay, then fit and evaluate one RF candidate."""

from collections import Counter, defaultdict
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent
VALIDATION = ROOT/'d2_validation'
sys.path[:0] = [str(VALIDATION), str(ROOT)]
from mobile_reference import prepare_scan, predict, realtime_step
from replay_realtime import read_csv, save_csv, scans_from_archive

ARCHIVE = STAGE/'data/temporal_train_20261111.zip'
V1 = VALIDATION/'D_handoff/realtime'
ANCHORS = {f'T8-F{floor}-{anchor}-TR01' for floor in range(2, 7) for anchor in 'ACFG'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def read_model():
    with gzip.open(V1/'wifi_model.json.gz', 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def load_temporal(archive_path, plan, config):
    scans, sessions = [], []
    with ZipFile(archive_path) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith('/meta.json') and '__MACOSX' not in PurePosixPath(n).parts)
        for name in names:
            meta = json.loads(archive.read(name))
            label = meta['labels']
            point = label['point_id']
            if (point not in ANCHORS or plan[point]['split'] != 'Train' or label['split'] != 'train'
                or label['building'] != 'T8' or label['mode'] != 'static' or label['orientation'] != 'N'
                or plan[point]['floor'] != label['floor'] or plan[point]['anchor_zone'] != label['zone']
                or meta['status'] != 'complete' or meta['start_local'][:10] != '2026-11-11'):
                raise ValueError(f'Unexpected temporal Train session {name}')
            session = dict(point_id=point, session_id=meta['session_id'], floor=label['floor'], zone=plan[point]['zone'],
                           orientation='N', date=meta['start_local'][:10], split='train', start_local=meta['start_local'],
                           collector=label['collector'], device=meta['device']['model'])
            members = scans_from_archive(archive, str(PurePosixPath(name).with_name('wifi.csv')))
            seen = set()
            for scan_id, rows in members:
                features = prepare_scan(rows,config)
                digest = hashlib.sha256(json.dumps(features,sort_keys=True).encode()).hexdigest() if features else None
                scans.append(dict(**session, sample_id=f"{session['session_id']}:{scan_id}", scan_id=scan_id,
                                  n_ap=len(features), fingerprint=digest, unique_in_point=bool(digest and digest not in seen),
                                  features=features))
                if digest:
                    seen.add(digest)
            sessions.append(dict(**session, raw_scans=len(members), unique_scans=len(seen)))
    if len(sessions)!=len(ANCHORS) or {r['point_id'] for r in sessions}!=ANCHORS or len({r['session_id'] for r in sessions})!=len(sessions):
        raise ValueError('Temporal input must contain one session for each of the 20 matched N anchors')
    return scans, sessions


def majority(values):
    votes = Counter(v for v in values if v is not None)
    return min(votes,key=lambda v:(-votes[v],v)) if votes else None


def replay(archive, out, plan, payload):
    scans, sessions = load_temporal(archive,plan,payload['config'])
    rows = []
    for row in scans:
        raw = predict(row['features'],payload)
        gated = realtime_step(row['features'],payload)
        rows.append(dict(**{k:v for k,v in row.items() if k!='features'}, raw_floor=raw['floor'], raw_zone=raw['zone'],
                         known_aps=raw['known_aps'], floor_vote_score=raw.get('floor_vote_score'),
                         updated=gated['is_new_estimate'], update_floor=gated['floor'], update_zone=gated['zone'],
                         floor_ok=raw['floor']==row['floor'],zone_ok=raw['zone']==row['zone']))
    points=[]
    for session in sessions:
        all_scans=[r for r in rows if r['point_id']==session['point_id']]
        unique=[r for r in all_scans if r['unique_in_point']]
        floor = majority(r['update_floor'] for r in unique)
        zone = majority(r['update_zone'] for r in unique)
        points.append(dict(**session, accepted_raw_scans=sum(r['updated'] for r in all_scans),
                           accepted_unique_scans=sum(r['updated'] for r in unique), floor_pred=floor,zone_pred=zone,
                           floor_ok=floor==session['floor'],zone_ok=zone==session['zone']))
    out.mkdir(exist_ok=True)
    save_csv(out/'v1_temporal_scans.csv',rows)
    save_csv(out/'v1_temporal_points.csv',points)
    unique = [r for r in rows if r['unique_in_point']]
    summary = dict(archive_sha256=sha256(archive), v1_sha256=sha256(V1/'wifi_model.json.gz'),
                   points=len(points), raw_scans=len(rows), eligible_scans=sum(bool(r['fingerprint']) for r in rows),
                   unique_scans=len(unique), identical_five_scan_sessions=sum(r['raw_scans']==5 and r['unique_scans']==1 for r in points),
                   accepted_raw_scans=sum(r['updated'] for r in rows), accepted_unique_scans=sum(r['updated'] for r in unique),
                   raw_floor_correct=sum(r['floor_ok'] for r in rows),raw_zone_correct=sum(r['zone_ok'] for r in rows),
                   unique_floor_correct=sum(r['floor_ok'] for r in unique),unique_zone_correct=sum(r['zone_ok'] for r in unique),
                   point_floor_correct=sum(r['floor_ok'] for r in points),point_zone_correct=sum(r['zone_ok'] for r in points),
                   min_known_aps=min(r['known_aps'] for r in rows), min_floor_vote_score=min(r['floor_vote_score'] for r in rows if r['floor_vote_score'] is not None),
                   replay_sha256=sha256(out/'v1_temporal_scans.csv'), device_models=sorted({r['device'] for r in rows}),
                   collectors=sorted({r['collector'] for r in rows}))
    write_json(out/'v1_temporal_summary.json',summary)
    print('V1 frozen replay',json.dumps(summary,ensure_ascii=False))


def load_d1(archive_path):
    from pipeline import load_data
    # Preserve the original loader's ordering and fingerprint rules without unpacking sensors.
    with TemporaryDirectory(prefix='floorsense_d1_wifi_') as folder, ZipFile(archive_path) as archive:
        root=Path(folder)
        for member in archive.namelist():
            p=PurePosixPath(member)
            if len(p.parts)<2 or p.name not in ('meta.json','wifi.csv') or '__MACOSX' in p.parts:
                continue
            if p.parent.name in ('','.','..'):
                raise ValueError(f'Invalid session path {member}')
            path=root/p.parent.name/p.name
            path.parent.mkdir(exist_ok=True)
            if path.exists():
                raise ValueError(f'Duplicate training record {member}')
            path.write_bytes(archive.read(member))
        return load_data(root,ROOT/'point_labels.json')


def evaluate_validation(val, name, bundle, payload):
    import pandas as pd
    from validate import predict_bundle
    scans, points, sessions = predict_bundle(val,name,bundle)
    by_id = dict(zip(val.sample_id,val.features))
    live_rows = []
    for row in scans.itertuples(index=False):
        features = by_id[row.sample_id]
        raw = predict(features,payload)
        assert (raw['floor'],raw['zone'],raw['known_aps'])==(row.floor_pred,row.zone_pred,row.known_aps)
        gated = realtime_step(features,payload)
        live_rows.append(dict(model=name,sample_id=row.sample_id,point_id=row.point_id,actual_floor=row.floor,
                              actual_zone=row.zone,known_aps=row.known_aps,raw_floor=row.floor_pred,raw_zone=row.zone_pred,
                              updated=gated['is_new_estimate'],floor=gated['floor'],zone=gated['zone'],reason=gated.get('reason',''),
                              floor_correct=gated['floor']==row.floor,zone_correct=gated['zone']==row.zone))
    live=pd.DataFrame(live_rows)
    return {'scan':scans,'point':points,'session':sessions,'realtime':live}


def save_d1_matched_replay(raw, out, payload):
    n_scans = raw[raw.point_id.isin(ANCHORS)&raw.orientation.eq('N')].drop_duplicates(['point_id','fingerprint'])
    rows=[]
    for row in n_scans.itertuples(index=False):
        raw_prediction=predict(row.features,payload)
        gated=realtime_step(row.features,payload)
        rows.append(dict(point_id=row.point_id,sample_id=row.sample_id,date=row.date,floor=row.floor,zone=row.zone,
                         n_ap=row.n_ap,known_aps=raw_prediction['known_aps'],raw_floor=raw_prediction['floor'],
                         raw_zone=raw_prediction['zone'],updated=gated['is_new_estimate'],
                         update_floor=gated['floor'],update_zone=gated['zone']))
    save_csv(out/'v1_d1_matched_n_scans.csv',rows)
    d2_points={r['point_id']:r for r in read_csv(out/'v1_temporal_points.csv')}
    pairs=[]
    for point in sorted(ANCHORS):
        scans=[r for r in rows if r['point_id']==point]
        d2=d2_points[point]
        pairs.append(dict(point_id=point,floor=d2['floor'],zone=d2['zone'],d1_unique_scans=len(scans),
                          d1_updates=sum(r['updated'] for r in scans),d1_floor_pred=majority(r['update_floor'] for r in scans),
                          d1_zone_pred=majority(r['update_zone'] for r in scans),d2_unique_scans=d2['unique_scans'],
                          d2_updates=d2['accepted_unique_scans'],d2_floor_pred=d2['floor_pred'],d2_zone_pred=d2['zone_pred']))
    save_csv(out/'v1_paired_point_predictions.csv',pairs)


def compare(archive, out, plan, v1_payload):
    import joblib
    import pandas as pd
    from sklearn.base import clone
    from candidates import MIN_AP_POINTS, _balanced_scan_weights, _stable_ap_vocabulary
    from export_d import portable
    from pipeline import encode_transform
    from validate import load_validation, save_results

    proof=json.loads((out/'v1_temporal_summary.json').read_text())
    if (proof['archive_sha256'],proof['v1_sha256'],proof['replay_sha256']) != (sha256(archive),sha256(V1/'wifi_model.json.gz'),sha256(out/'v1_temporal_scans.csv')):
        raise ValueError('Save the frozen V1 replay before fitting V2')
    d1_archive=ROOT/'data/static_train.zip'
    raw, d1, quality = load_d1(d1_archive)
    expected={k for k,v in plan.items() if v['split']=='Train'}
    if set(d1.point_id)!=expected or len(d1)!=271 or not ANCHORS.issubset(set(raw.loc[raw.orientation.eq('N'),'point_id'])):
        raise ValueError('Unexpected D1 data or missing N match')
    v1=joblib.load(V1/'selected.joblib')
    assert v1['config']==v1_payload['config'] and _stable_ap_vocabulary(d1)==v1['config']['bssids']
    save_d1_matched_replay(raw,out,v1_payload)
    temporal,_=load_temporal(archive,plan,v1_payload['config'])
    vocab_v1=set(v1_payload['config']['bssids'])
    added=pd.DataFrame(r for r in temporal if r['unique_in_point'] and len(set(r['features'])&vocab_v1)>=v1_payload['realtime_policy']['min_known_aps'])
    if added.empty or set(added.session_id)&set(raw.session_id):
        raise ValueError('Temporal samples missing or session IDs repeat D1')
    combined=pd.concat([d1,added],ignore_index=True).drop_duplicates(['point_id','fingerprint']).reset_index(drop=True)
    if set(combined.point_id)!=expected:
        raise ValueError('Training must use exactly the original Train identities')
    validation_archive=VALIDATION/'data/static_validation.zip'
    val,manifest,_=load_validation(validation_archive,plan,set(combined.point_id))
    assert set(val.session_id).isdisjoint(combined.session_id) and set(val.fingerprint).isdisjoint(combined.fingerprint)
    vocab=_stable_ap_vocabulary(combined)
    x=encode_transform(combined.features.tolist(),vocab)
    point_labels=combined[['point_id','floor','zone']].drop_duplicates()
    assert len(point_labels)==len(expected)
    weights=_balanced_scan_weights(combined,point_labels)
    models={target:clone(model).fit(x,combined[target],sample_weight=weights[target]) for target,model in v1['models'].items()}
    config=dict(v1['config'],bssids=vocab)
    config.pop('minimum_d1_points_per_ap',None)
    config['minimum_train_points_per_ap']=MIN_AP_POINTS
    training=dict(source='D1 Train plus 2026-11-11 matched Train revisits',points=len(expected),scans=len(combined),
                  temporal_added=len(combined)-len(d1),weighting=v1['training']['weighting'])
    v2=dict(config=config,models=models,training=training)
    v2_payload=dict(schema_version=1,name='stable_ap_balanced_rf_temporal',config=config,
                    realtime_policy=v1_payload['realtime_policy'],models={k:portable(m) for k,m in models.items()})
    candidates=out/'v2_candidate';candidates.mkdir(exist_ok=True)
    joblib.dump(v2,candidates/'candidate.joblib',compress=3)
    (candidates/'wifi_model.json.gz').write_bytes(gzip.compress(json.dumps(v2_payload,separators=(',',':')).encode(),mtime=0))
    write_json(candidates/'feature_config.json',config)
    stats=[]
    frames=defaultdict(list)
    for name,bundle,payload in [('V1',v1,v1_payload),('V2',joblib.load(candidates/'candidate.joblib'),v2_payload)]:
        results=evaluate_validation(val,name,bundle,payload)
        for unit,frame in results.items():
            frames[unit].append(frame)
        for unit in ('scan','point'):
            frame=results[unit];west=frame[frame.zone.eq('West')]
            stats.append(dict(model=name,unit=unit,n=len(frame),floor_correct=int(frame.floor_ok.sum()),
                              zone_correct=int(frame.zone_ok.sum()),joint_correct=int(frame.joint_ok.sum()),
                              west_n=len(west),west_correct=int(west.zone_ok.sum()),west_to_middle=int(west.zone_pred.eq('Middle').sum())))
        live=results['realtime'];west=live[live.actual_zone.eq('West')]
        stats.append(dict(model=name,unit='realtime_scan',n=len(live),floor_correct=int(live.floor_correct.sum()),
                          zone_correct=int(live.zone_correct.sum()),joint_correct=int((live.floor_correct&live.zone_correct).sum()),
                          west_n=len(west),west_correct=int(west.zone_correct.sum()),west_to_middle=int(west.zone.eq('Middle').sum()),
                          updates=int(live.updated.sum()),covered_points=live.loc[live.updated,'point_id'].nunique()))
    detail=out/'validation';detail.mkdir(exist_ok=True)
    live=pd.concat(frames.pop('realtime'),ignore_index=True)
    live.to_csv(detail/'realtime_predictions.csv',index=False)
    save_results(detail,{k:pd.concat(v,ignore_index=True) for k,v in frames.items()},plan)
    pd.DataFrame(stats).to_csv(out/'v1_v2_validation.csv',index=False)
    by_version={(r['model'],r['unit']):r for r in stats}
    watch={'point':('floor_correct','zone_correct','joint_correct','west_correct'),
           'realtime_scan':('updates','covered_points','floor_correct','joint_correct','west_correct')}
    changes={f'{unit}.{metric}':by_version['V2',unit][metric]-by_version['V1',unit][metric]
             for unit,metrics in watch.items() for metric in metrics}
    warrants_recheck=min(changes.values())>=0 and max(changes.values())>0
    decision=dict(selected='V1', v2_static_comparison_supports_dynamic_check=warrants_recheck,
                  comparison_delta=changes, train=training, D1_raw_scans=quality['raw_scans'],D1_unique_scans=len(d1),
                  D2_eligible_for_augmentation=len(added), training_points=len(expected), validation_points=manifest.point_id.nunique(),
                  v1_vocabulary=len(v1['config']['bssids']),v2_vocabulary=len(vocab),
                  d1_archive_sha256=sha256(d1_archive),validation_archive_sha256=sha256(validation_archive))
    write_json(out/'decision.json',decision)
    print(pd.DataFrame(stats).to_string(index=False));print(json.dumps(decision,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['replay','compare'])
    parser.add_argument('--archive',type=Path,default=ARCHIVE)
    parser.add_argument('--out',type=Path,default=STAGE/'results')
    args=parser.parse_args()
    plan=json.loads((ROOT/'point_labels.json').read_text())
    if args.phase=='replay':
        replay(args.archive,args.out,plan,read_model())
    else:
        compare(args.archive,args.out,plan,read_model())
