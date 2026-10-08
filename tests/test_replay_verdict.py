import copy
import json
import unittest
from usb_device_lab.replay_verdict import summarize_replays


def observation(*signatures, valid=True):
    return {'valid': valid, 'signatures': list(signatures)}


class ReplayVerdictTests(unittest.TestCase):
    def test_confirmation_states(self):
        cases = [
            ([observation('A'), observation('A'), observation()], 'reproduced'),
            ([observation('A'), observation(), observation()], 'flaky'),
            ([observation(), observation(), observation()], 'not_reproduced'),
            ([observation('A')], 'inconclusive'),
            ([observation('A'), observation('A', valid=False), observation()], 'inconclusive'),
            ([observation('A'), observation('A'), observation(valid=False)], 'reproduced'),
        ]
        for observations, state in cases:
            with self.subTest(state=state, observations=observations):
                result = summarize_replays({'A'}, observations, 3)
                self.assertEqual(result['confirmation'], state)
                self.assertEqual(result['attempted'], len(observations))
                self.assertEqual(result['requested'], 3)

    def test_duplicate_headers_count_once_per_attempt(self):
        result = summarize_replays({'A'}, [observation('A', 'A'), observation()], 2)
        self.assertEqual(result['matching_replays']['A'], 1)
        self.assertEqual(result['confirmation'], 'flaky')

    def test_unrelated_signatures_do_not_confirm_original(self):
        result = summarize_replays({'A'}, [observation('B'), observation('B')], 2)
        self.assertEqual(result['confirmation'], 'not_reproduced')
        self.assertEqual(result['reproduced_signatures'], [])

    def test_two_different_single_matches_do_not_confirm(self):
        result = summarize_replays({'A', 'B'}, [observation('A'), observation('B')], 2)
        self.assertEqual(result['confirmation'], 'flaky')
        self.assertEqual(result['matching_replays'], {'A': 1, 'B': 1})

    def test_invalid_observations_cannot_confirm(self):
        result = summarize_replays({'A'}, [observation('A', valid=False), observation('A', valid=False)], 2)
        self.assertEqual(result['confirmation'], 'inconclusive')
        self.assertEqual(result['usable'], 0)
        self.assertEqual(result['matching_replays']['A'], 0)

    def test_invalid_arguments_are_rejected(self):
        for expected, observations, requested in [
            (set(), [], 3), ({'A'}, [], 1), ({'A'}, [], True),
            ({'A'}, [], 2.0), ({'A'}, [observation()] * 3, 2),
        ]:
            with self.subTest(requested=requested):
                with self.assertRaises(ValueError):
                    summarize_replays(expected, observations, requested)

    def test_inputs_are_unchanged_and_report_is_json_serializable(self):
        observations = [observation('A'), observation('A')]
        before = copy.deepcopy(observations)
        result = summarize_replays({'A'}, observations, 2)
        self.assertEqual(observations, before)
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == '__main__':
    unittest.main()
