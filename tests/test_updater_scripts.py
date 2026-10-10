from __future__ import annotations

import io
import os
import runpy
import stat
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from polyglotmaker.update import CheckoutUpdate

ROOT = Path(__file__).resolve().parents[1]


class RootUpdaterScriptTests(unittest.TestCase):
    def test_python_entry_point_calls_github_checkout_updater(self):
        script = ROOT / "updater.py"
        result = CheckoutUpdate("main", "a" * 40, "b" * 40, False, True)
        with patch.object(sys, "argv", [str(script), "--branch", "main"]), patch(
            "polyglotmaker.update.update_repository_checkout", return_value=result
        ) as updater, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(script), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)
        updater.assert_called_once_with(ROOT, branch="main", check_only=False)

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
