"""Fit limited calibration from a Train ZIP, then score the frozen pair on D3."""

import argparse
from collections import Counter
import csv
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from time import perf_counter
from zipfile import ZipFile

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent
sys.path[:0] = [str(ROOT/'temporal_robustness'), str(ROOT/'d2_validation'), str(ROOT)]
from mobile_reference import prepare_scan, realtime_step
from replay_realtime import read_csv, save_csv, scans_from_archive
from temporal_eval import load_d1, read_model, sha256, write_json

V1 = ROOT/'d2_validation/D_handoff/realtime'
PLAN = ROOT/'point_labels.json'
D1 = ROOT/'data/static_train.zip'
CALIBRATION = {f'T8-F{floor}-{anchor}-TR01' for floor in range(2,7) for anchor in 'AG'}
PILOT_HOLDOUT = {f'T8-F{floor}-{anchor}-TR01' for floor in range(2,7) for anchor in 'CF'}
WINDOW_MS = 30_000
FLOORS = [2,3,4,5,6]
ZONES = ['West','Middle','East']


def session_labels(meta, plan, role, capture_date):
    labels = meta['labels']
    point_id = labels['point_id']
    point = plan.get(point_id)
    if not point or point['split'].lower()!=role or labels['split'].lower()!=role:
        raise ValueError(f'Archive role mismatch for {point_id}: expected {role}')
    if (meta['status']!='complete' or meta['start_local'][:10]!=capture_date or labels['mode']!='static'
        or labels['building']!='T8' or labels['orientation']!='N' or labels['floor']!=point['floor']
        or labels['zone']!=point['anchor_zone']):
        raise ValueError(f'Unexpected date, status, location, or orientation for {point_id}')
    return point_id, point['floor'], point['zone']


def require_full_window(meta, point_id):
    end = meta.get('end_wall_ms')
    duration = int(end)-int(meta['start_wall_ms']) if end is not None else None
    if duration is None or duration<WINDOW_MS:
        raise ValueError(f'{point_id}: formal capture must last at least 30 s (recorded {duration} ms). '
                         'Check the Collector capture duration before scoring.')
    return duration


def load_sessions(path, plan, required, role, capture_date, config, *, formal=False):
    sessions = {}
    session_ids = set()
    with ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith('/meta.json') and '__MACOSX' not in PurePosixPath(n).parts)
        for name in names:
            meta = json.loads(archive.read(name))
            point_id,floor,zone = session_labels(meta,plan,role,capture_date)
            duration = require_full_window(meta,point_id) if formal else None
            sid = meta['session_id']
            if point_id not in required or point_id in sessions or sid in session_ids:
                raise ValueError(f'Unexpected or duplicate point or Session: {point_id} / {sid}')
            session_ids.add(sid)
            member = str(PurePosixPath(name).with_name('wifi.csv'))
            raw = scans_from_archive(archive,member) if member in archive.namelist() or meta.get('stats',{}).get('wifi_scans')!=0 else []
            scans = [dict(scan_id=k,wall_ms=int(rows[0]['t_wall_ms']),features=prepare_scan(rows,config)) for k,rows in raw]
            sessions[point_id] = dict(point_id=point_id, floor=floor, zone=zone, session_id=sid,
                                      start_wall_ms=int(meta['start_wall_ms']), start_local=meta['start_local'],
                                      collector=meta['labels']['collector'], device=meta['device']['model'],
                                      timezone=meta.get('timezone'), duration_ms=duration,
                                      scans=sorted(scans,key=lambda r:(r['wall_ms'],r['scan_id'])))
    if set(sessions)!=required:
        raise ValueError(f'Expected {len(required)} {role} points; missing {sorted(required-set(sessions))}')
    return sessions


def in_window(session):
    start = session['start_wall_ms']
    return [r for r in session['scans'] if start<=r['wall_ms']<=start+WINDOW_MS]


def fingerprint_hash(features):
    canonical = json.dumps({key:float(value) for key,value in features.items()},sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def fit_calibration(sessions, capture_date, out, baseline, source_archive):
    import joblib
    import pandas as pd
    from sklearn.base import clone
    from candidates import MIN_AP_POINTS, _stable_ap_vocabulary, _balanced_scan_weights
    from export_d import portable
    from pipeline import encode_transform

    started = perf_counter()
    raw,d1,_ = load_d1(D1)
    if set(raw.session_id)&{s['session_id'] for s in sessions.values()}:
        raise ValueError('Calibration archive contains an original D1 Session ID')
    v1 = joblib.load(V1/'selected.joblib')
    if _stable_ap_vocabulary(d1)!=baseline['config']['bssids'] or v1['config']!=baseline['config']:
        raise ValueError('D1 fingerprints and V1 training configuration do not match')
    v1_ap = set(v1['config']['bssids'])
    original_keys = set(zip(d1.point_id,d1.fingerprint))
    rows = []
    anchor_counts = []
    for session in sessions.values():
        point = session['point_id']
        window = in_window(session)
        added = set()
        for scan in window:
            fp = scan['features']
            if len(set(fp)&v1_ap)<baseline['realtime_policy']['min_known_aps']:
                continue
            fingerprint = fingerprint_hash(fp)
            if fingerprint in added:
                continue
            added.add(fingerprint)
            rows.append(dict(point_id=point,floor=session['floor'],zone=session['zone'],session_id=session['session_id'],
                             features=fp,fingerprint=fingerprint))
        anchor_counts.append(dict(point_id=point,session_id=session['session_id'],start_local=session['start_local'],
                                  collector=session['collector'],device=session['device'],raw_scans=len(session['scans']),
                                  window_scans=len(window),unique_qualified_fingerprints=len(added),
                                  added_after_d1_dedup=sum((point,fp) not in original_keys for fp in added)))
    if not rows:
        raise ValueError('No qualified calibration fingerprints in any of the ten anchor windows')
    train = pd.concat([d1,pd.DataFrame(rows)],ignore_index=True).drop_duplicates(['point_id','fingerprint']).reset_index(drop=True)
    point_labels = train[['point_id','floor','zone']].drop_duplicates()
    if set(train.point_id)!=set(d1.point_id) or point_labels.point_id.duplicated().any():
        raise ValueError('Calibration must retain the original D1 Train points and labels')
    vocab = _stable_ap_vocabulary(train)
    x = encode_transform(train.features.tolist(),vocab)
    weights = _balanced_scan_weights(train,point_labels)
    models = {target:clone(model).fit(x,train[target],sample_weight=weights[target]) for target,model in v1['models'].items()}
    fit_seconds = perf_counter()-started
    config = dict(v1['config'],bssids=vocab,minimum_train_points_per_ap=MIN_AP_POINTS)
    config.pop('minimum_d1_points_per_ap',None)
    payload = dict(schema_version=1,name='Vcal10',config=config,realtime_policy=baseline['realtime_policy'],
                   models={target:portable(model) for target,model in models.items()})
    out.mkdir(parents=True,exist_ok=True)
    payload_path = out/'wifi_model.json.gz'
    payload_path.write_bytes(gzip.compress(json.dumps(payload,separators=(',',':')).encode(),mtime=0))
    joblib.dump(dict(config=config,models=models),out/'candidate.joblib',compress=3)
    write_json(out/'feature_config.json',config)
    save_csv(out/'calibration_anchors.csv',sorted(anchor_counts,key=lambda a:a['point_id']))
    observed = {}
    for row in train.itertuples(index=False):
        observed.setdefault(fingerprint_hash(row.features),set()).add(row.point_id)
    audit_path = out/'training_audit.json'
    write_json(audit_path,dict(d1_session_ids=sorted(raw.session_id.unique()),
                             fingerprint_points={key:sorted(points) for key,points in sorted(observed.items())}))
    gaps = {str(f):sorted({(date.fromisoformat(capture_date)-date.fromisoformat(day)).days
                          for day in raw.loc[raw.floor.eq(f),'date']}) for f in FLOORS}
    freeze = dict(capture_date=capture_date,window_ms=WINDOW_MS,anchor_ids=sorted(sessions),
                  anchor_session_ids=sorted(s['session_id'] for s in sessions.values()),
                  calibration_archive_sha256=sha256(source_archive),plan_sha256=sha256(PLAN),d1_sha256=sha256(D1),
                  v1_sha256=sha256(V1/'wifi_model.json.gz'),vcal_sha256=sha256(payload_path),training_audit_sha256=sha256(audit_path),
                  v1_thresholds=baseline['realtime_policy'],training_points=train.point_id.nunique(),
                  d1_unique_fingerprints=len(d1),calibration_unique_qualified=len(rows),
                  calibration_added_after_dedup=len(train)-len(d1),
                  anchors_with_qualified_scans=sum(a['unique_qualified_fingerprints']>0 for a in anchor_counts),
                  anchors_contributing_new_fingerprints=sum(a['added_after_d1_dedup']>0 for a in anchor_counts),
                  elapsed_days_from_d1_by_floor=gaps,
                  fit_seconds=round(fit_seconds,3))
    write_json(out/'freeze.json',freeze)
    return payload,freeze


def score_one(session,payload):
    previous = None
    last_update = None
    first_update_ms = None
    first_correct_ms = None
    events=[]
    for scan in in_window(session):
        step = realtime_step(scan['features'],payload,previous)
        elapsed = scan['wall_ms']-session['start_wall_ms']
        if step['is_new_estimate']:
            last_update = step
            previous = step['floor']
            if first_update_ms is None:
                first_update_ms = elapsed
            if first_correct_ms is None and previous==session['floor']:
                first_correct_ms = elapsed
        events.append(dict(point_id=session['point_id'],session_id=session['session_id'],scan_id=scan['scan_id'],
                           elapsed_ms=elapsed,expected_floor=session['floor'],expected_zone=session['zone'],**step))
    point = dict(point_id=session['point_id'],session_id=session['session_id'],floor=session['floor'],zone=session['zone'],
                 duration_ms=session.get('duration_ms'),
                 floor_pred=last_update['floor'] if last_update else None,zone_pred=last_update['zone'] if last_update else None,
                 first_update_ms=first_update_ms,first_correct_ms=first_correct_ms,timeout=last_update is None,window_scans=len(events),
                 updates=sum(e['is_new_estimate'] for e in events),
                 wrong_updates=sum(e['is_new_estimate'] and e['floor']!=session['floor'] for e in events))
    return point,events


def score_counts(points):
    from statistics import median
    floor_ok = [r['floor_pred']==r['floor'] for r in points]
    zone_ok = [r['zone_pred']==r['zone'] for r in points]
    first_correct = [r['first_correct_ms'] for r in points if r.get('first_correct_ms') is not None]
    covered=sum(r['floor_pred'] is not None for r in points)
    return dict(n=len(points),covered=covered,timeout_n=len(points)-covered,floor_correct=sum(floor_ok),
                zone_correct=sum(zone_ok),joint_correct=sum(f and z for f,z in zip(floor_ok,zone_ok)),
                west_n=sum(r['zone']=='West' for r in points),west_correct=sum(r['zone']=='West' and z for r,z in zip(points,zone_ok)),
                median_first_correct_ms=median(first_correct) if first_correct else None)


def score_pair(sessions, v1, vcal, out, official=False):
    out.mkdir(parents=True,exist_ok=True)
    points_by_model = {}
    event_rows=[]
    summary={}
    for model,payload in [('V1',v1),('Vcal10',vcal)]:
        points=[]
        for session in sorted(sessions.values(),key=lambda s:s['point_id']):
            point,events=score_one(session,payload)
            points.append(dict(model=model,**point))
            event_rows.extend(dict(model=model,**row) for row in events)
        points_by_model[model]=points
        summary[model]={'all':score_counts(points),'by_floor':{f:score_counts([p for p in points if p['floor']==f]) for f in FLOORS}}
        if official:
            summary[model]['basic_2_to_4']=score_counts([p for p in points if p['floor']<=4])
            summary[model]['basic_2_to_4']['goal_correct']=49
            summary[model]['all']['goal_correct']=67
        for target,labels in [('floor',FLOORS),('zone',ZONES)]:
            matrix = Counter((p[target],p[f'{target}_pred']) for p in points)
            save_csv(out/f'cm_{model}_{target}.csv',[{'actual':actual,**{str(pred):matrix[actual,pred] for pred in labels},
                     'no_update':matrix[actual,None]} for actual in labels])
    a,b=points_by_model.values()
    paired=[]
    for first,second in zip(a,b):
        assert first['point_id']==second['point_id']
        p=dict(point_id=first['point_id'],floor=first['floor'],zone=first['zone'])
        for version,row in [('V1',first),('Vcal10',second)]:
            p.update({f'{version}_{k}':row[k] for k in ('floor_pred','zone_pred','first_correct_ms')})
        p['floor_delta']=int(second['floor_pred']==second['floor'])-int(first['floor_pred']==first['floor'])
        paired.append(p)
    summary['paired_floor']={name:sum(r['floor_delta']==delta for r in paired) for name,delta in [('improved',1),('unchanged',0),('regressed',-1)]}
    save_csv(out/'point_predictions.csv',a+b)
    save_csv(out/'paired_points.csv',paired)
    fields=['model','point_id','session_id','scan_id','elapsed_ms','expected_floor','expected_zone',
            'status','reason','floor','zone','known_aps','is_new_estimate','floor_vote_score','zone_vote_score']
    with (out/'scan_events.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(event_rows)
    write_json(out/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='phase',required=True)
    fit=subs.add_parser('fit');fit.add_argument('--date',required=True);fit.add_argument('--calibration',type=Path,required=True);fit.add_argument('--out',type=Path,required=True)
    score=subs.add_parser('score');score.add_argument('--freeze-dir',type=Path,required=True);score.add_argument('--test',type=Path,required=True);score.add_argument('--out',type=Path,required=True)
    pilot=subs.add_parser('pilot');pilot.add_argument('--out',type=Path,default=STAGE/'pilot')
    args=parser.parse_args()
    plan=json.loads(PLAN.read_text())
    baseline=read_model()
    if args.phase=='fit':
        if date.fromisoformat(args.date)<=date(2026,11,11):
            parser.error('Use a newly collected date for the formal experiment; use pilot for the Nov 11 archive')
        sessions=load_sessions(args.calibration,plan,CALIBRATION,'train',args.date,baseline['config'],formal=True)
        _,freeze=fit_calibration(sessions,args.date,args.out,baseline,args.calibration)
        print(json.dumps(freeze,ensure_ascii=False))
    elif args.phase=='score':
        freeze=json.loads((args.freeze_dir/'freeze.json').read_text())
        if date.fromisoformat(freeze['capture_date'])<=date(2026,11,11):
            parser.error('Pilot calibration cannot be used for D3 scoring')
        payload_path=args.freeze_dir/'wifi_model.json.gz'
        audit_path=args.freeze_dir/'training_audit.json'
        if (set(freeze['anchor_ids'])!=CALIBRATION or freeze['window_ms']!=WINDOW_MS
            or freeze['plan_sha256']!=sha256(PLAN) or freeze['v1_sha256']!=sha256(V1/'wifi_model.json.gz')
            or freeze['vcal_sha256']!=sha256(payload_path) or freeze['training_audit_sha256']!=sha256(audit_path)
            or freeze['calibration_archive_sha256']==sha256(args.test)):
            raise ValueError('Frozen inputs or models do not match, or the calibration and Test archive are the same')
        with gzip.open(payload_path,'rt',encoding='utf-8') as stream:
            calibrated=json.load(stream)
        if calibrated['realtime_policy']!=baseline['realtime_policy']:
            raise ValueError('Score both versions with the same frozen update thresholds')
        test_ids={point for point,labels in plan.items() if labels['split']=='Test'}
        sessions=load_sessions(args.test,plan,test_ids,'test',freeze['capture_date'],baseline['config'],formal=True)
        audit=json.loads(audit_path.read_text())
        prior_ids=set(freeze['anchor_session_ids'])|set(audit['d1_session_ids'])
        prior_ids.update(row['session_id'] for row in read_csv(ROOT/'d2_validation/comparison/static_validation_manifest.csv'))
        if prior_ids&{s['session_id'] for s in sessions.values()}:
            raise ValueError('Test Session IDs overlap Train, Calibration or Validation')
        summary=score_pair(sessions,baseline,calibrated,args.out,official=True)
        overlaps=[]
        for session in sessions.values():
            for scan in in_window(session):
                if scan['features']:
                    match=audit['fingerprint_points'].get(fingerprint_hash(scan['features']))
                    if match:
                        overlaps.append(dict(test_point=session['point_id'],session_id=session['session_id'],scan_id=scan['scan_id'],training_points=match))
        write_json(args.out/'fingerprint_overlap_review.json',overlaps)
        write_json(args.out/'inputs.json',dict(freeze=freeze,test_archive_sha256=sha256(args.test)))
        print(json.dumps(summary,ensure_ascii=False))
    else:
        archive=ROOT/'temporal_robustness/data/temporal_train_20261111.zip'
        sessions=load_sessions(archive,plan,CALIBRATION|PILOT_HOLDOUT,'train','2026-11-11',baseline['config'])
        calibrated,_=fit_calibration({p:sessions[p] for p in sorted(CALIBRATION)},'2026-11-11',args.out/'fit',baseline,archive)
        summary=score_pair({p:sessions[p] for p in sorted(PILOT_HOLDOUT)},baseline,calibrated,args.out/'seen_location_replay')
        print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    main()
