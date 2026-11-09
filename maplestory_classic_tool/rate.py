"""Gain over time -> per-hour rate (shared by the EXP and meso trackers; pure, caller's clock)."""
from __future__ import annotations

WINDOW_S = 600.0  # per hour over the trailing 10 minutes (session window while shorter)
MIN_RATE_S = 60.0  # no rate until the session spans this long ...
MIN_INCREASES = 2  # ... and the value went up this many times
IDLE_S = 300.0  # no gain this long: "idle", the rate is frozen


class GainRate:
    """Cumulative gain with a trailing-window rate.

    start(now) begins the session, gain(amount, now) records an increase. Idle gaps (no gain for IDLE_S)
    do not count as time: on the first gain after one, the history is shifted forward by the gap, so the
    rate window covers active time only. While idle the rate is frozen at its value as of the last gain."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.gained = 0
        self.increases = 0
        self._first_t = None
        self._last_change_t = None
        self._history: list = []  # (t, cumulative gained) at each gain (and the start)

    def start(self, now: float) -> None:
        self._first_t = self._last_change_t = now
        self._history = [(now, 0)]

    def gain(self, amount, now: float) -> None:
        if self._last_change_t is not None and now - self._last_change_t >= IDLE_S:
            gap = now - self._last_change_t  # idle: shift the past forward so the window is active time
            self._history = [(t + gap, g) for t, g in self._history]
            self._first_t += gap
        self.gained += int(round(amount))
        self.increases += 1
        self._last_change_t = now
        self._history.append((now, self.gained))
        cutoff = now - 2 * WINDOW_S
        while len(self._history) > 2 and self._history[1][0] <= cutoff:
            self._history.pop(0)

    def idle(self, now: float) -> bool:
        return self._last_change_t is not None and now - self._last_change_t >= IDLE_S

    def rate(self, now: float):
        """Gain per hour over the trailing window, None until MIN_RATE_S and MIN_INCREASES."""
        if self._first_t is None or self.increases < MIN_INCREASES:
            return None
        end = self._last_change_t if self.idle(now) else now
        if end - self._first_t < MIN_RATE_S:
            return None
        start = max(self._first_t, end - WINDOW_S)
        ref = self._history[0][1]
        for t, g in self._history:
            if t > start:
                break
            ref = g
        gained_at_end = ref
        for t, g in self._history:
            if t > end:
                break
            gained_at_end = g
        return (gained_at_end - ref) / (end - start) * 3600.0
