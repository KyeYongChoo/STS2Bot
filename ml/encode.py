"""Encodes raw STS2MCP JSON state into fixed-size numeric arrays for a NN.

The core idea: card/relic/potion/enemy identity, and small closed-vocabulary
fields (type, rarity, target_type, keyword names, intent type) become
integer ids via ml.vocab.Vocab, meant for nn.Embedding lookups. This module
never reads the natural-language `description` field -- that text exists
for humans/LLMs, and a network doesn't need it once identity + these
structured fields are available. The one place free text is unavoidable is
raw damage/block *magnitudes*, which are parsed out with a small regex
helper (`_first_int`) rather than left as prose.

Currently only combat states (monster/elite/boss) are covered, since
that's where hand-of-cards + enemy encoding lives. Map/shop/rewards/etc.
follow the same pattern (id/categorical -> Vocab, numeric fields kept as
numeric) if/when it's worth building a learned policy for those too.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from ml.vocab import Vocab

MAX_HAND = 10
MAX_ENEMIES = 5

_ENEMY_INSTANCE_SUFFIX = re.compile(r"_\d+$")
_FIRST_INT = re.compile(r"\d+")


def enemy_base_id(entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    return _ENEMY_INSTANCE_SUFFIX.sub("", entity_id)


def _first_int(text: Any) -> int | None:
    if not text:
        return None
    m = _FIRST_INT.search(str(text))
    return int(m.group()) if m else None


def _cost_int(cost: Any, energy: int = 0) -> int:
    if cost is None:
        return 0
    s = str(cost).strip().upper()
    if s == "X":
        return energy
    try:
        return int(s)
    except ValueError:
        return 0


class Vocabs:
    """All the Vocab objects the encoder needs, loaded from ml/vocab/."""

    FIELDS = ("card_id", "enemy_id", "relic_id", "potion_id",
              "card_type", "target_type", "rarity", "keyword", "intent_type")

    def __init__(self, vocab_dir: str | Path = "ml/vocab"):
        d = Path(vocab_dir)
        for name in self.FIELDS:
            setattr(self, name, Vocab.load(d / f"{name}.json"))


def encode_card(card: dict, v: Vocabs, energy: int = 0) -> dict:
    keywords = np.zeros(len(v.keyword), dtype=np.float32)
    for k in card.get("keywords") or []:
        keywords[v.keyword.encode(k.get("name"))] = 1.0
    return {
        "id": v.card_id.encode(card.get("id")),
        "cost": float(_cost_int(card.get("cost"), energy)),
        "type": v.card_type.encode(card.get("type")),
        "target_type": v.target_type.encode(card.get("target_type")),
        "is_upgraded": float(bool(card.get("is_upgraded"))),
        "can_play": float(bool(card.get("can_play"))),
        "keywords": keywords,
    }


def encode_enemy(enemy: dict, v: Vocabs) -> dict:
    intents = enemy.get("intents") or []
    intent = intents[0] if intents else {}
    max_hp = enemy.get("max_hp") or 1
    dmg = _first_int(intent.get("label"))
    if dmg is None:
        dmg = _first_int(intent.get("description")) or 0
    return {
        "id": v.enemy_id.encode(enemy_base_id(enemy.get("entity_id"))),
        "hp_frac": enemy.get("hp", 0) / max_hp,
        "block": float(enemy.get("block", 0)),
        "intent_type": v.intent_type.encode(intent.get("type")),
        "intent_damage": float(dmg),
    }


def encode_combat_state(state: dict, v: Vocabs,
                         max_hand: int = MAX_HAND, max_enemies: int = MAX_ENEMIES) -> dict[str, np.ndarray]:
    """One combat screen (state_type monster/elite/boss) -> fixed-size arrays.

    Padding: hand/enemy arrays are always length max_hand/max_enemies;
    `hand_mask`/`enemy_mask` are 1.0 for real entries and 0.0 for padding
    (id 0 there too, i.e. <pad>) so a model can ignore padded slots.
    """
    player = state.get("player") or {}
    battle = state.get("battle") or {}
    energy = player.get("energy", 0)
    hand = (player.get("hand") or [])[:max_hand]
    enemies = [e for e in (battle.get("enemies") or []) if e.get("hp", 0) > 0][:max_enemies]

    out = {
        "hand_ids": np.zeros(max_hand, dtype=np.int64),
        "hand_cost": np.zeros(max_hand, dtype=np.float32),
        "hand_type": np.zeros(max_hand, dtype=np.int64),
        "hand_target": np.zeros(max_hand, dtype=np.int64),
        "hand_upgraded": np.zeros(max_hand, dtype=np.float32),
        "hand_can_play": np.zeros(max_hand, dtype=np.float32),
        "hand_keywords": np.zeros((max_hand, len(v.keyword)), dtype=np.float32),
        "hand_mask": np.zeros(max_hand, dtype=np.float32),
        "enemy_ids": np.zeros(max_enemies, dtype=np.int64),
        "enemy_hp": np.zeros(max_enemies, dtype=np.float32),
        "enemy_block": np.zeros(max_enemies, dtype=np.float32),
        "enemy_intent": np.zeros(max_enemies, dtype=np.int64),
        "enemy_dmg": np.zeros(max_enemies, dtype=np.float32),
        "enemy_mask": np.zeros(max_enemies, dtype=np.float32),
    }

    for i, c in enumerate(hand):
        enc = encode_card(c, v, energy)
        out["hand_ids"][i] = enc["id"]
        out["hand_cost"][i] = enc["cost"]
        out["hand_type"][i] = enc["type"]
        out["hand_target"][i] = enc["target_type"]
        out["hand_upgraded"][i] = enc["is_upgraded"]
        out["hand_can_play"][i] = enc["can_play"]
        out["hand_keywords"][i] = enc["keywords"]
        out["hand_mask"][i] = 1.0

    for i, e in enumerate(enemies):
        enc = encode_enemy(e, v)
        out["enemy_ids"][i] = enc["id"]
        out["enemy_hp"][i] = enc["hp_frac"]
        out["enemy_block"][i] = enc["block"]
        out["enemy_intent"][i] = enc["intent_type"]
        out["enemy_dmg"][i] = enc["intent_damage"]
        out["enemy_mask"][i] = 1.0

    max_energy = player.get("max_energy") or max(energy, 1)
    out["player_scalars"] = np.array([
        player.get("hp", 0) / max(player.get("max_hp", 1), 1),
        float(player.get("block", 0)),
        float(energy),
        float(max_energy),
    ], dtype=np.float32)

    relic_ids = [v.relic_id.encode(r.get("id")) for r in (player.get("relics") or [])]
    potion_ids = [v.potion_id.encode(p.get("id")) for p in (player.get("potions") or [])]
    out["relic_ids"] = np.array(relic_ids, dtype=np.int64)
    out["potion_ids"] = np.array(potion_ids, dtype=np.int64)

    return out


def encode_combat_action(action: dict, state: dict, max_hand: int = MAX_HAND) -> dict[str, int]:
    """Maps the heuristic's chosen action to training labels aligned with
    encode_combat_state's hand/enemy slot ordering.

    card_slot: index into the (unpadded) hand as sent to the API, or
    max_hand as a sentinel meaning "end_turn".
    target_slot: index into the (alive-filtered) enemy list the action's
    target entity_id refers to, or -1 if the action had no target.
    """
    name = action["name"]
    params = action.get("params") or {}
    if name == "end_turn":
        return {"card_slot": max_hand, "target_slot": -1}

    card_slot = int(params.get("card_index", -1))
    target_slot = -1
    target = params.get("target")
    if target:
        battle = state.get("battle") or {}
        enemies = [e for e in (battle.get("enemies") or []) if e.get("hp", 0) > 0]
        for i, e in enumerate(enemies):
            if e.get("entity_id") == target:
                target_slot = i
                break
    return {"card_slot": card_slot, "target_slot": target_slot}
