"""Regression coverage for real-time floor updates on weak scans."""

import unittest

import mobile_reference as mobile


def payload(floor_votes):
    def leaf(classes, votes):
        return dict(kind='random_forest', classes=classes, trees=[
            dict(left=[-1], right=[-1], feature=[-2], threshold=[-2], leaves={'0': votes})
        ])
    return {
        'config': dict(bssids=[f'ap{i}' for i in range(16)], missing_rssi=-100,
                       clip_rssi=[-100, -20], max_age_ms=10000),
        'models': {'floor': leaf([2, 3, 4, 5, 6], floor_votes),
                   'zone': leaf(['East', 'Middle', 'West'], [.1, .2, .7])},
        'realtime_policy': dict(min_known_aps=15, min_floor_vote_score=.5),
    }


class RealtimeTest(unittest.TestCase):
    def step(self, *args, **kwargs):
        step = getattr(mobile, 'realtime_step', None)
        self.assertTrue(callable(step), 'real-time quality gate is not implemented')
        return step(*args, **kwargs)

    def test_low_ap_scan_holds_previous_floor_or_waits(self):
        model = payload([.9, .1, 0, 0, 0])
        scan = {'ap0': -60, **{f'unknown{i}': -60 for i in range(20)}}
        held = self.step(scan, model, previous_floor=5)
        self.assertEqual((held['status'], held['floor'], held['zone'], held['known_aps']), ('held', 5, None, 1))
        self.assertFalse(held['is_new_estimate'])
        waiting = self.step(scan, model)
        self.assertEqual((waiting['status'], waiting['floor']), ('waiting', None))

    def test_16_known_aps_with_weak_vote_do_not_switch_to_2f(self):
        scan = {f'ap{i}': -60 for i in range(16)}
        result = self.step(scan, payload([.436, .2, .2, .1, .064]), previous_floor=5)
        self.assertEqual((result['status'], result['floor'], result['reason']), ('held', 5, 'low_floor_vote'))
        self.assertFalse(result['is_new_estimate'])

    def test_update_resumes_at_both_threshold_boundaries(self):
        scan = {f'AP{i}': -60 for i in range(15)}
        result = self.step(scan, payload([.1, .1, .2, .1, .5]), previous_floor=5)
        self.assertEqual((result['status'], result['floor'], result['known_aps']), ('ok', 6, 15))
        self.assertTrue(result['is_new_estimate'])

    def test_empty_ap_callback_becomes_empty_scan(self):
        placeholder = dict(fresh=1, results_updated=1, age_ms='', rssi='', bssid='', n_ap=0)
        self.assertEqual(mobile.prepare_scan([placeholder], payload([.9, .1, 0, 0, 0])['config']), {})
