"""Portable Wi-Fi inference reference. Requires only Python's standard library."""
import argparse
import gzip
import json
import math
from pathlib import Path
from statistics import median

REALTIME_POLICY = {'min_known_aps': 15, 'min_floor_vote_score': 0.50}


def prepare_scan(rows, config):
    """Convert raw AP rows from one updated scan to clipped, median BSSID RSSI."""
    if not rows or any(str(r['fresh']) != '1' or str(r['results_updated']) != '1' for r in rows):
        return {}
    by_bssid = {}
    lo, hi = config['clip_rssi']
    for row in rows:
        bssid = row['bssid'].lower()
        if not bssid.strip():
            continue  # The collector uses a blank AP row for an empty callback.
        age, rssi = float(row['age_ms']), float(row['rssi'])
        if 0 <= age <= config['max_age_ms'] and -127 <= rssi < 0:
            by_bssid.setdefault(bssid,[]).append(min(hi,max(lo,rssi)))
    return {b:median(values) for b,values in by_bssid.items()}


def target_prob(x, model):
    if model['kind'] == 'multinomial_logistic':
        logits = [math.fsum(c*v for c,v in zip(coef,x))+bias for coef,bias in zip(model['coef'],model['intercept'])]
        peak = max(logits)
        exp = [math.exp(v-peak) for v in logits]
        total = sum(exp)
        return [v/total for v in exp]
    if model['kind'] == 'random_forest':
        votes = [0.0]*len(model['classes'])
        for tree in model['trees']:
            node = 0
            while tree['left'][node] != -1:
                if x[tree['feature'][node]] <= tree['threshold'][node]:
                    node = tree['left'][node]
                else:
                    node = tree['right'][node]
            values = tree['leaves'][str(node)]
            total = sum(values)
            for i,value in enumerate(values):
                votes[i] += value/total
        return [v/len(model['trees']) for v in votes]
    if model['kind'] == 'distance_knn':
        best = sorted((math.fsum((a-b)**2 for a,b in zip(x,row)),i) for i,row in enumerate(model['x']))[:model['k']]
        votes = [0.0]*len(model['classes'])
        zero = best[0][0] == 0
        for distance,index in best:
            weight = float(distance == 0) if zero else 1/math.sqrt(distance)
            votes[model['encoded_labels'][index]] += weight
        total = sum(votes)
        return [v/total for v in votes]
    raise ValueError(f"Unsupported model kind {model['kind']}")


def encode_scan(scan, config):
    lo,hi = config['clip_rssi']
    cleaned = {}
    for bssid, raw in scan.items():
        value = float(raw)
        if not -127 <= value < 0:
            raise ValueError('RSSI must be finite and in [-127,0)')
        cleaned[bssid.lower()] = min(hi,max(lo,value))
    vocab = config['bssids']
    known = sum(b in cleaned for b in vocab)
    return [cleaned.get(b,config['missing_rssi']) for b in vocab], known


def predict_encoded(x, known, models):
    output = dict(status='ok',known_aps=known)
    for target,model in models.items():
        prob = target_prob(x,model)
        best = max(range(len(prob)),key=prob.__getitem__)
        output[target] = model['classes'][best]
        output[f'{target}_vote_score'] = prob[best]
    return output


def predict(scan, payload):
    """Score one fingerprint without the update policy, for offline comparisons."""
    x, known = encode_scan(scan, payload['config'])
    if known == 0:
        return dict(status='insufficient_known_aps',floor=None,zone=None,known_aps=0)
    return predict_encoded(x, known, payload['models'])


def realtime_step(scan, payload, previous_floor=None):
    """Update a single stream; the caller supplies its last accepted floor."""
    x, known = encode_scan(scan, payload['config'])
    policy = payload['realtime_policy']
    pending = dict(status='waiting' if previous_floor is None else 'held',
                   floor=previous_floor, zone=None, known_aps=known, is_new_estimate=False)
    if known < policy['min_known_aps']:
        return dict(pending, reason='low_known_aps')
    result = predict_encoded(x, known, payload['models'])
    if result['floor_vote_score'] < policy['min_floor_vote_score']:
        return dict(pending, reason='low_floor_vote', floor_vote_score=result['floor_vote_score'])
    return dict(result, is_new_estimate=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('model',type=Path)
    parser.add_argument('scan',type=Path)
    parser.add_argument('--previous-floor',type=int,choices=range(2,7))
    parser.add_argument('--raw',action='store_true',help='Score without the update policy for offline diagnosis')
    args = parser.parse_args()
    with gzip.open(args.model,'rt',encoding='utf-8') as stream:
        payload = json.load(stream)
    data = json.loads(args.scan.read_text())
    scan = prepare_scan(data,payload['config']) if isinstance(data,list) else data
    result = predict(scan,payload) if args.raw or 'realtime_policy' not in payload else realtime_step(scan,payload,args.previous_floor)
    print(json.dumps(result,ensure_ascii=False,indent=2))
