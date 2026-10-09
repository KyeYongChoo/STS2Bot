"""Reads tier-list data originally from the Untapped companion app (a
separate desktop app, not a game mod) so card-reward picks can rank options
by community tier instead of just rarity.

The bot reads a project-local copy in data/tier-lists/ (gitignored --
these are other people's lists, e.g. Baalorlord's), so it keeps working
once Untapped is uninstalled from the machine running bot runs. Refresh
the copy from Untapped's cache with:

    python -m sts2bot.tierlist

Untapped's cache (same layout as the copy) lives at %APPDATA%\\untapped-companion\\sts2\\tier-lists\\:
  - index.json lists every installed tier list (uuid, name, author,
    "color" = which class it's for, e.g. "ironclad"/"silent"/"colorless",
    isEnabled, gameVersion).
  - <uuid>.json holds {"items": {CARD_ID: {"tier": "S"/"A"/..., ...}}}.

This module only reads Untapped's data; it never writes to the companion app.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

PROJECT_TIER_LIST_DIR = Path(__file__).resolve().parent.parent / "data" / "tier-lists"

# Baalorlord's lists use a handful of non-letter labels for standout
# cards alongside S/A/B/C/D. Ranking below is a judgment call -- tune
# freely. Anything not in this map (including a card missing from the
# tier list entirely) ranks below everything here.
TIER_RANK = {
    "Always Amazing": 9,
    "S": 8,
    "Always Good": 7,
    "A": 6,
    "B": 5,
    "Needs Synergy": 4,
    "Inconsistent": 4,
    "C": 3,
    "D": 2,
    "Almost Never": 1,
}
UNRANKED = 0


def untapped_tier_list_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "untapped-companion" / "sts2" / "tier-lists"


def sync_from_untapped(src: str | Path | None = None, dest: str | Path | None = None) -> int:
    """Copies Untapped's cached tier lists (index.json + every <uuid>.json)
    into the project-local dir. Returns the number of files copied; raises
    FileNotFoundError if Untapped has no cached index.json to copy."""
    src = Path(src) if src else untapped_tier_list_dir()
    dest = Path(dest) if dest else PROJECT_TIER_LIST_DIR
    if not (src / "index.json").exists():
        raise FileNotFoundError(f"no Untapped tier-list cache at {src}")
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in src.glob("*.json"):
        shutil.copy2(path, dest / path.name)
        copied += 1
    return copied


def _character_to_color(character: str | None) -> str | None:
    if not character:
        return None
    return character.strip().lower().removeprefix("the ").replace(" ", "")


class TierList:
    """Loads and caches Untapped tier-list rankings, keyed by class.

    Degrades to "everything unranked" if no lists have been synced into
    data/tier-lists/ yet -- callers should treat rank() as advisory, not
    required.
    """

    def __init__(self, tier_list_dir: str | Path | None = None):
        self.dir = Path(tier_list_dir) if tier_list_dir else PROJECT_TIER_LIST_DIR
        self._index: dict | None = None
        self._items_by_color: dict[str, dict[str, dict]] = {}

    def _load_index(self) -> dict:
        if self._index is None:
            self._index = {}
            index_path = self.dir / "index.json"
            if index_path.exists():
                try:
                    self._index = json.loads(index_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    self._index = {}
            else:
                print(f"[tierlist] no tier lists at {self.dir}; card picks fall back to "
                      f"rarity. Run `python -m sts2bot.tierlist` to copy them from Untapped.")
        return self._index

    def _items_for_color(self, color: str) -> dict[str, dict]:
        if color in self._items_by_color:
            return self._items_by_color[color]

        items: dict[str, dict] = {}
        candidates = [e for e in self._load_index().values()
                      if e.get("isEnabled") and e.get("color") == color]
        # Newest-updated wins if more than one enabled list exists for a class.
        candidates.sort(key=lambda e: e.get("lastModified", ""), reverse=True)
        if candidates:
            path = self.dir / f"{candidates[0]['uuid']}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = data.get("items", {})
            except (json.JSONDecodeError, OSError):
                items = {}

        self._items_by_color[color] = items
        return items

    def rank(self, card_id: str | None, character: str | None) -> int:
        """Higher is better. Unranked (or if no tier-list data at all) is 0."""
        if not card_id:
            return UNRANKED
        color = _character_to_color(character)
        entry = None
        if color:
            entry = self._items_for_color(color).get(card_id)
        if entry is None:
            entry = self._items_for_color("colorless").get(card_id)
        if entry is None:
            return UNRANKED
        return TIER_RANK.get(entry.get("tier"), UNRANKED)

    def tier(self, card_id: str | None, character: str | None) -> str | None:
        """Raw tier label (e.g. "S"), for logging/debugging."""
        if not card_id:
            return None
        color = _character_to_color(character)
        entry = None
        if color:
            entry = self._items_for_color(color).get(card_id)
        if entry is None:
            entry = self._items_for_color("colorless").get(card_id)
        return entry.get("tier") if entry else None


if __name__ == "__main__":
    n = sync_from_untapped()
    print(f"Copied {n} tier-list files from {untapped_tier_list_dir()} to {PROJECT_TIER_LIST_DIR}")
    for entry in TierList()._load_index().values():
        if entry.get("isEnabled"):
            print(f"  enabled: {entry.get('name')} ({entry.get('color')})")
