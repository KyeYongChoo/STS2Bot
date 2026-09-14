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
"""
from __future__ import annotations

import argparse

from sts2bot.bot import Bot
from sts2bot.client import STS2Client
from sts2bot.heuristic import HeuristicStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=15526)
    parser.add_argument("--delay", type=float, default=0.6,
                         help="Seconds to wait between actions (0 = as fast as possible).")
    parser.add_argument("--log-dir", default="data/trajectories",
                         help="Where to write (state, action) trajectory logs for future DNN training.")
    args = parser.parse_args()

    client = STS2Client(host=args.host, port=args.port)
    strategy = HeuristicStrategy()
    bot = Bot(client, strategy, delay=args.delay, log_dir=args.log_dir)
    bot.run_forever()


if __name__ == "__main__":
    main()
