"""Replay the D2 archives with the unchanged classifier and two update policies."""

from collections import Counter, defaultdict
import csv
import gzip
import json
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from mobile_reference import REALTIME_POLICY, predict, prepare_scan, realtime_step

STAGE = Path(__file__).resolve().parent


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def save_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def scans_from_archive(archive, member):
    groups = defaultdict(list)
    text = archive.read(member).decode('utf-8-sig')
    for row in csv.DictReader(text.splitlines()):
        groups[int(row['scan_id'])].append(row)
    return sorted(groups.items(), key=lambda pair: (int(pair[1][0]['t_elapsed_ms']), pair[0]))


def read_static(payload):
    inputs = {}
    manifest = read_csv(STAGE/'comparison/static_validation_manifest.csv')
    with ZipFile(STAGE/'data/static_validation.zip') as archive:
        for row in manifest:
            member = row['meta_entry'].replace('meta.json', 'wifi.csv')
            for scan_id, rows in scans_from_archive(archive, member):
                key = f"{row['session_id']}:{scan_id}"
                inputs[key] = prepare_scan(rows, payload['config'])
    return inputs


def static_replay(payload, ap_only, out):
    inputs = read_static(payload)
    rows = []
    policies = {'ap_only': ap_only, 'dual_gate': payload}
    historical = read_csv(STAGE/'comparison/scan_predictions.csv')
    for row in historical:
        if row['model'] != payload['name']:
            continue
        scan = inputs[row['sample_id']]
        raw = predict(scan, payload)
        assert raw['known_aps'] == int(row['known_aps'])
        assert raw['floor'] == int(row['floor_pred']) and raw['zone'] == row['zone_pred']
        result = {k: row[k] for k in ('sample_id', 'point_id', 'orientation', 'floor', 'zone', 'n_ap', 'known_aps')}
        result.update(raw_floor=raw['floor'], raw_floor_vote_score=raw['floor_vote_score'], raw_zone=raw['zone'])
        for name, bundle in policies.items():
            gated = realtime_step(scan, bundle)
            result.update({f'{name}_floor': gated['floor'], f'{name}_zone': gated['zone'],
                           f'{name}_reason': gated.get('reason', ''), f'{name}_updated': gated['is_new_estimate']})
        rows.append(result)
    summary = []
    for name in ('raw', *policies):
        accepted = [r for r in rows if r[f'{name}_floor'] is not None]
        floor = sum(r[f'{name}_floor'] == int(r['floor']) for r in accepted)
        zone = sum(r[f'{name}_zone'] == r['zone'] for r in accepted)
        joint = sum(r[f'{name}_floor'] == int(r['floor']) and r[f'{name}_zone'] == r['zone'] for r in accepted)
        summary.append(dict(policy=name, scans=len(rows), updates=len(accepted), floor_correct=floor,
                            zone_correct=zone, joint_correct=joint, covered_points=len({r['point_id'] for r in accepted})))
    save_csv(out/'static_policy_predictions.csv', rows)
    save_csv(out/'static_policy_summary.csv', summary)
    return summary, inputs


def dynamic_replay(payload, ap_only, out):
    detail, summary = [], []
    interesting = {}
    manifest = read_csv(STAGE/'dynamic_validation_manifest.csv')
    with ZipFile(STAGE/'data/dynamic_validation.zip') as archive:
        for run in manifest:
            previous = {'ap_only': None, 'dual_gate': None}
            counts = Counter()
            total_aps, known_aps = [], []
            member = run['archive_directory'] + '/wifi.csv'
            for scan_id, rows in scans_from_archive(archive, member):
                scan = prepare_scan(rows, payload['config'])
                raw = predict(scan, payload)
                elapsed = int(rows[0]['t_elapsed_ms'])
                if run['move_start_elapsed_ms']:
                    start, end = int(run['move_start_elapsed_ms']), int(run['move_end_elapsed_ms'])
                    phase = 'before' if elapsed < start else 'after' if elapsed >= end else 'during'
                else:
                    phase = 'horizontal'
                result = dict(run_id=run['run_id'], scan_id=scan_id, elapsed_ms=elapsed, phase=phase,
                              total_aps=len(scan), known_aps=raw['known_aps'], raw_floor=raw['floor'],
                              raw_floor_vote_score=raw.get('floor_vote_score'))
                total_aps.append(len(scan))
                known_aps.append(raw['known_aps'])
                counts['raw_2f'] += raw['floor'] == 2
                for name, bundle in [('ap_only', ap_only), ('dual_gate', payload)]:
                    gated = realtime_step(scan, bundle, previous[name])
                    if gated['is_new_estimate']:
                        previous[name] = gated['floor']
                        counts[f'{name}_updates'] += 1
                        counts[f'{name}_2f_updates'] += gated['floor'] == 2
                        counts[f'{name}_new_end_floor_after_arrival'] += phase == 'after' and gated['floor'] == int(run['end_floor'])
                    counts[f"{name}_{gated['status']}"] += 1
                    for key in ('floor','status','reason'):
                        result[f'{name}_{key}'] = gated.get(key, '')
                detail.append(result)
                if run['run_id'] == 'T8-ST04-VA01' and result['ap_only_status'] == 'ok':
                    interesting[scan_id] = scan
            summary.append(dict(run_id=run['run_id'], start_floor=run['start_floor'], end_floor=run['end_floor'],
                                scans=len(total_aps), median_total_aps=median(total_aps), median_known_aps=median(known_aps),
                                raw_2f=counts['raw_2f'], **{f'{name}_{key}': counts[f'{name}_{key}']
                                for name in previous for key in ('updates','2f_updates','held','waiting','new_end_floor_after_arrival')},
                                dual_last_floor=previous['dual_gate']))
    save_csv(out/'dynamic_policy_predictions.csv', detail)
    save_csv(out/'dynamic_policy_summary.csv', summary)
    return summary, interesting


def main():
    handoff = STAGE/'D_handoff/realtime'
    with gzip.open(handoff/'wifi_model.json.gz', 'rt', encoding='utf-8') as stream:
        payload = json.load(stream)
    assert payload['realtime_policy'] == REALTIME_POLICY
    ap_only = dict(payload, realtime_policy=dict(REALTIME_POLICY, min_floor_vote_score=0.0))
    out = STAGE/'realtime_replay'
    out.mkdir(exist_ok=True)
    static, inputs = static_replay(payload, ap_only, out)
    dynamic, interesting = dynamic_replay(payload, ap_only, out)
    weak = next(iter(interesting.values()))
    good = next(scan for scan in inputs.values() if realtime_step(scan, payload)['is_new_estimate'])
    cases = [('cold start with no AP', {}, None), ('empty callback after 5F', {}, 5),
             ('STAIRS with enough AP but weak floor vote', weak, 5), ('fresh scan resumes updates', good, 5)]
    vectors = [dict(case=label, bssid_rssi=scan, previous_floor=previous,
                    expected=realtime_step(scan, payload, previous)) for label, scan, previous in cases]
    (handoff/'realtime_policy_vectors.json').write_text(json.dumps(vectors, indent=2, ensure_ascii=False)+'\n')
    print('Static policies:', json.dumps(static, ensure_ascii=False))
    print('Dynamic policies:', json.dumps(dynamic, ensure_ascii=False))


if __name__ == '__main__':
    main()
