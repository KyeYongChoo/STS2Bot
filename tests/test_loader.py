"""Offline tests for gamedata.py, deckgen.py and loader.py -- a tiny fake
game-data file, a fake API client and a fake console; no game needed."""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from sts2bot import loader
from sts2bot.deckgen import DeckCard, deck_matches, from_state_deck, stage_deck, starter_deck
from sts2bot.gamedata import EncounterInfo, GameData

FAKE_DATA = {"data": {
    "version": "test",
    "cards": [
        {"id": "STRIKE_IRONCLAD", "name": "Strike", "color": "ironclad", "rarity": "Basic", "type": "Attack"},
        {"id": "DEFEND_IRONCLAD", "name": "Defend", "color": "ironclad", "rarity": "Basic", "type": "Skill"},
        {"id": "BASH", "name": "Bash", "color": "ironclad", "rarity": "Basic", "type": "Attack"},
        {"id": "ANGER", "name": "Anger", "color": "ironclad", "rarity": "Common", "type": "Attack"},
        {"id": "INFLAME", "name": "Inflame", "color": "ironclad", "rarity": "Uncommon", "type": "Power"},
        {"id": "DEMON_FORM", "name": "Demon Form", "color": "ironclad", "rarity": "Rare", "type": "Power"},
        {"id": "NEUTRALIZE", "name": "Neutralize", "color": "silent", "rarity": "Basic", "type": "Attack"},
    ],
    "events": [{"id": "DENSE_VEGETATION"}],
    "enchantments": [{"id": "GLAM"}],
    "encounters": [
        {"id": "NIBBITS_WEAK", "type": "Monster", "acts": ["OVERGROWTH"], "lineups": [[{"id": "NIBBIT"}]]},
        {"id": "VANTOM_BOSS", "type": "Boss", "acts": ["OVERGROWTH"], "lineups": [[{"id": "VANTOM"}]]},
        {"id": "X_ELITE", "type": "Elite", "acts": ["HIVE"], "lineups": []},
    ],
}}


def make_gamedata(tmp: str) -> GameData:
    path = Path(tmp) / "data.json"
    path.write_text(json.dumps(FAKE_DATA), encoding="utf-8")
    return GameData(path)


class DeckgenTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.gd = make_gamedata(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gamedata_loads(self) -> None:
        self.assertEqual(self.gd.acts, ["OVERGROWTH", "HIVE"])
        self.assertEqual([c.id for c in self.gd.card_pool("ironclad")], ["ANGER", "INFLAME", "DEMON_FORM"])

    def test_starter_deck(self) -> None:
        self.assertEqual(Counter(c.id for c in starter_deck("ironclad")),
                         Counter({"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}))

    def test_stage_deck_keeps_basics_drops_rest_adds_floor_minus_one(self) -> None:
        current = [DeckCard("STRIKE_IRONCLAD"), DeckCard("BASH", upgrades=1), DeckCard("ANGER"),
                   DeckCard("NEUTRALIZE")]
        deck = stage_deck(current, floor=8, gamedata=self.gd, color="ironclad", rng=random.Random(1))
        self.assertEqual(deck[:2], [DeckCard("STRIKE_IRONCLAD"), DeckCard("BASH", upgrades=1)])
        self.assertEqual(len(deck), 2 + 7)
        self.assertTrue(all(c.id in {"ANGER", "INFLAME", "DEMON_FORM"} for c in deck[2:]))

    def test_stage_deck_same_seed_same_deck(self) -> None:
        a = stage_deck([], 10, self.gd, "ironclad", random.Random("abc"))
        b = stage_deck([], 10, self.gd, "ironclad", random.Random("abc"))
        self.assertEqual(a, b)

    def test_state_round_trip_and_match(self) -> None:
        state_deck = [{"id": "BASH", "upgrades": 1, "enchantment": "GLAM", "enchantment_amount": 2},
                      {"id": "STRIKE_IRONCLAD", "is_upgraded": False}]
        deck = from_state_deck(state_deck)
        self.assertEqual(deck[0], DeckCard("BASH", 1, "GLAM", 2))
        self.assertEqual(deck[0].to_api(), {"id": "BASH", "upgrades": 1, "enchantment": "GLAM",
                                            "enchantment_amount": 2})
        self.assertTrue(deck_matches(list(reversed(state_deck)), deck))
        self.assertFalse(deck_matches(state_deck[:1], deck))


class FakeClient:
    """Game whose act/deck change as soon as they're requested."""

    def __init__(self, act=1, act_id="OVERGROWTH", deck=None):
        self.state = {"run": {"act": act, "act_id": act_id, "floor": 3},
                      "player": {"character": "The Ironclad", "deck": deck or []}}
        self.set_decks = []

    def get_state(self):
        return json.loads(json.dumps(self.state))

    def dev_set_deck(self, cards):
        self.set_decks.append(cards)
        self.state["player"]["deck"] = [dict(c) for c in cards]

    def console(self, commands, _window):
        self.commands = getattr(self, "commands", []) + list(commands)
        for cmd in commands:
            arg = cmd.split()[1]
            if cmd.startswith("act ") and arg.isdigit():
                self.state["run"]["act"] = int(arg)
            elif cmd.startswith("act "):
                self.state["run"]["act_id"] = arg


class LoaderTest(unittest.TestCase):
    def test_default_floor(self) -> None:
        self.assertEqual(loader.default_floor(EncounterInfo("NIBBITS_WEAK", "Monster", ("OVERGROWTH",), ())), 2)
        self.assertEqual(loader.default_floor(EncounterInfo("X_ELITE", "Elite", ("HIVE",), ())), 27)
        self.assertEqual(loader.default_floor(EncounterInfo("V_BOSS", "Boss", ("OVERGROWTH",), ())), 16)

    def test_act_commands(self) -> None:
        self.assertEqual(loader.act_commands("UNDERDOCKS", 1, "OVERGROWTH"), [["act UNDERDOCKS"]])
        self.assertEqual(loader.act_commands("HIVE", 1, "OVERGROWTH"), [["act 2"], ["act HIVE"]])
        self.assertEqual(loader.act_commands("OVERGROWTH", 1, "OVERGROWTH"), [])
        with self.assertRaises(ValueError):
            loader.act_commands("NOWHERE", 1, None)

    def test_execute_full_sequence(self) -> None:
        client = FakeClient()
        deck = [DeckCard("BASH", 1), DeckCard("ANGER")]
        request = loader.LoadRequest("fight", "NIBBITS_WEAK", act_id="UNDERDOCKS", deck=deck, heal=True)
        logs = []
        ok = loader.execute(request, client, log=logs.append, console=client.console)
        self.assertTrue(ok, logs)
        self.assertEqual(client.commands, ["act UNDERDOCKS", "heal 1000", "fight NIBBITS_WEAK"])
        self.assertEqual(client.set_decks, [[{"id": "BASH", "upgrades": 1}, {"id": "ANGER"}]])

    def test_execute_keep_everything_just_event(self) -> None:
        client = FakeClient()
        ok = loader.execute(loader.LoadRequest("event", "DENSE_VEGETATION", heal=False), client,
                            log=lambda _m: None, console=client.console)
        self.assertTrue(ok)
        self.assertEqual(client.commands, ["event DENSE_VEGETATION"])
        self.assertEqual(client.set_decks, [])

    def test_execute_stops_when_no_run(self) -> None:
        client = FakeClient()
        client.state = {"state_type": "menu"}
        logs = []
        self.assertFalse(loader.execute(loader.LoadRequest("fight", "X"), client, log=logs.append,
                                        console=client.console))
        self.assertIn("No run in progress", logs[0])


if __name__ == "__main__":
    unittest.main()
