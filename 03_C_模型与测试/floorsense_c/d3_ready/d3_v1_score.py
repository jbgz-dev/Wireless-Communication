"""Score the frozen V1 on the D3 static Test ZIP without a calibration ZIP."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import json
from pathlib import Path
from zipfile import BadZipFile

from d3_eval import (D1, FLOORS, PLAN, ROOT, STAGE, V1, WINDOW_MS, ZONES,
                     fingerprint_hash, in_window, load_d1, load_sessions, read_csv,
                     read_model, save_csv, score_counts, score_one, sha256, write_json)
from offline_check.validation_rehearsal import freeze_baseline


def replay_v1(sessions, baseline, out, goals):
    points, events = [], []
    for point_id in sorted(sessions):
        point, scans = score_one(sessions[point_id], baseline)
        points.append(dict(model='V1', **point))
        events.extend(dict(model='V1', **scan) for scan in scans)

    summary = {
        'all': dict(score_counts(points), goal_correct=goals['all_2_to_6']),
        'basic_2_to_4': dict(score_counts([point for point in points if point['floor'] <= 4]),
                             goal_correct=goals['basic_2_to_4']),
        'by_floor': {floor: score_counts([point for point in points if point['floor'] == floor])
                     for floor in FLOORS},
    }
    out.mkdir(parents=True, exist_ok=True)
    for target, labels in [('floor', FLOORS), ('zone', ZONES)]:
        matrix = Counter((point[target], point[f'{target}_pred']) for point in points)
        rows = [dict(actual=actual, **{str(pred): matrix[actual, pred] for pred in labels},
                     no_update=matrix[actual, None]) for actual in labels]
        save_csv(out/f'cm_V1_{target}.csv', rows)
    save_csv(out/'point_predictions.csv', points)
    fields = ['model', 'point_id', 'session_id', 'scan_id', 'elapsed_ms', 'expected_floor', 'expected_zone',
              'status', 'reason', 'floor', 'zone', 'known_aps', 'is_new_estimate', 'floor_vote_score', 'zone_vote_score']
    with (out/'scan_events.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events)
    write_json(out/'summary.json', {'V1': summary})
    return summary


def score_v1(test, capture_date, out):
    plan = json.loads(PLAN.read_text())
    baseline = read_model()
    freeze_baseline(plan, baseline, verify_only=True)
    freeze_path = STAGE/'offline_check/baseline_freeze.json'
    freeze = json.loads(freeze_path.read_text())
    test_sha = sha256(test)
    sessions = load_sessions(test, plan, set(freeze['planned_d3_test_ids']), 'test',
                             capture_date, baseline['config'], formal=True)

    raw, train, _ = load_d1(D1)
    validation = read_csv(ROOT/'d2_validation/comparison/static_validation_manifest.csv')
    prior_ids = set(raw.session_id) | {row['session_id'] for row in validation}
    overlaps = prior_ids & {session['session_id'] for session in sessions.values()}
    if overlaps:
        raise ValueError(f'Test Session IDs overlap D1 Train or D2 Validation: {sorted(overlaps)}')

    fingerprints = defaultdict(set)
    for row in train.itertuples(index=False):
        fingerprints[fingerprint_hash(row.features)].add(row.point_id)
    matches = []
    for session in sessions.values():
        for scan in in_window(session):
            if scan['features']:
                training_points = fingerprints.get(fingerprint_hash(scan['features']))
                if training_points:
                    matches.append(dict(test_point=session['point_id'], session_id=session['session_id'],
                                        scan_id=scan['scan_id'], training_points=sorted(training_points)))

    summary = replay_v1(sessions, baseline, out, freeze['target_correct'])
    write_json(out/'fingerprint_overlap_review.json', matches)
    write_json(out/'inputs.json', dict(model='V1', capture_date=capture_date, window_ms=WINDOW_MS,
                                     baseline_freeze_sha256=sha256(freeze_path), test_archive_sha256=test_sha,
                                     v1_sha256=sha256(V1/'wifi_model.json.gz')))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True, help='Test collection date (YYYY-MM-DD)')
    parser.add_argument('--test', type=Path, required=True, help='Original static D3 Test ZIP')
    parser.add_argument('--out', type=Path, required=True, help='Directory for V1 test results')
    args = parser.parse_args()
    try:
        summary = score_v1(args.test, args.date.isoformat(), args.out)
    except (BadZipFile, KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({'V1': summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
