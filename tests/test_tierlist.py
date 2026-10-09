"""Offline tests for sts2bot/tierlist.py -- uses temp dirs, never touches
the real Untapped cache or data/tier-lists/.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from sts2bot.tierlist import UNRANKED, TierList, sync_from_untapped


def _write_fake_untapped(root: Path) -> None:
    index = {
        "old": {"uuid": "old", "color": "ironclad", "isEnabled": True, "lastModified": "2026-01-01"},
        "new": {"uuid": "new", "color": "ironclad", "isEnabled": True, "lastModified": "2026-09-01"},
        "off": {"uuid": "off", "color": "ironclad", "isEnabled": False, "lastModified": "2026-12-01"},
        "cl": {"uuid": "cl", "color": "colorless", "isEnabled": True, "lastModified": "2026-09-01"},
    }
    lists = {
        "old": {"BASH": {"tier": "D"}},
        "new": {"BASH": {"tier": "S"}},
        "off": {"BASH": {"tier": "Almost Never"}},
        "cl": {"FINESSE": {"tier": "A"}},
    }
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    for uuid, items in lists.items():
        (root / f"{uuid}.json").write_text(json.dumps({"items": items}), encoding="utf-8")


class TierListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src = root / "untapped"
        self.dest = root / "project"
        self.src.mkdir()
        _write_fake_untapped(self.src)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sync_copies_everything(self) -> None:
        self.assertEqual(sync_from_untapped(self.src, self.dest), 5)
        self.assertTrue((self.dest / "index.json").exists())

    def test_sync_without_untapped_cache_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            sync_from_untapped(self.dest / "missing", self.dest)

    def test_rank_reads_synced_copy(self) -> None:
        sync_from_untapped(self.src, self.dest)
        tl = TierList(self.dest)
        # newest enabled ironclad list wins; disabled list is ignored
        self.assertEqual(tl.tier("BASH", "The Ironclad"), "S")
        # falls back to the colorless list
        self.assertEqual(tl.tier("FINESSE", "The Ironclad"), "A")
        self.assertEqual(tl.rank("NOT_A_CARD", "The Ironclad"), UNRANKED)

    def test_missing_dir_degrades_with_hint(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            rank = TierList(self.dest / "empty").rank("BASH", "The Ironclad")
        self.assertEqual(rank, UNRANKED)
        self.assertIn("python -m sts2bot.tierlist", out.getvalue())


if __name__ == "__main__":
    unittest.main()
