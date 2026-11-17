import unittest

import d3_eval as d3


class D3Contract(unittest.TestCase):
    def test_validation_and_train_roles_cannot_be_used_as_d3_test(self):
        label = dict(point_id='T8-F3-A-TR01', split='train', mode='static', building='T8', floor=3,
                     zone='A', orientation='N')
        meta = dict(labels=label, status='complete', start_local='2026-11-18T14:00:00')
        plan = {label['point_id']: dict(split='Train', floor=3, anchor_zone='A', zone='West')}
        with self.assertRaisesRegex(ValueError,'role'):
            d3.session_labels(meta,plan,'test','2026-11-18')

    def test_window_keeps_exact_endpoint_and_excludes_late_updates(self):
        scan = lambda t: dict(wall_ms=t)
        point = dict(start_wall_ms=10000,scans=[scan(9999),scan(10000),scan(40000),scan(40001)])
        self.assertEqual([row['wall_ms'] for row in d3.in_window(point)], [10000,40000])

    def test_coverage_must_use_full_point_denominator(self):
        points = [{'floor': 2, 'zone': 'West', 'floor_pred':2, 'zone_pred':'West'},
                  {'floor': 4, 'zone': 'East', 'floor_pred':None, 'zone_pred':None}]
        result=d3.score_counts(points)
        self.assertEqual((result['n'],result['covered'],result['floor_correct']), (2,1,1))

    def test_formal_capture_rejects_early_collector_completion(self):
        duration = getattr(d3,'require_full_window',None)
        self.assertTrue(callable(duration))
        with self.assertRaisesRegex(ValueError,'30 s'):
            duration(dict(start_wall_ms=1000,end_wall_ms=8238),'T8-F3-B-TS01')
        self.assertEqual(duration(dict(start_wall_ms=1000,end_wall_ms=31000),'T8-F3-B-TS01'),30000)
