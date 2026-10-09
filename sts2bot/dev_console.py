"""Drives STS2's built-in developer console via real, OS-level keystrokes.

Everything else in this bot goes through the STS2MCP mod's HTTP API (see
README's "Trust note") -- this module is the one exception. There's no
HTTP action for console commands: McpMod.Actions.cs's full dispatch table
(ExecuteAction's switch statement) only covers real gameplay actions
(play_card, end_turn, choose_map_node, ...), nothing console-related. The
game's dev console is the only way to warp straight to a specific fight or
force-heal, so this is a dev/testing convenience -- used to rapidly
iterate on a single encounter's heuristic instead of replaying a full run
to reach it -- not something the bot touches during normal play.

Confirmed interaction (from the user, playing manually):
  ` (backtick) opens/closes the console. Typing a command and pressing
  Enter executes it and leaves the console open for the next command.

Requires `pyautogui` and `pygetwindow` (see requirements.txt) -- imported
lazily inside functions so the rest of the bot has no hard dependency on
GUI-automation libraries or a display.
"""
from __future__ import annotations

import time

CONSOLE_KEY = "`"
WINDOW_TITLE_HINT = "Slay the Spire 2"
# 1000 HP is comfortably above any character's max_hp -- "heal <amount>"
# caps at max_hp, so this is just a reliable "heal to full" regardless of
# which character/relics are in play.
FULL_HEAL_AMOUNT = 1000


def focus_game_window(title_hint: str = WINDOW_TITLE_HINT) -> None:
    """Brings the STS2 window to the foreground so keystrokes land in the
    game rather than wherever else has focus (this terminal, your editor).

    Windows can silently refuse to hand focus to a background process
    (its "foreground lock" anti-focus-stealing behavior) -- if this seems
    to run without error but keys aren't reaching the game, click the STS2
    window once yourself before retrying.
    """
    import pygetwindow as gw

    windows = [w for w in gw.getAllWindows() if title_hint.lower() in w.title.lower()]
    if not windows:
        raise RuntimeError(
            f"No window found with title containing {title_hint!r}. Is Slay the Spire 2 "
            "running? Pass a different --window-title if its title doesn't match."
        )
    window = windows[0]
    if window.isMinimized:
        window.restore()
    window.activate()
    time.sleep(0.3)  # let the OS actually complete the focus switch before sending keys


def run_console_command(command: str, *, settle: float = 0.15) -> None:
    """Opens the console (if not already open), types `command`, and
    presses Enter -- leaves the console open, matching the game's own
    behavior, so the caller can chain more commands or call
    close_console() when done."""
    import pyautogui

    pyautogui.press(CONSOLE_KEY)
    time.sleep(settle)
    pyautogui.typewrite(command, interval=0.02)
    time.sleep(settle)
    pyautogui.press("enter")
    time.sleep(settle)


def close_console(*, settle: float = 0.15) -> None:
    import pyautogui

    pyautogui.press(CONSOLE_KEY)
    time.sleep(settle)


def warp_to_fight(encounter_id: str, window_title_hint: str = WINDOW_TITLE_HINT) -> None:
    """Jumps straight to the start of `encounter_id`'s combat, e.g.
    warp_to_fight("NIBBITS_WEAK") for Act 1's lone-Nibbit easy-pool fight
    (see Encounter.fight_id in sts2bot/encounters.py for other known IDs --
    only encounters your profile has actually fought have a confirmed one;
    unconfirmed ones are left as None there)."""
    focus_game_window(window_title_hint)
    run_console_command(f"fight {encounter_id}")
    close_console()


def heal_to_full(window_title_hint: str = WINDOW_TITLE_HINT) -> None:
    """Tops HP back up to max via the console -- meant for right after a
    test fight ends (win or loss-by-HP-depletion mid-fight isn't possible
    to recover from this way, only a genuine run-ending death is), so
    repeated test attempts at the same encounter don't need a rest site."""
    focus_game_window(window_title_hint)
    run_console_command(f"heal {FULL_HEAL_AMOUNT}")
    close_console()
