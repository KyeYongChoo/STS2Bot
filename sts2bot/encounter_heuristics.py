"""Per-encounter combat strategy: this is where fight-specific discussion
and tuning lives, kept out of heuristic.py so that file stays the generic
engine. Each fight either uses one of the reusable strategies below or gets
its own bespoke `decide_*` function once its mechanics need something the
reusable ones don't cover (e.g. Bygone Effigy's Slow card sequencing, The
Kin's turn-1 target choice).

Reusable building blocks:
  - `_decide_favor_damage`: favor damage over block against one target,
    accepting up to an HP-trade cap of damage per turn. "Damage race" in the
    encounter notes = this with DAMAGE_RACE_CAP; "Damage race++" =
    DAMAGE_RACE_PLUS_CAP; "accept any HP trade" = ANY_HP_TRADE.
  - `_decide_kill_by_deadline`: "kill X by turn N". Simulates the coming
    turns' draws from the known draw/discard piles; if X dies by turn N with
    >= KILL_BY_DEADLINE_CONFIDENCE even with no more damage this turn, the
    rest of this turn goes to block; otherwise it damage-races.
  - kill orders (`decide_kill_order`) for fights whose notes name who to
    kill first.

`build_overrides(strategy)` is called once from HeuristicStrategy.__init__
and returns the `{Encounter.name: handler}` dict it stores as
`_encounter_overrides`. Every handler here takes `strategy` (the
HeuristicStrategy instance, for `_play`/`combat_memory`/etc.) as its first
argument -- `build_overrides` binds it via functools.partial so the
dispatch call site in heuristic.py's `_decide_combat` (which calls
`override(state, enemies, encounter)`) doesn't need to know or care. A
handler returning None falls through to the generic combat logic for that
decision.

Imports combat-math primitives back from heuristic.py rather than
duplicating them -- heuristic.py imports this module lazily (inside
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
    _card_hit_damage,
    _cost_int,
    _defense_value,
    _effective_hp,
    _enemy_attack_damage,
    _estimate_defense,
    _greedy_kill_combo,
    _is_attacking,
    _kill_probability_next_turn,
    _slippery_play_order,
    _slippery_stacks,
    _status_amount,
    expected_damage_over_turns,
    kill_probability_by_turns,
)

# STS2's base draw is 5 cards/turn for every character seen so far; there's
# no draw-count field in the API to read this from, so it's hardcoded here.
# Re-check this if a class with a different base draw shows up.
ASSUMED_DRAW = 5
LETHAL_CONFIDENCE = 0.8  # P(next turn's draw can solo-kill it) needed to bank a free block turn
DEFAULT_HP_TRADE_CAP = 3  # max HP willing to lose this turn in exchange for extra damage, absent a fight-specific value
DAMAGE_RACE_CAP = 6        # "Damage race" in the encounter notes
DAMAGE_RACE_PLUS_CAP = 10  # "Damage race++"
ANY_HP_TRADE = 999         # "accept any HP trade"
KILL_BY_DEADLINE_CONFIDENCE = 0.85  # P(kill by the deadline) at which the rest of a turn can go to block
SLIPPERY_SMALL_HIT = 9  # per-hit damage at or below this is a "small" hit worth spending on Slippery stacks


def _incoming(enemies: list[dict]) -> int:
    return sum(_enemy_attack_damage(e) for e in enemies)


def _by_base_id(enemies: list[dict], *ids: str) -> list[dict]:
    return [e for e in enemies if base_entity_id(e.get("entity_id")) in ids]


def _first_in_order(enemies: list[dict], order: tuple[str | frozenset[str], ...]) -> dict:
    """First alive enemy by kill order (each slot an id or a set of
    interchangeable ids; ties within a slot go to the lowest effective HP).
    Enemies not named in the order come last, lowest HP first."""
    for slot in order:
        ids = slot if isinstance(slot, frozenset) else {slot}
        matches = [e for e in enemies if base_entity_id(e.get("entity_id")) in ids]
        if matches:
            return min(matches, key=_effective_hp)
    return min(enemies, key=_effective_hp)


def _decide_favor_damage(strategy: HeuristicStrategy, state: dict, enemies: list[dict], target: dict,
                          incoming: int, hp_trade_cap: int,
                          lethal_confidence: float = LETHAL_CONFIDENCE,
                          assumed_draw: int = ASSUMED_DRAW,
                          require_free_block: bool = True) -> Action | None:
    """Shared core of every "favor damage over block, within an accepted
    HP-trade cap" strategy: favor damage over block against `target`.
    Specifically: accept up to `hp_trade_cap` HP of `incoming` damage this
    turn in exchange for dealing more damage, UNLESS next turn's draw has a
    >= `lethal_confidence` chance of a clean kill on `target` -- in that
    case bank this turn defensively instead of gambling HP for no real
    benefit. By default that only applies when this turn can be fully
    blocked for free (0 HP loss); `require_free_block=False` drops that
    condition (Byrdonis: "unless you're sure you can kill next turn").

    `target`/`incoming` are supplied by the caller rather than computed
    here, so this same core serves both a solo fight (target = the one
    enemy, incoming = its attack) and a multi-enemy fight with its own
    targeting rule (target = whichever enemy is next in that order,
    incoming = every enemy's attack summed). `target` may also be a
    modified copy of an enemy, e.g. Ceremonial Beast's HP-until-stun.

    Against a Slippery target, attacks go smallest-hit-first, so stacks are
    stripped by cheap hits before the big ones land.

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
    # about these matchups makes that a bad idea.
    powers = [c for c in playable if c.get("type") == "Power"]
    if powers:
        card = min(powers, key=lambda c: _cost_int(c.get("cost"), energy))
        return strategy._play(card, enemies)

    attacks = [c for c in playable if c.get("type") == "Attack"]
    status = target.get("status")
    effective_hp = _effective_hp(target)

    # Lethal right now on the priority target always wins -- no reason to
    # weigh anything else.
    combo = _greedy_kill_combo(attacks, effective_hp, energy, status)
    if combo is not None:
        return strategy._play(combo[0][0], enemies, prefer_target=target)

    defenders = [c for c in playable if _defense_value(c) > 0]
    hp_loss_if_full_block = max(0, incoming - (current_block + _estimate_defense(playable, energy)))

    if hp_loss_if_full_block == 0 or not require_free_block:
        draw_pile = player.get("draw_pile") or []
        discard_pile = player.get("discard_pile") or []
        p_lethal_next_turn = _kill_probability_next_turn(
            draw_pile, discard_pile, assumed_draw, max_energy, effective_hp, status)
        if p_lethal_next_turn >= lethal_confidence:
            if defenders and incoming > current_block:
                card = max(defenders, key=_defense_value)
                play_target = _biggest_threat(enemies) if card.get("target_type") == "AnyEnemy" else None
                return strategy._play(card, enemies, prefer_target=play_target)
            if require_free_block or not attacks:
                return ("end_turn", {})
            # Blocked enough and not required to hold back: spend the rest on damage.

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
            card = _pick_attack(attacks, status, energy)
            return strategy._play(card, enemies, prefer_target=target)

    if defenders:
        needed_block = incoming - current_block - hp_trade_cap
        card = _best_sufficient_defender(defenders, needed_block, energy, status)
        if card is not None:
            play_target = _biggest_threat(enemies) if card.get("target_type") == "AnyEnemy" else None
            return strategy._play(card, enemies, prefer_target=play_target)

    return None  # nothing but e.g. a non-damage Skill left -- defer to the generic path


def _pick_attack(attacks: list[dict], status: list[dict] | None, energy: int) -> dict:
    """Best attack to play next into a target: highest damage per energy,
    except against Slippery, where it's the smallest hit (strip stacks
    cheaply first; big hits wait until the stacks are gone)."""
    if _slippery_stacks(status) > 0:
        small = [c for c in attacks if _card_hit_damage(c, status) <= SLIPPERY_SMALL_HIT]
        return _slippery_play_order(small or attacks, status)[0]
    return max(attacks, key=lambda c: _card_damage(c, status) / max(_cost_int(c.get("cost"), energy), 1))


def _decide_kill_by_deadline(strategy: HeuristicStrategy, state: dict, enemies: list[dict], target: dict,
                              deadline_round: int, behind_cap: int,
                              confidence: float = KILL_BY_DEADLINE_CONFIDENCE) -> Action | None:
    """"Kill `target` by turn `deadline_round`": if the coming turns' draws
    (Monte Carlo over the known piles, see kill_probability_by_turns) kill
    it by the deadline with >= `confidence` even if this turn deals no more
    damage, this turn is on schedule -- block fully, attack with what's
    left. Otherwise it's behind: damage-race with `behind_cap`. Re-checked
    every card, so a good draw this turn banks damage and the rest goes to
    block, while a bad one keeps racing. Returns None once the deadline
    has passed (caller decides what "after" means)."""
    round_no = (state.get("battle") or {}).get("round") or 1
    if round_no > deadline_round:
        return None
    player = state.get("player") or {}
    p = kill_probability_by_turns(
        player.get("draw_pile") or [], player.get("discard_pile") or [], player.get("hand") or [],
        _effective_hp(target), deadline_round - round_no, player.get("max_energy", player.get("energy", 0)),
        ASSUMED_DRAW, target.get("status"))
    cap = 0 if p >= confidence else behind_cap
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), cap)


# --------------------------------------------------------------------- #
# Single-enemy fights
# --------------------------------------------------------------------- #
def decide_favor_damage_solo(strategy: HeuristicStrategy, state: dict, enemies: list[dict], encounter: Encounter,
                              hp_trade_cap: int = DEFAULT_HP_TRADE_CAP,
                              lethal_confidence: float = LETHAL_CONFIDENCE,
                              assumed_draw: int = ASSUMED_DRAW) -> Action | None:
    """_decide_favor_damage against the fight's one enemy. Registered
    per-fight via HP_TRADE_CAP below -- see each Encounter's notes in
    sts2bot/encounters.py for why that specific cap."""
    enemy = enemies[0]
    return _decide_favor_damage(strategy, state, enemies, enemy, _enemy_attack_damage(enemy),
                                 hp_trade_cap, lethal_confidence, assumed_draw)


# Encounters using decide_favor_damage_solo, and each one's accepted
# HP-trade cap. Add an entry here (no code needed) for any further fight
# that fits this same "ramps/gets worse over time, otherwise nothing
# special" shape.
HP_TRADE_CAP: dict[str, int] = {
    "A Lone Nibbit": 3,
    "Vantom": DEFAULT_HP_TRADE_CAP,  # the Slippery handling in _decide_favor_damage is the point
}


def decide_fuzzy_wurm_crawler(strategy, state, enemies, encounter) -> Action | None:
    """Kill by turn 4; block with whatever's spare while on schedule."""
    return _decide_kill_by_deadline(strategy, state, enemies, enemies[0], 4, DAMAGE_RACE_CAP)


def decide_byrdonis(strategy, state, enemies, encounter) -> Action | None:
    """Gains Strength every turn: accept any HP trade for damage, unless
    next turn's draw is near-certain to kill it -- then block what's
    needed and attack with the rest."""
    return _decide_favor_damage(strategy, state, enemies, enemies[0], _incoming(enemies), ANY_HP_TRADE,
                                lethal_confidence=0.9, require_free_block=False)


def decide_phrog_parasite(strategy, state, enemies, encounter) -> Action | None:
    """Kill the Parasite by turn 5 (the Monte Carlo draw simulation covers
    the Infections it shuffles in). When it dies, its 4 Wrigglers match
    Dense Vegetation and that strategy takes over."""
    phrog = _by_base_id(enemies, "PHROG_PARASITE")
    if not phrog:
        return None
    return _decide_kill_by_deadline(strategy, state, enemies, phrog[0], 5, DAMAGE_RACE_CAP)


def decide_ceremonial_beast(strategy, state, enemies, encounter) -> Action | None:
    """Damage-race to the Plow threshold (stun + lose all Strength), then
    play normally. The race targets a copy of the Beast whose HP is just
    the damage left until the stun, so "lethal" means "stuns it"."""
    beast = _by_base_id(enemies, "CEREMONIAL_BEAST")
    if not beast:
        return None
    plow = _status_amount(beast[0].get("status"), "Plow")
    if plow <= 0:
        return None
    until_stun = dict(beast[0], hp=beast[0].get("hp", 0) - plow)
    if until_stun["hp"] <= 0:
        return None
    return _decide_favor_damage(strategy, state, enemies, until_stun, _incoming(enemies), DAMAGE_RACE_CAP)


def decide_bygone_effigy(strategy, state, enemies, encounter) -> Action | None:
    """Slow: every card played this turn makes Attacks deal +10% to it, so
    play the non-attack cards you can afford alongside your best attacks
    *first*, then attacks smallest to biggest. Asleep: the same, all-out.
    Awake: all-out with any HP trade, unless next turn's draw likely kills
    it -- then block first and attack with what's left."""
    effigy = _by_base_id(enemies, "BYGONE_EFFIGY")
    if not effigy:
        return None
    effigy = effigy[0]
    player = state.get("player") or {}
    hand = player.get("hand") or []
    energy = player.get("energy", 0)
    current_block = player.get("block", 0)
    status = effigy.get("status")
    playable = [c for c in hand if c.get("can_play") and _cost_int(c.get("cost"), energy) <= energy]
    if not playable:
        return ("end_turn", {})

    attacks = [c for c in playable if c.get("type") == "Attack"]
    combo = _greedy_kill_combo(attacks, _effective_hp(effigy), energy, status)
    if combo is not None:
        return strategy._play(combo[0][0], enemies, prefer_target=effigy)

    powers = [c for c in playable if c.get("type") == "Power"]
    if powers:
        return strategy._play(min(powers, key=lambda c: _cost_int(c.get("cost"), energy)), enemies)

    incoming = _incoming(enemies)
    asleep = any(i.get("type") == "Sleep" for i in effigy.get("intents", []))
    if not asleep and incoming > current_block:
        p_lethal_next_turn = _kill_probability_next_turn(
            player.get("draw_pile") or [], player.get("discard_pile") or [], ASSUMED_DRAW,
            player.get("max_energy", energy), _effective_hp(effigy), status)
        defenders = [c for c in playable if _defense_value(c) > 0 and c.get("type") != "Attack"]
        if p_lethal_next_turn >= LETHAL_CONFIDENCE and defenders:
            return strategy._play(max(defenders, key=_defense_value), enemies)

    # Plan the attacks for this turn (best damage within energy), then
    # spend leftover energy on other cards *before* them.
    plan = _best_attack_plan(attacks, energy, status)
    leftover = energy - sum(_cost_int(c.get("cost"), energy) for c in plan)
    extras = [c for c in playable if c.get("type") != "Attack" and _cost_int(c.get("cost"), energy) <= leftover]
    if extras:
        card = max(extras, key=lambda c: (_cost_int(c.get("cost"), energy) == 0, _defense_value(c)))
        return strategy._play(card, enemies, prefer_target=effigy)
    if plan:
        return strategy._play(min(plan, key=lambda c: _card_damage(c, status)), enemies, prefer_target=effigy)
    return None


def _best_attack_plan(attacks: list[dict], energy: int, status: list[dict] | None) -> list[dict]:
    """The subset of `attacks` dealing the most damage within `energy`."""
    best: list[dict] = []
    best_damage = 0
    n = len(attacks)
    for mask in range(1, 1 << n):
        subset = [attacks[i] for i in range(n) if mask >> i & 1]
        if sum(_cost_int(c.get("cost"), energy) for c in subset) > energy:
            continue
        damage = sum(_card_damage(c, status) for c in subset)
        if damage > best_damage:
            best, best_damage = subset, damage
    return best


# --------------------------------------------------------------------- #
# Multi-enemy fights
# --------------------------------------------------------------------- #
def decide_kill_order(strategy, state, enemies, encounter, *, order: tuple[str | frozenset[str], ...],
                      cap: int) -> Action | None:
    """Favor damage against whoever's next in a fixed kill order."""
    target = _first_in_order(enemies, order)
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), cap)


# name -> (kill order, HP-trade cap), straight from each encounter's notes.
KILL_ORDERS: dict[str, tuple[tuple[str | frozenset[str], ...], int]] = {
    "Swarm of Slimes": ((frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}),
                         frozenset({"LEAF_SLIME_S", "TWIG_SLIME_S"})), DEFAULT_HP_TRADE_CAP),
    "Shroom and Slime": ((frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "FLYCONID"), DAMAGE_RACE_CAP),
    "Overgrowth Flora": (("SNAPPING_JAXFRUIT", "FLYCONID"), DAMAGE_RACE_PLUS_CAP),
    "Overgrowth Crawlers": (("SHRINKER_BEETLE", "FUZZY_WURM_CRAWLER"), DAMAGE_RACE_CAP),
}


# Group of Slimes (easy pool): fixed kill order, and an HP-trade cap that
# changes once the Medium Slime dies.
_GROUP_OF_SLIMES_KILL_ORDER: tuple[str | frozenset[str], ...] = (
    "LEAF_SLIME_S", frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "TWIG_SLIME_S",
)
_MEDIUM_SLIME_IDS = frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"})
GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES = 4
GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES = 0  # no HP trading once the bigger threat is gone -- just block


def decide_group_of_slimes(strategy: HeuristicStrategy, state: dict, enemies: list[dict],
                            encounter: Encounter) -> Action | None:
    """Group of Slimes: fixed kill order -- Leaf Slime -> Medium Slime ->
    Twig Slime -- instead of generic biggest-threat targeting. HP-trade cap
    is GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES while the Medium
    Slime is alive, then GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES (0:
    pure block, no HP trading at all) once it's dead -- the remaining Twig
    Slime isn't worth any risk once the bigger threat is gone."""
    target = _first_in_order(enemies, _GROUP_OF_SLIMES_KILL_ORDER)
    medium_alive = bool(_by_base_id(enemies, *_MEDIUM_SLIME_IDS))
    hp_trade_cap = (GROUP_OF_SLIMES_HP_TRADE_CAP_BEFORE_MEDIUM_DIES if medium_alive
                    else GROUP_OF_SLIMES_HP_TRADE_CAP_AFTER_MEDIUM_DIES)
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), hp_trade_cap)


def decide_inklets(strategy, state, enemies, encounter) -> Action | None:
    """Damage race; kill one by turn 2. Focus the left or right Inklet --
    the middle one's turn-2 attack isn't the 10-damage one. Slippery (1
    stack each) is handled by _decide_favor_damage's small-hits-first.
    "Middle" is the middle of the enemy list as first seen (assumed to be
    screen order -- unverified)."""
    memory = strategy.combat_memory
    if "inklet_middle" not in memory and len(enemies) == 3:
        memory["inklet_middle"] = enemies[1].get("entity_id")
    sides = [e for e in enemies if e.get("entity_id") != memory.get("inklet_middle")]
    target = min(sides or enemies, key=_effective_hp)
    if len(enemies) == 3:  # first kill not done yet
        action = _decide_kill_by_deadline(strategy, state, enemies, target, 2, DAMAGE_RACE_CAP)
        if action is not None:
            return action
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), DAMAGE_RACE_CAP)


def decide_pair_of_nibbits(strategy, state, enemies, encounter) -> Action | None:
    """Damage race; kill one by turn 4. Once one dies, the survivor matches
    A Lone Nibbit and that strategy takes over ("normalish rules")."""
    target = min(enemies, key=_effective_hp)
    action = _decide_kill_by_deadline(strategy, state, enemies, target, 4, DAMAGE_RACE_CAP)
    if action is not None:
        return action
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), DAMAGE_RACE_CAP)


def decide_strangler_and_friend(strategy, state, enemies, encounter) -> Action | None:
    """Damage race until one enemy is left (lowest HP first, the fastest
    way down to one), then the generic logic."""
    if len(enemies) <= 1:
        return None
    target = min(enemies, key=_effective_hp)
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), DAMAGE_RACE_CAP)


DENSE_VEGETATION_BLOCK_SLACK = 3  # "full or almost full block": HP loss accepted per turn before attacking


def decide_dense_vegetation(strategy, state, enemies, encounter) -> Action | None:
    """4 Wrigglers (also Phrog Parasite's second phase). Aim to kill one
    every 1-2 turns: kill an attacking Wriggler now if possible (biggest
    hitter first); otherwise set up next turn's kill by hitting a
    *non*-attacking one (lowest HP first). Block to within
    DENSE_VEGETATION_BLOCK_SLACK of the incoming damage, then attack."""
    player = state.get("player") or {}
    energy = player.get("energy", 0)
    attacks = [c for c in player.get("hand") or []
               if c.get("can_play") and c.get("type") == "Attack" and _cost_int(c.get("cost"), energy) <= energy]
    for wriggler in sorted((e for e in enemies if _is_attacking(e)), key=lambda e: -_enemy_attack_damage(e)):
        combo = _greedy_kill_combo(attacks, _effective_hp(wriggler), energy, wriggler.get("status"))
        if combo is not None:
            return strategy._play(combo[0][0], enemies, prefer_target=wriggler)
    idle = [e for e in enemies if not _is_attacking(e)]
    focus = min(idle or enemies, key=_effective_hp)
    return _decide_favor_damage(strategy, state, enemies, focus, _incoming(enemies), DENSE_VEGETATION_BLOCK_SLACK)


KIN_DECISION_ROUND = 8


def decide_the_kin(strategy, state, enemies, encounter) -> Action | None:
    """At the start of the fight, estimate single-target damage through
    round 8 (Monte Carlo over the draw pile). If that's enough to kill the
    Kin Priest, focus the Priest (Followers have Minion and flee when it
    dies); otherwise take the Followers out first, lowest HP first."""
    priest = _by_base_id(enemies, "KIN_PRIEST")
    followers = _by_base_id(enemies, "KIN_FOLLOWER")
    memory = strategy.combat_memory
    if "kin_focus" not in memory and priest:
        player = state.get("player") or {}
        round_no = (state.get("battle") or {}).get("round") or 1
        expected = expected_damage_over_turns(
            player.get("draw_pile") or [], player.get("discard_pile") or [], player.get("hand") or [],
            KIN_DECISION_ROUND - round_no + 1, player.get("max_energy", player.get("energy", 0)), ASSUMED_DRAW)
        memory["kin_focus"] = "priest" if expected >= _effective_hp(priest[0]) else "followers"
        print(f"[The Kin] expected damage by round {KIN_DECISION_ROUND}: {expected:.0f} vs Priest "
              f"{_effective_hp(priest[0])} -> focus {memory['kin_focus']}")
    if memory.get("kin_focus") == "followers" and followers:
        target = min(followers, key=_effective_hp)
    elif priest:
        target = priest[0]
    else:
        return None
    return _decide_favor_damage(strategy, state, enemies, target, _incoming(enemies), DEFAULT_HP_TRADE_CAP)


# Encounters with their own bespoke decide_* function -- each takes
# (strategy, state, enemies, encounter).
CUSTOM_OVERRIDES: dict[str, Callable[[HeuristicStrategy, dict, list[dict], Encounter], Action | None]] = {
    "Group of Slimes": decide_group_of_slimes,
    "Fuzzy Wurm Crawler": decide_fuzzy_wurm_crawler,
    "Inklets": decide_inklets,
    "A Pair of Nibbits": decide_pair_of_nibbits,
    "Strangler and Friend": decide_strangler_and_friend,
    "Dense Vegetation": decide_dense_vegetation,
    "Bygone Effigy": decide_bygone_effigy,
    "Byrdonis": decide_byrdonis,
    "Phrog Parasite": decide_phrog_parasite,
    "Ceremonial Beast": decide_ceremonial_beast,
    "The Kin": decide_the_kin,
}


def build_overrides(strategy: HeuristicStrategy) -> dict[str, Callable[[dict, list[dict], Encounter], Action | None]]:
    overrides: dict[str, Callable[[dict, list[dict], Encounter], Action | None]] = {
        name: functools.partial(decide_favor_damage_solo, strategy, hp_trade_cap=cap)
        for name, cap in HP_TRADE_CAP.items()
    }
    for name, (order, cap) in KILL_ORDERS.items():
        overrides[name] = functools.partial(decide_kill_order, strategy, order=order, cap=cap)
    for name, fn in CUSTOM_OVERRIDES.items():
        overrides[name] = functools.partial(fn, strategy)
    return overrides
