"""Rule-based decision engine.

This is deliberately simple and readable: it exists to (a) drive the bot
end-to-end right now, and (b) generate a labeled trajectory dataset
(see logger.py) that a future DNN policy can be trained on and then
compared against. Every method here is a natural place to later swap in
a learned model — see ml/README.md for that plan.

Each `decide_*` method returns (action_name, params) where action_name is
the name of an STS2Client method. Returning None means "no rule for this
state" and the bot will surface the raw state for manual handling.
"""
from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

from sts2bot.tierlist import TierList

Action = tuple[str, dict[str, Any]]

RARITY_WEIGHT = {"Common": 1, "Uncommon": 2, "Rare": 3, "Special": 2}
REST_LOW_HP_THRESHOLD = 0.6
ELITE_SAFE_HP_THRESHOLD = 0.65
POTION_EMERGENCY_HP_THRESHOLD = 0.3
BLOCK_DECK_FRACTION_THRESHOLD = 0.33

# Roughly descending "how good is it to grab this map node" when nothing
# more specific applies. Tune freely.
NODE_TYPE_PRIORITY = ["Treasure", "Elite", "Merchant", "Shop", "Event", "Monster", "Unknown"]


def _cost_int(cost: Any, energy: int) -> int:
    if cost is None:
        return 0
    if isinstance(cost, int):
        return cost
    s = str(cost).strip().upper()
    if s == "X":
        return energy
    try:
        return int(s)
    except ValueError:
        return 0


def _first_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


# Precise extraction instead of "first number in the text" -- several cards
# grant both damage and block in one description (Iron Wave: "Gain 5 Block.
# Deal 5 damage." -- Block listed *first*), and grabbing whichever number
# comes first silently mixes the two up whenever they differ.
_DAMAGE_RE = re.compile(r"[Dd]eal\s+(\d+)\s+damage")
_BLOCK_RE = re.compile(r"[Gg]ain\s+(\d+)\s+[Bb]lock")
# Confirmed against real card text (Mangle: "Enemy loses 10 Strength this
# turn."; Piercing Wail: "ALL enemies lose 6 Strength this turn."). Cards
# named but unconfirmed (Malaise, Shared Fate) aren't in this profile's
# discovered-card history to check via the wiki endpoint -- this pattern is
# a best-effort match for that general phrasing, not verified against them.
_ENEMY_STRENGTH_DEBUFF_RE = re.compile(r"nem(?:y|ies)\s+lose(?:s)?\s+(\d+)\s+Strength")


def _damage_amount(text: str | None) -> int | None:
    m = _DAMAGE_RE.search(text or "")
    return int(m.group(1)) if m else None


def _block_amount(text: str | None) -> int | None:
    m = _BLOCK_RE.search(text or "")
    return int(m.group(1)) if m else None


def _enemy_strength_debuff(card: dict) -> int | None:
    """How much Strength this card strips from an enemy (Mangle, Piercing
    Wail, Dark Shackles, ...). Not real Block, but it reduces the damage
    that lands this turn the same way Block does, so it's worth counting
    as one when deciding whether to block or attack."""
    m = _ENEMY_STRENGTH_DEBUFF_RE.search(card.get("description") or "")
    return int(m.group(1)) if m else None


def _status_amount(statuses: list[dict] | None, name: str) -> int:
    for s in statuses or []:
        if s.get("name") == name:
            return s.get("amount") or 0
    return 0


def _is_vulnerable(status: list[dict] | None) -> bool:
    return _status_amount(status, "Vulnerable") > 0


def _intent_damage(intent: dict) -> int:
    """Enemy intent numbers come straight from the game's own live tooltip
    renderer (STS2MCP calls Intent.GetIntentLabel, which itself runs
    Hook.ModifyDamage against the actual attacker/defender) -- this value
    already has the enemy's current Strength/Weak *and* the player's
    current Vulnerable baked in. Don't re-apply any of those on top."""
    if "Attack" not in str(intent.get("type", "")):
        return 0
    n = _first_int(intent.get("label")) or _first_int(intent.get("description"))
    return n or 0


def _is_attacking(enemy: dict) -> bool:
    return any("Attack" in str(i.get("type", "")) for i in enemy.get("intents", []))


def _is_summoning(enemy: dict) -> bool:
    return any(i.get("type") == "Summon" for i in enemy.get("intents", []))


def _enemy_base_id(entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    return re.sub(r"_\d+$", "", entity_id)


def _enemy_attack_damage(enemy: dict) -> int:
    return sum(_intent_damage(i) for i in enemy.get("intents", []))


def _card_damage(card: dict, enemy_status: list[dict] | None = None) -> int:
    """A card's damage description is also already adjusted for the
    player's own current Strength/Weak (Hook.ModifyDamage runs against the
    attacker regardless of target) -- but NOT for a specific enemy's
    Vulnerable, since the generic hand-card description is built with no
    target selected yet. That's the one adjustment still needed here."""
    base = _damage_amount(card.get("description")) or 0
    return round(base * 1.5) if _is_vulnerable(enemy_status) else base


def _biggest_threat(enemies: list[dict]) -> dict | None:
    return max(enemies, key=_enemy_attack_damage, default=None)


def _effective_hp(enemy: dict) -> int:
    """Total damage needed to kill: damage clears Block before it touches
    HP, so it's hp + block, not hp - block (an enemy with 1 HP and 5 Block
    still needs 6 damage, not ~0)."""
    return enemy.get("hp", 0) + enemy.get("block", 0)


def _greedy_kill_combo(attacks: list[dict], effective_hp: int, energy_budget: int,
                        enemy_status: list[dict] | None = None) -> tuple[list[dict], int] | None:
    """Finds playable attack card(s) that can finish off effective_hp within
    energy_budget, accounting for Vulnerable on the target. Returns
    (cards_used, energy_spent), or None if no combo found. Not a globally
    optimal subset-sum solve -- just "can we plausibly finish this off
    right now with what's in hand", which is what the kill-vs-block
    comparison in _decide_combat needs.

    Prefers the cheapest single card that can solo the kill over a pricier,
    higher-damage one that also could (e.g. Strike over Bash on a target
    either would finish) -- overkilling with the expensive card wastes
    energy that could go toward a second kill or blocking. Only combines
    multiple cards when no single one suffices, in which case cards are
    spent highest damage-per-energy first.
    """
    if effective_hp <= 0:
        return None

    def dmg(c: dict) -> int:
        return _card_damage(c, enemy_status)

    affordable = [c for c in attacks if dmg(c) > 0
                  and _cost_int(c.get("cost"), energy_budget) <= energy_budget]

    lethal_singles = [c for c in affordable if dmg(c) >= effective_hp]
    if lethal_singles:
        best = min(lethal_singles, key=lambda c: (_cost_int(c.get("cost"), energy_budget), dmg(c)))
        return [best], _cost_int(best.get("cost"), energy_budget)

    candidates = sorted(affordable, key=lambda c: -dmg(c) / max(_cost_int(c.get("cost"), energy_budget), 1))
    combo: list[dict] = []
    total_dmg = 0
    total_cost = 0
    for c in candidates:
        cost = _cost_int(c.get("cost"), energy_budget - total_cost)
        if total_cost + cost > energy_budget:
            continue
        combo.append(c)
        total_dmg += dmg(c)
        total_cost += cost
        if total_dmg >= effective_hp:
            return combo, total_cost
    return None


def _defense_value(card: dict) -> int:
    """How much this card reduces HP loss this turn: real Block (any card
    type -- Iron Wave is an Attack that also grants Block) plus a
    block-equivalent for stripping an attacking enemy's Strength (Mangle,
    Piercing Wail, Dark Shackles, ...), since that also reduces the damage
    that's about to land even though it's not tagged Block."""
    return (_block_amount(card.get("description")) or 0) + (_enemy_strength_debuff(card) or 0)


def _estimate_defense(cards: list[dict], energy_budget: int) -> int:
    """Best-effort total defense value (see _defense_value) obtainable from
    `cards` within energy_budget, used to compare "spend energy killing"
    vs "spend energy defending"."""
    defenders = sorted((c for c in cards if _defense_value(c) > 0), key=lambda c: -_defense_value(c))
    total = 0
    total_cost = 0
    for c in defenders:
        cost = _cost_int(c.get("cost"), energy_budget - total_cost)
        if total_cost + cost > energy_budget:
            continue
        total += _defense_value(c)
        total_cost += cost
    return total


def _hp_frac(player: dict | None) -> float:
    if not player or not player.get("max_hp"):
        return 1.0
    return player.get("hp", 0) / player["max_hp"]


def _potions_full(player: dict | None) -> bool:
    if not player:
        return False
    max_slots = player.get("max_potion_slots")
    if not max_slots:
        return False
    return len(player.get("potions") or []) >= max_slots


class HeuristicStrategy:
    def __init__(self, tier_list_dir: str | Path | None = None):
        self.tier_list = TierList(tier_list_dir)
        # Enemies that have ever shown a Summon-type intent (e.g. Fogmog,
        # Decimillipede, Obscura/Parafright) -- remembered for the rest of
        # the run so they stay the priority kill target even on turns
        # where they're doing something other than summoning. Killing the
        # summoner is what stops the respawn loop; whacking the adds it
        # keeps producing doesn't.
        self._known_summoners: set[str] = set()

    def _observe_summoners(self, enemies: list[dict]) -> None:
        for e in enemies:
            if _is_summoning(e):
                base = _enemy_base_id(e.get("entity_id"))
                if base:
                    self._known_summoners.add(base)

    def _is_known_summoner(self, enemy: dict) -> bool:
        return _enemy_base_id(enemy.get("entity_id")) in self._known_summoners

    def decide(self, state: dict) -> Action | None:
        state_type = state.get("state_type")
        handler = getattr(self, f"_decide_{state_type}", None)
        if handler is None:
            return None
        return handler(state)

    # ------------------------------------------------------------------ #
    # Combat
    # ------------------------------------------------------------------ #
    def _decide_monster(self, state):
        return self._decide_combat(state)

    def _decide_elite(self, state):
        return self._decide_combat(state)

    def _decide_boss(self, state):
        return self._decide_combat(state)

    def _decide_combat(self, state) -> Action | None:
        player = state.get("player") or {}
        battle = state.get("battle") or {}
        hand = player.get("hand") or []
        energy = player.get("energy", 0)
        enemies = [e for e in battle.get("enemies", []) if e.get("hp", 0) > 0]
        self._observe_summoners(enemies)

        playable = [c for c in hand if c.get("can_play") and _cost_int(c.get("cost"), energy) <= energy]
        if not playable or not enemies:
            return ("end_turn", {})

        current_block = player.get("block", 0)
        incoming = sum(_enemy_attack_damage(e) for e in enemies)
        need_block = incoming > current_block

        powers = [c for c in playable if c.get("type") == "Power"]
        if powers:
            card = min(powers, key=lambda c: _cost_int(c.get("cost"), energy))
            return self._play(card, enemies)

        attacks = [c for c in playable if c.get("type") == "Attack"]

        # While a known summoner (Fogmog, Decimillipede, Obscura/Parafright,
        # ...) is alive, it's the only thing worth spending an attack card
        # to kill -- its adds respawn, so a turn spent finishing one off is
        # usually wasted effort that doesn't move the fight toward ending.
        # Restrict every kill/target consideration below to summoners-only
        # in that case; block (below) handles the adds' damage instead.
        # Once no summoner is left alive, everything reverts to normal.
        summoners_alive = [e for e in enemies if self._is_known_summoner(e)]
        kill_targets = summoners_alive if summoners_alive else enemies

        # A dead enemy deals no damage at all. If something we're going to
        # take a hit from this turn is killable right now, and killing it
        # leaves us no worse off (HP-loss-wise) than just defending would,
        # take the kill instead of turtling.
        if attacks:
            attackers = sorted(
                (e for e in kill_targets if _is_attacking(e) or self._is_known_summoner(e)),
                key=lambda e: -_enemy_attack_damage(e),
            )
            for enemy in attackers:
                combo = _greedy_kill_combo(attacks, _effective_hp(enemy), energy, enemy.get("status"))
                if combo is None:
                    continue
                combo_cards, combo_cost = combo
                remaining_energy = energy - combo_cost
                remaining_incoming = incoming - _enemy_attack_damage(enemy)

                hp_loss_if_kill = max(0, remaining_incoming
                                       - (current_block + _estimate_defense(playable, remaining_energy)))
                hp_loss_if_block = max(0, incoming
                                        - (current_block + _estimate_defense(playable, energy)))

                if hp_loss_if_kill <= hp_loss_if_block:
                    return self._play(combo_cards[0], enemies, prefer_target=enemy)

        if need_block:
            # Real Block, or an enemy Strength-debuff (see _defense_value) --
            # the latter needs a target: the debuffed enemy for a single-
            # target card (Mangle), or none for an AoE one (Piercing Wail),
            # which _play's default targeting already handles correctly.
            defenders = [c for c in playable if _defense_value(c) > 0]
            if defenders:
                card = max(defenders, key=_defense_value)
                target = _biggest_threat(kill_targets) if card.get("target_type") == "AnyEnemy" else None
                return self._play(card, enemies, prefer_target=target)

        if attacks:
            # Prefer a lethal finisher (cheapest sufficient card, see
            # _greedy_kill_combo) over raw damage -- the kill-vs-block check
            # above already handled currently-attacking targets, this also
            # catches non-attacking ones (e.g. finishing a low-HP enemy
            # that isn't currently telegraphing anything threatening).
            for enemy in sorted(kill_targets, key=lambda e: e.get("hp", 0)):
                combo = _greedy_kill_combo(attacks, _effective_hp(enemy), energy, enemy.get("status"))
                if combo is not None:
                    return self._play(combo[0][0], enemies, prefer_target=enemy)
            # No lethal target: pick one (a known summoner if one's alive,
            # else biggest threat) to chip away at, then the best card
            # against *that* target, since Vulnerable makes "best card"
            # target-dependent.
            target = _biggest_threat(kill_targets)
            card = max(attacks, key=lambda c: _card_damage(c, target.get("status") if target else None)
                       / max(_cost_int(c.get("cost"), energy), 1))
            return self._play(card, enemies, prefer_target=target)

        skills = [c for c in playable if c.get("type") == "Skill"]
        if skills:
            return self._play(skills[0], enemies)

        return self._play(playable[0], enemies)

    def _play(self, card: dict, enemies: list[dict], prefer_target: dict | None = None) -> Action:
        target = None
        if card.get("target_type") == "AnyEnemy":
            if prefer_target is not None:
                target = prefer_target.get("entity_id")
            else:
                summoners = [e for e in enemies if self._is_known_summoner(e)]
                biggest = _biggest_threat(summoners) if summoners else _biggest_threat(enemies)
                target = biggest.get("entity_id") if biggest else enemies[0].get("entity_id")
        return ("play_card", {"card_index": card["index"], "target": target})

    def _decide_hand_select(self, state) -> Action | None:
        hs = state.get("hand_select") or {}
        cards = hs.get("cards") or hs.get("hand") or []
        if hs.get("can_confirm"):
            return ("combat_confirm_selection", {})
        if cards:
            return ("combat_select_card", {"card_index": cards[0].get("index", 0)})
        return None

    # ------------------------------------------------------------------ #
    # Map / progression
    # ------------------------------------------------------------------ #
    def _decide_map(self, state) -> Action | None:
        m = state.get("map") or {}
        options = m.get("next_options") or []
        if not options:
            return None
        player = state.get("player")
        hp = _hp_frac(player)
        gold = (player or {}).get("gold", 0)

        def score(opt):
            t = opt.get("type")
            if t == "RestSite" and hp < REST_LOW_HP_THRESHOLD:
                return -1
            if t == "Elite" and hp < ELITE_SAFE_HP_THRESHOLD:
                return 99  # avoid elites while low HP
            if t == "Merchant" or t == "Shop":
                return 1 if gold > 50 else 5
            try:
                return NODE_TYPE_PRIORITY.index(t)
            except ValueError:
                return len(NODE_TYPE_PRIORITY)

        best = min(options, key=score)
        return ("choose_map_node", {"index": best["index"]})

    def _decide_rewards(self, state) -> Action | None:
        r = state.get("rewards") or {}
        items = r.get("items") or []
        # A potion reward silently no-ops (the mod reports "ok" but nothing
        # happens) once your belt is full -- claiming it forever is a stuck
        # loop, so skip it and take other rewards, or move on entirely.
        potions_full = _potions_full(state.get("player"))
        claimable = [i for i in items if not (potions_full and i.get("type") == "potion")]
        if claimable:
            return ("claim_reward", {"index": claimable[0]["index"]})
        if r.get("can_proceed"):
            return ("proceed", {})
        return None

    def _decide_card_reward(self, state) -> Action | None:
        cr = state.get("card_reward") or {}
        cards = cr.get("cards") or []
        if not cards:
            return ("proceed", {}) if cr.get("can_proceed") else None

        player = state.get("player") or {}
        character = player.get("character")
        deck = player.get("deck")  # requires the STS2MCP build with player.deck exposed

        if deck:
            block_count = sum(1 for c in deck
                               if any(k.get("name") == "Block" for k in c.get("keywords") or []))
            block_fraction = block_count / len(deck)
            if block_fraction < BLOCK_DECK_FRACTION_THRESHOLD:
                block_cards = [c for c in cards
                                if any(k.get("name") == "Block" for k in c.get("keywords") or [])]
                if block_cards:
                    best = max(block_cards, key=lambda c: self.tier_list.rank(c.get("id"), character))
                    return ("select_card_reward", {"card_index": best["index"]})

        best = max(cards, key=lambda c: self.tier_list.rank(c.get("id"), character))
        if self.tier_list.rank(best.get("id"), character) == 0:
            # No Untapped tier data for any offered card (app not
            # installed, or these cards aren't in its lists) -- fall back
            # to the old rarity-based pick rather than choosing arbitrarily.
            best = max(cards, key=lambda c: RARITY_WEIGHT.get(c.get("rarity"), 1) + (1 if c.get("is_upgraded") else 0))
        return ("select_card_reward", {"card_index": best["index"]})

    def _decide_shop(self, state) -> Action | None:
        s = state.get("shop") or {}
        items = s.get("items") or []
        potions_full = _potions_full(state.get("player"))
        buyable = [i for i in items if i.get("is_stocked") and i.get("can_afford")
                   and not (potions_full and i.get("category") == "potion")]
        for category in ("relic", "card", "potion"):
            for item in buyable:
                if item.get("category") == category:
                    return ("shop_purchase", {"index": item["index"]})
        # NOTE: shop's `can_proceed` only reflects the Proceed button while the
        # purchase-inventory overlay is open, where it's always false. The mod's
        # `proceed` action already closes that overlay before clicking Proceed,
        # so call it unconditionally here rather than gating on can_proceed
        # (unlike rewards/rest_site/treasure, where can_proceed is accurate).
        return ("proceed", {})

    def _decide_rest_site(self, state) -> Action | None:
        rs = state.get("rest_site") or {}
        options = [o for o in (rs.get("options") or []) if o.get("is_enabled")]
        if not options:
            return ("proceed", {}) if rs.get("can_proceed") else None
        hp = _hp_frac(state.get("player"))
        want = "rest" if hp < REST_LOW_HP_THRESHOLD else "upgrade"
        for o in options:
            if o.get("id") == want:
                return ("choose_rest_option", {"index": o["index"]})
        return ("choose_rest_option", {"index": options[0]["index"]})

    def _decide_event(self, state) -> Action | None:
        ev = state.get("event") or {}
        if ev.get("in_dialogue"):
            return ("advance_dialogue", {})
        options = [o for o in (ev.get("options") or []) if not o.get("is_locked")]
        if not options:
            return None
        non_proceed = [o for o in options if not o.get("is_proceed")]
        pool = non_proceed if non_proceed else options

        if ev.get("event_id") == "NEOW":
            # Neow's options are high-variance, run-defining, and mostly
            # not cards (curses, HP/gold trades, big relics) -- there's no
            # tier list or simple heuristic for these yet, and always
            # taking the first listed option is just a different kind of
            # arbitrary. Pick uniformly at random instead of pretending
            # either is an informed choice.
            chosen = random.choice(pool)
        else:
            chosen = pool[0]
        return ("choose_event_option", {"index": chosen["index"]})

    def _decide_relic_select(self, state) -> Action | None:
        rsel = state.get("relic_select") or {}
        relics = rsel.get("relics") or []
        if relics:
            return ("select_relic", {"index": relics[0]["index"]})
        if rsel.get("can_skip"):
            return ("skip_relic_selection", {})
        return None

    def _decide_treasure(self, state) -> Action | None:
        t = state.get("treasure") or {}
        relics = t.get("relics") or []
        if relics:
            return ("claim_treasure_relic", {"index": relics[0]["index"]})
        return ("proceed", {}) if t.get("can_proceed") else None

    def _decide_card_select(self, state) -> Action | None:
        cs = state.get("card_select") or {}
        if cs.get("can_confirm"):
            return ("confirm_selection", {})
        cards = cs.get("cards") or []
        if cards:
            return ("select_card", {"index": cards[0]["index"]})
        if cs.get("can_cancel"):
            return ("cancel_selection", {})
        return None

    def _decide_crystal_sphere(self, state) -> Action | None:
        # Under-documented minigame; best-effort only.
        return ("crystal_sphere_proceed", {})

    def _decide_bundle_select(self, state) -> Action | None:
        # e.g. the Scroll Boxes relic: "choose 1 of 2 packs of cards" --
        # choosing a bundle grants every card in it, not just one, so score
        # each bundle by the total tier rank of its cards rather than just
        # its single best card.
        bs = state.get("bundle_select") or {}
        if bs.get("can_confirm"):
            return ("confirm_bundle_selection", {})
        bundles = bs.get("bundles") or []
        if bundles:
            character = (state.get("player") or {}).get("character")
            score = lambda b: sum(self.tier_list.rank(c.get("id"), character) for c in b.get("cards") or [])
            best = max(bundles, key=score)
            return ("select_bundle", {"index": best["index"]})
        if bs.get("can_cancel"):
            return ("cancel_bundle_selection", {})
        return None
