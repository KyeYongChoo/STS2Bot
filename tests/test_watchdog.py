"""Offline tests for sts2bot/watchdog.py and its wiring into Bot -- no game
needed. Run with:  python -m unittest discover tests
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from sts2bot.bot import Bot
from sts2bot.client import STS2ApiError
from sts2bot.watchdog import Watchdog

MAP_STATE = {"state_type": "map", "run": {"act": 1, "floor": 1}}


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class WatchdogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jam_dir = self._tmp.name
        self.clock = FakeClock()
        self.wd = Watchdog("test", jam_dir=self.jam_dir, clock=self.clock)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stall_fires_once_per_episode(self) -> None:
        self.wd.on_poll(MAP_STATE)
        self.clock.t = 44
        self.assertIsNone(self.wd.check(MAP_STATE))
        self.clock.t = 46
        jam = self.wd.check(MAP_STATE)
        self.assertEqual(jam.reason, "stall")
        self.assertIsNone(self.wd.check(MAP_STATE))

    def test_state_change_resets_stall(self) -> None:
        self.wd.on_poll(MAP_STATE)
        self.clock.t = 40
        moved = {**MAP_STATE, "run": {"act": 1, "floor": 2}}
        self.wd.on_poll(moved)
        self.clock.t = 80
        self.assertIsNone(self.wd.check(moved))

    def test_repeat_identical_action_without_state_change(self) -> None:
        for i in range(6):
            self.wd.on_poll(MAP_STATE)
            self.wd.on_action(MAP_STATE, "choose_map_node", {"index": 0})
            self.wd.on_action_result(None)
            jam = self.wd.check(MAP_STATE)
            if i < 5:
                self.assertIsNone(jam)
        self.assertEqual(jam.reason, "repeat")

    def test_repeat_with_state_changing_is_progress(self) -> None:
        # e.g. playing card slot 0 several times in one turn
        for i in range(10):
            state = {**MAP_STATE, "i": i}
            self.wd.on_poll(state)
            self.wd.on_action(state, "play_card", {"card_index": 0})
            self.wd.on_action_result(None)
            self.assertIsNone(self.wd.check(state))

    def test_consecutive_rejections(self) -> None:
        for i in range(5):
            state = {**MAP_STATE, "i": i}
            self.wd.on_poll(state)
            self.wd.on_action(state, "play_card", {"card_index": i})
            self.wd.on_action_result(Exception("bad"))
        jam = self.wd.check(state)
        self.assertEqual(jam.reason, "rejected")

    def test_accepted_action_resets_rejections(self) -> None:
        for i in range(4):
            self.wd.on_action_result(Exception("bad"))
        self.wd.on_action_result(None)
        self.wd.on_action_result(Exception("bad"))
        self.wd.on_poll(MAP_STATE)
        self.assertIsNone(self.wd.check(MAP_STATE))

    def test_no_rule_rereports_every_interval_not_every_poll(self) -> None:
        reports = 0
        for i in range(50):
            self.clock.t = i
            state = {"state_type": "overlay", "i": i}
            self.wd.on_poll(state)
            self.wd.on_no_rule(state)
            if self.wd.check(state):
                reports += 1
        self.assertEqual(reports, 2)  # at t=20 and t=40

    def test_idle_states_never_jam(self) -> None:
        for state_type in ("menu", "game_over"):
            state = {"state_type": state_type}
            self.wd.on_poll(state)
            self.wd.on_no_rule(state)
            self.clock.t += 999
            self.assertIsNone(self.wd.check(state))

    def test_snapshot_contents(self) -> None:
        self.wd.on_poll(MAP_STATE)
        self.wd.on_action(MAP_STATE, "choose_map_node", {"index": 0})
        self.clock.t = 100
        jam = self.wd.check(MAP_STATE)
        record = json.loads(jam.path.read_text(encoding="utf-8"))
        self.assertEqual(record["reason"], "stall")
        self.assertEqual(record["state"], MAP_STATE)
        self.assertEqual(record["recent"][-1]["name"], "choose_map_node")


class BotWatchdogTest(unittest.TestCase):
    def test_abort_stops_bot_on_jam(self) -> None:
        class RejectingClient:
            def wait_until_ready(self) -> None:
                pass

            def get_state(self) -> dict:
                return MAP_STATE

            def choose_map_node(self, **params) -> None:
                raise STS2ApiError("nope")

        class FixedStrategy:
            def decide(self, state: dict):
                return ("choose_map_node", {"index": 0})

        with tempfile.TemporaryDirectory() as tmp:
            bot = Bot(RejectingClient(), FixedStrategy(), delay=0,
                      log_dir=tmp, jam_dir=tmp, on_jam="abort")
            out = StringIO()
            with redirect_stdout(out):
                bot.run_forever()  # must return instead of looping forever
            self.assertIn("Stopping: bot is jammed", out.getvalue())
            self.assertEqual(bot.watchdog.jam_count, 1)

    def test_rejects_unknown_on_jam(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Bot(None, None, log_dir=tmp, on_jam="explode")


if __name__ == "__main__":
    unittest.main()
