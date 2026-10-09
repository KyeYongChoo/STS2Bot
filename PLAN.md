# STS2Bot Plan

Goal: a neural-network bot that beats the hand-written heuristic at Ironclad A0.
Heuristic = teacher + baseline. Live game until the heuristic gate; emulator before the NN.

## Phase 0 — Reliability foundation (do first, small)
- [ ] Jam watchdog in Bot loop (`sts2bot/bot.py`): detect repeated identical state / no progress
      for N s / unhandled screen → dump snapshot to `data/jams/`, log, idle or abort
- [ ] Per-run summary log (floor reached, HP, win/loss, cause of death) next to trajectories

## Phase 1 — Combat play (live game, eyeball via `run.py --start-at X --repeat-test`)
- [ ] 1.1 Named strategy for every Act 1 encounter (`encounters.py` / `encounter_heuristics.py`),
      then Acts 2-3. Overgrowth: strategies written for every fight whose notes asked for
      one; ids/lineups confirmed from game data. Still to do: watch each one live via the dev
      loader (`python -m sts2bot.loader_ui`), confirm Inklets' left/middle/right = list
      order, and Underdocks (act 1's alternate act)
- [ ] 1.2 Generic combat improvements in `heuristic.py` (targeting by intent / kill thresholds
      in multi-enemy fights, 1-3 turn damage lookahead). Known gaps:
      - Powers are always played first, but usually aren't worth it near the end of a
        fight (e.g. enemy dies within a turn or two)
      - Second-phase fights (`Encounter.second_phase`: Phrog Parasite, Waterfall Giant,
        Test Subject) continue after the main enemy dies, so "spend everything on the
        killing blow" is wrong there. The boss's pending damage is cancelled on death, so
        nothing breaks yet
- [ ] 1.1b Card notes: fill in `sts2bot/cards.py` (Ironclad + colorless, one entry per card,
      like `encounters.py`), then turn them into card-play / card-value rules
- [ ] 1.1c Encounter notes for Underdocks / Hive / Glory / event fights (entries exist in
      `encounters.py` with empty notes)
- [ ] 1.3 Potion use: per-potion rule triggers (prevent lethal, elite/boss turn 1,
      use before discarding when slots full); out-of-combat potions (e.g. Foul Potion at
      merchant/? nodes) as special cases
- [ ] 1.4 Combat evaluator v1: estimate (win?, HP lost) for current deck vs an encounter.
      Option (C) from the kill-by-deadline discussion: score plays by HP lost now + expected
      HP lost on extra enemy turns. Reuses the draw simulator built for kill-by-deadline
      (`sample_future_hands` / `max_hand_damage` in `heuristic.py`) -- adds enemy-intent
      forecasting. `max_hand_damage` is the single place to swap in the Phase 5 emulator
      once damage depends on powers/relics
- [ ] 1.5 Crystal Sphere: a scripted subroutine that takes over on that event (not learned)

## Phase 2 — Deck model & deck-building (Ironclad)
- [ ] 2.1 Card valuation v1: Untapped tier list (`tierlist.py`) + per-card rule exceptions
- [ ] 2.2 Card valuation v2: bucket tags per card (frontload damage, frontload block,
      scaling, acceleration/draw) + a deck "needs" profile, so a good card is worth less
      when the deck already has enough of that bucket
- [ ] 2.3 Ironclad character strategy: tie archetypes (strength, exhaust, block...) to 2.1/2.2
- [ ] 2.4 Card reward picks (incl. skip) using 2.1/2.2. Boss-aware: when the act boss is
      Vantom (Slippery), value multi-hit and scaling cards higher (see its encounter notes)
- [ ] 2.5 Upgrade priorities (rest site smith, upgrade effects)
- [ ] 2.6 Card removal priorities
- [ ] 2.7 Shop: buy/remove choices, built on 2.1–2.6 + potion rules (1.3)

## Phase 3 — Run-level strategy
- [ ] 3.1 Relic valuation (incl. relics that carry setup into the next fight, relics that
      change other evaluators)
- [ ] 3.2 Run-state evaluator: value = f(HP, deck power from Phase 2, relics, gold,
      potions, floors to next boss); "raw power − floors remaining" idea
- [ ] 3.3 Map pathing: uses 3.2 + combat evaluator (1.4). Path to elites when the deck
      handles them easily; also take elites when the deck can't beat the boss as-is (needs
      relics/rares); account for elite rare-card odds
- [ ] 3.4 Events (? nodes), rest-vs-smith, treasure, boss relic choice

## Phase 4 — Automation & measurement (after the heuristic passes the eyeball check)
- [ ] Before the first unattended/overnight batch: uninstall the Untapped Companion (or
      run bot sessions under a separate Windows user without it). It uploads every
      finished `.run` file, including modded A0 runs, and backfills runs it missed on its
      next launch, so just closing it isn't enough. The bot already reads its own copy of
      the tier lists (`data/tier-lists/`, refresh with `python -m sts2bot.tierlist`
      while Untapped is still installed)
- [ ] Menu / character select / game_over handling (investigate `menu_select` in
      `vendor/STS2MCP/McpMod.cs`) → unattended back-to-back runs
- [ ] Win-rate tracker over N runs (Act 1/2/3 clear %, final boss win %)
- [ ] Iterate Phases 1–3 against the numbers

**GATE → Phase 5:** heuristic beats the final boss in >50% of Ironclad A0 runs.

## Phase 5 — Emulator (combat-only, Ironclad + Act 1 first)
- [ ] Python combat simulator: Ironclad cards, Act 1 enemies, common relics/potions
- [ ] Correctness tests: replay logged live trajectories (`data/trajectories`) through the
      emulator, compare predicted vs actual next state
- [ ] Investigate porting the base game's fuzz tests (jam/softlock detection)
- [ ] Expand: Acts 2-3 enemies, more relics, then non-combat (map/rewards/shop)
- [ ] Card valuation v3 (from 2.x): score a card by simulating likely upcoming elites/boss
      with vs without it

## Phase 6 — Neural network
- [ ] 6.1 Behaviour cloning (BC) warm-start on live logs (`ml/encode.py`, `ml/build_dataset.py`
      already exist): check the encoder and training code work end to end, compare to heuristic
- [ ] 6.2 Value net + lookahead: hard-coded search does the arithmetic ("calculator"),
      value net scores leaf positions; trained on emulator outcomes. Start with the combat
      value; run-state value later
- [ ] 6.3 Fight curriculum: replay single fights to master them; for later-act fights use
      a mix of "human-like" decks (from heuristic runs) and randomised decks/relics/HP
- [ ] 6.4 Self-play / RL fine-tuning in the emulator; validate on the live game
- [ ] 6.5 Once NN > heuristic: NN becomes the new teacher/baseline; keep heuristic as
      fallback + sanity check

## Appendix A — Open questions & decisions (from original notes)
- **Emulator before NN?** → Yes. Gate is the >50% win rate; v1 = combat, Ironclad, Act 1.
- **How many evaluators?** → Two: combat evaluator + run-state evaluator; map/shop/reward/
  upgrade/potion choices query them.
- **Is the evaluator a NN?** → Hand-made in Phases 1–3; becomes a learned value net in 6.2.
- **Imitate heuristic vs reward evaluator-valued positions?** → BC first (cheap, checks the
  pipeline, capped at heuristic level), then outcome-trained value net + search (can beat it).
- **Calculator access for the NN?** → Yes: hard-coded lookahead/search computes damage;
  the net judges positions. Lookahead features can also be fed as inputs early on
  (Jorbs' note: full lookahead is slow, so keep search shallow/pruned).
- **Permute hand order?** → Use a permutation-invariant set encoder (masked mean /
  self-attention over hand slots, as `ml/README.md` plans); no augmentation needed.
- **Potion permutations with many slots?** → Rule triggers now; search over potion use only
  once the emulator makes evaluation cheap.
- **Decks for later-act fight training?** → Mix of heuristic-run decks and randomised ones (6.3).
- **Pathing needs to talk to combat evaluator?** → Yes, by design (3.3 uses 1.4 + 3.2).
- **"Assume deck X, test whether adding card Y helps"?** → Card valuation v3 (Phase 5).
- **What if NN overtakes the heuristic?** → See 6.5.
- **Still open:** exact run-state evaluator formula; how relic effects plug into evaluators;
  whether the gate gets loosened if progress stalls.

## Appendix B — Reading list
- Dominion research (possible ideas for evaluators/card valuation): https://arxiv.org/abs/2405.06846
- Streamer close to an STS2 bot: https://www.youtube.com/watch?v=LQ3lH3luEsk (read the transcript)
