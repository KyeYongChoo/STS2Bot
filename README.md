# STS2Bot

A bot that plays Slay the Spire 2 for real, in your actual running game
window, by talking to the [STS2MCP](https://github.com/Gennadiyev/STS2MCP)
mod's local HTTP API. Because the mod drives the game engine directly
(not simulated mouse/keyboard input), every action the bot takes — playing
a card, picking a map node, buying from the shop — actually happens and
animates in your live game.

Two pieces:
- **`sts2bot/`** — the bot itself: a REST client for the mod's API, a
  rule-based ("heuristic") decision engine, and the main loop.
- **`ml/`** — not built yet. The heuristic bot logs every decision it
  makes to `data/trajectories/`, which is meant to become the training
  set for a DNN policy later. See `ml/README.md`.

## How it works

STS2 has a built-in mod loader. STS2MCP is a third-party (MIT-licensed,
open source) mod that, once loaded, starts an HTTP server on
`localhost:15526` inside the game process. It exposes:
- `GET /api/v1/singleplayer` — full JSON snapshot of the current screen
  (combat state, map, shop, rewards, etc.)
- `POST /api/v1/singleplayer` — issue an action (`play_card`, `end_turn`,
  `choose_map_node`, `shop_purchase`, ...)

This bot polls the state, runs it through `HeuristicStrategy` in
`sts2bot/heuristic.py`, and posts back whatever action the strategy picks.

The mod's source is cloned locally (gitignored, not part of this repo) at
`vendor/STS2MCP/` if you want to read the C# side of the hook — see
`McpMod.Actions.cs` and `McpMod.StateBuilder.cs` in there.

## Setup

1. **Install the mod** into your Slay the Spire 2 install:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_mod.ps1
   ```
   (defaults to `C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2`;
   pass `-GameDir "..."` if yours is elsewhere.)

2. **Launch Slay the Spire 2** and enable mods when the consent dialog
   appears (first launch after installing a mod).

3. **Verify the API is up** (once the game has finished loading):
   ```powershell
   curl.exe http://localhost:15526/api/v1/singleplayer
   ```

4. **Install Python deps:**
   ```powershell
   pip install -r requirements.txt
   ```

5. **Optional — copy card tier lists** from the
   [Untapped.gg Companion](https://sts2.untapped.gg/) app (if installed)
   so card-reward picks use community tiers instead of just rarity:
   ```powershell
   python -m sts2bot.tierlist
   ```
   This copies them into `data/tier-lists/` (gitignored); the bot only
   reads that copy, so Untapped doesn't need to stay installed.

6. **Start a run manually** — pick a character, get past the menu to the
   map or your first combat. Menu/character-select automation isn't
   implemented yet (see Limitations below).

7. **Run the bot:**
   ```powershell
   python run.py                 # deliberate pace, ~0.6s between actions
   python run.py --delay 0       # as fast as possible
   python run.py --delay 1.5     # slower, easier to watch/follow
   ```
   Watch it play in the actual game window. Console output shows every
   decision as it's made (e.g. `[Act 1 Floor 3] monster: play_card(card_index=2, target=jaw_worm_0)`).

## Current heuristic strategy

Deliberately simple, all in `sts2bot/heuristic.py`, one `_decide_<state_type>`
method per screen type — read it, it's short:
- **Combat**: play Powers first, block if incoming damage exceeds current
  block, otherwise prioritize a lethal finisher, then highest
  damage-per-energy attack, then any playable card. Targets the enemy
  with the biggest incoming attack unless a specific kill is lined up.
- **Map**: avoid Elites below ~65% HP, prefer Rest Sites below ~60% HP,
  otherwise a fixed node-type priority order.
- **Rewards/shop/rest/events/relics/treasure**: straightforward
  greedy rules (grab the best-rarity card, buy relics > cards > potions
  while affordable, rest when low, take the first non-locked event
  option, etc).

None of this is meant to be a strong player — it's meant to be readable
and to produce a labeled dataset (see below) for training something
better.

## Trajectory logging (for training a DNN later)

Every decision the bot makes is appended to `data/trajectories/<run>.jsonl`
as `{state, action, state_type, source}`. This is gitignored (it'll get
large fast) but is exactly what you'd use for behavior cloning once you're
ready to train a model to replace/improve on the heuristic. See
`ml/README.md` for the plan there — nothing is built yet.

## Limitations / not implemented

- **Menu and game-over screens are unhandled on purpose.** The mod's
  `menu_select` action exists but its valid `option` values aren't
  documented anywhere I could find; guessing at them risked silently
  doing the wrong thing. Start your run manually; the bot picks up once
  you're on the map/in combat/etc. If you want to dig into this, check
  `vendor/STS2MCP/McpMod.cs`.
- **`hand_select`** (e.g. discard/exhaust-N-cards effects) and the
  **crystal sphere minigame** are best-effort stubs — their exact JSON
  shapes weren't in the docs I could pull, so they're implemented
  defensively but unverified against real gameplay.
- Any screen with no matching rule prints a message and idles rather than
  guessing; extend `sts2bot/heuristic.py` when you hit one.

## Trust note

STS2MCP is a third-party mod, not made by MegaCrit. It's open source and
MIT-licensed, and its HTTP server only binds to localhost (not exposed to
your network), but it does run arbitrary code inside your game process —
worth knowing before installing it.
