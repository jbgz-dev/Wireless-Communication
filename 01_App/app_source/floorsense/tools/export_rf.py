"""Export the D2-selected Random Forest (Train only, scans with >= 10 APs) for the Android app,
plus parity fixtures (scikit-learn predict_proba on real and crafted scans).

Run with the floorsense_c environment (scikit-learn 1.9.1):
    python -I export_rf.py --c-dir ../../../floorsense_c --static <static train sessions> \
        --unseen <validation sessions dir> [--unseen ...] --model-out ../src/main/assets/rf_model.json \
        --fixtures-out ../src/test/resources/rf_fixtures.json

Validation sessions are only used as unseen *inputs* for the parity check, never for fitting.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier

MIN_TRAIN_AP = 10


def rf():
    # identical to validation_d2/variants.py rf()
    return RandomForestClassifier(n_estimators=300, max_features='sqrt', class_weight='balanced_subsample',
                                  random_state=42, n_jobs=-1)


def plain(v):
    return v.item() if hasattr(v, 'item') else v


def export_tree(est):
    t = est.tree_
    leaf = t.children_left == -1
    value = t.value[:, 0, :]
    value = value / value.sum(1, keepdims=True)   # what DecisionTreeClassifier.predict_proba returns
    return dict(
        feature=[int(f) if not l else -1 for f, l in zip(t.feature, leaf)],
        threshold=[float(x) if not l else 0.0 for x, l in zip(t.threshold, leaf)],
        left=[int(x) for x in t.children_left],
        right=[int(x) for x in t.children_right],
        # leaves only, to keep the file small; internal nodes get []
        value=[[round(float(v), 12) for v in row] if l else [] for row, l in zip(value, leaf)],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--c-dir', type=Path, required=True)
    p.add_argument('--static', type=Path, required=True)
    p.add_argument('--unseen', type=Path, action='append', default=[])
    p.add_argument('--model-out', type=Path, required=True)
    p.add_argument('--fixtures-out', type=Path, required=True)
    args = p.parse_args()

    sys.path.insert(0, str(args.c_dir))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pipeline import load_data, encode_fit, encode_transform, MAX_AGE_MS
    from make_fixtures import scan_rows, to_aps, reference_fingerprint

    _, train, _ = load_data(args.static, args.c_dir / 'point_labels.json')
    tr = train[train.n_ap >= MIN_TRAIN_AP]
    vocab, x = encode_fit(tr.features.tolist())
    models = {t: rf().fit(x, tr[t]) for t in ('floor', 'zone')}

    base_cfg = json.loads((args.c_dir / 'results/models/feature_config.json').read_text())
    config = dict(base_cfg, bssids=vocab)
    out = dict(
        format='floorsense-rf-1',
        description=f'RandomForest x300, Train scans with >= {MIN_TRAIN_AP} APs ({len(tr)} of {len(train)}), '
                    'split rule: go left if float32(x) <= threshold',
        config=config,
        train_scans=len(tr),
        models={t: dict(classes=[plain(c) for c in m.classes_], trees=[export_tree(e) for e in m.estimators_])
                for t, m in models.items()},
    )
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.model_out.write_text(json.dumps(out, separators=(',', ':')))

    # parity fixtures: a few train scans, one scan per unseen session, crafted edge cases
    cases = []
    for d in sorted(x for x in args.static.iterdir() if (x / 'wifi.csv').exists())[::10]:
        for sid, rows in sorted(scan_rows(d).items()):
            if all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows) and rows[0]['bssid']:
                cases.append(dict(name=f'train:{d.name}:{sid}', aps=to_aps(rows)))
                break
    for root in args.unseen:
        for d in sorted(x for x in root.iterdir() if (x / 'wifi.csv').exists()):
            usable = [(sid, rows) for sid, rows in sorted(scan_rows(d).items())
                      if all(r['fresh'] == '1' and r['results_updated'] == '1' for r in rows) and rows[0]['bssid']]
            for sid, rows in usable[:1] + usable[len(usable) // 2:len(usable) // 2 + 1]:
                cases.append(dict(name=f'unseen:{d.name}:{sid}', aps=to_aps(rows)))
    base = cases[0]['aps']
    cases += [
        dict(name='few_aps', aps=base[:3]),
        dict(name='single_ap', aps=base[:1]),
        dict(name='half_dbm', aps=[dict(a, rssi=a['rssi'] - 0.5) for a in base[:12]]),
    ]
    fixtures = []
    vs = set(vocab)
    for c in cases:
        fp = reference_fingerprint(c['aps'], MAX_AGE_MS)
        entry = dict(name=c['name'], aps=c['aps'], known_aps=sum(b in vs for b in fp))
        xx = encode_transform([fp], vocab)
        for t, m in models.items():
            entry[t] = plain(m.predict(xx)[0])
            entry[f'{t}_proba'] = m.predict_proba(xx)[0].tolist()
        fixtures.append(entry)
    args.fixtures_out.parent.mkdir(parents=True, exist_ok=True)
    args.fixtures_out.write_text(json.dumps(dict(model=out['description'], cases=fixtures), indent=1))
    n_nodes = {t: sum(len(tr_['feature']) for tr_ in m['trees']) for t, m in out['models'].items()}
    print(f'model: {len(vocab)} BSSIDs, {len(tr)} train scans, nodes {n_nodes}, '
          f'{args.model_out.stat().st_size / 1e6:.1f} MB; fixtures: {len(fixtures)} cases')


if __name__ == '__main__':
    main()
