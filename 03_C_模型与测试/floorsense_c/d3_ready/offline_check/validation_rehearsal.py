"""Freeze the D3 reference and replay the historical Validation archive."""

import argparse
from pathlib import Path
import gzip
import json
import sys

OFFLINE = Path(__file__).resolve().parent
sys.path.insert(0,str(OFFLINE.parent))
from d3_eval import CALIBRATION, D1, PLAN, ROOT, STAGE, V1, WINDOW_MS, read_model, sha256, write_json
from temporal_eval import evaluate_validation


def freeze_baseline(plan, baseline, *, verify_only=False):
    files = [V1/'wifi_model.json.gz', V1/'selected.joblib', V1/'feature_config.json',
             D1, PLAN, STAGE/'d3_eval.py', V1/'mobile_reference.py']
    test_ids = sorted(point for point,labels in plan.items() if labels['split']=='Test')
    basic_ids = [point for point in test_ids if plan[point]['floor']<=4]
    assert len(test_ids)==74 and len(basic_ids)==54 and len(CALIBRATION)==10
    assert all(plan[point]['split']=='Train' for point in CALIBRATION)
    manifest = dict(reference_model='V1',reference_training='D1 Train',
                    capture_orientation='N', scoring_window_ms=WINDOW_MS,
                    point_scoring='Cold start per point; last accepted estimate in inclusive 0–30 s. No update counts as incorrect.',
                    realtime_policy=baseline['realtime_policy'],calibration_train_ids=sorted(CALIBRATION),
                    planned_d3_test_ids=test_ids,planned_basic_test_ids=basic_ids,
                    target_correct=dict(basic_2_to_4=49,all_2_to_6=67),
                    file_sha256={str(path.relative_to(ROOT)):sha256(path) for path in files})
    path = OFFLINE/'baseline_freeze.json'
    if path.exists():
        if json.loads(path.read_text())!=manifest:
            raise ValueError('Current reference files or scoring settings differ from the saved V1 freeze')
    elif verify_only:
        raise ValueError(f'Missing frozen baseline manifest: {path}')
    else:
        write_json(path,manifest)


def compare_historical_validation(plan, baseline):
    import joblib
    import pandas as pd
    from validate import load_validation, save_results

    pilot = STAGE/'pilot/fit'
    pilot_freeze = json.loads((pilot/'freeze.json').read_text())
    candidate_path = pilot/'wifi_model.json.gz'
    if (pilot_freeze['v1_sha256']!=sha256(V1/'wifi_model.json.gz')
        or pilot_freeze['vcal_sha256']!=sha256(candidate_path)
        or pilot_freeze['capture_date']!='2026-11-11'):
        raise ValueError('The Vcal10 pilot does not match its freeze record')
    with gzip.open(candidate_path,'rt',encoding='utf-8') as stream:
        calibrated = json.load(stream)
    if baseline['realtime_policy']!=calibrated['realtime_policy']:
        raise ValueError('Replay requires the same V1 and Vcal10 update thresholds')
    train_points = {point for point,labels in plan.items() if labels['split']=='Train'}
    val_archive = ROOT/'d2_validation/data/static_validation.zip'
    val,manifest,_ = load_validation(val_archive,plan,train_points)
    if set(manifest.start_local.str[:10])!={'2026-11-10'}:
        raise ValueError('The historical replay requires the existing November 10 Validation archive')
    results = [evaluate_validation(val,'V1',joblib.load(V1/'selected.joblib'),baseline),
               evaluate_validation(val,'Vcal10',joblib.load(pilot/'candidate.joblib'),calibrated)]
    output = OFFLINE/'historical_validation'
    output.mkdir(exist_ok=True)
    save_results(output,{unit:pd.concat([result[unit] for result in results],ignore_index=True)
                        for unit in ('scan','point','session')},plan)
    live = pd.concat([result['realtime'] for result in results],ignore_index=True)
    live.to_csv(output/'realtime_scan_predictions.csv',index=False)
    summary = []
    for name,result in zip(('V1','Vcal10'),results):
        for unit in ('point','scan','realtime'):
            frame=result[unit]
            if unit=='realtime':
                floor_ok,zone_ok=frame.floor_correct,frame.zone_correct
                west=frame.actual_zone.eq('West')
                covered=int(frame.updated.sum())
            else:
                floor_ok,zone_ok=frame.floor_ok,frame.zone_ok
                west=frame.zone.eq('West')
                covered=None
            summary.append(dict(model=name,unit=unit,n=len(frame),floor_correct=int(floor_ok.sum()),
                                zone_correct=int(zone_ok.sum()),joint_correct=int((floor_ok&zone_ok).sum()),
                                west_n=int(west.sum()),west_correct=int((west&zone_ok).sum()),updates=covered))
    pd.DataFrame(summary).to_csv(output/'summary.csv',index=False)
    write_json(output/'inputs.json',dict(purpose='Historical validation regression; later calibration and earlier evaluation',
                                       validation_date='2026-11-10',calibration_date=pilot_freeze['capture_date'],
                                       validation_sha256=sha256(val_archive),v1_sha256=pilot_freeze['v1_sha256'],
                                       vcal_sha256=pilot_freeze['vcal_sha256'],vcal_anchors=pilot_freeze['anchor_ids']))
    print(pd.DataFrame(summary).to_string(index=False))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-freeze',action='store_true',help='Check the saved V1 freeze without replaying Validation')
    parser.add_argument('--freeze-only',action='store_true',help='Save or check the V1 freeze without replaying Validation')
    args = parser.parse_args()
    plan=json.loads(PLAN.read_text())
    baseline=read_model()
    freeze_baseline(plan,baseline,verify_only=args.verify_freeze)
    if not (args.verify_freeze or args.freeze_only):
        compare_historical_validation(plan,baseline)
    else:
        print('V1 files, labels and D3 scoring settings match the baseline freeze.')
