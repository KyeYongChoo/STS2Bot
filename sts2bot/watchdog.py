"""Detects when the bot is jammed -- neither winning nor losing, just stuck --
and dumps a snapshot to disk so the cause can be fixed later.

Jam conditions (each fires at most once per "episode"; an episode ends as
soon as the game state changes or the bot makes real progress):
  - stall:    game state hasn't changed at all for `stall_seconds`
  - no_rule:  the strategy has returned no action for `no_rule_seconds`
              (an unhandled screen, or a handler that silently gives up)
  - repeat:   the identical action was sent `repeat_limit` times in a row
              without the state changing
  - rejected: the API rejected `reject_limit` actions in a row

`menu` and `game_over` are left unhandled on purpose for now (runs are
started by hand), so idling there is not treated as a jam.

Snapshots go to data/jams/<run_id>_<n>_<reason>.json with the full state
plus the last few actions/events leading up to it.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

IDLE_STATE_TYPES = frozenset({"menu", "game_over"})


@dataclass
class Jam:
    reason: str  # "stall" | "no_rule" | "repeat" | "rejected"
    detail: str
    path: Path


class JamError(RuntimeError):
    """Raised by Bot when on_jam="abort" and the watchdog reports a jam."""

    def __init__(self, jam: Jam):
        super().__init__(f"{jam.reason}: {jam.detail} (snapshot: {jam.path})")
        self.jam = jam


def state_fingerprint(state: dict) -> str:
    return hashlib.sha1(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


class Watchdog:
    def __init__(self, run_id: str, jam_dir: str = "data/jams", *,
                 stall_seconds: float = 45.0, no_rule_seconds: float = 20.0,
                 repeat_limit: int = 6, reject_limit: int = 5, history: int = 30,
                 clock: Callable[[], float] = time.monotonic):
        self.run_id = run_id
        self.dir = Path(jam_dir)
        self.stall_seconds = stall_seconds
        self.no_rule_seconds = no_rule_seconds
        self.repeat_limit = repeat_limit
        self.reject_limit = reject_limit
        self.clock = clock
        self.jam_count = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._fingerprint: str | None = None
        self._state_since = clock()
        self._no_rule_since: float | None = None
        self._last_action_key: tuple | None = None
        self._repeat_count = 0
        self._reject_count = 0
        self._reported = False

    # -- observations, called by Bot ------------------------------------- #
    def on_poll(self, state: dict) -> None:
        fp = state_fingerprint(state)
        if fp != self._fingerprint:
            self._fingerprint = fp
            self._state_since = self.clock()
            self._repeat_count = 0
            self._reported = False

    def on_no_rule(self, state: dict) -> None:
        if self._no_rule_since is None:
            self._no_rule_since = self.clock()
            self._remember("no_rule", state_type=state.get("state_type"))

    def on_action(self, state: dict, action_name: str, params: dict) -> None:
        self._no_rule_since = None
        key = (action_name, tuple(sorted(params.items())))
        self._repeat_count = self._repeat_count + 1 if key == self._last_action_key else 1
        self._last_action_key = key
        self._remember("action", state_type=state.get("state_type"), name=action_name, params=params)

    def on_action_result(self, error: Exception | None) -> None:
        if error is None:
            self._reject_count = 0
        else:
            self._reject_count += 1
            self._remember("rejected", error=str(error))

    # -- detection ------------------------------------------------------- #
    def check(self, state: dict) -> Jam | None:
        """Returns a Jam (and writes its snapshot) the first time a jam
        condition holds in the current episode, else None."""
        if self._reported or state.get("state_type") in IDLE_STATE_TYPES:
            return None
        now = self.clock()
        found = None
        if self._reject_count >= self.reject_limit:
            found = ("rejected", f"API rejected {self._reject_count} actions in a row")
        elif self._repeat_count >= self.repeat_limit:
            name, params = self._last_action_key
            found = ("repeat", f"{name}({dict(params)}) sent {self._repeat_count} times "
                               f"with no state change")
        elif self._no_rule_since is not None and now - self._no_rule_since >= self.no_rule_seconds:
            found = ("no_rule", f"no action chosen for {now - self._no_rule_since:.0f}s")
        elif now - self._state_since >= self.stall_seconds:
            found = ("stall", f"state unchanged for {now - self._state_since:.0f}s")
        if found is None:
            return None
        self._reported = True
        # Restart the no_rule timer too: on an unhandled screen whose state
        # keeps changing, each change starts a new episode, and without this
        # it would re-report on every poll instead of every no_rule_seconds.
        if self._no_rule_since is not None:
            self._no_rule_since = now
        return self._dump(state, *found)

    # -- internals ------------------------------------------------------- #
    def _remember(self, kind: str, **fields: Any) -> None:
        self._recent.append({"t": time.time(), "kind": kind, **fields})

    def _dump(self, state: dict, reason: str, detail: str) -> Jam:
        self.jam_count += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{self.run_id}_{self.jam_count:03d}_{reason}.json"
        record = {
            "t": time.time(),
            "run_id": self.run_id,
            "reason": reason,
            "detail": detail,
            "state_type": state.get("state_type"),
            "state": state,
            "recent": list(self._recent),
        }
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        return Jam(reason, detail, path)
