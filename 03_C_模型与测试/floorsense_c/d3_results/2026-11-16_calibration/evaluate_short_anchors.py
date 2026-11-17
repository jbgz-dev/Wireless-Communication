"""Compare frozen V1 with a limited calibration using short same-day A/G records."""

import argparse
import csv
import gzip
import json
from pathlib import Path, PurePosixPath
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

RESULTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RESULTS / '2026-11-16_70pt'), str(RESULTS / '2026-11-16'),
                str(RESULTS.parent / 'd3_ready')]
import d3_eval as d3
from evaluate_static_subset import members, planned_points, prepare_subset

DAY = '2026-11-16'
MODEL_NAME = 'Vcal9'


def canonicalize_anchors(source, target, plan):
    rows, selected = [], set()
    with ZipFile(source) as archive, ZipFile(target, 'w') as merged:
        for member in members(archive):
            meta = json.loads(archive.read(member))
            label = meta['labels']
            point = f"T8-F{label['floor']}-{label['zone']}-TR{int(label['point_id']):02d}"
            if (point not in d3.CALIBRATION or plan[point]['split'] != 'Train' or
                    (label['building'], label['split'], label['mode'], meta['status'], meta['start_local'][:10]) !=
                    ('T8', 'train', 'static', 'complete', DAY)):
                raise ValueError(f'Unexpected calibration record {member}')
            north = label['orientation'] == 'N'
            duration = meta['end_wall_ms'] - meta['start_wall_ms']
            if duration <= 0:
                raise ValueError(f'Invalid capture duration at {point}')
            rows.append(dict(point_id=point, raw_point_id=label['point_id'], orientation=label['orientation'],
                             duration_ms=duration, session_id=meta['session_id'], device=meta['device']['model'],
                             start_local=meta['start_local'], raw_wifi_callbacks=meta['stats']['wifi_scans'],
                             selected_for_fit=north))
            if north:
                if point in selected:
                    raise ValueError(f'Multiple N sessions at {point}')
                selected.add(point)
                label['point_id'] = point
                merged.writestr(point+'/meta.json', json.dumps(meta))
                merged.writestr(point+'/wifi.csv', archive.read(str(PurePosixPath(member).with_name('wifi.csv'))))
    if d3.CALIBRATION - selected != {'T8-F5-A-TR01'}:
        raise ValueError('This report expects the actual nine-anchor panel of November 16')
    return selected, rows


def compare(sessions, baseline, calibrated, out):
    cases = {}
    predictions, event_rows = [], []
    for name, model in [('V1', baseline), (MODEL_NAME, calibrated)]:
        cases[name] = {}
        for point in sorted(sessions):
            row, events = d3.score_one(sessions[point], model)
            cases[name][point] = row
            predictions.append(dict(model=name, **row))
            event_rows.extend(dict(model=name, **e) for e in events)
    d3.save_csv(out/'point_predictions.csv', predictions)
    event_fields = list(dict.fromkeys(key for event in event_rows for key in event))
    with (out/'scan_events.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=event_fields)
        writer.writeheader()
        writer.writerows(event_rows)

    paired = []
    for point, a in cases['V1'].items():
        b = cases[MODEL_NAME][point]
        row = dict(point_id=point, floor=a['floor'], zone=a['zone'], device=sessions[point]['device'])
        for name, result in [('V1', a), (MODEL_NAME, b)]:
            row.update({f'{name}_{key}': result[key] for key in ('floor_pred', 'zone_pred', 'first_correct_ms')})
        floor = [result['floor_pred']==result['floor'] for result in (a, b)]
        zone = [result['zone_pred']==result['zone'] for result in (a, b)]
        row.update(floor_delta=int(floor[1])-int(floor[0]), zone_delta=int(zone[1])-int(zone[0]),
                   joint_delta=int(floor[1] and zone[1])-int(floor[0] and zone[0]))
        paired.append(row)
    d3.save_csv(out/'paired_points.csv', paired)
    summary = {name: {'all': d3.score_counts(list(points.values())),
                      'basic_2_to_4': d3.score_counts([r for r in points.values() if r['floor'] <= 4]),
                      'by_floor': {f: d3.score_counts([r for r in points.values() if r['floor']==f]) for f in d3.FLOORS},
                      'by_zone': {z: d3.score_counts([r for r in points.values() if r['zone']==z]) for z in d3.ZONES},
                      'wrong_floor_updates': sum(r['wrong_updates'] for r in points.values())}
               for name, points in cases.items()}
    summary['paired_delta'] = {target: {direction: sum(r[f'{target}_delta']==n for r in paired)
                                      for direction,n in [('improved',1),('unchanged',0),('regressed',-1)]}
                               for target in ('floor','zone','joint')}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anchors', type=Path, required=True)
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--test', type=Path, nargs=2, required=True)
    parser.add_argument('--d1', type=Path, default=d3.D1)
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    reference_path = RESULTS/'2026-11-16_70pt/inputs.json'
    reference = json.loads(reference_path.read_text())
    for file in [args.workbook, *args.test, args.d1]:
        if d3.sha256(file)!=reference['inputs'][file.name]:
            raise ValueError(f'Input changed from verified D3: {file}')
    freeze_path = d3.STAGE/'offline_check/baseline_freeze.json'
    if d3.sha256(freeze_path)!=reference['baseline_freeze_sha256']:
        raise ValueError('V1 freeze changed')
    original = json.loads(freeze_path.read_text())
    for name, digest in original['file_sha256'].items():
        path = args.d1 if name=='data/static_train.zip' else d3.ROOT/name
        if d3.sha256(path)!=digest:
            raise ValueError(f'Frozen input changed: {path}')
    d3.D1 = args.d1
    plan = json.loads(d3.PLAN.read_text())
    baseline = d3.read_model()
    out = args.out
    with TemporaryDirectory() as temp:
        path = Path(temp)/'anchors.zip'
        selected, anchor_audit = canonicalize_anchors(args.anchors, path, plan)
        anchors = d3.load_sessions(path, plan, selected, 'train', DAY, baseline['config'])
        fit_dir = out/'fit'
        calibrated, freeze = d3.fit_calibration(anchors, DAY, fit_dir, baseline, args.anchors)
        calibrated['name'] = MODEL_NAME
        model_path = fit_dir/'wifi_model.json.gz'
        model_path.write_bytes(gzip.compress(json.dumps(calibrated,separators=(',',':')).encode(),mtime=0))
        freeze.update(model_name=MODEL_NAME, vcal_sha256=d3.sha256(model_path),
                      anchor_orientation='N', missing_anchor_ids=sorted(d3.CALIBRATION-selected),
                      short_anchors=True, evaluation='exploratory_short_anchor_protocol')
        d3.write_json(fit_dir/'freeze.json', freeze)

        path = Path(temp)/'test.zip'
        planned = planned_points(args.workbook, plan)
        prepare_subset(args.test, path, planned)
        sessions = d3.load_sessions(path, plan, set(planned), 'test', DAY, baseline['config'], formal=True)

    training = json.loads((fit_dir/'training_audit.json').read_text())
    prior_sessions = set(freeze['anchor_session_ids']) | set(training['d1_session_ids'])
    prior_sessions.update(r['session_id'] for r in d3.read_csv(d3.ROOT/'d2_validation/comparison/static_validation_manifest.csv'))
    if sessions.keys() & selected or prior_sessions & {s['session_id'] for s in sessions.values()}:
        raise ValueError('Train or Validation sessions overlap Test')
    matches = []
    for point, session in sessions.items():
        for scan in d3.in_window(session):
            if scan['features'] and (hit := training['fingerprint_points'].get(d3.fingerprint_hash(scan['features']))):
                matches.append(dict(test_point=point, scan_id=scan['scan_id'], training_points=hit))
    summary = compare(sessions, baseline, calibrated, out)
    previous = json.loads(reference_path.with_name('summary.json').read_text())['V1']
    if any(summary['V1'][key]!=previous[key] for key in ('all','basic_2_to_4')):
        raise ValueError('Paired V1 replay disagrees with the original static report')
    d3.save_csv(out/'submitted_anchor_sessions.csv', anchor_audit)
    d3.write_json(out/'training_fingerprint_overlap.json', matches)
    d3.write_json(out/'summary.json', summary)
    d3.write_json(out/'inputs.json', dict(d3_reference=reference, anchor_sha256=d3.sha256(args.anchors),
                                          fit_freeze_sha256=d3.sha256(fit_dir/'freeze.json')))


if __name__ == '__main__':
    main()
