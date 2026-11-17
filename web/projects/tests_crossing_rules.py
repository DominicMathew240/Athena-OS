"""Synthetic trajectories for finite-line crossing episodes (not model accuracy)."""
from django.test import SimpleTestCase

from .operations.crossings import CrossingCounter


class CrossingEpisodeTests(SimpleTestCase):
    def counter(self, **kwargs):
        return CrossingCounter((0.2, 0.5), (0.8, 0.5), dead_band=0.02, confirmations=2, max_gap=1.0, **kwargs)

    def feed(self, counter, points):
        events = []
        for t, x, y in points:
            event = counter.observe(t, (x, y))
            if event:
                events.append(event)
        return events

    def test_crossing_and_return_are_each_counted_once(self):
        counter = self.counter()
        events = self.feed(counter, [(0, .5, .3), (.1, .5, .3), (.2, .5, .49), (.3, .5, .55),
                                     (.4, .5, .6), (.5, .5, .6), (.6, .5, .4), (.7, .5, .3)])
        self.assertEqual([event.direction for event in events], ['negative_to_positive', 'positive_to_negative'])
        self.assertAlmostEqual(events[0].time, .2 + .1 / 6)

    def test_jitter_touch_parallel_and_outside_segment_do_not_count(self):
        counter = self.counter()
        points = [(0, .5, .3), (.1, .5, .3), (.2, .5, .5), (.3, .5, .49), (.4, .5, .51),
                  (.5, .5, .49), (.6, .5, .3), (.7, .5, .3), (.8, .95, .3), (.9, .95, .7),
                  (1, .95, .7), (1.1, .96, .7)]
        self.assertEqual(self.feed(counter, points), [])

    def test_first_frame_and_long_gap_do_not_manufacture_crossings(self):
        counter = self.counter()
        self.assertEqual(self.feed(counter, [(0, .5, .3), (.1, .5, .3), (2, .5, .7), (2.1, .5, .7)]), [])
        self.assertEqual(len(self.feed(counter, [(2.2, .5, .3), (2.3, .5, .3)])), 1)

    def test_out_of_order_and_invalid_observations_are_rejected_without_corrupting_state(self):
        counter = self.counter()
        self.feed(counter, [(0, .5, .3), (.1, .5, .3)])
        for timestamp, point in [(.1, (.5, .8)), (.2, (float('nan'), .8)), (-1, (.5, .8))]:
            with self.subTest(timestamp=timestamp, point=point), self.assertRaises(ValueError):
                counter.observe(timestamp, point)
        self.assertEqual(len(self.feed(counter, [(.2, .5, .7), (.3, .5, .7)])), 1)

    def test_boundary_observations_do_not_bridge_a_long_ambiguous_interval(self):
        counter = self.counter()
        points = [(0, .5, .3), (.1, .5, .3), (.5, .5, .5), (1, .5, .5), (1.4, .5, .7), (1.5, .5, .7)]
        self.assertEqual(self.feed(counter, points), [])

    def test_stale_candidate_is_not_counted_after_remaining_in_dead_band(self):
        counter = self.counter()
        points = [(0, .5, .3), (.1, .5, .3), (.2, .5, .51), (.7, .5, .51),
                  (1.2, .5, .51), (1.7, .5, .7), (1.8, .5, .7)]
        self.assertEqual(self.feed(counter, points), [])

    def test_crossing_exactly_at_a_segment_endpoint_is_ambiguous(self):
        counter = self.counter()
        points = [(0, .2, .3), (.1, .2, .3), (.2, .2, .7), (.3, .2, .7)]
        self.assertEqual(self.feed(counter, points), [])

    def test_degenerate_line_and_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            CrossingCounter((.5, .5), (.5, .5))
        for kwargs in ({'dead_band': -1}, {'confirmations': 0}, {'max_gap': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CrossingCounter((0, 0), (1, 0), **kwargs)
