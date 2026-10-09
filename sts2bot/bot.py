from __future__ import annotations

import time
from datetime import datetime

from .client import STS2ApiError, STS2Client
from .heuristic import HeuristicStrategy
from .logger import TrajectoryLogger

STUCK_REPEAT_LIMIT = 6


def _describe(state: dict, action_name: str, params: dict) -> str:
    run = state.get("run") or {}
    loc = f"Act {run.get('act', '?')} Floor {run.get('floor', '?')}" if run else ""
    st = state.get("state_type")
    return f"[{loc}] {st}: {action_name}({', '.join(f'{k}={v}' for k, v in params.items() if v is not None)})"


class Bot:
    def __init__(self, client: STS2Client, strategy: HeuristicStrategy, delay: float = 0.6,
                 log_dir: str = "data/trajectories", on_state=None):
        self.client = client
        self.strategy = strategy
        self.delay = delay
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.logger = TrajectoryLogger(run_id, log_dir=log_dir)
        self._last_action: tuple[str, str] | None = None
        self._repeat_count = 0
        # Optional callback(state) invoked every poll before deciding an
        # action -- lets external tooling (e.g. sts2bot/dev_console.py's
        # encounter test loop) observe state transitions like "combat just
        # ended" without Bot needing to know anything about that use case.
        self.on_state = on_state

    def run_forever(self) -> None:
        print("Waiting for Slay the Spire 2 (STS2MCP API)...")
        self.client.wait_until_ready()
        print("Connected. Starting bot loop. Ctrl+C to stop.\n")
        try:
            while True:
                self._step()
                time.sleep(self.delay)
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            self.logger.close()

    def _step(self) -> None:
        state = self.client.get_state()
        if self.on_state is not None:
            self.on_state(state)
        decision = self.strategy.decide(state)

        if decision is None:
            print(f"[{state.get('state_type')}] no rule for this screen; waiting. "
                  f"(extend sts2bot/heuristic.py to handle it)")
            return

        action_name, params = decision

        # Keyed on the full action incl. params, so playing several different
        # cards in a row (e.g. a turn where an energy-refund card lets you
        # squeeze in extra plays) doesn't look like a stuck loop. This only
        # fires for a genuinely identical action repeated back to back.
        key = (state.get("state_type"), action_name, tuple(sorted(params.items())))
        if key == self._last_action:
            self._repeat_count += 1
        else:
            self._repeat_count = 0
            self._last_action = key
        if self._repeat_count and self._repeat_count % STUCK_REPEAT_LIMIT == 0:
            print(f"[{state.get('state_type')}] '{action_name}' with the same params "
                  f"has repeated {self._repeat_count} times in a row; still trying it "
                  f"(check heuristic.py for this state_type if it never progresses).")

        print(_describe(state, action_name, params))
        self.logger.log(state, action_name, params)

        method = getattr(self.client, action_name)
        try:
            method(**params)
        except STS2ApiError as e:
            print(f"  -> API rejected action: {e}")
