"""Fixed-vocabulary <-> integer-id mapping for categorical game identifiers
(card id, enemy id, relic id, ...), meant to back nn.Embedding lookups.

Index 0 is always <pad> (for padding out variable-length lists like hand/
enemies) and index 1 is always <unk> (any id not seen when the vocab was
built -- a new card/enemy/relic you haven't encountered yet). Every
Vocab.encode() call is total: it never raises on an unknown token.
"""
from __future__ import annotations

import json
from pathlib import Path


class Vocab:
    PAD = "<pad>"
    UNK = "<unk>"

    def __init__(self):
        self.token_to_id: dict[str, int] = {self.PAD: 0, self.UNK: 1}

    def _add(self, token: str) -> None:
        if token not in self.token_to_id:
            self.token_to_id[token] = len(self.token_to_id)

    @classmethod
    def build(cls, tokens) -> "Vocab":
        v = cls()
        for t in sorted({t for t in tokens if t}):
            v._add(t)
        return v

    def encode(self, token: str | None) -> int:
        return self.token_to_id.get(token, self.token_to_id[self.UNK])

    def __len__(self) -> int:
        return len(self.token_to_id)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.token_to_id, indent=2, sort_keys=False))

    @classmethod
    def load(cls, path: str | Path) -> "Vocab":
        v = cls()
        v.token_to_id = json.loads(Path(path).read_text())
        return v
