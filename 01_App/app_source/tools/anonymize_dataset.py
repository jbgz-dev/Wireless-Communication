"""Make an anonymized copy of FloorSense session data for submission.

Usage:
    python -I tools/anonymize_dataset.py --out <new empty folder> [--key-file key.txt] <session folder or zip> ...

Each input can be a folder of session folders, one session folder, or a zip of either.
The originals are never changed. Output: one folder per session plus `anonymization.json`.

What changes, file by file:
  wifi.csv    bssid -> "ap_" + 12 hex (keyed hash, lowercase first, so the same AP gets the same id in
              every session and the copy can still be used to train and evaluate a model; results can
              differ slightly because the feature order changes); ssid emptied.
  ble.csv     address -> "ble_" + 12 hex (keyed hash); iBeacon uuid/major/minor emptied.
  cell.csv    area (LAC/TAC) and cid (cell id) -> keyed hash; mcc, mnc, pci, arfcn and signal kept.
  meta.json   collector codes like P1/p2 are already pseudonyms and are kept (upper-cased); anything
              else (e.g. a real name) -> X1, X2, ...; device "device" codename removed
              (manufacturer/model kept: the report compares phone models); label_history notes removed.
  *.csv / meta.json  t_wall_ms and start/end_wall_ms shifted so the first session starts at
              2026-01-01 00:00 local time (relative timing kept); start_local rewritten to match.
  sensors.csv, events.csv, node_*.csv, route_snapshot.json, sensors.json: copied unchanged
              (no identifiers; t_elapsed_ms / t_ns are time since phone boot).

The key is random unless --key-file is given. Keep the key private: anyone with it can test whether a
known BSSID is in the data. Without the key the ids cannot be reversed.
"""
import argparse
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

EPOCH = datetime(2026, 1, 1)
COPY_AS_IS = {'sensors.csv', 'sensors.json', 'events.csv', 'node_visits.csv', 'node_marks.csv', 'route_snapshot.json'}


class Anonymizer:
    def __init__(self, key: bytes):
        self.key = key
        self.collectors = {}
        self.shift_ms = None
        self.counts = {'bssid': set(), 'ble': set(), 'cell': set()}

    def hid(self, prefix, value, kind):
        v = value.strip().lower()
        if not v:
            return ''
        self.counts[kind].add(v)
        return prefix + hmac.new(self.key, f'{kind}:{v}'.encode(), hashlib.sha256).hexdigest()[:12]

    def collector(self, name):
        key = name.strip().upper()
        if key not in self.collectors:
            self.collectors[key] = key if re.fullmatch(r'P\d+', key) else f'X{len(self.collectors) + 1}'
        return self.collectors[key]

    def wall(self, v):
        if v in ('', None):
            return v
        return str(int(v) + self.shift_ms)

    # ---------- files ----------
    def csv_file(self, text, fix):
        src = csv.DictReader(io.StringIO(text))
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=src.fieldnames, lineterminator='\n')
        w.writeheader()
        for row in src:
            if 't_wall_ms' in row:
                row['t_wall_ms'] = self.wall(row['t_wall_ms'])
            fix(row)
            w.writerow(row)
        return out.getvalue()

    def wifi(self, row):
        row['bssid'] = self.hid('ap_', row.get('bssid', ''), 'bssid')
        if 'ssid' in row:
            row['ssid'] = ''

    def ble(self, row):
        row['address'] = self.hid('ble_', row.get('address', ''), 'ble')
        for k in ('ibeacon_uuid', 'ibeacon_major', 'ibeacon_minor'):
            if k in row:
                row[k] = ''

    def cell(self, row):
        for k in ('area', 'cid'):
            if row.get(k):
                row[k] = self.hid(f'{k}_', row[k], 'cell')

    def meta(self, m):
        m = json.loads(json.dumps(m))
        for k in ('start_wall_ms', 'end_wall_ms'):
            if m.get(k) is not None:
                m[k] = int(m[k]) + self.shift_ms
        if m.get('start_wall_ms') is not None:
            local = EPOCH + timedelta(milliseconds=m['start_wall_ms'] - self.epoch_ms)
            m['start_local'] = local.strftime('%Y-%m-%dT%H:%M:%S')
        labels = m.get('labels', {})
        if 'collector' in labels:
            labels['collector'] = self.collector(labels['collector'])
        m.get('device', {}).pop('device', None)
        m.pop('label_history', None)
        old = m.get('session_id', '')
        m['session_id'] = self.session_name(old, m)
        return m

    def session_name(self, old, m):
        # 20261110-114444_T8_F5_transition_to6-stairs_P2 -> <new date>-<time>_<middle>_<new collector>
        parts = old.split('_')
        stamp = (EPOCH + timedelta(milliseconds=m['start_wall_ms'] - self.epoch_ms)).strftime('%Y%m%d-%H%M%S') \
            if m.get('start_wall_ms') is not None else parts[0]
        middle = '_'.join(parts[1:-1]) if len(parts) > 2 else ''
        who = m.get('labels', {}).get('collector', '')
        return '_'.join(p for p in (stamp, middle, who) if p)


def iter_sessions(path):
    """Yield (name, {filename: bytes}) for every session folder (one containing meta.json)."""
    if path.is_file() and path.suffix == '.zip':
        with zipfile.ZipFile(path) as z:
            groups = {}
            for n in z.namelist():
                p = PurePosixPath(n)
                if '__MACOSX' in p.parts or n.endswith('/') or p.name.startswith('._'):
                    continue
                groups.setdefault(str(p.parent), {})[p.name] = n
            for parent, files in sorted(groups.items()):
                if 'meta.json' in files:
                    yield PurePosixPath(parent).name, {k: z.read(v) for k, v in files.items()}
        return
    metas = [path / 'meta.json'] if (path / 'meta.json').exists() else sorted(path.glob('**/meta.json'))
    for m in metas:
        d = m.parent
        yield d.name, {f.name: f.read_bytes() for f in d.iterdir() if f.is_file() and not f.name.startswith('.')}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--key-file', type=Path, help='hex key; reuse it to keep ids stable across runs')
    a = ap.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        sys.exit(f'{a.out} 不是空文件夹')
    for p in a.inputs:
        if p.is_dir() and a.out.resolve().is_relative_to(p.resolve()):
            sys.exit('输出文件夹不能在输入文件夹里面')
    key = bytes.fromhex(a.key_file.read_text().strip()) if a.key_file else secrets.token_bytes(32)

    sessions = [s for p in a.inputs for s in iter_sessions(p)]
    if not sessions:
        sys.exit('没有找到 session（需要包含 meta.json 的文件夹）')
    starts = [json.loads(files['meta.json'])['start_wall_ms'] for _, files in sessions]
    an = Anonymizer(key)
    # local time of the first session -> 2026-01-01 00:00; t_wall_ms is UTC ms, Asia/Shanghai is +8 h
    first_local = datetime(1970, 1, 1) + timedelta(milliseconds=min(starts), hours=8)
    an.shift_ms = int((EPOCH - first_local).total_seconds() * 1000)
    an.epoch_ms = min(starts) + an.shift_ms

    a.out.mkdir(parents=True, exist_ok=True)
    written, skipped = [], set()
    for name, files in sessions:
        meta = an.meta(json.loads(files['meta.json']))
        d = a.out / meta['session_id']
        if d.exists():
            sys.exit(f'重复的 session：{name}')
        d.mkdir()
        (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        for fname, raw in files.items():
            if fname == 'meta.json':
                continue
            text = raw.decode('utf-8-sig') if fname.endswith(('.csv', '.json')) else None
            if fname == 'wifi.csv':
                (d / fname).write_text(an.csv_file(text, an.wifi), encoding='utf-8')
            elif fname == 'ble.csv':
                (d / fname).write_text(an.csv_file(text, an.ble), encoding='utf-8')
            elif fname == 'cell.csv':
                (d / fname).write_text(an.csv_file(text, an.cell), encoding='utf-8')
            elif fname in COPY_AS_IS:
                (d / fname).write_bytes(raw)
            else:
                skipped.add(fname)   # unknown file: leave it out rather than risk leaking something
        written.append(dict(original=name, anonymized=meta['session_id']))
    report = dict(sessions=len(written), unique_bssids=len(an.counts['bssid']), unique_ble=len(an.counts['ble']),
                  unique_cell_ids=len(an.counts['cell']), collectors=len(an.collectors),
                  time_shift='first session moved to 2026-01-01 00:00 local; relative timing kept',
                  skipped_unknown_files=sorted(skipped))
    (a.out / 'anonymization.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not a.key_file:
        print('本次使用随机密钥，没有保存；同一批 ID 只在这一次输出里一致。需要多次输出保持一致时用 --key-file。')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
