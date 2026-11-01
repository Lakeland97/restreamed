"""The stream layer's selection engine: polls, a simulated audience, and weighted mod choice.

Pure logic, no HTTP and no clock of its own — the mock passes `now` in, tests pass a fixed one. The
catalogue numbers are the game's own template parameters (duration in seconds, selection weight), decoded
from `templates.tplc`.
"""
import random

# (duration, weight) per template, from ModComponent.templateData.
CATALOGUE = {
    "game_mod_big_head_mode": (30, 3), "game_mod_bonus_bonus": (30, 2),
    "game_mod_concrete_shoes": (30, 2), "game_mod_crabcore": (15, 2),
    "game_mod_energy_drink": (20, 4), "game_mod_floor_is_lava": (60, 4),
    "game_mod_make_it_rain": (45, 2), "game_mod_moon_boots": (60, 4),
    "game_mod_multi_ball": (30, 2), "game_mod_nerfed": (30, 2),
    "game_mod_no_touching": (30, 3), "game_mod_perpetual_motion": (15, 2),
    "game_mod_pleb": (15, 2), "game_mod_pogo": (30, 3),
    "game_mod_points_b_gone": (45, 2), "game_mod_puke_town": (15, 1),
    "game_mod_ragdoll": (30, 3), "game_mod_rise_from_your_graves": (30, 3),
    "game_mod_sandstorm": (45, 2), "game_mod_shady_business": (45, 2),
    "game_mod_shrink_ray": (30, 2), "game_mod_slo_thuggin": (30, 3),
    "game_mod_spiritual_cleansing": (30, 2), "game_mod_stop_drop_roll": (45, 1),
    "game_mod_ugh_ice_level": (60, 4), "game_mod_vengeful_spirits": (45, 2),
    "game_mod_wolf_among_us": (45, 2), "game_mod_xray_glasses": (45, 3),
    "game_mod_yakety_hill": (60, 4),
}

# Per-mode allow-lists from ModGroupComponent.mods.
GROUP_TAG = [
    "game_mod_concrete_shoes", "game_mod_crabcore", "game_mod_floor_is_lava",
    "game_mod_moon_boots", "game_mod_no_touching", "game_mod_perpetual_motion",
    "game_mod_pleb", "game_mod_pogo", "game_mod_sandstorm", "game_mod_shrink_ray",
    "game_mod_ugh_ice_level", "game_mod_xray_glasses", "game_mod_yakety_hill",
    "game_mod_bonus_bonus", "game_mod_multi_ball", "game_mod_make_it_rain",
    "game_mod_wolf_among_us", "game_mod_slo_thuggin", "game_mod_energy_drink",
    "game_mod_puke_town", "game_mod_ragdoll", "game_mod_big_head_mode",
]


class Poll:
    """One vote over candidate mods, with an audience that votes over the window."""

    def __init__(self, poll_id, options, opened_at, duration, audience, rng):
        self.id = poll_id
        self.options = list(options)
        self.opened_at = opened_at
        self.duration = duration
        self.closed = False
        self.winner = None
        # Each simulated viewer has a vote time inside the window and a preference.
        self._votes = []  # (at, option)
        weights = [CATALOGUE[o][1] for o in self.options]
        for _ in range(audience):
            at = opened_at + rng.random() * duration
            # Bias the draw by template weight but keep it noisy, so a low-weight mod can win.
            pick = rng.choices(self.options, weights=[w * 0.7 + rng.random() * 0.6 for w in weights])[0]
            self._votes.append((at, pick))
        self._votes.sort()

    def tally(self, now):
        counts = {o: 0 for o in self.options}
        for at, option in self._votes:
            if at <= now:
                counts[option] += 1
        return counts

    def state(self, now):
        remaining = max(0, int(self.opened_at + self.duration - now))
        counts = self.tally(now)
        if remaining == 0 and not self.closed:
            self.closed = True
            self.winner = max(counts, key=lambda k: (counts[k], k))
        return {
            "poll_id": self.id,
            "options": list(self.options),
            "votes": counts,
            "remaining": remaining,
            "closed": self.closed,
            "winner": self.winner,
        }


def pick_options(rng, count=3, group=None):
    """Weighted draw without replacement from a mode's allow-list."""
    pool = [m for m in (group or GROUP_TAG) if m in CATALOGUE]
    chosen = []
    while pool and len(chosen) < count:
        weights = [CATALOGUE[m][1] for m in pool]
        pick = rng.choices(pool, weights=weights)[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen


class StreamSession:
    """Owns the current poll for one channel."""

    def __init__(self, channel, seed=None, window=25, audience=14):
        self.channel = channel
        self.rng = random.Random(seed)
        self.window = window
        self.audience = audience
        self.poll = None
        self._next_id = 1
        self.history = []

    def start_poll(self, now, count=3, group=None):
        options = pick_options(self.rng, count, group)
        poll = Poll("p%d" % self._next_id, options, now, self.window, self.audience, self.rng)
        self._next_id += 1
        self.poll = poll
        return poll

    def current(self, now):
        """The poll for this instant, starting one if none is open and the last one finished."""
        if self.poll is None:
            return self.start_poll(now)
        if self.poll.closed or now >= self.poll.opened_at + self.poll.duration:
            if self.poll.winner:
                self.history.append(self.poll)
            return self.start_poll(now)
        return self.poll

    def state(self, now):
        return self.current(now).state(now)

    def submit(self, option, now):
        """A real player's vote counts once and only while the poll is open."""
        poll = self.current(now)
        if poll.closed or option not in poll.options:
            return False
        poll._votes.append((now, option))
        poll._votes.sort()
        return True
