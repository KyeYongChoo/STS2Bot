"""Reference data: named combat encounters, by act.

Sourced from two places:
  - slaythespire.wiki.gg (Overgrowth/Monsters/Elites/Bosses pages and
    individual enemy pages), transcribed 2026-09-15 -- not observed from
    the live API, so treat compositions and mechanics notes as "probably
    right, re-verify against `battle.enemies` the first time you actually
    see the fight," not ground truth.
  - `GET /api/v1/profile`'s `encounter_stats`/`enemy_stats` (keyed by the
    actual internal IDs, since they're literally SaveManager.Progress's
    dictionary keys) from a live, already-played profile, queried
    2026-09-15 -- this IS ground truth, but only covers encounters/enemies
    that profile has actually fought. Confirmed `fight_id`/entity_ids came
    from this; anything still unconfirmed is a fight that profile hadn't
    played yet.

`entity_id` fields (in `enemies`) are the enemy's base ID as STS2MCP
reports it (matching `ml/vocab/enemy_id.json` / `base_entity_id()` below)
where that ID has actually been observed and recorded; unconfirmed ones are
left as the wiki's display name in a "?display name" string so they're easy
to grep for and swap once confirmed.

`fight_id` is the encounter's own ID, distinct from any single enemy's
entity_id -- what the game's dev console's `fight <id>` command expects
(see sts2bot/dev_console.py). None where unconfirmed.

`identify_encounter()` matches a live fight's `battle.enemies` against this
data to name which encounter you're in, so HeuristicStrategy can dispatch to
a fight-specific override -- see `_encounter_overrides` in heuristic.py.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass
class Encounter:
    name: str
    kind: str  # "monster" | "elite" | "boss" | "event"
    pool: str  # "easy" | "normal" | "event" | "elite" | "boss"
    # Each slot is a confirmed base entity_id (str), a frozenset of confirmed
    # candidate IDs for a slot that's randomized between known options
    # (e.g. Group of Slimes' medium-slime slot), or a "?Display Name" string
    # for a slot that's still completely unconfirmed.
    enemies: list[str | frozenset[str]]
    fight_id: str | None = None  # console `fight <id>` target; None if unconfirmed
    notes: str = ""


def base_entity_id(entity_id: str | None) -> str | None:
    """Strips the live per-instance suffix STS2 appends to make entity_ids
    unique within a fight (e.g. "nibbit_1" -> "nibbit"), so copies of the
    same monster count as the same base ID."""
    if not entity_id:
        return None
    return re.sub(r"_\d+$", "", entity_id)


def enemy_signature(enemies: list[dict]) -> Counter[str]:
    return Counter(base_entity_id(e.get("entity_id")) for e in enemies if e.get("entity_id"))


def _enemies_match(template: list[str | frozenset[str]], live: Counter[str]) -> bool:
    """True if `live` (a fight's actual enemy composition) matches
    `template` (an Encounter.enemies list). Any "?Display Name" entry makes
    the whole template unmatchable -- a fuzzy/partial match risks silently
    applying the wrong encounter's override, which is worse than just
    falling back to the generic heuristic (see identify_encounter). A
    frozenset entry is a "one of these confirmed IDs" random slot (e.g.
    Group of Slimes' medium-slime slot) -- unlike a "?" placeholder, this
    IS treated as fully confirmed, since every candidate in it is a known
    real ID, just not pinned to one specific pick for this template.
    """
    if any(isinstance(e, str) and e.startswith("?") for e in template):
        return False
    if len(template) != sum(live.values()):
        return False

    fixed = Counter(e for e in template if isinstance(e, str))
    if any(live[base_id] < count for base_id, count in fixed.items()):
        return False
    remaining = Counter(live)
    remaining.subtract(fixed)
    remaining = +remaining  # drop the now-zeroed-out fixed slots

    wildcards = [e for e in template if isinstance(e, frozenset)]
    if len(wildcards) > 1:
        # Only one random slot appears in any Act 1 Overgrowth encounter so
        # far -- this doesn't attempt general bipartite matching for
        # multiple ambiguous slots with overlapping candidates, just this
        # common single-wildcard case. Extend here if that's ever needed.
        return False
    if sum(remaining.values()) != len(wildcards):
        return False
    if wildcards:
        [remaining_id] = remaining.elements()
        if remaining_id not in wildcards[0]:
            return False
    return True


def identify_encounter(enemies: list[dict], pool: list[Encounter] | None = None) -> Encounter | None:
    """Names which known encounter a live fight's `enemies` list is (see
    _enemies_match for the matching rules). Returns None on no match, or
    more than one candidate matching (ambiguous)."""
    live = enemy_signature(enemies)
    if not live:
        return None

    candidates = [enc for enc in (pool if pool is not None else ACT1_OVERGROWTH)
                  if _enemies_match(enc.enemies, live)]
    return candidates[0] if len(candidates) == 1 else None


# --------------------------------------------------------------------- #
# Act 1: Overgrowth
# --------------------------------------------------------------------- #
# First 3 fights of the act are drawn (3 of 4, no repeats) from the "easy"
# pool; every fight after that is drawn from "normal". "elite"/"boss" pools
# are each a flat list you'll face one of, chosen elsewhere by the map gen.

ACT1_OVERGROWTH: list[Encounter] = [
    # --- easy pool (first 3 encounters) ---
    Encounter("A Lone Nibbit", "monster", "easy",
              ["NIBBIT"],
              fight_id="NIBBITS_WEAK",
              notes="Gains Strength every turn it survives -- speed beats a "
                    "clean fight. Uses the shared decide_favor_damage_solo "
                    "strategy (sts2bot/encounter_heuristics.py) with a 3 HP "
                    "trade cap: favor damage over block, accepting up to 3 "
                    "HP of hits per turn to deal more damage, unless this "
                    "turn can be fully blocked for free AND next turn's "
                    "draw has a >=80% chance (computed by exact enumeration "
                    "over draw_pile) of a clean solo-kill -- then bank the "
                    "free turn instead."),
    Encounter("Group of Slimes", "monster", "easy",
              ["LEAF_SLIME_S", frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "TWIG_SLIME_S"],
              fight_id="SLIMES_WEAK",
              notes="'Medium slime' slot is confirmed random between "
                    "LEAF_SLIME_M and TWIG_SLIME_M (both seen in the "
                    "profile's enemy_stats) -- odds between the two "
                    "unconfirmed, but identify_encounter() matches either. "
                    "Has a dedicated override, decide_group_of_slimes in "
                    "encounter_heuristics.py: fixed kill order Leaf Slime -> "
                    "Medium Slime -> Twig Slime (not generic biggest-threat "
                    "targeting), with a 4 HP trade cap while the Medium "
                    "Slime is alive, dropping to 0 (pure block, no HP "
                    "trading) once it's dead -- the remaining Twig Slime "
                    "isn't worth any risk once the bigger threat is gone."),
    Encounter("Shrinker Beetle", "monster", "easy",
              ["SHRINKER_BEETLE"],
              fight_id="SHRINKER_BEETLE_WEAK"),
    Encounter("Fuzzy Wurm Crawler", "monster", "easy",
              ["FUZZY_WURM_CRAWLER"],
              fight_id="FUZZY_WURM_CRAWLER_WEAK"),

    # --- normal pool ---
    Encounter("Inklets", "monster", "normal",
              ["INKLET", "INKLET", "INKLET"],
              fight_id="INKLETS_NORMAL"),
    Encounter("Mawler", "monster", "normal",
              ["MAWLER"],
              fight_id="MAWLER_NORMAL"),
    Encounter("A Pair of Nibbits", "monster", "normal",
              ["NIBBIT", "NIBBIT"],
              fight_id="NIBBITS_NORMAL"),
    Encounter("Ruby Raiders", "monster", "normal",
              ["?3 of: Axe/Assassin/Brute/Crossbow/Tracker Raider, no dupes"],
              fight_id="RUBY_RAIDERS_NORMAL",
              notes="3 randomly chosen (no repeats) from 5 variants. Confirmed "
                    "IDs: AXE_RUBY_RAIDER, BRUTE_RUBY_RAIDER, "
                    "CROSSBOW_RUBY_RAIDER, TRACKER_RUBY_RAIDER. Only "
                    "ASSASSIN_RUBY_RAIDER still unconfirmed (profile hasn't "
                    "fought one yet). Assassin Raider (Killshot, flat 10 "
                    "dmg/turn) and Crossbow Raider (alternates Reload block / "
                    "14 dmg Fire!) are the two worth focusing down first -- "
                    "Assassin has no telegraphed ramp so it's just steady "
                    "damage every turn, and Crossbow's Fire! turn is the "
                    "single biggest hit in the group."),
    Encounter("Swarm of Slimes", "monster", "normal",
              ["TWIG_SLIME_M", frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "LEAF_SLIME_S", "TWIG_SLIME_S"]),
    Encounter("Shroom and Slime", "monster", "normal",
              ["?medium slime (random)", "FLYCONID"],
              fight_id="FLYCONID_NORMAL",
              notes="fight_id inferred (only fixed member -> keyed by "
                    "FLYCONID), not double-checked -- FLYCONID_NORMAL might "
                    "instead be Overgrowth Flora if the game's naming picked "
                    "the other fixed-member fight for this key; re-verify by "
                    "console-warping to it and reading battle.enemies."),
    Encounter("Overgrowth Flora", "monster", "normal",
              ["SNAPPING_JAXFRUIT", "FLYCONID"],
              fight_id="SNAPPING_JAXFRUIT_NORMAL",
              notes="fight_id inferred (keyed by its other fixed member, "
                    "SNAPPING_JAXFRUIT, since FLYCONID_NORMAL was assumed "
                    "taken by Shroom and Slime) -- same re-verify caveat as "
                    "that entry."),
    Encounter("Fogmog", "monster", "normal",
              ["FOGMOG"],
              fight_id="FOGMOG_NORMAL",
              notes="Summons EYE_WITH_TEETH. Fogmog is the summoner -- kill it "
                    "first (heuristic.py already treats known summoners as "
                    "priority target via _known_summoners)."),
    Encounter("Cubex Construct", "monster", "normal",
              ["?Cubex Construct"],
              notes="Charges up, then cycles Repeater Blast x2 (dmg + gains "
                    "2 Str each) into Expel Blast (2-hit). Strength ramps "
                    "every turn it's not interrupted -- worth prioritizing "
                    "over a pure HP race. Not yet in the profile's "
                    "enemy_stats (unfought), so entity_id/fight_id unconfirmed."),
    Encounter("Vine Shambler", "monster", "normal",
              ["VINE_SHAMBLER"],
              fight_id="VINE_SHAMBLER_NORMAL"),
    Encounter("Overgrowth Crawlers", "monster", "normal",
              ["SHRINKER_BEETLE", "FUZZY_WURM_CRAWLER"],
              fight_id="OVERGROWTH_CRAWLERS"),
    Encounter("Strangler and Friend", "monster", "normal",
              ["SLITHERING_STRANGLER",
               "?Snapping Jaxfruit / medium slime / small slimes (random)"],
              fight_id="SLITHERING_STRANGLER_NORMAL"),

    # --- event (not a map Monster node, but a real fight) ---
    Encounter("Dense Vegetation", "event", "event",
              ["?Wriggler", "?Wriggler", "?Wriggler", "?Wriggler"],
              notes="Triggered by the Dense Vegetation event, not a normal "
                    "map node. 4 Wrigglers -- same add-type Phrog Parasite "
                    "summons on death, so its attack pattern (Nasty Bite 6 "
                    "dmg / Wriggle: shuffle Infection + gain 2 Str) applies."),

    # --- elites (one fought per Elite node) ---
    Encounter("Bygone Effigy", "elite", "elite",
              ["BYGONE_EFFIGY"],
              fight_id="BYGONE_EFFIGY_ELITE",
              notes="3-turn loop: Sleep (does nothing) -> Wake (gains 10 Str) "
                    "-> Slashes (13 dmg, repeating). Also has Slow: each card "
                    "you play this turn adds +10% Attack-card damage taken, "
                    "stacking -- only helps Attacks, not Powers/Poison/Doom. "
                    "Since it does nothing on turn 1, that's a free setup "
                    "turn; since it gains a flat 10 Str on turn 2 rather than "
                    "ramping turn-over-turn, there's no urgency to rush it "
                    "down before then -- but once Wake resolves, every card "
                    "you dump into one turn multiplies via Slow, so hold "
                    "burst for after Wake rather than before."),
    Encounter("Byrdonis", "elite", "elite",
              ["?Byrdonis"],
              notes="Alternates Swoop (17 dmg) / Peck (3x3 dmg). Territorial: "
                    "gains Strength at end of every turn, and since Peck hits "
                    "3x, each Str point is worth 3x normal on Peck turns -- "
                    "this fight punishes going long far more than most, favor "
                    "a fast kill over a grindy block-and-chip plan."),
    Encounter("Phrog Parasite", "elite", "elite",
              ["?Phrog Parasite"],
              notes="Alternates Infect (shuffles 3 Infection into discard) / "
                    "Lash (4x4 dmg). On death, Infested triggers and summons "
                    "4 Wrigglers (Nasty Bite 6 dmg, or Wriggle: shuffle "
                    "Infection + gain 2 Str) -- this is a two-phase fight, "
                    "budget block/cards for the post-death add wave rather "
                    "than dumping everything into the killing blow."),

    # --- bosses (one fought per Boss node) ---
    Encounter("Ceremonial Beast", "boss", "boss",
              ["CEREMONIAL_BEAST"],
              fight_id="CEREMONIAL_BEAST_BOSS",
              notes="252 HP (262 asc8). Turn 1 Stamp: gains Plow 150 (160 "
                    "asc9), then repeats Plow (18 dmg, +2 Str) until enough "
                    "damage is dealt to burn through the Plow stack, at which "
                    "point it's stunned and loses all Strength -- effectively "
                    "a free turn once you cross that threshold. Phase 2: "
                    "cycles Beast Cry (Ringing: 1 card/turn limit) / Stomp "
                    "(15 dmg) / Crush (17 dmg, +3 Str). Since Plow strips all "
                    "accumulated Strength on trigger, racing to the stun "
                    "threshold matters more than trickling damage in."),
    Encounter("The Kin", "boss", "boss",
              ["?Kin Priest", "?Kin Follower", "?Kin Follower"],
              notes="3-enemy fight. Kin Priest 190 HP (199 asc8): cycles Orb "
                    "of Frailty (8 dmg + Frail) / Orb of Weakness (8 dmg + "
                    "Weak) / Soul Beam (3x3 dmg) / Dark Ritual (+2 Str). Each "
                    "Kin Follower 58-59 HP (62-63 asc8): cycles Quick Slash "
                    "(5 dmg) / Boomerang (2x2 dmg) / Power Dance (+2 Str), "
                    "offset from each other so they don't sync. Followers "
                    "have Minion and flee once the Priest dies -- this is a "
                    "known-summoner-style fight (see _known_summoners in "
                    "heuristic.py) except the priority target is the "
                    "non-summoning leader, not a summoner, so that mechanism "
                    "doesn't directly apply; a dedicated encounter override "
                    "should just hard-code Kin Priest as the always-target."),
    Encounter("Vantom", "boss", "boss",
              ["VANTOM"],
              fight_id="VANTOM_BOSS",
              notes="173 HP (183 asc8). Fixed 4-move cycle: Ink Blot (7 dmg) "
                    "-> Inky Lance (6x2 dmg) -> Dismember (27 dmg + shuffles "
                    "3 Wounds) -> Prepare (+2 Str). Slippery 9: the next 9 "
                    "instances of HP loss only deal 1 HP each, regardless of "
                    "the hit's real damage -- a single big attack is mostly "
                    "wasted against it, multi-hit/DoT sources burn through "
                    "Slippery stacks far more efficiently than they do "
                    "against a normal HP pool. Also telegraphs Dismember one "
                    "turn ahead (it's on a fixed cycle), so it's worth saving "
                    "burst block for that specific turn rather than spreading "
                    "block evenly."),
]
