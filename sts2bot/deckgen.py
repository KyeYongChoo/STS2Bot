"""Deck setups for dev test fights (used by sts2bot/loader_ui.py).

A deck here is a list of DeckCard -- what STS2Client.dev_set_deck takes
(via to_api()) and what player.deck reads back as (via from_state()).
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from sts2bot.gamedata import CardInfo, GameData

STARTER_DECKS: dict[str, list[str]] = {
    "ironclad": ["STRIKE_IRONCLAD"] * 5 + ["DEFEND_IRONCLAD"] * 4 + ["BASH"],
}

# Rarity odds for a normal fight's card reward. These are STS1's numbers
# (60/37/3) -- STS2's exact odds aren't confirmed; tune if they turn out
# different.
REWARD_RARITY_ODDS: dict[str, float] = {"Common": 0.60, "Uncommon": 0.37, "Rare": 0.03}


@dataclass(frozen=True)
class DeckCard:
    id: str
    upgrades: int = 0
    enchantment: str | None = None
    enchantment_amount: int = 1

    def to_api(self) -> dict:
        d: dict = {"id": self.id}
        if self.upgrades:
            d["upgrades"] = self.upgrades
        if self.enchantment:
            d["enchantment"] = self.enchantment
            d["enchantment_amount"] = self.enchantment_amount
        return d

    def label(self, gamedata: GameData | None = None) -> str:
        info = gamedata.card(self.id) if gamedata else None
        name = info.name if info else self.id
        text = name + ("+" * self.upgrades if self.upgrades <= 1 else f"+{self.upgrades}")
        if self.enchantment:
            text += f"  [{self.enchantment} {self.enchantment_amount}]"
        return f"{text}   ({self.id})"

    @classmethod
    def from_state(cls, card: dict) -> "DeckCard":
        upgrades = card.get("upgrades")
        if upgrades is None:  # older mod build without the upgrades field
            upgrades = 1 if card.get("is_upgraded") else 0
        return cls(card["id"], upgrades, card.get("enchantment") or None, card.get("enchantment_amount") or 1)


def from_state_deck(deck: list[dict]) -> list[DeckCard]:
    return [DeckCard.from_state(c) for c in deck]


def starter_deck(color: str) -> list[DeckCard]:
    if color not in STARTER_DECKS:
        raise ValueError(f"no starter deck defined for {color!r} (add it to STARTER_DECKS)")
    return [DeckCard(card_id) for card_id in STARTER_DECKS[color]]


def basic_card_ids(gamedata: GameData, color: str) -> set[str]:
    return {c.id for c in gamedata.cards if c.color == color and c.rarity == "Basic"}


def random_reward_cards(pool: list[CardInfo], n: int, rng: random.Random,
                        odds: dict[str, float] = REWARD_RARITY_ODDS) -> list[DeckCard]:
    """n cards drawn like card rewards: roll a rarity by `odds`, then a
    uniform card of that rarity from `pool`. Duplicates allowed, as in a
    real run."""
    by_rarity = {r: [c for c in pool if c.rarity == r] for r in odds}
    rarities = [r for r in odds if by_rarity[r]]
    if n > 0 and not rarities:
        raise ValueError("card pool has no cards of any rewardable rarity")
    weights = [odds[r] for r in rarities]
    picks = []
    for _ in range(n):
        rarity = rng.choices(rarities, weights)[0]
        picks.append(DeckCard(rng.choice(by_rarity[rarity]).id))
    return picks


def stage_deck(current: list[DeckCard], floor: int, gamedata: GameData, color: str,
               rng: random.Random) -> list[DeckCard]:
    """A deck roughly like one you'd have reached `floor` with: keep the
    current deck's basic cards (Strike/Defend/Bash, upgrades and all), drop
    everything else, then add floor-1 random reward-odds cards of the
    character's color."""
    keep_ids = basic_card_ids(gamedata, color)
    kept = [c for c in current if c.id in keep_ids]
    return kept + random_reward_cards(gamedata.card_pool(color), max(0, floor - 1), rng)


def deck_matches(state_deck: list[dict], target: list[DeckCard]) -> bool:
    """True once the live player.deck holds exactly `target` (order-free)."""
    return Counter(from_state_deck(state_deck)) == Counter(target)
