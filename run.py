#!/usr/bin/env python3
"""Entry point: plays Slay the Spire 2 via the STS2MCP mod's local API.

Prerequisites:
  - STS2MCP mod installed (see scripts/install_mod.ps1) and mods enabled
    in-game.
  - Slay the Spire 2 running, mods loaded, and a run already started
    (menu/character-select automation isn't implemented yet — start the
    run yourself, then launch this).

Examples:
  python run.py                  # deliberate pace, ~0.6s between actions
  python run.py --delay 0        # as fast as possible
  python run.py --delay 1.5      # slower, easier to follow

  # Dev/testing: warp straight to one encounter instead of playing a full
  # run to reach it (see sts2bot/dev_console.py -- uses the game's own dev
  # console via real keystrokes, since there's no API for this). Requires
  # `pip install pyautogui pygetwindow` and the STS2 window visible/
  # focusable (not just running in the background).
  python run.py --start-at NIBBITS_WEAK
  # ...and keep re-testing it: heals to full and re-warps each time the
  # fight ends, instead of falling through to normal play afterward.
  python run.py --start-at NIBBITS_WEAK --repeat-test
"""
from __future__ import annotations

import argparse

from sts2bot.bot import Bot
from sts2bot.client import STS2Client
from sts2bot.heuristic import HeuristicStrategy

COMBAT_STATE_TYPES = {"monster", "elite", "boss"}


def _make_encounter_test_hook(encounter_id: str, window_title: str, repeat: bool):
    """Returns a Bot(on_state=...) callback that heals to full when the
    targeted fight ends, optionally re-warping to it for another attempt.
    "Ended" is detected as a monster/elite/boss state_type transitioning to
    anything else -- covers a win (-> rewards) as well as a run-ending loss
    (-> game_over), though healing obviously can't recover from the latter.
    """
    from sts2bot.dev_console import heal_to_full, warp_to_fight

    state = {"was_in_combat": False}

    def on_state(s: dict) -> None:
        in_combat = s.get("state_type") in COMBAT_STATE_TYPES
        if state["was_in_combat"] and not in_combat:
            print(f"[test-loop] '{encounter_id}' ended (now {s.get('state_type')}); healing to full.")
            heal_to_full(window_title)
            if repeat:
                print(f"[test-loop] re-warping to '{encounter_id}' for another attempt.")
                warp_to_fight(encounter_id, window_title)
        state["was_in_combat"] = in_combat

    return on_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=15526)
    parser.add_argument("--delay", type=float, default=0.6,
                         help="Seconds to wait between actions (0 = as fast as possible).")
    parser.add_argument("--log-dir", default="data/trajectories",
                         help="Where to write (state, action) trajectory logs for future DNN training.")
    parser.add_argument("--start-at", metavar="ENCOUNTER_ID", default=None,
                         help="Dev/testing only: warp to this encounter's fight via the game's dev "
                              "console before starting the bot loop, e.g. NIBBITS_WEAK. See "
                              "Encounter.fight_id in sts2bot/encounters.py for known IDs.")
    parser.add_argument("--repeat-test", action="store_true",
                         help="With --start-at: heal to full and re-warp to the same encounter every "
                              "time it ends, instead of letting the bot continue into normal play. "
                              "Runs until Ctrl+C.")
    parser.add_argument("--window-title", default="Slay the Spire 2",
                         help="Substring to find the game window by, for --start-at/--repeat-test.")
    args = parser.parse_args()

    client = STS2Client(host=args.host, port=args.port)
    strategy = HeuristicStrategy()

    on_state = None
    if args.start_at:
        from sts2bot.dev_console import warp_to_fight
        print(f"Warping to encounter '{args.start_at}' via the game's dev console "
              f"(window title contains {args.window_title!r})...")
        warp_to_fight(args.start_at, args.window_title)
        on_state = _make_encounter_test_hook(args.start_at, args.window_title, args.repeat_test)
    elif args.repeat_test:
        parser.error("--repeat-test requires --start-at")

    bot = Bot(client, strategy, delay=args.delay, log_dir=args.log_dir, on_state=on_state)
    bot.run_forever()


if __name__ == "__main__":
    main()
