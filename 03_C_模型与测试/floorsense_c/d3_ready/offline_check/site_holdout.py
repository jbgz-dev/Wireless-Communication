"""Rehearse cross-day calibration with ten Train point IDs excluded from both fits."""

import argparse
from collections import Counter
import csv
from datetime import date
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath
import sys
from zipfile import ZipFile

import joblib
import pandas as pd
import sklearn
from sklearn.base import clone

OFFLINE = Path(__file__).resolve().parent
sys.path.insert(0, str(OFFLINE.parent))
from d3_eval import (CALIBRATION, D1, FLOORS, PILOT_HOLDOUT, PLAN, ROOT, V1, WINDOW_MS,
                     fingerprint_hash, in_window, load_sessions, save_csv, score_counts, score_one)
from temporal_eval import ARCHIVE, load_d1, read_model, sha256, write_json
from candidates import MIN_AP_POINTS, _balanced_scan_weights, _stable_ap_vocabulary
from export_d import portable
from pipeline import encode_transform

CAPTURE_DATE = '2026-11-11'
BASELINE = 'D1_40'
CALIBRATED = 'D1_40_plus_AG10'


def archive_metadata(path):
    with ZipFile(path) as archive:
        return [json.loads(archive.read(name)) for name in sorted(archive.namelist())
                if name.endswith('/meta.json') and '__MACOSX' not in PurePosixPath(name).parts]


def fit_payload(name, train, reference, policy, vocab):
    labels = train[['point_id', 'floor', 'zone']].drop_duplicates()
    if labels.point_id.duplicated().any() or not vocab:
        raise ValueError('Inconsistent training point labels or empty AP vocabulary')
    x = encode_transform(train.features.tolist(), vocab)
    weights = _balanced_scan_weights(train, labels)
    models = {target: portable(clone(estimator).fit(x, train[target], sample_weight=weights[target]))
              for target, estimator in reference['models'].items()}
    config = dict(reference['config'], bssids=vocab)
    config.pop('minimum_d1_points_per_ap', None)
    config['minimum_train_points_per_ap'] = MIN_AP_POINTS
    return dict(schema_version=1, name=name, config=config, realtime_policy=policy, models=models)


def calibration_rows(sessions, baseline_train, vocab, min_known):
    rows, counts = [], []
    baseline_keys = set(zip(baseline_train.point_id, baseline_train.fingerprint))
    for session in sessions:
        point = session['point_id']
        window = in_window(session)
        seen = set()
        added = 0
        for scan in window:
            features = scan['features']
            if len(vocab.intersection(features)) < min_known:
                continue
            fingerprint = fingerprint_hash(features)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            if (point, fingerprint) in baseline_keys:
                continue
            rows.append(dict(point_id=point, floor=session['floor'], zone=session['zone'],
                             session_id=session['session_id'], fingerprint=fingerprint, features=features))
            added += 1
        counts.append(dict(point_id=point, session_id=session['session_id'], floor=session['floor'],
                           zone=session['zone'], start_local=session['start_local'],
                           duration_ms=session['duration_ms'], raw_scans=len(session['scans']),
                           window_scans=len(window), qualified_unique_fingerprints=len(seen),
                           added_fingerprints=added))
    if not rows:
        raise ValueError('No qualified new calibration fingerprints')
    return rows, counts


def score(sessions, payloads, out):
    summary, events, points = {}, [], {}
    for name, payload in payloads.items():
        results = []
        for session in sessions:
            result, scans = score_one(session, payload)
            results.append(result)
            events.extend(dict(model=name, **scan) for scan in scans)
        points[name] = results
        summary[name] = dict(all=score_counts(results),
                             by_floor={f: score_counts([p for p in results if p['floor'] == f]) for f in FLOORS})

    pairs = []
    for base, calibrated in zip(points[BASELINE], points[CALIBRATED], strict=True):
        pair = {key: base[key] for key in ('point_id', 'session_id', 'floor', 'zone', 'duration_ms', 'window_scans')}
        for name, result in ((BASELINE, base), (CALIBRATED, calibrated)):
            pair.update({f'{name}_{key}': result[key] for key in ('floor_pred', 'zone_pred', 'timeout',
                         'first_update_ms', 'first_correct_ms', 'updates', 'wrong_updates')})
        base_floor = base['floor_pred'] == pair['floor']
        calibrated_floor = calibrated['floor_pred'] == pair['floor']
        pair['floor_delta'] = int(calibrated_floor) - int(base_floor)
        pair['joint_delta'] = (int(calibrated_floor and calibrated['zone_pred'] == pair['zone'])
                               - int(base_floor and base['zone_pred'] == pair['zone']))
        pairs.append(pair)
    for target in ('floor', 'joint'):
        deltas = Counter(pair[f'{target}_delta'] for pair in pairs)
        summary[f'paired_{target}'] = dict(improved=deltas[1], unchanged=deltas[0], regressed=deltas[-1])

    save_csv(out/'paired_points.csv', pairs)
    # Waiting, held and accepted events expose different optional fields.
    event_fields = ('model', 'point_id', 'session_id', 'scan_id', 'elapsed_ms', 'expected_floor',
                    'expected_zone', 'status', 'reason', 'floor', 'zone', 'known_aps',
                    'is_new_estimate', 'floor_vote_score', 'zone_vote_score')
    with (out/'scan_events.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=event_fields)
        writer.writeheader()
        writer.writerows(events)
    return summary


def run(out):
    plan = json.loads(PLAN.read_text())
    all_train = {point for point, labels in plan.items() if labels['split'] == 'Train'}
    fit_ids = all_train - PILOT_HOLDOUT
    if (len(all_train) != 50 or len(fit_ids) != 40 or not PILOT_HOLDOUT <= all_train
            or not CALIBRATION <= fit_ids):
        raise ValueError('The plan must contain 40 retained Train points and 10 excluded C/F points')

    original = read_model()
    reference = joblib.load(V1/'selected.joblib')
    if original['config'] != reference['config']:
        raise ValueError('V1 portable and sklearn configuration disagree')
    policy = original['realtime_policy']
    raw, d1, _ = load_d1(D1)
    d1_meta = archive_metadata(D1)
    d1_session_ids = {m['session_id'] for m in d1_meta}
    d1_removed = [m for m in d1_meta if m['labels']['point_id'] in PILOT_HOLDOUT]
    d1_dates = sorted({m['start_local'][:10] for m in d1_meta})
    removed_orientations = Counter((m['labels']['point_id'], m['labels']['orientation']) for m in d1_removed)
    if (set(d1.point_id) != all_train or {m['labels']['point_id'] for m in d1_meta} != all_train
            or len(d1_session_ids) != len(d1_meta) or max(d1_dates) >= CAPTURE_DATE
            or removed_orientations != {(p, direction): 1 for p in PILOT_HOLDOUT for direction in 'NESW'}):
        raise ValueError('Unexpected original D1 Train point, session or date coverage')
    base_train = d1[d1.point_id.isin(fit_ids)].reset_index(drop=True)
    base_vocab = _stable_ap_vocabulary(base_train)
    baseline = fit_payload(BASELINE, base_train, reference, policy, base_vocab)

    sessions = load_sessions(ARCHIVE, plan, CALIBRATION | PILOT_HOLDOUT, 'train', CAPTURE_DATE, baseline['config'])
    metadata = {m['session_id']: m for m in archive_metadata(ARCHIVE)}
    if d1_session_ids.intersection(metadata):
        raise ValueError('Temporal Session IDs must be disjoint from the D1 archive')
    for session in sessions.values():
        meta = metadata[session['session_id']]
        session['duration_ms'] = int(meta['end_wall_ms']) - session['start_wall_ms']
        if session['duration_ms'] <= 0:
            raise ValueError(f"Missing positive capture duration for {session['point_id']}")
    anchors = [sessions[p] for p in sorted(CALIBRATION)]
    held_out = [sessions[p] for p in sorted(PILOT_HOLDOUT)]
    new_rows, anchor_counts = calibration_rows(anchors, base_train, set(base_vocab), policy['min_known_aps'])
    calibrated_train = pd.concat([base_train, pd.DataFrame(new_rows)], ignore_index=True)
    if set(calibrated_train.point_id) != fit_ids or calibrated_train.duplicated(['point_id', 'fingerprint']).any():
        raise ValueError('Calibration must preserve fit point IDs and fingerprint uniqueness within each point')
    calibrated_vocab = _stable_ap_vocabulary(calibrated_train)
    calibrated = fit_payload(CALIBRATED, calibrated_train, reference, policy, calibrated_vocab)

    out.mkdir(parents=True, exist_ok=True)
    summary = score(held_out, {BASELINE: baseline, CALIBRATED: calibrated}, out)
    save_csv(out/'calibration_anchors.csv', anchor_counts)
    holdout_fingerprints = {fingerprint_hash(r['features']) for s in held_out for r in in_window(s) if r['features']}
    summary['training'] = dict(d1_clean_fingerprints_removed=len(d1)-len(base_train),
                               d1_eligible_scans_removed=int(raw.point_id.isin(PILOT_HOLDOUT).sum()),
                               calibrated_fingerprints_added=len(new_rows),
                               anchors_contributing=sum(a['added_fingerprints'] > 0 for a in anchor_counts),
                               anchor_capture_duration_ms=[min(s['duration_ms'] for s in anchors),
                                                           max(s['duration_ms'] for s in anchors)],
                               **{BASELINE: dict(points=len(fit_ids), fingerprints=len(base_train), bssids=len(base_vocab)),
                                  CALIBRATED: dict(points=len(fit_ids), fingerprints=len(calibrated_train),
                                                   bssids=len(calibrated_vocab))})
    summary['holdout_capture'] = dict(min_duration_ms=min(s['duration_ms'] for s in held_out),
                                      max_duration_ms=max(s['duration_ms'] for s in held_out),
                                      window_scans=sum(len(in_window(s)) for s in held_out),
                                      by_zone=dict(sorted(Counter(s['zone'] for s in held_out).items())),
                                      unique_fingerprints=len(holdout_fingerprints),
                                      exact_fingerprint_matches_to_base=len(holdout_fingerprints.intersection(base_train.fingerprint)),
                                      exact_fingerprint_matches_to_calibrated=len(holdout_fingerprints.intersection(calibrated_train.fingerprint)))
    inputs = dict(purpose='Development comparison of refit RF models on the November 11 C/F Train revisits',
                  scope='Point-ID holdout. Physical site separation remains unverified. C/F are Train IDs covering Middle and East.',
                  capture_date=CAPTURE_DATE, d1_dates=d1_dates,
                  elapsed_days_from_d1=sorted((date.fromisoformat(CAPTURE_DATE)-date.fromisoformat(d)).days for d in d1_dates),
                  fit_point_ids=sorted(fit_ids), calibration_point_ids=sorted(CALIBRATION),
                  holdout_point_ids=sorted(PILOT_HOLDOUT), d1_temporal_session_id_overlap=0,
                  d1_archive_sessions=dict(total=len(d1_meta), removed=len(d1_removed), retained=len(d1_meta)-len(d1_removed)),
                  scoring_window_ms=WINDOW_MS, realtime_policy=policy,
                  scoring='Cold start per Session. Score the last accepted estimate within 0–30 s inclusive. Captures may end earlier. No estimate is a miss.',
                  calibration_qualification=(f"At least {policy['min_known_aps']} APs in the D1_40 vocabulary. "
                                             "Deduplicate per point against earlier scans from the same anchor and against retained D1 fingerprints."),
                  vocabulary_rule=f'Fit each model vocabulary on its own training rows. APs must occur at {MIN_AP_POINTS} or more distinct point IDs.',
                  weighting='Equal class mass and equal point mass within each class, separately for floor and zone.',
                  rf_parameters={target: estimator.get_params() for target, estimator in reference['models'].items()},
                  runtime=dict(python=sys.version.split()[0], sklearn=sklearn.__version__, pandas=pd.__version__,
                               **{package: version(package) for package in ('numpy', 'scipy', 'joblib')}),
                  file_sha256={str(path.relative_to(ROOT)): sha256(path) for path in
                               (D1, ARCHIVE, PLAN, V1/'wifi_model.json.gz', V1/'selected.joblib')})
    write_json(out/'inputs.json', inputs)
    write_json(out/'summary.json', summary)
    print(json.dumps({name: summary[name]['all'] for name in (BASELINE, CALIBRATED)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=OFFLINE/'site_holdout_results')
    run(parser.parse_args().out)
