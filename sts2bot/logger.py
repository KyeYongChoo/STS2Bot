"""Records (state, action) pairs to disk so they can later be used as a
behavior-cloning dataset for training a DNN policy that improves on the
heuristic strategy in sts2bot/heuristic.py.

Each line in the output .jsonl file is one decision point:
    {
      "t": <unix timestamp>,
      "run_id": <str>,
      "state_type": <str>,
      "state": <full raw state dict from the API>,
      "action": {"name": <str>, "params": {...}},
      "source": "heuristic"
    }
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TrajectoryLogger:
    def __init__(self, run_id: str, log_dir: str = "data/trajectories"):
        self.run_id = run_id
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")

    def log(self, state: dict[str, Any], action_name: str, action_params: dict[str, Any],
             source: str = "heuristic") -> None:
        record = {
            "t": time.time(),
            "run_id": self.run_id,
            "state_type": state.get("state_type"),
            "state": state,
            "action": {"name": action_name, "params": action_params},
            "source": source,
        }
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
