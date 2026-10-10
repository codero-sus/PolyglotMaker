from __future__ import annotations

import os
import runpy
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class RootUpdaterScriptTests(unittest.TestCase):
    def test_python_entry_point_forwards_update_arguments(self):
        script = ROOT / "updater.py"
        with patch.object(sys, "argv", [str(script), "--check", "--json"]), patch(
            "polyglotmaker.cli.main", return_value=7
        ) as cli_main:
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(script), run_name="__main__")

        self.assertEqual(raised.exception.code, 7)
        cli_main.assert_called_once_with(["update", "--check", "--json"])

    def test_shell_and_batch_launchers_target_python_entry_point(self):
        shell = (ROOT / "updater.sh").read_text(encoding="utf-8")
        batch = (ROOT / "updater.bat").read_text(encoding="utf-8")
        self.assertIn('"$SCRIPT_DIR/updater.py" "$@"', shell)
        self.assertIn('"%SCRIPT_DIR%updater.py" %*', batch)

    @unittest.skipIf(os.name == "nt", "Unix executable-bit check")
    def test_shell_launcher_is_executable(self):
        mode = (ROOT / "updater.sh").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
