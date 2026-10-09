"""Scenario tests for the per-encounter strategies: hand-built combat
states run through HeuristicStrategy.decide, checking which card it plays
and at whom. No game needed."""
from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO

from sts2bot.heuristic import HeuristicStrategy, _combo_damage, kill_probability_by_turns


def strike(i, dmg=6, cost=1):
    return {"index": i, "id": "STRIKE_IRONCLAD", "type": "Attack", "cost": str(cost), "can_play": True,
            "target_type": "AnyEnemy", "description": f"Deal {dmg} damage."}


def twin(i):
    return {"index": i, "id": "TWIN_STRIKE", "type": "Attack", "cost": "1", "can_play": True,
            "target_type": "AnyEnemy", "description": "Deal 5 damage twice."}


def bash(i):
    return {"index": i, "id": "BASH", "type": "Attack", "cost": "2", "can_play": True,
            "target_type": "AnyEnemy", "description": "Deal 8 damage. Apply 2 Vulnerable."}


def defend(i, block=5):
    return {"index": i, "id": "DEFEND_IRONCLAD", "type": "Skill", "cost": "1", "can_play": True,
            "target_type": "Self", "description": f"Gain {block} Block."}


def pile(*cards):  # draw/discard piles carry no index/type/can_play
    return [{"name": c["id"], "cost": c["cost"], "description": c["description"]} for c in cards]


def enemy(entity_id, hp, attack=0, status=None, intent=None):
    intents = [{"type": "Attack", "label": str(attack)}] if attack else [{"type": intent or "Buff"}]
    return {"entity_id": entity_id, "hp": hp, "max_hp": hp, "block": 0, "status": status or [], "intents": intents}


def combat(hand, enemies, energy=3, block=0, round_no=1, draw=(), discard=(), state_type="monster"):
    return {"state_type": state_type, "run": {"act": 1, "floor": 5},
            "battle": {"round": round_no, "turn": "player", "is_play_phase": True, "enemies": list(enemies)},
            "player": {"character": "The Ironclad", "hp": 70, "max_hp": 80, "energy": energy, "max_energy": 3,
                       "block": block, "hand": list(hand), "draw_pile": list(draw), "discard_pile": list(discard)}}


def slippery(n):
    return [{"name": "Slippery", "amount": n}]


class StrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = HeuristicStrategy()

    def decide(self, state):
        with redirect_stdout(StringIO()):
            return self.s.decide(state)

    def assertPlays(self, action, card_index, target=None):
        self.assertEqual(action[0], "play_card", action)
        self.assertEqual(action[1]["card_index"], card_index, action)
        if target is not None:
            self.assertEqual(action[1]["target"], target, action)

    # --- shared machinery ------------------------------------------------
    def test_slippery_combo_damage(self) -> None:
        # Bash into 1 Slippery stack deals 1; Twin Strike first soaks it.
        self.assertEqual(_combo_damage([bash(0)], slippery(1)), 1)
        self.assertEqual(_combo_damage([twin(0), bash(1)], slippery(1)), 1 + 5 + 8)

    def test_kill_probability_reflects_draw_pile(self) -> None:
        attack_heavy = pile(*[strike(0)] * 10)
        defend_heavy = pile(*[defend(0)] * 10)
        self.assertEqual(kill_probability_by_turns(attack_heavy, [], [], 30, 2, 3, 5), 1.0)
        self.assertEqual(kill_probability_by_turns(defend_heavy, [], [], 30, 2, 3, 5), 0.0)

    def test_encounter_remembered_after_a_death_and_reset_after_combat(self) -> None:
        inklets = [enemy("INKLET_0", 12, 3, slippery(1)), enemy("INKLET_1", 12, 3, slippery(1)),
                   enemy("INKLET_2", 12, 3, slippery(1))]
        self.decide(combat([strike(0)], inklets))
        self.assertEqual(self.s.current_encounter.name, "Inklets")
        self.decide(combat([strike(0)], inklets[:2], round_no=2))
        self.assertEqual(self.s.current_encounter.name, "Inklets")
        self.decide({"state_type": "rewards"})
        self.assertIsNone(self.s.current_encounter)

    def test_no_crash_when_every_enemy_is_dead(self) -> None:
        self.decide(combat([strike(0)], [enemy("NIBBIT_0", 20, 5)]))
        dead = enemy("NIBBIT_0", 0, 5)
        self.assertEqual(self.decide(combat([strike(0)], [dead], round_no=3)), ("end_turn", {}))

    def test_phrog_death_switches_to_dense_vegetation(self) -> None:
        self.decide(combat([strike(0)], [enemy("PHROG_PARASITE_0", 40, 16)], state_type="elite"))
        self.assertEqual(self.s.current_encounter.name, "Phrog Parasite")
        wrigglers = [enemy(f"WRIGGLER_{i}", 15, 6) for i in range(4)]
        self.decide(combat([strike(0)], wrigglers, round_no=4, state_type="elite"))
        self.assertEqual(self.s.current_encounter.name, "Dense Vegetation")

    # --- fights ------------------------------------------------------------
    def test_inklets_strip_slippery_with_small_hit_and_skip_middle(self) -> None:
        inklets = [enemy("INKLET_0", 12, 2, slippery(1)), enemy("INKLET_1", 9, 2, slippery(1)),
                   enemy("INKLET_2", 11, 2, slippery(1))]
        action = self.decide(combat([bash(0), strike(1), defend(2)], inklets, draw=pile(defend(0))))
        # Lowest-HP non-middle Inklet; Strike before Bash to soak the stack.
        self.assertPlays(action, 1, "INKLET_2")

    def test_ceremonial_beast_stun_counts_as_lethal(self) -> None:
        # 5 HP above the Plow threshold: Strike "kills" (stuns) it, so attack
        # despite 18 incoming instead of blocking.
        beast = enemy("CEREMONIAL_BEAST_0", 155, 18, [{"name": "Plow", "amount": 150}])
        action = self.decide(combat([strike(0), defend(1)], [beast], state_type="boss"))
        self.assertPlays(action, 0, "CEREMONIAL_BEAST_0")

    def test_ceremonial_beast_blocks_big_hit_when_stun_out_of_reach(self) -> None:
        beast = enemy("CEREMONIAL_BEAST_0", 200, 18, [{"name": "Plow", "amount": 150}])
        action = self.decide(combat([strike(0), defend(1)], [beast], state_type="boss"))
        self.assertPlays(action, 1)  # 18 incoming > damage-race cap 6

    def test_ceremonial_beast_after_stun_falls_back(self) -> None:
        beast = enemy("CEREMONIAL_BEAST_0", 140, 0, [])
        from sts2bot.encounter_heuristics import decide_ceremonial_beast
        self.assertIsNone(decide_ceremonial_beast(self.s, combat([strike(0)], [beast]), [beast], None))

    def test_bygone_effigy_plays_spare_defend_before_attacks(self) -> None:
        effigy = enemy("BYGONE_EFFIGY_0", 120, intent="Sleep", status=[{"name": "Slow", "amount": 0}])
        action = self.decide(combat([strike(0), strike(1), defend(2)], [effigy], state_type="elite"))
        self.assertPlays(action, 2)  # 2 Strikes use 2 energy; the 3rd goes to Defend, played first

    def test_bygone_effigy_attacks_when_no_spare_energy(self) -> None:
        effigy = enemy("BYGONE_EFFIGY_0", 120, intent="Sleep")
        action = self.decide(combat([strike(0), strike(1), strike(2), defend(3)], [effigy], state_type="elite"))
        self.assertPlays(action, 0, "BYGONE_EFFIGY_0")

    def test_dense_vegetation_kills_attacking_wriggler(self) -> None:
        wrigglers = [enemy("WRIGGLER_0", 6, 6), enemy("WRIGGLER_1", 4, intent="Buff"),
                     enemy("WRIGGLER_2", 15, 6), enemy("WRIGGLER_3", 15, 6)]
        action = self.decide(combat([strike(0), defend(1)], wrigglers, state_type="monster"))
        self.assertPlays(action, 0, "WRIGGLER_0")

    def test_dense_vegetation_sets_up_idle_wriggler(self) -> None:
        wrigglers = [enemy("WRIGGLER_0", 15, 2), enemy("WRIGGLER_1", 12, intent="Buff"),
                     enemy("WRIGGLER_2", 15, 0, intent="Buff"), enemy("WRIGGLER_3", 15, 0, intent="Buff")]
        action = self.decide(combat([strike(0), defend(1)], wrigglers))
        self.assertPlays(action, 0, "WRIGGLER_1")  # 2 incoming is within the block slack -> attack idle, lowest HP

    def test_fuzzy_wurm_on_schedule_blocks(self) -> None:
        wurm = enemy("FUZZY_WURM_CRAWLER_0", 20, 8)
        action = self.decide(combat([strike(0), defend(1)], [wurm], energy=1, round_no=1,
                                    draw=pile(*[strike(0)] * 10)))
        self.assertPlays(action, 1)  # 3 turns of Strikes kill it anyway: block this hit

    def test_fuzzy_wurm_behind_schedule_attacks(self) -> None:
        wurm = enemy("FUZZY_WURM_CRAWLER_0", 40, 5)
        action = self.decide(combat([strike(0), defend(1)], [wurm], energy=1, round_no=3,
                                    draw=pile(*[defend(0)] * 10)))
        self.assertPlays(action, 0, "FUZZY_WURM_CRAWLER_0")  # 5 dmg is within the damage-race cap

    def test_the_kin_focus_depends_on_expected_damage(self) -> None:
        kin = [enemy("KIN_PRIEST_0", 190, 8), enemy("KIN_FOLLOWER_0", 58, 5), enemy("KIN_FOLLOWER_1", 50, 4)]
        strong = self.decide(combat([strike(0, dmg=30)], kin, state_type="boss",
                                    draw=pile(*[strike(0, dmg=30)] * 10)))
        self.assertEqual(self.s.combat_memory["kin_focus"], "priest")
        self.assertPlays(strong, 0, "KIN_PRIEST_0")

        weak = HeuristicStrategy()
        with redirect_stdout(StringIO()):
            action = weak.decide(combat([strike(0)], kin, state_type="boss", draw=pile(*[defend(0)] * 10)))
        self.assertEqual(weak.combat_memory["kin_focus"], "followers")
        self.assertPlays(action, 0, "KIN_FOLLOWER_1")

    def test_kill_order_overgrowth_flora_jaxfruit_first(self) -> None:
        flora = [enemy("FLYCONID_0", 20, 3), enemy("SNAPPING_JAXFRUIT_0", 30, 3)]
        action = self.decide(combat([strike(0), defend(1)], flora))
        self.assertPlays(action, 0, "SNAPPING_JAXFRUIT_0")


if __name__ == "__main__":
    unittest.main()
