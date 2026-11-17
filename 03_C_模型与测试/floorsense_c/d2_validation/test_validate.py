import unittest


class ValidationContract(unittest.TestCase):
    def test_va_role_overrides_meta_and_rejects_training_identity(self):
        from validate import effective_label
        plan = {'T8-F2-B-VA01': dict(split='Validation', floor=2, anchor_zone='B', zone='West'),
                'T8-F2-B-TR01': dict(split='Train', floor=2, anchor_zone='B', zone='West')}
        raw = dict(building='T8', floor=2, zone='B', point_id='T8-F2-B-VA01', split='train', mode='static', orientation='N')
        self.assertEqual(effective_label(raw, plan)['split'], 'validation')
        self.assertEqual(effective_label(raw, plan)['zone'], 'West')
        with self.assertRaises(ValueError):
            effective_label(dict(raw, point_id='T8-F2-B-TR01'), plan)

    def test_scan_clip_then_median_and_skip_stale(self):
        from validate import scan_fingerprints
        rows = 'scan_id,fresh,results_updated,bssid,rssi,age_ms\n0,1,1,AA,-105,0\n0,1,1,aa,-65,1\n0,1,1,bb,-30,10001\n1,1,0,cc,-41,3\n'
        scans, quality = scan_fingerprints(rows)
        self.assertEqual(scans, [(0, {'aa': -82.5})])
        self.assertEqual(quality['excluded_not_fresh_scans'], 1)
        self.assertEqual(quality['stale_ap_rows'], 1)
