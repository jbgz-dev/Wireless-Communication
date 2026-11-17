import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from d3_eval import read_model, score_pair, V1
from d3_v1_score import replay_v1


class V1Scoring(unittest.TestCase):
    def test_matches_frozen_paired_scoring_and_keeps_timeouts(self):
        baseline = read_model()
        features = json.loads((V1/'example_scan.json').read_text())
        goals = {'basic_2_to_4': 49, 'all_2_to_6': 67}
        points = {
            'basic': dict(point_id='basic', session_id='s1', floor=2, zone='West', start_wall_ms=1000,
                          duration_ms=32000, scans=[dict(scan_id=1, wall_ms=31000, features=features)]),
            'timeout': dict(point_id='timeout', session_id='s2', floor=6, zone='East', start_wall_ms=1000,
                            duration_ms=32000, scans=[dict(scan_id=1, wall_ms=31001, features=features)]),
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paired = score_pair(points, baseline, baseline, root/'pair', official=True)
            standalone = replay_v1(points, baseline, root/'v1', goals)
            self.assertEqual(standalone, paired['V1'])
            self.assertEqual(standalone['all']['timeout_n'], 1)
            self.assertEqual(standalone['all']['n'], 2)
            self.assertEqual(standalone['all']['floor_correct'], 1)
            for target in ('floor', 'zone'):
                filename = f'cm_V1_{target}.csv'
                self.assertEqual((root/'v1'/filename).read_bytes(), (root/'pair'/filename).read_bytes())
            with (root/'v1/point_predictions.csv').open(newline='') as stream:
                self.assertEqual({row['model'] for row in csv.DictReader(stream)}, {'V1'})

    def test_empty_callback_stream_still_writes_event_header(self):
        point = dict(point_id='p1', session_id='s1', floor=4, zone='Middle', start_wall_ms=1000, scans=[])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = replay_v1({'p1': point}, None, root, {'basic_2_to_4': 49, 'all_2_to_6': 67})
            self.assertEqual(result['all']['timeout_n'], 1)
            with (root/'scan_events.csv').open(newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(list(reader), [])
                self.assertIn('scan_id', reader.fieldnames)
