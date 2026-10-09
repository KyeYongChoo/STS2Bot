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

  - The game's own encounter list (ids + every possible lineup), from the
    model database copy the Untapped companion app ships (see
    sts2bot/gamedata.py), read 2026-10-09 -- ground truth for ids and
    lineups, and the source for every Overgrowth id below that was still a
    "?" placeholder before.

`fight_id` is the encounter's own ID, distinct from any single enemy's
entity_id -- what the game's dev console's `fight <id>` command expects
(see sts2bot/dev_console.py). None where unconfirmed.

`identify_encounter()` matches a live fight's `battle.enemies` against this
data to name which encounter you're in, so HeuristicStrategy can dispatch to
a fight-specific override -- see `_encounter_overrides` in heuristic.py.
"""
from __future__ import annotations

import itertools
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
    # Every possible exact lineup, for encounters whose random slots don't
    # fit a single `enemies` template (Ruby Raiders: any 3 of 5 raiders).
    # When set, matching uses these instead of `enemies`.
    lineups: tuple[tuple[str, ...], ...] = ()
    # The fight continues into a second phase after the main enemy dies
    # (Phrog Parasite -> its Wrigglers; Waterfall Giant; Test Subject).
    # Damage it was about to deal is cancelled on death, so nothing uses
    # this yet -- but e.g. "dump everything into the killing blow" is wrong
    # for these, since the fight isn't over.
    second_phase: bool = False


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

    candidates = [enc for enc in (pool if pool is not None else ALL_ENCOUNTERS)
                  if (any(Counter(lineup) == live for lineup in enc.lineups) if enc.lineups
                      else _enemies_match(enc.enemies, live))]
    return candidates[0] if len(candidates) == 1 else None


# --------------------------------------------------------------------- #
# Act 1: Overgrowth
# --------------------------------------------------------------------- #
# First 3 fights of the act are drawn (3 of 4, no repeats) from the "easy"
# pool; every fight after that is drawn from "normal". "elite"/"boss" pools
# are each a flat list you'll face one of, chosen elsewhere by the map gen.

_RUBY_RAIDERS = ("AXE_RUBY_RAIDER", "ASSASSIN_RUBY_RAIDER", "BRUTE_RUBY_RAIDER",
                 "CROSSBOW_RUBY_RAIDER", "TRACKER_RUBY_RAIDER")

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
              fight_id="SHRINKER_BEETLE_WEAK",
              notes="No special heuristics"),
    Encounter("Fuzzy Wurm Crawler", "monster", "easy",
              ["FUZZY_WURM_CRAWLER"],
              fight_id="FUZZY_WURM_CRAWLER_WEAK",
              notes="Calculate damage in advance, primary goal is kill by turn 4 then block if extra energy"),

    # --- normal pool ---
    Encounter("Inklets", "monster", "normal",
              ["INKLET", "INKLET", "INKLET"],
              fight_id="INKLETS_NORMAL",
              notes="Damage race. Aim to kill one by turn 2. use multiattacks or weak attacks first before playing large attacks to strip away slippery debuff which reduces next damage instance to 1. Target the left/right one to kill first, because middle wont be attacking for 10 damage on turn 2"),
    Encounter("Mawler", "monster", "normal",
              ["MAWLER"],
              fight_id="MAWLER_NORMAL",
              notes="No special heuristics"),
    Encounter("A Pair of Nibbits", "monster", "normal",
              ["NIBBIT", "NIBBIT"],
              fight_id="NIBBITS_NORMAL",
              notes="Damage race. Aim to kill 1 by turn 3 or 4. then proceed with normalish rules"),
    Encounter("Ruby Raiders", "monster", "normal",
              ["3 distinct of the 5 raiders -- see lineups"],
              fight_id="RUBY_RAIDERS_NORMAL",
              lineups=tuple(itertools.combinations(_RUBY_RAIDERS, 3)),
              notes="3 randomly chosen (no repeats) from 5 variants -- all 10 "
                    "combinations are in the game's encounter data. Assassin Raider (Killshot, flat 10 "
                    "dmg/turn) and Crossbow Raider (alternates Reload block / "
                    "14 dmg Fire!) are the two worth focusing down first -- "
                    "Assassin has no telegraphed ramp so it's just steady "
                    "damage every turn, and Crossbow's Fire! turn is the "
                    "single biggest hit in the group."),
    Encounter("Swarm of Slimes", "monster", "normal",
              ["TWIG_SLIME_M", "LEAF_SLIME_M", "LEAF_SLIME_S", "TWIG_SLIME_S"],
              fight_id="SLIMES_NORMAL",
              notes="Kill Medium first then small"),
    Encounter("Shroom and Slime", "monster", "normal",
              [frozenset({"LEAF_SLIME_M", "TWIG_SLIME_M"}), "FLYCONID"],
              fight_id="FLYCONID_NORMAL",
              notes="fight_id and lineups confirmed by the game's encounter data."
                    " More notes: Damage race, kill slime first then Flyconid"),
    Encounter("Overgrowth Flora", "monster", "normal",
              ["SNAPPING_JAXFRUIT", "FLYCONID"],
              fight_id="SNAPPING_JAXFRUIT_NORMAL",
              notes="fight_id confirmed by the game's encounter data."
                    " More notes: Damage Race++ Kill Jaxfruit first"),
    Encounter("Fogmog", "monster", "normal",
              ["FOGMOG"],
              fight_id="FOGMOG_NORMAL",
              notes="Summons EYE_WITH_TEETH. Fogmog is the summoner -- kill it "
                    "first (heuristic.py already treats known summoners as "
                    "priority target via _known_summoners)."),
    Encounter("Cubex Construct", "monster", "normal",
              ["CUBEX_CONSTRUCT"],
              fight_id="CUBEX_CONSTRUCT_NORMAL",
              notes="Charges up, then cycles Repeater Blast x2 (dmg + gains "
                    "2 Str each) into Expel Blast (2-hit). Strength ramps "
                    "every turn it's not interrupted -- worth prioritizing "
                    "over a pure HP race."),
    Encounter("Vine Shambler", "monster", "normal",
              ["VINE_SHAMBLER"],
              fight_id="VINE_SHAMBLER_NORMAL",
              notes="Note that from turn 3 onwards in this fight, attacks cost +1. No special rules though"),
    Encounter("Overgrowth Crawlers", "monster", "normal",
              ["SHRINKER_BEETLE", "FUZZY_WURM_CRAWLER"],
              fight_id="OVERGROWTH_CRAWLERS",
              notes="Kill Shrinker Bettle first. Damage Race"),
    Encounter("Strangler and Friend", "monster", "normal",
              ["SLITHERING_STRANGLER", "+ a random friend -- see lineups"],
              fight_id="SLITHERING_STRANGLER_NORMAL",
              lineups=(("LEAF_SLIME_M", "SLITHERING_STRANGLER"),
                       ("TWIG_SLIME_M", "SLITHERING_STRANGLER"),
                       ("SNAPPING_JAXFRUIT", "SLITHERING_STRANGLER"),
                       ("LEAF_SLIME_S", "LEAF_SLIME_S", "SLITHERING_STRANGLER"),
                       ("LEAF_SLIME_S", "TWIG_SLIME_S", "SLITHERING_STRANGLER"),
                       ("TWIG_SLIME_S", "TWIG_SLIME_S", "SLITHERING_STRANGLER")),
              notes="Damage race until 1 enemy left"),

    # --- event (not a map Monster node, but a real fight) ---
    Encounter("Dense Vegetation", "event", "event",
              ["WRIGGLER", "WRIGGLER", "WRIGGLER", "WRIGGLER"],
              fight_id="DENSE_VEGETATION_EVENT_ENCOUNTER",
              notes="Triggered by the Dense Vegetation event, not a normal "
                    "map node. 4 Wrigglers -- same add-type Phrog Parasite "
                    "summons on death, so its attack pattern (Nasty Bite 6 "
                    "dmg / Wriggle: shuffle Infection + gain 2 Str) applies."
                    " Choices for focusing down matter - ideally you can kill one every 2 turns or every 1 turn, so if this turn you cant kill an attacking wriggler, attack a not attacking wriggler so you can kill it next turn. Also, if you can full or almost full block this turns attack then attack the next enemy instead so you have a chance of killing the enemy"),

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
                    "burst for after Wake rather than before."
                    " Consider all attack cards, and if you have energy to dump on defends after playing attack cards, play defends first, then attack to optimize the damage. When it wakes up, just attack full on, accept any HP trade, unless your draw pile is likely to draw enough damage to kill it next turn"),
    Encounter("Byrdonis", "elite", "elite",
              ["BYRDONIS"],
              fight_id="BYRDONIS_ELITE",
              notes="Alternates Swoop (17 dmg) / Peck (3x3 dmg). Territorial: "
                    "gains Strength at end of every turn, and since Peck hits "
                    "3x, each Str point is worth 3x normal on Peck turns -- "
                    "this fight punishes going long far more than most, favor "
                    "a fast kill over a grindy block-and-chip plan."
                    " Accept any HP trade to deal damage, unless you're sure youre able to kill next turn and you can get away with playing less than max possible damage"),
    Encounter("Phrog Parasite", "elite", "elite",
              ["PHROG_PARASITE"],
              fight_id="PHROG_PARASITE_ELITE",
              second_phase=True,
              notes="Alternates Infect (shuffles 3 Infection into discard) / "
                    "Lash (4x4 dmg). On death, Infested triggers and summons "
                    "4 Wrigglers (Nasty Bite 6 dmg, or Wriggle: shuffle "
                    "Infection + gain 2 Str) -- this is a two-phase fight, "
                    "budget block/cards for the post-death add wave rather "
                    "than dumping everything into the killing blow."
                    " Goal is to kill by turn 4/5 (simulate shuffles and damage), then pretend its the dense vegation event"),

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
                    "threshold matters more than trickling damage in."
                    " damage race to stun threshold, then play normally"),
    Encounter("The Kin", "boss", "boss",
              ["KIN_PRIEST", "KIN_FOLLOWER", "KIN_FOLLOWER"],
              fight_id="THE_KIN_BOSS",
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
                    "should just hard-code Kin Priest as the always-target."
                    " Start of fight calculate expected single target damage by round 8. If more than Kin priest HP, Attack Kin Priest. If less than Kin Priest HP, target a cult follower first"),
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
                    "block evenly."
                    " This boss is the most unique, where whether you're winning or not happens before the fight itself. Incentivize multihit cards if act boss is vantom, as well as scaling cards. dont play large value attacks until slipper stacks are gone, play small attacks or multihits."),
]


# --------------------------------------------------------------------- #
# Act 1 (alternate): Underdocks
# --------------------------------------------------------------------- #
ACT1_UNDERDOCKS: list[Encounter] = [
    # --- easy ---
    Encounter("Corpse Slugs (Weak)", "monster", "easy",
              ["CORPSE_SLUG", "CORPSE_SLUG"],  # Corpse Slug, Corpse Slug
              fight_id="CORPSE_SLUGS_WEAK",
              notes=""),
    Encounter("Seapunk (Weak)", "monster", "easy",
              ["SEAPUNK"],  # Seapunk
              fight_id="SEAPUNK_WEAK",
              notes=""),
    Encounter("Sludge Spinner", "monster", "easy",
              ["SLUDGE_SPINNER"],  # Sludge Spinner
              fight_id="SLUDGE_SPINNER_WEAK",
              notes=""),
    Encounter("Toadpoles", "monster", "easy",
              ["TOADPOLE", "TOADPOLE"],  # Toadpole, Toadpole
              fight_id="TOADPOLES_WEAK",
              notes=""),
    # --- normal ---
    Encounter("Corpse Slugs", "monster", "normal",
              ["CORPSE_SLUG", "CORPSE_SLUG", "CORPSE_SLUG"],  # Corpse Slug, Corpse Slug, Corpse Slug
              fight_id="CORPSE_SLUGS_NORMAL",
              notes=""),
    Encounter("Cultists", "monster", "normal",
              ["CALCIFIED_CULTIST", "DAMP_CULTIST"],  # Calcified Cultist, Damp Cultist
              fight_id="CULTISTS_NORMAL",
              notes=""),
    Encounter("Fossil Stalker", "monster", "normal",
              ["FOSSIL_STALKER"],  # Fossil Stalker
              fight_id="FOSSIL_STALKER_NORMAL",
              notes=""),
    Encounter("Gremlin Merc", "monster", "normal",
              ["GREMLIN_MERC"],  # Gremlin Merc
              fight_id="GREMLIN_MERC_NORMAL",
              notes=""),
    Encounter("Haunted Ship", "monster", "normal",
              ["HAUNTED_SHIP"],  # Haunted Ship
              fight_id="HAUNTED_SHIP_NORMAL",
              notes=""),
    Encounter("Living Fog", "monster", "normal",
              ["LIVING_FOG"],  # Living Fog
              fight_id="LIVING_FOG_NORMAL",
              notes=""),
    Encounter("Punch Construct", "monster", "normal",
              ["PUNCH_CONSTRUCT"],  # Punch Construct
              fight_id="PUNCH_CONSTRUCT_NORMAL",
              notes=""),
    Encounter("Seapunk", "monster", "normal",
              ["CALCIFIED_CULTIST", "SEAPUNK"],  # Calcified Cultist, Seapunk
              fight_id="SEAPUNK_NORMAL",
              notes=""),
    Encounter("Sewer Clam", "monster", "normal",
              ["SEWER_CLAM"],  # Sewer Clam
              fight_id="SEWER_CLAM_NORMAL",
              notes=""),
    Encounter("Two Tailed Rats", "monster", "normal",
              ["TWO_TAILED_RAT", "TWO_TAILED_RAT", "TWO_TAILED_RAT"],  # Two-Tailed Rat, Two-Tailed Rat, Two-Tailed Rat
              fight_id="TWO_TAILED_RATS_NORMAL",
              notes=""),
    # --- elite ---
    Encounter("Phantasmal Gardeners", "elite", "elite",
              ["PHANTASMAL_GARDENER", "PHANTASMAL_GARDENER", "PHANTASMAL_GARDENER", "PHANTASMAL_GARDENER"],  # Phantasmal Gardener, Phantasmal Gardener, Phantasmal Gardener, Phantasmal Gardener
              fight_id="PHANTASMAL_GARDENERS_ELITE",
              notes=""),
    Encounter("Skulking Colony", "elite", "elite",
              ["SKULKING_COLONY"],  # Skulking Colony
              fight_id="SKULKING_COLONY_ELITE",
              notes=""),
    Encounter("Terror Eel", "elite", "elite",
              ["TERROR_EEL"],  # Terror Eel
              fight_id="TERROR_EEL_ELITE",
              notes=""),
    # --- boss ---
    Encounter("Lagavulin Matriarch", "boss", "boss",
              ["LAGAVULIN_MATRIARCH"],  # Lagavulin Matriarch
              fight_id="LAGAVULIN_MATRIARCH_BOSS",
              notes=""),
    Encounter("Soul Fysh", "boss", "boss",
              ["SOUL_FYSH"],  # Soul Fysh
              fight_id="SOUL_FYSH_BOSS",
              notes=""),
    Encounter("Waterfall Giant", "boss", "boss",
              ["WATERFALL_GIANT"],  # Waterfall Giant
              fight_id="WATERFALL_GIANT_BOSS",
              second_phase=True,
              notes=""),
]

# --------------------------------------------------------------------- #
# Act 2: Hive
# --------------------------------------------------------------------- #
ACT2_HIVE: list[Encounter] = [
    # --- easy ---
    Encounter("Bowlbugs (Weak)", "monster", "easy",
              ["random -- see lineups"],  # e.g. Bowlbug (Rock), Bowlbug (Egg)
              lineups=(("BOWLBUG_ROCK", "BOWLBUG_EGG",),
                       ("BOWLBUG_ROCK", "BOWLBUG_NECTAR",)),
              fight_id="BOWLBUGS_WEAK",
              notes=""),
    Encounter("Exoskeletons (Weak)", "monster", "easy",
              ["EXOSKELETON", "EXOSKELETON", "EXOSKELETON"],  # Exoskeleton, Exoskeleton, Exoskeleton
              fight_id="EXOSKELETONS_WEAK",
              notes=""),
    Encounter("Thieving Hopper", "monster", "easy",
              ["THIEVING_HOPPER"],  # Thieving Hopper
              fight_id="THIEVING_HOPPER_WEAK",
              notes=""),
    Encounter("Tunneler", "monster", "easy",
              ["TUNNELER"],  # Tunneler
              fight_id="TUNNELER_WEAK",
              notes=""),
    # --- normal ---
    Encounter("Bowlbugs", "monster", "normal",
              ["random -- see lineups"],  # e.g. Bowlbug (Rock), Bowlbug (Egg), Bowlbug (Nectar)
              lineups=(("BOWLBUG_ROCK", "BOWLBUG_EGG", "BOWLBUG_NECTAR",),
                       ("BOWLBUG_ROCK", "BOWLBUG_EGG", "BOWLBUG_SILK",),
                       ("BOWLBUG_ROCK", "BOWLBUG_NECTAR", "BOWLBUG_SILK",)),
              fight_id="BOWLBUGS_NORMAL",
              notes=""),
    Encounter("Chompers", "monster", "normal",
              ["CHOMPER", "CHOMPER"],  # Chomper, Chomper
              fight_id="CHOMPERS_NORMAL",
              notes=""),
    Encounter("Exoskeletons", "monster", "normal",
              ["EXOSKELETON", "EXOSKELETON", "EXOSKELETON", "EXOSKELETON"],  # Exoskeleton, Exoskeleton, Exoskeleton, Exoskeleton
              fight_id="EXOSKELETONS_NORMAL",
              notes=""),
    Encounter("Hunter Killer", "monster", "normal",
              ["HUNTER_KILLER"],  # Hunter Killer
              fight_id="HUNTER_KILLER_NORMAL",
              notes=""),
    Encounter("Louse Progenitor", "monster", "normal",
              ["LOUSE_PROGENITOR"],  # Louse Progenitor
              fight_id="LOUSE_PROGENITOR_NORMAL",
              notes=""),
    Encounter("Mytes", "monster", "normal",
              ["MYTE", "MYTE"],  # Myte, Myte
              fight_id="MYTES_NORMAL",
              notes=""),
    Encounter("Ovicopter", "monster", "normal",
              ["OVICOPTER"],  # Ovicopter
              fight_id="OVICOPTER_NORMAL",
              notes=""),
    Encounter("Slumbering Beetle", "monster", "normal",
              ["BOWLBUG_ROCK", "BOWLBUG_SILK", "SLUMBERING_BEETLE"],  # Bowlbug (Rock), Bowlbug (Silk), Slumbering Beetle
              fight_id="SLUMBERING_BEETLE_NORMAL",
              notes=""),
    Encounter("Spiny Toad", "monster", "normal",
              ["SPINY_TOAD"],  # Spiny Toad
              fight_id="SPINY_TOAD_NORMAL",
              notes=""),
    Encounter("The Obscura", "monster", "normal",
              ["THE_OBSCURA"],  # The Obscura
              fight_id="THE_OBSCURA_NORMAL",
              notes=""),
    # --- elite ---
    Encounter("Decimillipede", "elite", "elite",
              ["DECIMILLIPEDE_SEGMENT_FRONT", "DECIMILLIPEDE_SEGMENT_MIDDLE", "DECIMILLIPEDE_SEGMENT_BACK"],  # Decimillipede, Decimillipede, Decimillipede
              fight_id="DECIMILLIPEDE_ELITE",
              notes=""),
    Encounter("Entomancer", "elite", "elite",
              ["ENTOMANCER"],  # Entomancer
              fight_id="ENTOMANCER_ELITE",
              notes=""),
    Encounter("Infested Prisms", "elite", "elite",
              ["INFESTED_PRISM"],  # Infested Prism
              fight_id="INFESTED_PRISMS_ELITE",
              notes=""),
    # --- boss ---
    Encounter("Kaiser Crab", "boss", "boss",
              ["CRUSHER", "ROCKET"],  # Crusher, Rocket
              fight_id="KAISER_CRAB_BOSS",
              notes=""),
    Encounter("Knowledge Demon", "boss", "boss",
              ["KNOWLEDGE_DEMON"],  # Knowledge Demon
              fight_id="KNOWLEDGE_DEMON_BOSS",
              notes=""),
    Encounter("The Insatiable", "boss", "boss",
              ["THE_INSATIABLE"],  # The Insatiable
              fight_id="THE_INSATIABLE_BOSS",
              notes=""),
]

# --------------------------------------------------------------------- #
# Act 3: Glory
# --------------------------------------------------------------------- #
ACT3_GLORY: list[Encounter] = [
    # --- easy ---
    Encounter("Devoted Sculptor", "monster", "easy",
              ["DEVOTED_SCULPTOR"],  # Devoted Sculptor
              fight_id="DEVOTED_SCULPTOR_WEAK",
              notes=""),
    Encounter("Scrolls of Biting (Weak)", "monster", "easy",
              ["SCROLL_OF_BITING", "SCROLL_OF_BITING", "SCROLL_OF_BITING"],  # Scroll of Biting, Scroll of Biting, Scroll of Biting
              fight_id="SCROLLS_OF_BITING_WEAK",
              notes=""),
    Encounter("Turret Operator", "monster", "easy",
              ["LIVING_SHIELD", "TURRET_OPERATOR"],  # Living Shield, Turret Operator
              fight_id="TURRET_OPERATOR_WEAK",
              notes=""),
    # --- normal ---
    Encounter("Axebots", "monster", "normal",
              ["AXEBOT"],  # Axebot
              fight_id="AXEBOTS_NORMAL",
              notes=""),
    Encounter("Construct Menagerie", "monster", "normal",
              ["PUNCH_CONSTRUCT", "CUBEX_CONSTRUCT", "CUBEX_CONSTRUCT"],  # Punch Construct, Cubex Construct, Cubex Construct
              fight_id="CONSTRUCT_MENAGERIE_NORMAL",
              notes=""),
    Encounter("Fabricator", "monster", "normal",
              ["FABRICATOR"],  # Fabricator
              fight_id="FABRICATOR_NORMAL",
              notes=""),
    Encounter("Frog Knight", "monster", "normal",
              ["FROG_KNIGHT"],  # Frog Knight
              fight_id="FROG_KNIGHT_NORMAL",
              notes=""),
    Encounter("Globe Head", "monster", "normal",
              ["GLOBE_HEAD"],  # Globe Head
              fight_id="GLOBE_HEAD_NORMAL",
              notes=""),
    Encounter("Owl Magistrate", "monster", "normal",
              ["OWL_MAGISTRATE"],  # Owl Magistrate
              fight_id="OWL_MAGISTRATE_NORMAL",
              notes=""),
    Encounter("Scrolls of Biting", "monster", "normal",
              ["SCROLL_OF_BITING", "SCROLL_OF_BITING", "SCROLL_OF_BITING", "SCROLL_OF_BITING"],  # Scroll of Biting, Scroll of Biting, Scroll of Biting, Scroll of Biting
              fight_id="SCROLLS_OF_BITING_NORMAL",
              notes=""),
    Encounter("Slimed Berserker", "monster", "normal",
              ["SLIMED_BERSERKER"],  # Slimed Berserker
              fight_id="SLIMED_BERSERKER_NORMAL",
              notes=""),
    Encounter("The Lost and Forgotten", "monster", "normal",
              ["THE_LOST", "THE_FORGOTTEN"],  # The Lost, The Forgotten
              fight_id="THE_LOST_AND_FORGOTTEN_NORMAL",
              notes=""),
    # --- elite ---
    Encounter("Knights", "elite", "elite",
              ["FLAIL_KNIGHT", "SPECTRAL_KNIGHT", "MAGI_KNIGHT"],  # Flail Knight, Spectral Knight, Magi Knight
              fight_id="KNIGHTS_ELITE",
              notes=""),
    Encounter("Mecha Knight", "elite", "elite",
              ["MECHA_KNIGHT"],  # Mecha Knight
              fight_id="MECHA_KNIGHT_ELITE",
              notes=""),
    Encounter("Soul Nexus", "elite", "elite",
              ["SOUL_NEXUS"],  # Soul Nexus
              fight_id="SOUL_NEXUS_ELITE",
              notes=""),
    # --- boss ---
    Encounter("Aeonglass", "boss", "boss",
              ["AEONGLASS"],  # Aeonglass
              fight_id="AEONGLASS_BOSS",
              notes=""),
    Encounter("Queen", "boss", "boss",
              ["TORCH_HEAD_AMALGAM", "QUEEN"],  # Torch Head Amalgam, Queen
              fight_id="QUEEN_BOSS",
              notes=""),
    Encounter("Test Subject", "boss", "boss",
              ["TEST_SUBJECT"],  # Test Subject #C8
              fight_id="TEST_SUBJECT_BOSS",
              second_phase=True,
              notes=""),
]

# --------------------------------------------------------------------- #
# Fights started by events (other than Dense Vegetation, above)
# --------------------------------------------------------------------- #
EVENT_ENCOUNTERS: list[Encounter] = [
    # --- event ---
    Encounter("Battleworn Dummy V1", "event", "event",
              ["BATTLE_FRIEND_V1"],  # Battle Friend V1.0
              fight_id="BATTLEWORN_DUMMY_EVENT_V1_ENCOUNTER",
              notes=""),
    Encounter("Battleworn Dummy V2", "event", "event",
              ["BATTLE_FRIEND_V2"],  # Battle Friend V2.0
              fight_id="BATTLEWORN_DUMMY_EVENT_V2_ENCOUNTER",
              notes=""),
    Encounter("Battleworn Dummy V3", "event", "event",
              ["BATTLE_FRIEND_V3"],  # Battle Friend V3.0
              fight_id="BATTLEWORN_DUMMY_EVENT_V3_ENCOUNTER",
              notes=""),
    Encounter("Fake Merchant", "event", "event",
              ["FAKE_MERCHANT_MONSTER"],  # The Merchant???
              fight_id="FAKE_MERCHANT_EVENT_ENCOUNTER",
              notes=""),
    Encounter("Mysterious Knight", "event", "event",
              ["MYSTERIOUS_KNIGHT"],  # Mysterious Knight
              fight_id="MYSTERIOUS_KNIGHT_EVENT_ENCOUNTER",
              notes=""),
    Encounter("Punch Off", "event", "event",
              ["PUNCH_CONSTRUCT", "PUNCH_CONSTRUCT"],  # Punch Construct, Punch Construct
              fight_id="PUNCH_OFF_EVENT_ENCOUNTER",
              notes=""),
    Encounter("The Architect", "event", "event",
              ["ARCHITECT"],  # The Architect
              fight_id="THE_ARCHITECT_EVENT_ENCOUNTER",
              notes=""),
]

ALL_ENCOUNTERS: list[Encounter] = ACT1_OVERGROWTH + ACT1_UNDERDOCKS + ACT2_HIVE + ACT3_GLORY + EVENT_ENCOUNTERS
