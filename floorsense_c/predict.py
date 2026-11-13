"""Python integration reference: BSSID->RSSI JSON to floor/zone prediction."""
import argparse
import json
import math
from pathlib import Path
import joblib
from pipeline import encode_transform


def predict(scan, bundle):
    normalized = {}
    for bssid, rssi in scan.items():
        value = float(rssi)
        if not math.isfinite(value) or not -127 <= value < 0:
            raise ValueError('RSSI must be a finite negative dBm value in [-127, 0)')
        normalized[bssid.lower()] = value
    vocab = bundle['config']['bssids']
    known = set(normalized).intersection(vocab)
    if not known:
        return {'status':'insufficient_known_aps','floor':None,'zone':None,'known_aps':0}
    x = encode_transform([normalized],vocab)
    result = {'status':'ok','known_aps':len(known),'observed_aps':len(normalized)}
    for target, model in bundle['models'].items():
        value = model.predict(x)[0]
        result[target] = int(value) if target == 'floor' else str(value)
        result[f'{target}_vote_score'] = float(model.predict_proba(x)[0].max())
    result['score_note'] = 'Uncalibrated vote score; not guaranteed correctness probability.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('scan',type=Path)
    parser.add_argument('--model',type=Path,default=Path(__file__).parent/'results/models/RandomForest.joblib')
    args = parser.parse_args()
    print(json.dumps(predict(json.loads(args.scan.read_text()),joblib.load(args.model)),ensure_ascii=False,indent=2))
