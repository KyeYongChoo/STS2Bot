"""Per-encounter combat strategy: this is where fight-specific discussion
and tuning lives, kept out of heuristic.py so that file stays the generic
engine. Each fight either uses one of the reusable strategies below (most
early trash fights share the same shape -- see `decide_favor_damage_solo`)
or gets its own bespoke `decide_*` function once its mechanics need
something the reusable ones don't cover (e.g. Bygone Effigy's Sleep/Wake
setup turn, Vantom's Slippery stacks favoring multi-hit over big-hit).

`build_overrides(strategy)` is called once from HeuristicStrategy.__init__
and returns the `{Encounter.name: handler}` dict it stores as
`_encounter_overrides`. Every handler here takes `strategy` (the
HeuristicStrategy instance, for `_play`/`_is_known_summoner`/etc.) as its
first argument -- `build_overrides` binds it via functools.partial so the
dispatch call site in heuristic.py's `_decide_combat` (which calls
`override(state, enemies, encounter)`) doesn't need to know or care.

Imports combat-math primitives (`_cost_int`, `_effective_hp`,
`_greedy_kill_combo`, etc.) back from heuristic.py rather than duplicating
them -- those are generic enough to belong there, this file is just their
fight-specific caller. heuristic.py imports this module lazily (inside
__init__, not at module level) specifically so this back-import doesn't
create a circular import.
"""
from __future__ import annotations

import functools
from typing import Callable

from sts2bot.encounters import Encounter, base_entity_id
from sts2bot.heuristic import (
    Action,
    HeuristicStrategy,
    _best_sufficient_defender,
    _biggest_threat,
    _card_damage,
    _cost_int,
    _defense_value,
    _effective_hp,
    _enemy_attack_damage,
    _estimate_defense,
    _greedy_kill_combo,
    _kill_probability_next_turn,
)

# STS2's base draw is 5 cards/turn for every character seen so far; there's
# no draw-count field in the API to read this from, so it's hardcoded here.
# Re-check this if a class with a different base draw shows up.
ASSUMED_DRAW = 5
LETHAL_CONFIDENCE = 0.8  # P(next turn's draw can solo-kill it) needed to bank a free block turn
DEFAULT_HP_TRADE_CAP = 3  # max HP willing to lose this turn in exchange for extra damage, absent a fight-specific value


def _decide_favor_damage(strategy: HeuristicStrategy, state: dict, enemies: list[dict], target: dict,
                          incoming: int, hp_trade_cap: int,
                          lethal_confidence: float = LETHAL_CONFIDENCE,
                          assumed_draw: int = ASSUMED_DRAW) -> Action | None:
    """Shared core of every "favor damage over block, within an accepted
    HP-trade cap" strategy: favor damage over block against `target`.
    Specifically: accept up to `hp_trade_cap` HP of `incoming` damage this
    turn in exchange for dealing more damage, UNLESS this turn can be fully
    blocked for free (0 HP loss) AND next turn's draw has a >=
    `lethal_confidence` chance of a clean kill on `target` -- in that case
    bank the free turn instead of gambling HP for no real benefit.

    `target`/`incoming` are supplied by the caller rather than computed
    here, so this same core serves both a solo fight (target = the one
    enemy, incoming = its attack) and a multi-enemy fight with its own
    targeting rule, e.g. decide_group_of_slimes's fixed kill order
    (target = whichever enemy is next in that order, incoming = every
    enemy's attack summed).

    Re-evaluated on every call (once per card played this turn, like the
    generic combat loop), so "block just enough to stay under the HP-loss
    cap, then spend the rest on damage" falls out naturally: each call
    recomputes current_block/hp_loss against the *current* hand, converging
    turn-by-turn rather than needing to plan the whole turn up front.
    """
    player = state.get("player") or {}
    hand = player.get("hand") or []
    energy = player.get("energy", 0)
    max_energy = player.get("max_energy", energy)
    current_block = player.get("block", 0)

    playable = [c for c in hand if c.get("can_play") and _cost_int(c.get("cost"), energy) <= energy]
    if not playable:
        return ("end_turn", {})

    # Powers first, same priority as the generic combat loop -- nothing
    # about this matchup makes that a bad idea.
    powers = [c for c in playable if c.get("type") == "Power"]
    if powers:
        card = min(powers, key=lambda c: _cost_int(c.get("cost"), energy))
        return strategy._play(card, enemies)

    attacks = [c for c in playable if c.get("type") == "Attack"]
    effective_hp = _effective_hp(target)

    # Lethal right now on the priority target always wins -- no reason to
    # weigh anything else.
    combo = _greedy_kill_combo(attacks, effective_hp, energy, target.get("status"))
    if combo is not None:
        return strategy._play(combo[0][0], enemies, prefer_target=target)

    defenders = [c for c in playable if _defense_value(c) > 0]
    hp_loss_if_full_block = max(0, incoming - (current_block + _estimate_defense(playable, energy)))

    if hp_loss_if_full_block == 0:
        draw_pile = player.get("draw_pile") or []
        discard_pile = player.get("discard_pile") or []
        p_lethal_next_turn = _kill_probability_next_turn(
            draw_pile, discard_pile, assumed_draw, max_energy, effective_hp, target.get("status"))
        if p_lethal_next_turn >= lethal_confidence:
            if defenders:
                card = max(defenders, key=_defense_value)
                play_target = _biggest_threat(enemies) if card.get("target_type") == "AnyEnemy" else None
                return strategy._play(card, enemies, prefer_target=play_target)
            return ("end_turn", {})

    # Default: favor damage. Attack now if doing so (with no further
    # blocking this call) keeps this turn's HP loss within the accepted
    # trade cap, or if there's nothing left to block with anyway.
    # Otherwise play just enough block to get back under the cap (the best
    # single defender that covers the gap -- preferring one that also
    # deals bonus damage, see _best_sufficient_defender) so the rest of
    # this turn's energy stays free for damage -- the next call re-checks
    # with the improved current_block, so blocking stops as soon as the
    # cap is satisfied.
    if attacks:
        hp_loss_if_no_more_block = max(0, incoming - current_block)
        if hp_loss_if_no_more_block <= hp_trade_cap or not defenders:
            card = max(attacks, key=lambda c: _card_damage(c, target.get("status"))
                       / max(_cost_int(c.get("cost"), energy), 1))
            return strategy._play(card, enemies, prefer_target=target)

    if defenders:
        needed_block = incoming - current_block - hp_trade_cap
        card = _best_sufficient_defender(defenders, needed_block, energy, target.get("status"))
        if card is not None:
            play_target = _biggest_threat(enemies) if card.get("target_type") == "AnyEnemy" else None
            return strategy._play(card, enemies, prefer_target=play_target)

    return None  # nothing but e.g. a non-damage Skill left -- defer to the generic path


def decide_favor_damage_solo(strategy: HeuristicStrategy, state: dict, enemies: list[dict], encounter: Encounter,
                              hp_trade_cap: int = DEFAULT_HP_TRADE_CAP,
                              lethal_confidence: float = LETHAL_CONFIDENCE,
                              assumed_draw: int = ASSUMED_DRAW) -> Action | None:
    """_decide_favor_damage against the fight's one enemy. Registered
    per-fight via HP_TRADE_CAP below, e.g. A Lone Nibbit (cap 3), Fuzzy
    Wurm Crawler (cap 4), Shrinker Beetle (cap 3) -- see each Encounter's
    notes in sts2bot/encounters.py for why that specific cap."""
    enemy = enemies[0]
    return _decide_favor_damage(strategy, state, enemies, enemy, _enemy_attack_damage(enemy),
                                 hp_trade_cap, lethal_confidence, assumed_draw)


# Act 1 Overgrowth encounters using decide_favor_damage_solo, and each
# one's accepted HP-trade cap. Add an entry here (no code needed) for any
# further fight that fits this same "ramps/gets worse over time, otherwise
# nothing special" shape; give it its own decide_* function + registration
# below instead when it needs different logic.
HP_TRADE_CAP: dict[str, int] = {
    "A Lone Nibbit": 3,
    "Fuzzy Wurm Crawler": 4,
    "Shrinker Beetle": 3,
}


# Group of Slimes (Act 1 Overgrowth, easy pool): fixed kill order rather
# than generic biggest-threat targeting, and an HP-trade cap that changes
# once the Medium Slime dies. See decide_group_of_slimes.
_GROUP_OF_SLIMES_KILL_ORDER: tuple[str | frozenset[str], ...] = (
    "LEAF_SLIME_S", frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "TWIG_SLIME_S",
)
_MEDIUM_SLIME_IDS = frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"})
GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES = 4
GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES = 0  # no HP trading once the bigger threat is gone -- just block


def _group_of_slimes_target(enemies: list[dict]) -> dict:
    by_id = {base_entity_id(e.get("entity_id")): e for e in enemies}
    for slot in _GROUP_OF_SLIMES_KILL_ORDER:
        candidates = slot if isinstance(slot, frozenset) else {slot}
        for candidate_id in candidates:
            if candidate_id in by_id:
                return by_id[candidate_id]
    return enemies[0]  # shouldn't happen for this encounter -- never crash over it


def decide_group_of_slimes(strategy: HeuristicStrategy, state: dict, enemies: list[dict],
                            encounter: Encounter) -> Action | None:
    """Group of Slimes: fixed kill order -- Leaf Slime -> Medium Slime ->
    Twig Slime -- instead of generic biggest-threat targeting. HP-trade cap
    is GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES (favor damage, same
    shape as decide_favor_damage_solo) while the Medium Slime is still
    alive, then drops to GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES (0:
    pure block, no HP trading at all) once it's dead -- the remaining Twig
    Slime isn't worth any risk once the bigger threat is gone."""
    target = _group_of_slimes_target(enemies)
    medium_alive = any(base_entity_id(e.get("entity_id")) in _MEDIUM_SLIME_IDS for e in enemies)
    hp_trade_cap = (GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES if medium_alive
                     else GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES)
    incoming = sum(_enemy_attack_damage(e) for e in enemies)
    return _decide_favor_damage(strategy, state, enemies, target, incoming, hp_trade_cap)


# Encounters with their own bespoke decide_* function (not just a
# HP_TRADE_CAP entry) -- each takes (strategy, state, enemies, encounter).
CUSTOM_OVERRIDES: dict[str, Callable[[HeuristicStrategy, dict, list[dict], Encounter], Action | None]] = {
    "Group of Slimes": decide_group_of_slimes,
}


def build_overrides(strategy: HeuristicStrategy) -> dict[str, Callable[[dict, list[dict], Encounter], Action | None]]:
    overrides: dict[str, Callable[[dict, list[dict], Encounter], Action | None]] = {
        name: functools.partial(decide_favor_damage_solo, strategy, hp_trade_cap=cap)
        for name, cap in HP_TRADE_CAP.items()
    }
    for name, fn in CUSTOM_OVERRIDES.items():
        overrides[name] = functools.partial(fn, strategy)
    return overrides
