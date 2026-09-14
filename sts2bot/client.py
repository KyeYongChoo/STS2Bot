"""Thin REST client for the STS2MCP mod's local HTTP API.

See vendor/STS2MCP (https://github.com/Gennadiyev/STS2MCP) for the mod
that exposes this API from inside a running Slay the Spire 2 process.
"""
from __future__ import annotations

import time
from typing import Any

import requests


class STS2ApiError(RuntimeError):
    """Raised when the mod's API returns status != 'ok'."""


class STS2Client:
    def __init__(self, host: str = "localhost", port: int = 15526, timeout: float = 5.0):
        self.base_url = f"http://{host}:{port}/api/v1"
        self.timeout = timeout
        self.session = requests.Session()

    def wait_until_ready(self, retry_delay: float = 2.0) -> None:
        while True:
            try:
                self.get_state()
                return
            except requests.exceptions.RequestException:
                print(f"Waiting for STS2MCP API at {self.base_url} ... "
                      f"(is the game running with mods enabled?)")
                time.sleep(retry_delay)

    def get_state(self) -> dict[str, Any]:
        resp = self.session.get(
            f"{self.base_url}/singleplayer",
            params={"format": "json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def act(self, action: str, **params: Any) -> dict[str, Any]:
        body = {"action": action, **{k: v for k, v in params.items() if v is not None}}
        resp = self.session.post(
            f"{self.base_url}/singleplayer",
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            raise STS2ApiError(f"{action}({params}) -> {data.get('message')}")
        return data

    # --- convenience wrappers, one per action the mod supports -------------

    def play_card(self, card_index: int, target: str | None = None):
        return self.act("play_card", card_index=card_index, target=target)

    def end_turn(self):
        return self.act("end_turn")

    def use_potion(self, slot: int, target: str | None = None):
        return self.act("use_potion", slot=slot, target=target)

    def discard_potion(self, slot: int):
        return self.act("discard_potion", slot=slot)

    def combat_select_card(self, card_index: int):
        return self.act("combat_select_card", card_index=card_index)

    def combat_confirm_selection(self):
        return self.act("combat_confirm_selection")

    def choose_map_node(self, index: int):
        return self.act("choose_map_node", index=index)

    def choose_event_option(self, index: int):
        return self.act("choose_event_option", index=index)

    def advance_dialogue(self):
        return self.act("advance_dialogue")

    def choose_rest_option(self, index: int):
        return self.act("choose_rest_option", index=index)

    def shop_purchase(self, index: int):
        return self.act("shop_purchase", index=index)

    def claim_reward(self, index: int):
        return self.act("claim_reward", index=index)

    def select_card_reward(self, card_index: int):
        return self.act("select_card_reward", card_index=card_index)

    def skip_card_reward(self):
        return self.act("skip_card_reward")

    def proceed(self):
        return self.act("proceed")

    def select_card(self, index: int):
        return self.act("select_card", index=index)

    def confirm_selection(self):
        return self.act("confirm_selection")

    def cancel_selection(self):
        return self.act("cancel_selection")

    def select_relic(self, index: int | None = None):
        return self.act("select_relic", index=index)

    def skip_relic_selection(self):
        return self.act("skip_relic_selection")

    def claim_treasure_relic(self, index: int):
        return self.act("claim_treasure_relic", index=index)

    def crystal_sphere_set_tool(self, tool: str):
        return self.act("crystal_sphere_set_tool", tool=tool)

    def crystal_sphere_click_cell(self, x: int, y: int):
        return self.act("crystal_sphere_click_cell", x=x, y=y)

    def crystal_sphere_proceed(self):
        return self.act("crystal_sphere_proceed")

    def menu_select(self, option: str, seed: str | None = None):
        return self.act("menu_select", option=option, seed=seed)
