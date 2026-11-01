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


class RotationTest(unittest.TestCase):
    def test_every_catalogued_mod_has_a_bingo_slot(self):
        self.assertEqual(len(se.BINGO_GROUP), 29)
        self.assertEqual(set(se.BINGO_GROUP), set(se.CATALOGUE))
        self.assertEqual(set(se.BINGO_GROUP.values()), set(se.BINGO_SLOTS))
        for mod in se.GROUP_TAG:
            self.assertIn(mod, se.BINGO_GROUP)

    def test_one_mod_per_slot_from_its_own_group(self):
        rot = se.Rotation(seed=3)
        for now in range(0, 1200, 7):
            slots = rot.state(now)["slots"]
            self.assertEqual(len({s["slot"] for s in slots}), len(slots))
            for s in slots:
                self.assertEqual(se.BINGO_GROUP[s["mod"]], s["slot"])
                self.assertIn(s["mod"], se.GROUP_TAG)
                self.assertGreaterEqual(s["remaining"], 0)

    def test_a_slot_hands_over_without_a_gap_or_a_repeat(self):
        rot = se.Rotation(seed=4)
        previous = {}
        for now in range(0, 1800):
            for s in rot.state(now)["slots"]:
                old = previous.get(s["slot"])
                if old is not None and old["mod"] != s["mod"]:
                    self.assertEqual(s["started_at"], old["ends_at"])
                    self.assertNotEqual(s["mod"], old["mod"])
                previous[s["slot"]] = s
            self.assertLessEqual(len(rot.mods(now)), 4)

    def test_a_jumped_clock_matches_a_stepped_one(self):
        stepped = se.Rotation(seed=5)
        jumped = se.Rotation(seed=5)
        stepped.state(0)
        jumped.state(0)
        for now in range(1, 901):
            stepped.state(now)
        self.assertEqual(stepped.mods(900), jumped.mods(900))
        self.assertEqual(stepped.state(900)["slots"], jumped.state(900)["slots"])

    def test_same_seed_same_rotation(self):
        a = se.Rotation(seed=9, pool=se.GROUP_TAG[:12])
        b = se.Rotation(seed=9, pool=se.GROUP_TAG[:12])
        self.assertEqual([a.mods(t) for t in (0, 60, 300)], [b.mods(t) for t in (0, 60, 300)])

    def test_weighted_draw_favours_heavy_mods(self):
        rot = se.Rotation(seed=13)
        counts = {}
        for _ in range(2000):
            mod = rot._pick(2, None, 0)
            counts[mod] = counts.get(mod, 0) + 1
        # Group 2 weights: energy_drink 4, moon_boots 4, pogo 3, xray_glasses 3, bonus_bonus 2, puke_town 1.
        heavy = counts.get("game_mod_energy_drink", 0) + counts.get("game_mod_moon_boots", 0)
        self.assertGreater(heavy, counts.get("game_mod_puke_town", 0) * 3)

    def test_empty_pool_yields_no_slots(self):
        rot = se.Rotation(seed=1, pool=[])
        self.assertEqual(rot.state(0)["slots"], [])
        self.assertEqual(rot.mods(999), [])

    def test_a_slot_without_a_legal_successor_reports_empty_then_reseeds(self):
        rot = se.Rotation(seed=1, pool=["game_mod_puke_town"])   # the only group-2 member offered
        self.assertEqual([s["mod"] for s in rot.state(0)["slots"]], ["game_mod_puke_town"])
        self.assertEqual(rot.state(20)["slots"], [])              # 15 s over, no successor allowed
        self.assertEqual([s["mod"] for s in rot.state(21)["slots"]], ["game_mod_puke_town"])


class ProfileTest(unittest.TestCase):
    def test_profile_describes_the_pool_it_serves(self):
        prof = se.profile()
        self.assertEqual(prof["mode"], "tag")
        self.assertEqual(prof["pool_size"], len(se.GROUP_TAG))
        self.assertEqual(len(prof["mods"]), prof["pool_size"])
        for m in prof["mods"]:
            self.assertEqual(m["duration"], se.CATALOGUE[m["mod"]][0])
            self.assertEqual(m["weight"], se.CATALOGUE[m["mod"]][1])
            self.assertEqual(m["slot"], se.BINGO_GROUP[m["mod"]])
        self.assertEqual(prof["rules"]["duration_seconds"], {"min": 15, "max": 60})
        self.assertEqual(prof["rules"]["weight_range"], {"min": 1, "max": 4})
        self.assertFalse(prof["rules"]["rotation_uses_votes"])
        self.assertEqual(prof["slots"], [1, 2, 3, 4])

    def test_profile_reflects_the_knobs_the_service_uses(self):
        prof = se.profile(window=40, audience=3, seed=7, rotation_seed=8, options=4)
        self.assertEqual((prof["window"], prof["audience"], prof["seed"], prof["rotation_seed"]),
                         (40, 3, 7, 8))
        self.assertEqual(prof["rules"]["vote_window_seconds"], 40)
        self.assertEqual(prof["rules"]["poll_options"], 4)

    def test_an_undecoded_mode_is_rejected_by_name(self):
        with self.assertRaises(KeyError) as caught:
            se.profile(mode="bitball")
        self.assertIn("tag", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
