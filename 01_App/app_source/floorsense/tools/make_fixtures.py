"""Generate Kotlin parity fixtures: scikit-learn KNN outputs for crafted and real scans.

Run with the same environment as floorsense_c (scikit-learn 1.9.1):
    python make_fixtures.py --c-dir ../../floorsense_c --static <static sessions dir> \
        --unseen <validation sessions dir> --out <fixtures.json>
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np


def scan_rows(session_dir):
    scans = defaultdict(list)
    with (session_dir / 'wifi.csv').open(newline='') as f:
        for r in csv.DictReader(f):
            scans[int(r['scan_id'])].append(r)
    return scans


def to_aps(rows):
    return [dict(bssid=r['bssid'], rssi=float(r['rssi']), age_ms=int(float(r['age_ms'])) if r['age_ms'] else None)
            for r in rows if r['bssid'].strip()]


def reference_fingerprint(aps, max_age):
    """Same rules as pipeline.load_data, written independently of the Kotlin code."""
    values = defaultdict(list)
    for ap in aps:
        rssi = ap['rssi']
        if not np.isfinite(rssi) or not -127 <= rssi < 0:
            continue
        age = ap.get('age_ms')
        if age is not None and (age < 0 or age > max_age):
            continue
        values[ap['bssid'].lower()].append(float(np.clip(rssi, -100, -20)))
    return {b: float(np.median(v)) for b, v in values.items()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--c-dir', type=Path, required=True)
    p.add_argument('--static', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--real', type=int, default=60)
    p.add_argument('--unseen', type=Path, action='append', default=[],
                   help='session dirs NOT in training (e.g. validation); exercises non-zero-distance voting')
    args = p.parse_args()

    import sys
    sys.path.insert(0, str(args.c_dir))
    from pipeline import encode_transform

    bundle = joblib.load(args.c_dir / 'results/models/KNN.joblib')
    vocab = bundle['config']['bssids']
    max_age = bundle['config']['max_age_ms']
    models = bundle['models']

    cases = []
    sessions = sorted(d for d in args.static.iterdir() if (d / 'wifi.csv').exists())
    rng = np.random.default_rng(0)
    chosen = [sessions[i] for i in sorted(rng.choice(len(sessions), size=min(args.real, len(sessions)), replace=False))]
    for d in chosen:
        scans = scan_rows(d)
        sid, rows = next((k, v) for k, v in sorted(scans.items())
                         if all(r['fresh'] == '1' and r['results_updated'] == '1' for r in v) and v[0]['bssid'])
        cases.append(dict(name=f'real:{d.name}:{sid}', aps=to_aps(rows)))

    for root in args.unseen:
        for d in sorted(x for x in root.iterdir() if (x / 'wifi.csv').exists()):
            for sid, rows in sorted(scan_rows(d).items()):
                if all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows) and rows[0]['bssid']:
                    cases.append(dict(name=f'unseen:{d.name}:{sid}', aps=to_aps(rows)))
                    break

    base = cases[0]['aps']
    known = [b for b in vocab[:40]]
    cases += [
        dict(name='uppercase_bssid', aps=[dict(a, bssid=a['bssid'].upper()) for a in base]),
        dict(name='duplicate_bssid_even', aps=base + [dict(base[0], rssi=base[0]['rssi'] - 10)]),
        dict(name='duplicate_bssid_odd', aps=base + [dict(base[0], rssi=-30.0), dict(base[0], rssi=-95.0)]),
        dict(name='unknown_bssids', aps=base + [dict(bssid=f'ff:ff:ff:00:00:{i:02x}', rssi=-40.0, age_ms=0) for i in range(5)]),
        dict(name='rssi_out_of_range', aps=base + [dict(bssid=known[0], rssi=0.0, age_ms=0),
                                                   dict(bssid=known[1], rssi=-130.0, age_ms=0),
                                                   dict(bssid=known[2], rssi=-10.0, age_ms=0),
                                                   dict(bssid=known[3], rssi=-110.0, age_ms=0)]),
        dict(name='stale_ap', aps=[dict(a, age_ms=20000) if i % 3 == 0 else a for i, a in enumerate(base)]),
        dict(name='all_unknown', aps=[dict(bssid=f'ee:ee:ee:00:00:{i:02x}', rssi=-50.0, age_ms=0) for i in range(8)]),
        dict(name='few_aps', aps=base[:3]),
        dict(name='single_ap', aps=base[:1]),
    ]

    def plain(v):
        return v.item() if hasattr(v, 'item') else v

    out = []
    for c in cases:
        fp = reference_fingerprint(c['aps'], max_age)
        n_known = sum(b in set(vocab) for b in fp)
        entry = dict(name=c['name'], aps=c['aps'], fingerprint=fp, known_aps=n_known)
        if n_known:
            x = encode_transform([fp], vocab)
            for target, m in models.items():
                entry[target] = plain(m.predict(x)[0])
                entry[f'{target}_classes'] = [plain(v) for v in m.classes_]
                entry[f'{target}_proba'] = m.predict_proba(x)[0].tolist()
        out.append(entry)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(model='KNN.joblib', cases=out), indent=1))
    print(f'wrote {len(out)} cases to {args.out}')


if __name__ == '__main__':
    main()
