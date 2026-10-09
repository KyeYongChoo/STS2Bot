"""Dev loader: put the game into a chosen act / deck / fight / event for
testing a heuristic, without playing a run to get there. The window is
sts2bot/loader_ui.py; this module is the part that talks to the game, kept
separate so it can be tested without a display.

Steps, in order, each waiting for the game to confirm before the next:
  1. Act:   console `act <n>` (if on a different act number), then
            `act <ACT_ID>` -- which replaces the current act's model, e.g.
            OVERGROWTH -> UNDERDOCKS, without abandoning the run.
  2. Deck:  STS2Client.dev_set_deck (needs this project's patched STS2MCP).
  3. Heal:  console `heal`.
  4. Load:  console `fight <ENCOUNTER_ID>` or `event <EVENT_ID>`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from sts2bot.deckgen import DeckCard, deck_matches
from sts2bot.dev_console import FULL_HEAL_AMOUNT, WINDOW_TITLE_HINT, run_console_commands
from sts2bot.gamedata import ACT_NUMBER, EncounterInfo

FLOORS_PER_ACT = 17  # approximate; only used to default the floor spinbox


@dataclass
class LoadRequest:
    kind: str                       # "fight" | "event" | "none" (act/deck only)
    target_id: str | None = None    # encounter id or event id
    act_id: str | None = None       # None = stay on the current act
    deck: list[DeckCard] | None = None  # None = keep the current deck
    heal: bool = True


def default_floor(encounter: EncounterInfo) -> int:
    """Rough floor a fight shows up on, for the stage-deck floor default."""
    act = min((ACT_NUMBER.get(a, 1) for a in encounter.acts), default=1)
    if encounter.kind == "Boss":
        base = FLOORS_PER_ACT - 1
    elif encounter.kind == "Elite":
        base = 10
    elif encounter.id.endswith("_WEAK"):
        base = 2
    else:
        base = 8
    return (act - 1) * FLOORS_PER_ACT + base


def act_commands(target_act_id: str, current_act: int | None, current_act_id: str | None) -> list[list[str]]:
    """Console commands to reach `target_act_id`, as separate batches -- the
    caller waits for the game between batches, since `act <n>` plays an act
    transition before `act <ID>` can replace the new act's model."""
    number = ACT_NUMBER.get(target_act_id)
    if number is None:
        raise ValueError(f"unknown act {target_act_id!r}; add it to gamedata.ACT_NUMBER")
    batches = []
    if current_act != number:
        batches.append([f"act {number}"])
    if current_act != number or current_act_id != target_act_id:
        batches.append([f"act {target_act_id}"])
    return batches


def wait_for(predicate: Callable[[], bool], timeout: float, interval: float = 0.4) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:  # game mid-transition can briefly fail to serve state
            pass
        time.sleep(interval)
    return False


def execute(request: LoadRequest, client, log: Callable[[str], None] = print,
            window_title: str = WINDOW_TITLE_HINT, console=run_console_commands) -> bool:
    """Runs a LoadRequest against the live game. Returns False (after
    logging why) if a step couldn't be confirmed; later steps don't run."""
    state = client.get_state()
    run = state.get("run")
    if not run:
        log("No run in progress -- start a run first.")
        return False

    if request.act_id:
        batches = act_commands(request.act_id, run.get("act"), run.get("act_id"))
        if not batches:
            log(f"Already on {request.act_id}.")
        for batch in batches:
            log(f"Console: {', '.join(batch)}")
            console(batch, window_title)
        if batches:
            number = ACT_NUMBER[request.act_id]
            if "act_id" in run:
                ok = wait_for(lambda: (client.get_state().get("run") or {}).get("act_id") == request.act_id, 20)
            else:  # mod build without run.act_id -- can only check the number
                log("(mod has no run.act_id -- can only confirm the act number)")
                ok = wait_for(lambda: (client.get_state().get("run") or {}).get("act") == number, 20)
            if not ok:
                log(f"Act didn't switch to {request.act_id} within 20s; stopping.")
                return False
            log(f"Now on {request.act_id}.")

    if request.deck is not None:
        log(f"Setting deck ({len(request.deck)} cards)...")
        client.dev_set_deck([c.to_api() for c in request.deck])
        if not wait_for(lambda: deck_matches((client.get_state().get("player") or {}).get("deck") or [],
                                             request.deck), 15):
            log("Deck didn't match the requested deck within 15s; stopping.")
            return False
        log("Deck set.")

    commands = []
    if request.heal:
        commands.append(f"heal {FULL_HEAL_AMOUNT}")
    if request.kind == "fight":
        commands.append(f"fight {request.target_id}")
    elif request.kind == "event":
        commands.append(f"event {request.target_id}")
    if commands:
        log(f"Console: {', '.join(commands)}")
        console(commands, window_title)
    log("Done.")
    return True
