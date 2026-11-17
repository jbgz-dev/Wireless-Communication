"""Select the D2 integration candidate and verify portable inference against sklearn."""
import argparse
import gzip
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier

from validate import ROOT, STAGE, encode_transform, load_validation
from mobile_reference import REALTIME_POLICY, predict


def portable(model):
    classes = model.classes_.tolist()
    if isinstance(model, LogisticRegression) and len(classes) > 2:
        return dict(kind='multinomial_logistic',classes=classes,coef=model.coef_.tolist(),intercept=model.intercept_.tolist())
    if isinstance(model, RandomForestClassifier):
        trees = []
        for estimator in model.estimators_:
            t = estimator.tree_
            leaves = {str(i): t.value[i,0].tolist() for i in np.flatnonzero(t.children_left==-1)}
            trees.append(dict(left=t.children_left.tolist(),right=t.children_right.tolist(),feature=t.feature.tolist(),
                              threshold=t.threshold.tolist(),leaves=leaves))
        return dict(kind='random_forest',classes=classes,trees=trees)
    if isinstance(model,KNeighborsClassifier) and model.weights=='distance' and model.metric=='euclidean':
        return dict(kind='distance_knn',classes=classes,x=model._fit_X.tolist(),encoded_labels=model._y.tolist(),k=model.n_neighbors)
    raise TypeError(f'Unsupported portable model: {type(model)}')


def select(metrics,mode):
    point = metrics[(metrics.unit=='point')&(metrics.scope=='2–6F')].set_index('model')
    scan = metrics[(metrics.unit=='scan')&(metrics.scope=='2–6F')].set_index('model')
    if mode == 'point':
        return sorted(point.index,key=lambda n:(point.loc[n,'floor_accuracy'],point.loc[n,'joint_accuracy'],
                      scan.loc[n,'floor_accuracy'],scan.loc[n,'zone_macro_f1'],n=='D1_RandomForest'),reverse=True)
    return sorted(scan.index,key=lambda n:(scan.loc[n,'floor_accuracy'],scan.loc[n,'joint_accuracy'],
                  scan.loc[n,'zone_macro_f1'],n=='D1_RandomForest'),reverse=True)


def export(archive, comparison, output, mode):
    ranked = select(pd.read_csv(comparison/'metrics.csv'),mode)
    selected = ranked[0]
    path = ROOT/f'results/models/{selected.removeprefix("D1_")}.joblib' if selected.startswith('D1_') else comparison/f'models/{selected}.joblib'
    bundle = joblib.load(path)
    payload = dict(schema_version=1,name=selected,config=bundle['config'],
                   models={target:portable(model) for target,model in bundle['models'].items()})
    if mode == 'scan' and selected == 'stable_ap_balanced_rf':
        payload['realtime_policy'] = REALTIME_POLICY
    output.mkdir(parents=True,exist_ok=True)
    with gzip.open(output/'wifi_model.json.gz','wt',encoding='utf-8') as stream:
        json.dump(payload,stream,separators=(',',':'))
    (output/'feature_config.json').write_text(json.dumps(bundle['config'],indent=2))
    shutil.copyfile(path,output/'selected.joblib')
    shutil.copyfile(STAGE/'mobile_reference.py',output/'mobile_reference.py')
    plan = json.loads((ROOT/'point_labels.json').read_text())
    val,_,_ = load_validation(archive,plan,{k for k,v in plan.items() if v['split']=='Train'})
    val = val.drop_duplicates(['point_id','fingerprint']).reset_index(drop=True)
    vocab = bundle['config']['bssids']
    x = encode_transform(val.features.tolist(),vocab)
    expected = {t:m.predict_proba(x) for t,m in bundle['models'].items()}
    # Portable logistic uses double-precision dot products. Separately check the
    # exact class decisions against the float32 validation scoring path.
    score_reference = {t:m.predict_proba(x.astype(np.float64)) if isinstance(m,LogisticRegression)
                       else expected[t] for t,m in bundle['models'].items()}
    with gzip.open(output/'wifi_model.json.gz','rt',encoding='utf-8') as stream:
        reloaded = json.load(stream)
    joblib_reloaded = joblib.load(output/'selected.joblib')
    for target in expected:
        np.testing.assert_allclose(expected[target],joblib_reloaded['models'][target].predict_proba(x))
    sample_vectors = []
    for i,row in val.iterrows():
        result = predict(row.features,reloaded)
        if result['status']=='ok':
            for target,model in bundle['models'].items():
                prob = expected[target][i]
                assert result[target] == model.classes_[np.argmax(prob)], (i,target,result)
                np.testing.assert_allclose(result[f'{target}_vote_score'],score_reference[target][i].max(),rtol=1e-9,atol=1e-10)
        else:
            assert not set(row.features).intersection(vocab)
        if row.floor not in {item['reference_floor'] for item in sample_vectors}:
            sample_vectors.append(dict(reference_floor=int(row.floor),bssid_rssi=row.features,expected_model_output=result))
    assert predict({},reloaded)['status']=='insufficient_known_aps'
    (output/'example_scan.json').write_text(json.dumps(sample_vectors[0]['bssid_rssi'],indent=2))
    (output/'parity_vectors.json').write_text(json.dumps(sample_vectors,ensure_ascii=False,indent=2))
    decision = dict(selected=selected,mode=mode,ranked=ranked,fit_source='D1 Train only',selection_source='D2 Validation',
                    portable_parity_scans=len(val),model_kind={t:m['kind'] for t,m in reloaded['models'].items()},
                    top_score_precision='double-precision reference for logistic regression')
    (output/'selection.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2))
    print(decision)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive',type=Path)
    parser.add_argument('--comparison',type=Path,default=STAGE/'comparison')
    parser.add_argument('--out',type=Path,default=STAGE/'D_handoff')
    parser.add_argument('--mode',choices=('scan','point'),default='scan')
    args = parser.parse_args()
    export(args.archive,args.comparison,args.out,args.mode)
