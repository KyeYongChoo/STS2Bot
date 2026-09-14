#!/usr/bin/env python3
"""Scans logged trajectories (data/trajectories/*.jsonl) and builds the
categorical vocabularies encode.py needs, saving them under ml/vocab/.

Only covers what's actually been seen in play so far, so it under-covers
the full card/relic/enemy pool early on -- that's fine, Vocab.encode()
falls back to <unk> for anything unrecognized. Re-run this after
accumulating more trajectories (or once there's a compendium dump handy)
to widen coverage; nn.Embedding sizes just need to grow to match.

Usage:
  python -m ml.build_vocab
  python -m ml.build_vocab --trajectories "data/trajectories/*.jsonl"
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from collections import defaultdict
from pathlib import Path

from ml.vocab import Vocab

ENEMY_INSTANCE_SUFFIX = re.compile(r"_\d+$")


def enemy_base_id(entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    return ENEMY_INSTANCE_SUFFIX.sub("", entity_id)


def collect(paths: list[str]) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = defaultdict(set)

    def add(name, value):
        if value:
            fields[name].add(value)

    for path in paths:
        for line in open(path, encoding="utf-8"):
            state = json.loads(line)["state"]
            player = state.get("player") or {}
            battle = state.get("battle") or {}

            for c in player.get("hand") or []:
                add("card_id", c.get("id"))
                add("card_type", c.get("type"))
                add("target_type", c.get("target_type"))
                for k in c.get("keywords") or []:
                    add("keyword", k.get("name"))

            for e in battle.get("enemies") or []:
                add("enemy_id", enemy_base_id(e.get("entity_id")))
                for i in e.get("intents") or []:
                    add("intent_type", i.get("type"))

            for r in player.get("relics") or []:
                add("relic_id", r.get("id"))
            for p in player.get("potions") or []:
                add("potion_id", p.get("id"))

            cr = state.get("card_reward") or {}
            for c in cr.get("cards") or []:
                add("card_id", c.get("id"))
                add("card_type", c.get("type"))
                add("rarity", c.get("rarity"))

            rsel = state.get("relic_select") or {}
            for r in rsel.get("relics") or []:
                add("relic_id", r.get("id"))
                add("rarity", r.get("rarity"))

            treasure = state.get("treasure") or {}
            for r in treasure.get("relics") or []:
                add("relic_id", r.get("id"))
                add("rarity", r.get("rarity"))

            shop = state.get("shop") or {}
            for item in shop.get("items") or []:
                add("card_id", item.get("card_id"))
                add("relic_id", item.get("relic_id"))
                add("potion_id", item.get("potion_id"))
                add("rarity", item.get("card_rarity"))

            rewards = state.get("rewards") or {}
            for item in rewards.get("items") or []:
                add("potion_id", item.get("potion_id"))

    return fields


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories", default="data/trajectories/*.jsonl")
    parser.add_argument("--out-dir", default="ml/vocab")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.trajectories))
    if not paths:
        raise SystemExit(f"No trajectory files matched {args.trajectories!r}")

    fields = collect(paths)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, tokens in fields.items():
        vocab = Vocab.build(tokens)
        vocab.save(out_dir / f"{name}.json")
        print(f"{name}: {len(tokens)} tokens seen -> {len(vocab)} ids (incl. pad/unk)")


if __name__ == "__main__":
    main()
