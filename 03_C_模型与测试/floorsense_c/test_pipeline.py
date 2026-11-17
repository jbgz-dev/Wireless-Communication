"""Checks that protect accuracy estimates against leakage and feature mismatch."""
import unittest
from pathlib import Path


class FeatureContract(unittest.TestCase):
    def test_features_do_not_learn_validation_aps(self):
        self.assertTrue(Path(__file__).with_name('pipeline.py').exists(), 'pipeline is not implemented')
        from pipeline import encode_fit, encode_transform
        vocab, x = encode_fit([{'aa': -50}, {'bb': -80}])
        self.assertEqual(vocab, ['aa', 'bb'])
        self.assertEqual(x.tolist(), [[-50., -100.], [-100., -80.]])
        self.assertEqual(encode_transform([{'aa': -60, 'unseen': -30}], vocab).tolist(), [[-60., -100.]])

    def test_duplicate_bridge_points_stay_together(self):
        self.assertTrue(Path(__file__).with_name('pipeline.py').exists(), 'pipeline is not implemented')
        from pipeline import connected_groups
        rows = [{'point_id':'a','fingerprint':'x'}, {'point_id':'a','fingerprint':'y'},
                {'point_id':'b','fingerprint':'y'}, {'point_id':'c','fingerprint':'z'}]
        groups = connected_groups(rows)
        self.assertEqual(groups['a'], groups['b'])
        self.assertNotEqual(groups['a'], groups['c'])

    def test_fold_macro_f1_keeps_all_target_classes(self):
        import pandas as pd
        from pipeline import metric_rows
        # A fold with only Middle/East must still use a three-class macro denominator.
        samples = pd.DataFrame({'model':['x','x'], 'point_id':['a','b'],
                                'floor':[2,3], 'floor_pred':[2,3],
                                'zone':['Middle','East'], 'zone_pred':['Middle','East']})
        result = metric_rows(samples,['model']).iloc[0]
        self.assertAlmostEqual(result.zone_macro_f1, 2/3)
        self.assertAlmostEqual(result.floor_macro_f1, 2/5)

    def test_unknown_aps_do_not_force_a_prediction(self):
        from predict import predict
        result = predict({'unknown': -40}, {'config': {'bssids': ['known']}, 'models': {}})
        self.assertEqual(result['status'], 'insufficient_known_aps')
        self.assertIsNone(result['floor'])


if __name__ == '__main__':
    unittest.main()
