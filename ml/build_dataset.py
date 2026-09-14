#!/usr/bin/env python3
"""Converts logged trajectories into a single .npz of stacked numeric
arrays, ready to load into a PyTorch/numpy training loop -- no JSON, no
natural-language text, just ids and floats. Combat decisions only
(state_type monster/elite/boss); see ml/encode.py.

Usage:
  python -m ml.build_dataset
  python -m ml.build_dataset --trajectories "data/trajectories/*.jsonl" --out data/dataset/combat.npz
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from ml.encode import Vocabs, encode_combat_action, encode_combat_state

COMBAT_STATE_TYPES = {"monster", "elite", "boss"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trajectories", default="data/trajectories/*.jsonl")
    parser.add_argument("--vocab-dir", default="ml/vocab")
    parser.add_argument("--out", default="data/dataset/combat.npz")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.trajectories))
    if not paths:
        raise SystemExit(f"No trajectory files matched {args.trajectories!r}")

    v = Vocabs(args.vocab_dir)
    columns: dict[str, list] = defaultdict(list)
    n_seen = 0
    n_kept = 0

    for path in paths:
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            n_seen += 1
            if rec["state_type"] not in COMBAT_STATE_TYPES:
                continue
            n_kept += 1
            feats = encode_combat_state(rec["state"], v)
            labels = encode_combat_action(rec["action"], rec["state"])
            for k, arr in feats.items():
                columns[k].append(arr)
            columns["card_slot"].append(labels["card_slot"])
            columns["target_slot"].append(labels["target_slot"])

    # relic_ids/potion_ids are variable-length (owned relics/potions differ
    # run to run) -- everything else is fixed-size and stacks directly.
    arrays = {}
    for k, vals in columns.items():
        if k in ("relic_ids", "potion_ids"):
            arrays[k] = np.array(vals, dtype=object)
        else:
            arrays[k] = np.stack(vals)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrays)

    print(f"{n_seen} decisions scanned, {n_kept} were combat -> {out_path}")
    print(f"hand_ids: {arrays['hand_ids'].shape}, enemy_ids: {arrays['enemy_ids'].shape}, "
          f"card_slot labels: {arrays['card_slot'].shape}")


if __name__ == "__main__":
    main()
