"""Offline tests for the stream-layer selection engine."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import stream_engine as se  # noqa: E402


class CatalogueTest(unittest.TestCase):
    def test_catalogue_matches_the_group(self):
        for mod in se.GROUP_TAG:
            self.assertIn(mod, se.CATALOGUE)
        self.assertEqual(len(se.CATALOGUE), 29)
        self.assertEqual(len(se.GROUP_TAG), 22)


class PollTest(unittest.TestCase):
    def test_options_are_distinct_and_from_the_group(self):
        rng = se.random.Random(7)
        opts = se.pick_options(rng, 3)
        self.assertEqual(len(opts), 3)
        self.assertEqual(len(set(opts)), 3)
        for o in opts:
            self.assertIn(o, se.GROUP_TAG)

    def test_tally_grows_and_closes_with_a_winner(self):
        rng = se.random.Random(1)
        poll = se.Poll("p1", se.pick_options(rng, 3), opened_at=0, duration=10, audience=20, rng=rng)
        early = poll.state(1)["votes"]
        late = poll.state(9)["votes"]
        self.assertLessEqual(sum(early.values()), sum(late.values()))
        final = poll.state(10)
        self.assertTrue(final["closed"])
        self.assertIn(final["winner"], poll.options)
        self.assertEqual(sum(final["votes"].values()), 20)

    def test_submit_only_while_open(self):
        rng = se.random.Random(2)
        poll = se.Poll("p2", se.pick_options(rng, 2), opened_at=0, duration=5, audience=2, rng=rng)
        se_session = se.StreamSession("chan", seed=2, window=5, audience=2)
        se_session.poll = poll
        self.assertTrue(se_session.submit(poll.options[0], 1))
        self.assertFalse(se_session.submit("game_mod_not_a_mod", 1))
        # A vote after the window lands in the next poll, which is the behaviour a live service wants.
        later = se_session.current(99)
        self.assertIsNot(later, poll)
        self.assertTrue(se_session.submit(later.options[0], 99))

    def test_same_seed_same_poll(self):
        a = se.StreamSession("chan", seed=11, window=10, audience=5)
        b = se.StreamSession("chan", seed=11, window=10, audience=5)
        self.assertEqual(a.state(0)["options"], b.state(0)["options"])
        self.assertEqual(a.state(3)["votes"], b.state(3)["votes"])


if __name__ == "__main__":
    unittest.main()
