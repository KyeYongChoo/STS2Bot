"""Static game data -- every card, event, encounter and enchantment id --
for the dev loader UI's dropdowns and the stage-deck generator.

Source: the Untapped companion app's copy of the game's model database
(%APPDATA%\\untapped-companion\\sts2\\sts2-data-public*.json -- extracted
from the game files, so ids match what the dev console and STS2MCP use).
Like the tier lists (see tierlist.py), the bot reads a project-local copy
in data/gamedata/ (gitignored) so it doesn't depend on Untapped staying
installed. Refresh both copies with:

    python -m sts2bot.gamedata
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sts2bot import tierlist

PROJECT_GAMEDATA_PATH = Path(__file__).resolve().parent.parent / "data" / "gamedata" / "sts2-data.json"

# Which act number each act model belongs to. The game data only names
# encounters' acts, not their order -- add alternates here as they ship
# (UNDERDOCKS is act 1's alternate to OVERGROWTH; acts 2 and 3 are getting
# alternates in a future update).
ACT_NUMBER: dict[str, int] = {
    "OVERGROWTH": 1,
    "UNDERDOCKS": 1,
    "HIVE": 2,
    "GLORY": 3,
}


@dataclass(frozen=True)
class EncounterInfo:
    id: str            # console `fight <id>` target, e.g. "NIBBITS_WEAK"
    kind: str          # "Monster" | "Elite" | "Boss"
    acts: tuple[str, ...]  # act model ids; empty for event-only fights
    lineups: tuple[tuple[str, ...], ...]  # possible enemy compositions


@dataclass(frozen=True)
class CardInfo:
    id: str
    name: str
    color: str   # "ironclad", "colorless", "curse", ...
    rarity: str  # "Basic", "Common", "Uncommon", "Rare", ...
    type: str


def untapped_gamedata_path() -> Path | None:
    """Newest sts2-data-public*.json in Untapped's cache (the beta file
    tracks the current game build more closely than the plain one)."""
    root = Path(os.environ.get("APPDATA", "")) / "untapped-companion" / "sts2"
    files = sorted(root.glob("sts2-data-public*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def sync_from_untapped(src: str | Path | None = None, dest: str | Path | None = None) -> Path:
    src = Path(src) if src else untapped_gamedata_path()
    if src is None or not src.exists():
        raise FileNotFoundError("no Untapped game data (sts2-data-public*.json) to copy")
    dest = Path(dest) if dest else PROJECT_GAMEDATA_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


class GameData:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else PROJECT_GAMEDATA_PATH
        if not self.path.exists():
            raise FileNotFoundError(
                f"no game data at {self.path}. Run `python -m sts2bot.gamedata` to copy it from Untapped.")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        data = raw.get("data", raw)
        self.version: str | None = data.get("version")
        self.cards = [CardInfo(c["id"], c.get("name") or c["id"], c.get("color") or "", c.get("rarity") or "",
                               c.get("type") or "")
                      for c in data.get("cards", [])]
        self.events = sorted(e["id"] for e in data.get("events", []))
        self.enchantments = sorted(e["id"] for e in data.get("enchantments", []))
        self.encounters = [EncounterInfo(e["id"], e.get("type") or "", tuple(e.get("acts") or ()),
                                         tuple(tuple(m["id"] for m in lineup) for lineup in e.get("lineups") or ()))
                           for e in data.get("encounters", [])]
        self.acts = sorted({a for e in self.encounters for a in e.acts},
                           key=lambda a: (ACT_NUMBER.get(a, 99), a))
        self._cards_by_id = {c.id: c for c in self.cards}

    def card(self, card_id: str) -> CardInfo | None:
        return self._cards_by_id.get(card_id)

    def card_pool(self, color: str, rarities: tuple[str, ...] = ("Common", "Uncommon", "Rare")) -> list[CardInfo]:
        return [c for c in self.cards if c.color == color and c.rarity in rarities]


if __name__ == "__main__":
    dest = sync_from_untapped()
    gd = GameData(dest)
    print(f"Copied game data {gd.version} to {dest}: {len(gd.cards)} cards, {len(gd.events)} events, "
          f"{len(gd.encounters)} encounters, acts {gd.acts}")
    n = tierlist.sync_from_untapped()
    print(f"Copied {n} tier-list files to {tierlist.PROJECT_TIER_LIST_DIR}")
