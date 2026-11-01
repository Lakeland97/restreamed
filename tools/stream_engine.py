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

# Bingo group per mod, from ModComponent.templateData. The four groups read as four slots; that a
# card holds at most one mod per slot is an inference from an array field and four group ids, not an
# observed rule, so callers may widen it.
BINGO_GROUP = {
    "game_mod_big_head_mode": 3, "game_mod_bonus_bonus": 2, "game_mod_concrete_shoes": 4,
    "game_mod_crabcore": 1, "game_mod_energy_drink": 2, "game_mod_floor_is_lava": 1,
    "game_mod_make_it_rain": 3, "game_mod_moon_boots": 2, "game_mod_multi_ball": 1,
    "game_mod_nerfed": 2, "game_mod_no_touching": 3, "game_mod_perpetual_motion": 4,
    "game_mod_pleb": 1, "game_mod_pogo": 2, "game_mod_points_b_gone": 1,
    "game_mod_puke_town": 2, "game_mod_ragdoll": 1, "game_mod_rise_from_your_graves": 2,
    "game_mod_sandstorm": 4, "game_mod_shady_business": 3, "game_mod_shrink_ray": 3,
    "game_mod_slo_thuggin": 4, "game_mod_spiritual_cleansing": 4, "game_mod_stop_drop_roll": 4,
    "game_mod_ugh_ice_level": 3, "game_mod_vengeful_spirits": 3, "game_mod_wolf_among_us": 1,
    "game_mod_xray_glasses": 2, "game_mod_yakety_hill": 3,
}

BINGO_SLOTS = (1, 2, 3, 4)

# The only mode allow-list decoded so far (`ModGroupComponent.mods` for Tag). The default and bitball
# lists exist in the templates but have not been transcribed, so they are not offered here.
POOLS = {"tag": GROUP_TAG}


def pool_for(mode):
    try:
        return list(POOLS[mode])
    except KeyError:
        raise KeyError("unknown mode %r; decoded modes: %s" % (mode, ", ".join(sorted(POOLS))))


def profile(mode="tag", window=25, audience=14, seed=1, rotation_seed=1, options=3):
    """The effective stream profile: the knobs the engine runs with and the rules they imply.

    Intended for a tool or overlay that needs to show what the channel is doing without duplicating
    the catalogue. Every value here is read from the same inputs the service uses, so the profile
    cannot drift from behaviour.
    """
    pool = pool_for(mode)
    durations = [CATALOGUE[m][0] for m in pool]
    weights = [CATALOGUE[m][1] for m in pool]
    return {
        "mode": mode,
        "window": window,
        "audience": audience,
        "seed": seed,
        "rotation_seed": rotation_seed,
        "slots": list(BINGO_SLOTS),
        "rules": {
            "poll_options": options,
            "vote_window_seconds": window,
            "rotation_uses_votes": False,
            "one_mod_per_slot": True,
            "no_immediate_repeat": True,
            "duration_seconds": {"min": min(durations), "max": max(durations)},
            "weight_range": {"min": min(weights), "max": max(weights)},
        },
        "pool_size": len(pool),
        "mods": [
            {"mod": m, "duration": CATALOGUE[m][0], "weight": CATALOGUE[m][1],
             "slot": BINGO_GROUP[m]}
            for m in pool
        ],
    }




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


class Rotation:
    """Timer-driven mod rotation with no vote: the algorithmic audience fills each bingo slot.

    One mod per slot, timed by the template's own `duration`, chosen by `weight`, never the mod that
    just left that slot, and never a mod already sitting in another slot. Slots fill lazily, so the
    rotation needs no background timer: ask it for the state at any `now` and it catches up in
    chronological order, which keeps a jumped clock identical to a stepped one.
    """

    def __init__(self, seed=None, pool=None, slots=BINGO_SLOTS, max_steps=10000):
        self.rng = random.Random(seed)
        self.pool = [m for m in (GROUP_TAG if pool is None else pool) if m in BINGO_GROUP]
        self.slots = {g: None for g in slots}  # g -> (mod, started_at, ends_at)
        self.max_steps = max_steps

    def _busy(self, slot, at):
        return {s[0] for g, s in self.slots.items()
                if g != slot and s is not None and s[2] > at}

    def _pick(self, slot, exclude, at):
        busy = self._busy(slot, at)
        candidates = [m for m in self.pool
                      if BINGO_GROUP[m] == slot and m != exclude and m not in busy]
        if not candidates:
            return None
        return self.rng.choices(candidates, weights=[CATALOGUE[m][1] for m in candidates])[0]

    def _start(self, slot, mod, at):
        self.slots[slot] = (mod, at, at + CATALOGUE[mod][0])

    def _advance(self, now):
        for slot in self.slots:
            if self.slots[slot] is None:
                mod = self._pick(slot, None, now)
                if mod is not None:
                    self._start(slot, mod, now)
        for _ in range(self.max_steps):
            due = [(s[2], slot) for slot, s in self.slots.items() if s is not None and s[2] <= now]
            if not due:
                return
            end, slot = min(due)
            previous = self.slots[slot][0]
            mod = self._pick(slot, previous, end)
            if mod is None:              # nothing legal left in this slot
                self.slots[slot] = None
            else:
                self._start(slot, mod, end)
        # A gap wider than max_steps * shortest duration re-rolls the card rather than stalling.
        for slot in self.slots:
            if self.slots[slot] is not None and self.slots[slot][2] <= now:
                mod = self._pick(slot, None, now)
                self.slots[slot] = None if mod is None else (mod, now, now + CATALOGUE[mod][0])

    def state(self, now):
        self._advance(now)
        active = []
        for slot, entry in self.slots.items():
            if entry is None:
                continue
            mod, started_at, ends_at = entry
            active.append({
                "mod": mod,
                "slot": slot,
                "started_at": started_at,
                "ends_at": ends_at,
                "remaining": max(0, int(ends_at - now)),
                "duration": CATALOGUE[mod][0],
            })
        active.sort(key=lambda a: a["slot"])
        return {"source": "rotation", "slots": active}

    def mods(self, now):
        return [a["mod"] for a in self.state(now)["slots"]]

