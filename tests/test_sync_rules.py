from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_rules.py"
SPEC = importlib.util.spec_from_file_location("sync_rules", SCRIPT)
assert SPEC and SPEC.loader
sync_rules = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_rules)


class SyncRulesTests(unittest.TestCase):
    def test_update_file_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            self.assertTrue(sync_rules.update_file(path, "one\n", check=False))
            self.assertEqual(path.read_text(encoding="utf-8"), "one\n")
            self.assertFalse(sync_rules.update_file(path, "one\n", check=False))

    def test_check_mode_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            self.assertTrue(sync_rules.update_file(path, "one\n", check=True))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
