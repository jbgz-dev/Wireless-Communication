"""Replay the November 16 static Test subset against the frozen V1."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
from statistics import median
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'd3_ready'))
from d3_eval import (PLAN, STAGE, V1, WINDOW_MS, fingerprint_hash, in_window, load_sessions,
                     read_csv, read_model, save_csv, score_counts, sha256, write_json)
from d3_v1_score import replay_v1
from replay_realtime import scans_from_archive
from mobile_reference import prepare_scan

DAY = '2026-11-16'


def planned_points(workbook_path, plan):
    book = load_workbook(workbook_path, read_only=True, data_only=True)
    points = {}
    try:
        for row in book.worksheets[0].iter_rows(max_row=1000, max_col=15, values_only=True):
            point_id, building, floor, anchor, split, landmark, site, *_ = row
            if not (isinstance(point_id, str) and point_id.startswith('T8-F') and '-TS' in point_id):
                continue
            label = plan[point_id]
            # The revised workbook omits the coarse Zone column. Its A–H labels
            # still have to match the frozen point register before using that mapping.
            coarse_zone = label['zone'] if row[14] is None else row[14]
            if (point_id in points or (building, floor, anchor, split, coarse_zone) !=
                    ('T8', label['floor'], label['anchor_zone'], label['split'], label['zone']) or split != 'Test'):
                raise ValueError(f'Planning workbook disagrees with the frozen truth table for {point_id}')
            points[point_id] = dict(point_id=point_id, floor=floor, anchor=anchor, zone=coarse_zone,
                                    planned_landmark=landmark, actual_site=site)
    finally:
        book.close()
    return points


def members(archive):
    return [name for name in sorted(archive.namelist())
            if name.endswith('/meta.json') and '__MACOSX' not in PurePosixPath(name).parts]


def prepare_subset(sources, target, planned):
    selected, audit, excluded = {}, [], Counter()
    with ZipFile(target, 'w') as merged:
        for path in sources:
            with ZipFile(path) as source:
                if source.testzip() is not None:
                    raise ValueError(f'Invalid archive: {path.name}')
                for item in members(source):
                    meta = json.loads(source.read(item))
                    labels = meta['labels']
                    if labels['mode'] != 'static':
                        excluded[labels['mode']] += 1
                        continue
                    point_id = labels['point_id']
                    if point_id.startswith('TS') and point_id[2:].isdecimal():
                        point_id = f"T8-F{labels['floor']}-{labels['zone']}-{point_id}"
                    if point_id not in planned:
                        raise ValueError(f'Unexpected static point {point_id}')
                    review = dict(point_id=point_id, raw_point_id=labels['point_id'], source=path.name,
                                  session_id=meta['session_id'], status=meta['status'],
                                  duration_ms=meta['end_wall_ms'] - meta['start_wall_ms'],
                                  start_local=meta['start_local'], collector=labels['collector'],
                                  device=meta['device']['model'], raw_wifi_callbacks=meta['stats']['wifi_scans'],
                                  label_history=json.dumps(meta.get('label_history', []), ensure_ascii=False))
                    audit.append(review)
                    if meta['status'] != 'complete':
                        continue
                    if point_id in selected:
                        raise ValueError(f'Multiple complete records for {point_id}')
                    selected[point_id] = review
                    labels['point_id'] = point_id
                    merged.writestr(point_id+'/meta.json', json.dumps(meta))
                    merged.writestr(point_id+'/wifi.csv', source.read(str(PurePosixPath(item).with_name('wifi.csv'))))
    for row in audit:
        row['scored'] = selected.get(row['point_id']) is row
    return selected, audit, excluded


def previous_fingerprints(path, config):
    hashes, session_ids, points = defaultdict(set), set(), set()
    with ZipFile(path) as archive:
        for item in members(archive):
            meta = json.loads(archive.read(item))
            point_id = meta['labels']['point_id']
            points.add(point_id)
            session_ids.add(meta['session_id'])
            name = str(PurePosixPath(item).with_name('wifi.csv'))
            for _, rows in scans_from_archive(archive, name):
                features = prepare_scan(rows, config)
                if features:
                    hashes[fingerprint_hash(features)].add(point_id)
    return hashes, session_ids, points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--test', type=Path, nargs='+', required=True)
    parser.add_argument('--d1', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    out = args.out
    plan = json.loads(PLAN.read_text())
    baseline = read_model()
    freeze_path = STAGE/'offline_check/baseline_freeze.json'
    frozen = json.loads(freeze_path.read_text())
    validation_record = json.loads((freeze_path.parent/'historical_validation/inputs.json').read_text())
    if (sha256(args.d1) != frozen['file_sha256']['data/static_train.zip'] or
            sha256(args.validation) != validation_record['validation_sha256']):
        raise ValueError('Reference archive hash mismatch')
    for relative, digest in frozen['file_sha256'].items():
        if relative != 'data/static_train.zip' and sha256(PLAN.parent/relative) != digest:
            raise ValueError(f'Frozen file hash mismatch: {relative}')
    if baseline['realtime_policy'] != frozen['realtime_policy'] or WINDOW_MS != frozen['scoring_window_ms']:
        raise ValueError('Frozen scoring rules mismatch')
    planned = planned_points(args.workbook, plan)
    with TemporaryDirectory() as temporary:
        canonical = Path(temporary)/'static.zip'
        selected, audit, excluded = prepare_subset(args.test, canonical, planned)
        sessions = load_sessions(canonical, plan, set(planned), 'test', DAY, baseline['config'], formal=True)
    earlier = {role: previous_fingerprints(path, baseline['config']) for role, path in
               [('D1 Train', args.d1), ('D2 Validation', args.validation)]}
    for role, (_, session_ids, point_ids) in earlier.items():
        if point_ids & sessions.keys() or session_ids & {s['session_id'] for s in sessions.values()}:
            raise ValueError(f'Test point IDs or session IDs overlap {role}')

    replay_v1(sessions, baseline, out, frozen['target_correct'])
    summary = json.loads((out/'summary.json').read_text())['V1']
    for key in ('all', 'basic_2_to_4'):
        summary[key].pop('goal_correct')
    predictions = read_csv(out/'point_predictions.csv')
    events = defaultdict(list)
    for event in read_csv(out/'scan_events.csv'):
        events[event['point_id']].append(event)
    reviews, exact_matches, test_hashes = [], [], defaultdict(set)
    for prediction in predictions:
        point = prediction['point_id']
        scans = in_window(sessions[point])
        qualified = [r for r in events[point] if r['is_new_estimate']=='True']
        accepted = qualified[-1] if qualified else {}
        known = [int(r['known_aps']) for r in events[point]]
        occurrences = Counter(fingerprint_hash(r['features']) for r in scans if r['features'])
        for fp, count in occurrences.items():
            test_hashes[fp].add(point)
            for role, (fingerprints, _, _) in earlier.items():
                if fp in fingerprints:
                    exact_matches.append(dict(point_id=point, reference=role, matching_scans=count,
                                              reference_points=sorted(fingerprints[fp])))
        flags = Counter(r['reason'] for r in events[point])
        zone_updates = Counter(r['zone'] for r in qualified)
        reviews.append(dict(**prediction, floor_correct=prediction['floor']==prediction['floor_pred'],
                            zone_correct=prediction['zone']==prediction['zone_pred'],
                            planned_landmark=planned[point]['planned_landmark'],
                            raw_wifi_callbacks=selected[point]['raw_wifi_callbacks'],
                            usable_in_window_scans=sum(occurrences.values()), distinct_in_window=len(occurrences),
                            min_known_aps=min(known, default=None), median_known_aps=median(known) if known else None,
                            max_known_aps=max(known, default=None), insufficient_ap=flags['low_known_aps'],
                            low_vote=flags['low_floor_vote'], zone_update_counts=json.dumps(dict(zone_updates), sort_keys=True),
                            last_update_ms=accepted.get('elapsed_ms'),
                            last_update_known_aps=accepted.get('known_aps'), last_floor_vote=accepted.get('floor_vote_score'),
                            last_zone_vote=accepted.get('zone_vote_score'), label_history=selected[point]['label_history']))
    save_csv(out/'session_audit.csv', audit)
    save_csv(out/'point_review.csv', reviews)
    write_json(out/'fingerprint_overlap_review.json', dict(earlier_sets=exact_matches,
               distinct_test_point_matches=[dict(fingerprint=fp, points=sorted(ps))
                                            for fp, ps in sorted(test_hashes.items()) if len(ps)>1]))
    typed_points = [dict(floor=int(r['floor']), zone=r['zone'], floor_pred=int(r['floor_pred']) if r['floor_pred'] else None,
                         zone_pred=r['zone_pred'] or None, point_id=r['point_id'],
                         first_correct_ms=int(r['first_correct_ms']) if r['first_correct_ms'] else None) for r in reviews]
    counts_by_zone = {zone: score_counts([r for r in typed_points if r['zone']==zone])
                      for zone in ('West', 'Middle', 'East')}
    original_missing = sorted(set(frozen['planned_d3_test_ids']) - sessions.keys())
    save_csv(out/'remaining_original_plan.csv', [dict(point_id=p, **plan[p]) for p in original_missing])
    label_edits = {r['point_id']: json.loads(r['label_history']) for r in audit
                   if r['scored'] and r['label_history']!='[]'}
    edited_floor = [point for point, history in label_edits.items() if any('floor' in h['after'] for h in history)]
    write_json(out/'summary.json', dict(V1=summary, by_zone=counts_by_zone,
               without_edited_floor_points=score_counts([r for r in typed_points if r['point_id'] not in edited_floor]),
               attached_plan_n=len(planned), attached_plan_scored_n=len(sessions),
               original_plan_n=len(frozen['planned_d3_test_ids']), original_plan_missing=original_missing,
               label_edits=label_edits, floor_label_edited=edited_floor,
               incomplete_static=[r['point_id'] for r in audit if not r['scored']],
               dynamic_records=dict(excluded), point_ids_without_stand_location=sum(not p['actual_site'] for p in planned.values()),
               scan_summary=dict(raw_static=sum(r['raw_wifi_callbacks'] for r in reviews),
                                 window=sum(int(r['window_scans']) for r in reviews),
                                 clean=sum(r['usable_in_window_scans'] for r in reviews),
                                 distinct_per_point=sum(r['distinct_in_window'] for r in reviews),
                                 updates=sum(int(r['updates']) for r in reviews),
                                 wrong_floor_updates=sum(int(r['wrong_updates']) for r in reviews))))
    write_json(out/'inputs.json', dict(capture_date=DAY, scoring_window_ms=WINDOW_MS,
                                     v1_sha256=sha256(V1/'wifi_model.json.gz'),
                                     baseline_freeze_sha256=sha256(freeze_path),
                                     inputs={p.name: sha256(p) for p in [*args.test, args.workbook, args.d1, args.validation]}))


if __name__ == '__main__':
    main()
