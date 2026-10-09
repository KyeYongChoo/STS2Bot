# STS2Bot — notes for Claude

A bot that plays Slay the Spire 2 in the real game through the STS2MCP mod's
local HTTP API (`localhost:15526`). Goal and roadmap: `PLAN.md` (heuristic →
automation → emulator → NN; target Ironclad A0). Setup and usage: `README.md`.

## Layout
- `sts2bot/heuristic.py` — generic decision engine (one `_decide_<state_type>`
  per screen) plus the combat math: damage parsing, kill combos, Slippery/Slow/
  multi-hit, and the draw simulators (see below).
- `sts2bot/encounters.py` — every encounter (ids, lineups, `notes`). The user
  writes strategy in `notes`; Claude turns the notes into code.
- `sts2bot/encounter_heuristics.py` — per-encounter strategies, keyed by
  `Encounter.name`. Reusable cores: `_decide_favor_damage` (HP-trade cap),
  `_decide_kill_by_deadline`, kill orders.
- `sts2bot/cards.py` — per-card notes, same workflow as encounters. Not wired
  into decisions yet.
- `sts2bot/loader.py` + `loader_ui.py` — dev loader (Tkinter): act / deck /
  fight / event. `deckgen.py` builds the decks.
- `sts2bot/dev_console.py` — drives the in-game dev console with real
  keystrokes (pyautogui), so the game window must be visible.
- `sts2bot/gamedata.py`, `tierlist.py` — read the copies in `data/` (see Data).
- `sts2bot/watchdog.py` — jam detection in the bot loop.
- `ml/` — feature encoding for the future NN.
- `tests/` — `python -m unittest discover tests`. Offline tests with fake
  clients and hand-built states. Keep them passing; add scenario tests for new
  strategies in `tests/test_encounter_heuristics.py`.

## Draw simulation (two versions, intentionally)
- `next_draw_probability` / `_kill_probability_next_turn`: next turn only,
  exact enumeration when small. Used by favor-damage's "bank a free block
  turn" check.
- `sample_future_hands` / `kill_probability_by_turns` /
  `expected_damage_over_turns`: multi-turn Monte Carlo (seeded) with the
  discard reshuffle. Used by kill-by-deadline and The Kin.
- `max_hand_damage` is the one place future-turn damage is estimated. Swap in
  the emulator there later, when powers/relics drive damage.

## The game and the mod
- `vendor/STS2MCP` is a gitignored clone of the mod. The user's working branch
  is `local/v0111-plus-deck` (their own deck/game-over commits plus ours).
  Our mod changes are also exported to `patches/sts2mcp/`. After changing the
  mod: commit in vendor, run `vendor/STS2MCP/build.ps1 -GameDir ...`, update
  the patches, and have the user run `scripts/install_mod.ps1 -FromBuild` with
  the game closed (the running game locks the DLL; don't close it for them).
- The mod adds `dev_set_deck` (replace the master deck, with upgrades and
  enchantments), `run.act_id`, and `upgrades`/`enchantment` on card info.
- Dev console facts, read from `sts2.dll`:
  - `act <n>` / `act <ACT_ID>`: the name replaces the current act (Overgrowth
    ↔ Underdocks) without abandoning the run.
  - `upgrade <hand-idx>` and `enchant <id> [amt] [hand-idx]` only affect the
    combat hand, never the deck. Use `dev_set_deck` for the deck.
  - `card <ID> [pile]` and `remove_card <ID> [Hand|Deck]` (removes one copy).
- The console's output is not readable: `godot.log` only echoes commands. To
  learn how a command works, decompile it with `ilspycmd` (installed), e.g.
  `ilspycmd -t MegaCrit.Sts2.Core.DevConsole.ConsoleCommands.ActConsoleCmd <game>/data_sts2_windows_x86_64/sts2.dll`.
- Enemy statuses seen: Slippery (next N HP losses become 1), Slow (+N% Attack
  damage taken), Plow (stun threshold HP), Minion, Illusion. The `Sleep`
  intent type means asleep.
- Second-phase fights (`Encounter.second_phase`): Phrog Parasite, Waterfall
  Giant, Test Subject. The boss's pending damage is cancelled on death.
- The bot remembers the identified encounter for the whole combat, and a fresh
  full-lineup match replaces it (Phrog → Dense Vegetation, last Nibbit → A
  Lone Nibbit).

## Data (`data/`, gitignored)
- `data/gamedata/sts2-data.json` and `data/tier-lists/` are copies of the
  Untapped companion app's cache, made by `python -m sts2bot.gamedata`. They
  hold every card/event/encounter/enchantment id and lineup, extracted from the
  game. Prefer this over the wiki.
- Untapped uploads every finished `.run`, modded A0 bot runs included, and
  backfills missed runs on its next launch. The user keeps it installed for
  their own play; PLAN.md Phase 4 says to uninstall it before overnight runs.
- `data/trajectories/*.jsonl` are every logged decision. Replaying them through
  `HeuristicStrategy.decide` is a good crash and behaviour-diff check after
  combat changes.

## Working with this user
- They write strategy notes in plain language and want questions asked when
  something is ambiguous. Use AskUserQuestion, and put explanations or
  trade-offs in the question text or the final message: they can't see text
  written between tool calls.
- Commits: separate the user's own uncommitted work from Claude's into
  different commits. Don't push unless asked. Use the co-author line given in
  the system reminder.
- Don't flood Untapped with bot runs, and don't commit third-party data
  (tier lists, game data). The repo has a GitHub remote.

## Gotchas
- In the Bash tool, backslashes inside heredoc'd Python get mangled (`\v`,
  `\b` became control characters in a .ps1). For Windows paths or files
  containing `\`, write a script file with the Write tool, or use Edit.
- Run Python from the repo root (`python -m sts2bot.x`).
