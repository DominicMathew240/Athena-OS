"""Replay metric math on synthetic events, not recognition or accuracy claims."""
from django.test import SimpleTestCase

from .operations.comparison import compare_crossings
from .operations.crossings import Crossing, CrossingCounter


class CrossingComparisonTests(SimpleTestCase):
    def test_counter_events_feed_the_same_comparison_contract(self):
        counter = CrossingCounter((.2, .5), (.8, .5), confirmations=2, max_gap=1)
        observations = [(0, (.5, .2)), (.1, (.5, .2)), (.2, (.5, .8)), (.3, (.5, .8))]
        events = [event for t, point in observations if (event := counter.observe(t, point)) is not None]
        result = compare_crossings([Crossing('negative_to_positive', .15)], events, tolerance=.01)
        self.assertEqual((len(result.matches), len(result.missed), len(result.extra)), (1, 0, 0))
        self.assertAlmostEqual(result.matches[0].offset, 0)

    def test_pairs_once_per_direction_and_reports_misses_extras_and_timing(self):
        expected = [Crossing('negative_to_positive', 1), Crossing('negative_to_positive', 3), Crossing('positive_to_negative', 2)]
        actual = [Crossing('negative_to_positive', 3.1), Crossing('negative_to_positive', 1.2), Crossing('negative_to_positive', 1.21), Crossing('positive_to_negative', 4)]
        result = compare_crossings(expected, actual, tolerance=.25)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(len(result.missed), 1)
        self.assertEqual(len(result.extra), 2)
        self.assertEqual(result.missed[0], expected[2])
        self.assertAlmostEqual(result.matches[0].offset, .2)

    def test_matching_maximizes_count_when_closest_first_would_lose_match(self):
        expected = [Crossing('negative_to_positive', 1), Crossing('negative_to_positive', 1.2)]
        actual = [Crossing('negative_to_positive', .85), Crossing('negative_to_positive', 1.05)]
        result = compare_crossings(expected, actual, tolerance=.2)
        self.assertEqual((len(result.matches), len(result.missed), len(result.extra)), (2, 0, 0))

    def test_wrong_direction_and_empty_input_do_not_make_a_match(self):
        result = compare_crossings([Crossing('negative_to_positive', 1)], [Crossing('positive_to_negative', 1)], tolerance=0)
        self.assertEqual((len(result.matches), len(result.missed), len(result.extra)), (0, 1, 1))
        self.assertEqual(len(compare_crossings([], [], tolerance=0).matches), 0)

    def test_pair_count_matches_brute_force_for_small_ambiguous_timelines(self):
        from itertools import combinations_with_replacement

        times = (0, .1, .2, .3)
        for references in combinations_with_replacement(times, 3):
            for predictions in combinations_with_replacement(times, 3):
                def optimum(i, available):
                    if i == len(references):
                        return 0
                    scores = [optimum(i + 1, available)]
                    for j in available:
                        if abs(predictions[j] - references[i]) <= .11:
                            scores.append(1 + optimum(i + 1, available - {j}))
                    return max(scores)

                result = compare_crossings(
                    [Crossing('negative_to_positive', t) for t in references],
                    [Crossing('negative_to_positive', t) for t in predictions], tolerance=.11,
                )
                self.assertEqual(len(result.matches), optimum(0, set(range(3))))

    def test_invalid_tolerance_events_and_timestamps_are_rejected(self):
        for tolerance in (-1, float('nan'), float('inf'), True):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                compare_crossings([], [], tolerance=tolerance)
        for event in (Crossing('wrong', 1), Crossing('negative_to_positive', -1), Crossing('negative_to_positive', float('nan'))):
            with self.subTest(event=event), self.assertRaises(ValueError):
                compare_crossings([event], [], tolerance=1)
