from __future__ import annotations

import io
import os
import runpy
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from polyglotmaker.update import CheckoutUpdate, GITHUB_UPDATE_BRANCH

ROOT = Path(__file__).resolve().parents[1]


class RootUpdaterScriptTests(unittest.TestCase):
    def _assert_bootstrap_reexecs(self, setting: str, *, from_env: bool):
        selected = sys.executable
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "updater.py"
            script.write_text((ROOT / "updater.py").read_text(encoding="utf-8"), encoding="utf-8")
            if not from_env:
                (Path(temporary) / "python.env").write_text(setting + "\n", encoding="utf-8")

            environment = {"2PY2": setting if from_env else ""}
            with patch.dict("os.environ", environment), patch.object(sys, "executable", "/different/python"), patch.object(
                sys, "argv", [str(script), "--check"]
            ), patch("os.execv", side_effect=OSError("test re-exec")) as execv, redirect_stderr(
                io.StringIO()
            ):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(script), run_name="__main__")

            self.assertEqual(raised.exception.code, 2)
            execv.assert_called_once_with(selected, [selected, str(script), "--check"])

    def test_python_bootstrap_reexecs_using_2py2(self):
        self._assert_bootstrap_reexecs(sys.executable, from_env=True)

    def test_python_bootstrap_reexecs_using_python_env(self):
        self._assert_bootstrap_reexecs(sys.executable, from_env=False)

    def test_python_entry_point_calls_github_checkout_updater(self):
        script = ROOT / "updater.py"
        result = CheckoutUpdate(GITHUB_UPDATE_BRANCH, "a" * 40, "b" * 40, False, True)
        with patch.dict("os.environ", {"2PY2": sys.executable}), patch.object(
            sys, "argv", [str(script), "--check"]
        ), patch("polyglotmaker.update.update_repository_checkout", return_value=result) as updater, redirect_stdout(
            io.StringIO()
        ):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(script), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)
        updater.assert_called_once_with(ROOT, check_only=True)

    def test_shell_and_batch_launchers_target_python_entry_point(self):
        shell = (ROOT / "updater.sh").read_text(encoding="utf-8")
        batch = (ROOT / "updater.bat").read_text(encoding="utf-8")
        self.assertIn('"$SCRIPT_DIR/updater.py" "$@"', shell)
        self.assertIn('"%SCRIPT_DIR%updater.py" %*', batch)
        self.assertIn("2PY2", shell)
        self.assertIn("python.env", shell)
        self.assertIn("2PY2", batch)
        self.assertIn("python.env", batch)

    def test_python_entry_point_rejects_branch_overrides(self):
        script = ROOT / "updater.py"
        with patch.dict("os.environ", {"2PY2": sys.executable}), patch.object(
            sys, "argv", [str(script), "--branch", "main"]
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(raised.exception.code, 2)

    @unittest.skipIf(os.name == "nt", "POSIX shell launcher")
    def test_shell_launcher_honors_digit_leading_2py2_environment(self):
        environment = os.environ.copy()
        environment["2PY2"] = "/missing/polyglot/python"
        completed = subprocess.run(
            [str(ROOT / "updater.sh"), "--help"],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 127)
        self.assertIn("/missing/polyglot/python", completed.stderr)

    @unittest.skipIf(os.name == "nt", "Unix executable-bit check")
    def test_shell_launcher_is_executable(self):
        mode = (ROOT / "updater.sh").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
