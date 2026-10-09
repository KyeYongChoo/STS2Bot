from __future__ import annotations

import time
from datetime import datetime

from .client import STS2ApiError, STS2Client
from .heuristic import HeuristicStrategy
from .logger import TrajectoryLogger
from .watchdog import JamError, Watchdog


def _describe(state: dict, action_name: str, params: dict) -> str:
    run = state.get("run") or {}
    loc = f"Act {run.get('act', '?')} Floor {run.get('floor', '?')}" if run else ""
    st = state.get("state_type")
    return f"[{loc}] {st}: {action_name}({', '.join(f'{k}={v}' for k, v in params.items() if v is not None)})"


class Bot:
    def __init__(self, client: STS2Client, strategy: HeuristicStrategy, delay: float = 0.6,
                 log_dir: str = "data/trajectories", on_state=None,
                 jam_dir: str = "data/jams", on_jam: str = "continue"):
        # "continue": report the jam and keep going (fine while watching);
        # "abort": stop the bot (for unattended runs, so it doesn't spin forever).
        if on_jam not in ("continue", "abort"):
            raise ValueError(f"on_jam must be 'continue' or 'abort', not {on_jam!r}")
        self.on_jam = on_jam
        self.client = client
        self.strategy = strategy
        self.delay = delay
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.logger = TrajectoryLogger(run_id, log_dir=log_dir)
        self.watchdog = Watchdog(run_id, jam_dir=jam_dir)
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
        except JamError as e:
            print(f"\nStopping: bot is jammed -- {e}")
        finally:
            self.logger.close()

    def _step(self) -> None:
        state = self.client.get_state()
        if self.on_state is not None:
            self.on_state(state)
        self.watchdog.on_poll(state)
        decision = self.strategy.decide(state)

        if decision is None:
            self.watchdog.on_no_rule(state)
            print(f"[{state.get('state_type')}] no rule for this screen; waiting. "
                  f"(extend sts2bot/heuristic.py to handle it)")
        else:
            action_name, params = decision
            self.watchdog.on_action(state, action_name, params)
            print(_describe(state, action_name, params))
            self.logger.log(state, action_name, params)

            method = getattr(self.client, action_name)
            try:
                method(**params)
            except STS2ApiError as e:
                print(f"  -> API rejected action: {e}")
                self.watchdog.on_action_result(e)
            else:
                self.watchdog.on_action_result(None)

        jam = self.watchdog.check(state)
        if jam is not None:
            print(f"[watchdog] JAM ({jam.reason}) on {state.get('state_type')}: {jam.detail}. "
                  f"Snapshot: {jam.path}")
            if self.on_jam == "abort":
                raise JamError(jam)
