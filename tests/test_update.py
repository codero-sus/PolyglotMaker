from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from polyglotmaker.cli import main
from polyglotmaker.update import (
    GITHUB_API,
    GitHubSnapshot,
    UpdateError,
    fetch_latest_snapshot,
    install_snapshot,
    is_snapshot_installed,
    record_installed_snapshot,
    update_repository_checkout,
)


def _response(payload):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return response


class GitHubLookupTests(unittest.TestCase):
    def test_default_branch_is_resolved_and_archive_is_pinned_to_its_commit(self):
        sha = "A" * 40
        with patch(
            "polyglotmaker.update.urlopen",
            side_effect=[_response({"default_branch": "main"}), _response({"sha": sha})],
        ) as opener:
            snapshot = fetch_latest_snapshot()

        self.assertEqual(snapshot.branch, "main")
        self.assertEqual(snapshot.commit, sha.lower())
        self.assertEqual(snapshot.url, f"https://github.com/codero-sus/PolyglotMaker/commit/{sha.lower()}")
        self.assertEqual(
            snapshot.source_url,
            f"https://github.com/codero-sus/PolyglotMaker/archive/{sha.lower()}.zip",
        )
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(opener.call_args_list[0].args[0].full_url, GITHUB_API)
        self.assertEqual(opener.call_args_list[1].args[0].full_url, f"{GITHUB_API}/commits/main")
        self.assertIn("PolyglotMaker/", opener.call_args_list[0].args[0].get_header("User-agent"))

    def test_selected_branch_is_url_encoded(self):
        with patch(
            "polyglotmaker.update.urlopen",
            side_effect=[_response({"default_branch": "main"}), _response({"sha": "b" * 40})],
        ) as opener:
            snapshot = fetch_latest_snapshot("feature/release-candidate")

        self.assertEqual(snapshot.branch, "feature/release-candidate")
        self.assertEqual(
            opener.call_args_list[1].args[0].full_url,
            f"{GITHUB_API}/commits/feature%2Frelease-candidate",
        )

    def test_bad_commit_id_is_rejected(self):
        with patch(
            "polyglotmaker.update.urlopen",
            side_effect=[_response({"default_branch": "main"}), _response({"sha": "not-a-commit"})],
        ):
            with self.assertRaises(UpdateError):
                fetch_latest_snapshot()

    def test_missing_repository_or_branch_is_reported(self):
        error = HTTPError("https://api.github.com/", 404, "Not Found", {}, None)
        with patch("polyglotmaker.update.urlopen", side_effect=error):
            with self.assertRaises(UpdateError):
                fetch_latest_snapshot()

    def test_update_state_marker_tracks_installed_commit_and_environment(self):
        snapshot = GitHubSnapshot(
            "main", "a" * 40, "https://github.com/commit", "https://github.com/archive.zip"
        )
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "github-update-state.json"
            with patch("polyglotmaker.update._local_checkout", return_value=None), patch(
                "polyglotmaker.update._state_path", return_value=marker
            ), patch("polyglotmaker.update._python_prefix", return_value="/test/python"):
                self.assertFalse(is_snapshot_installed(snapshot))
                record_installed_snapshot(snapshot)
                self.assertTrue(is_snapshot_installed(snapshot))
                payload = json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual(payload["commit"], snapshot.commit)
        self.assertEqual(payload["branch"], "main")
        self.assertEqual(payload["python_prefix"], "/test/python")

    def test_checkout_containing_remote_commit_is_not_offered_a_downgrade(self):
        snapshot = GitHubSnapshot(
            "main", "a" * 40, "https://github.com/commit", "https://github.com/archive.zip"
        )
        with patch("polyglotmaker.update._local_checkout", return_value=(Path("/repo"), "b" * 40)), patch(
            "polyglotmaker.update.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ) as runner:
            self.assertTrue(is_snapshot_installed(snapshot))
        self.assertIn("merge-base", runner.call_args.args[0])

    def test_pip_installer_uses_a_commit_archive_and_never_a_shell(self):
        snapshot = GitHubSnapshot(
            "main", "a" * 40, "https://github.com/commit", "https://github.com/archive.zip"
        )
        with patch("polyglotmaker.update.subprocess.run") as runner:
            install_snapshot(snapshot, capture_output=True)

        command = runner.call_args.args[0]
        self.assertIn("--force-reinstall", command)
        self.assertIn(snapshot.source_url, command)
        self.assertTrue(runner.call_args.kwargs["capture_output"])
        self.assertNotIn("shell", runner.call_args.kwargs)


class RepositoryCheckoutUpdateTests(unittest.TestCase):
    root = Path("/mock/repository")
    local_commit = "1" * 40
    remote_commit = "2" * 40

    def _outputs(self, final_commit: str | None = None):
        values = [
            str(self.root),
            "",  # clean working tree
            "main",
            "main",  # validated ref
            self.local_commit,
            self.remote_commit,
        ]
        if final_commit is not None:
            values.append(final_commit)
        return values

    @unittest.skipUnless(shutil.which("git"), "Git required for checkout-updater integration test")
    def test_real_checkout_updates_from_a_github_style_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote = base / "remote.git"
            checkout = base / "checkout"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)

            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True
                )
                return result.stdout.strip()

            git("checkout", "-b", "main")
            git("config", "user.name", "Updater test")
            git("config", "user.email", "updater-test@example.invalid")
            file = checkout / "version.txt"
            file.write_text("first\n", encoding="utf-8")
            git("add", "version.txt")
            git("commit", "-m", "first")
            first_commit = git("rev-parse", "HEAD")
            git("push", "-u", "origin", "main")

            file.write_text("second\n", encoding="utf-8")
            git("commit", "-am", "second")
            second_commit = git("rev-parse", "HEAD")
            git("push", "origin", "main")
            git("reset", "--hard", first_commit)

            with patch("polyglotmaker.update.GITHUB_CLONE_URL", str(remote)):
                report = update_repository_checkout(checkout, check_only=True)
                self.assertTrue(report.update_available)
                self.assertEqual(file.read_text(encoding="utf-8"), "first\n")
                report = update_repository_checkout(checkout)

            self.assertTrue(report.updated)
            self.assertEqual(report.latest_commit, second_commit)
            self.assertEqual(file.read_text(encoding="utf-8"), "second\n")

    def test_check_reports_a_fast_forward_without_changing_files(self):
        ancestry = [
            subprocess.CompletedProcess([], 1, "", ""),  # remote is not an ancestor of HEAD
            subprocess.CompletedProcess([], 0, "", ""),  # HEAD is an ancestor of remote
        ]
        with patch("polyglotmaker.update._git_output", side_effect=self._outputs()), patch(
            "polyglotmaker.update._git_result",
            side_effect=[subprocess.CompletedProcess([], 0, "", ""), *ancestry],
        ) as git:
            result = update_repository_checkout(self.root, check_only=True)

        self.assertEqual(result.branch, "main")
        self.assertEqual(result.previous_commit, self.local_commit)
        self.assertEqual(result.latest_commit, self.remote_commit)
        self.assertTrue(result.update_available)
        self.assertFalse(result.updated)
        self.assertFalse(any("merge" == call.args[1] for call in git.call_args_list))

    def test_clean_checkout_fast_forwards_from_github(self):
        outputs = self._outputs(final_commit=self.remote_commit)
        ancestry = [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with patch("polyglotmaker.update._git_output", side_effect=outputs), patch(
            "polyglotmaker.update._git_result",
            side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                *ancestry,
                subprocess.CompletedProcess([], 0, "Fast-forward", ""),
            ],
        ) as git:
            result = update_repository_checkout(self.root)

        self.assertTrue(result.updated)
        self.assertFalse(result.update_available)
        self.assertIn("--ff-only", git.call_args.args[1:])

    def test_dirty_checkout_is_refused_before_fetch(self):
        with patch(
            "polyglotmaker.update._git_output",
            side_effect=[str(self.root), " M README.md"],
        ), patch("polyglotmaker.update._git_result") as git:
            with self.assertRaisesRegex(UpdateError, "uncommitted changes"):
                update_repository_checkout(self.root)
        git.assert_not_called()

    def test_diverged_history_is_refused(self):
        with patch("polyglotmaker.update._git_output", side_effect=self._outputs()), patch(
            "polyglotmaker.update._git_result",
            side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 1, "", ""),
                subprocess.CompletedProcess([], 1, "", ""),
            ],
        ):
            with self.assertRaisesRegex(UpdateError, "diverged"):
                update_repository_checkout(self.root)

    def test_explicit_remote_branch_is_fetched_without_switching_local_branch(self):
        outputs = [str(self.root), "", "main", "release", self.local_commit, self.remote_commit]
        ancestry = [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with patch("polyglotmaker.update._git_output", side_effect=outputs), patch(
            "polyglotmaker.update._git_result",
            side_effect=[subprocess.CompletedProcess([], 0, "", ""), *ancestry],
        ) as git:
            result = update_repository_checkout(self.root, branch="release", check_only=True)

        self.assertEqual(result.branch, "release")
        self.assertTrue(result.update_available)
        self.assertEqual(git.call_args_list[0].args[-1], "refs/heads/release")


class UpdateCommandTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = GitHubSnapshot(
            branch="main",
            commit="c" * 40,
            url="https://github.com/codero-sus/PolyglotMaker/commit/" + "c" * 40,
            source_url="https://github.com/codero-sus/PolyglotMaker/archive/" + "c" * 40 + ".zip",
        )

    def test_check_only_never_installs(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=False
        ), patch("polyglotmaker.cli.install_snapshot") as installer, redirect_stdout(output):
            result = main(["update", "--check"])

        self.assertEqual(result, 0)
        self.assertIn("GitHub update available", output.getvalue())
        installer.assert_not_called()

    def test_branch_option_is_forwarded(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot) as fetch, patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=True
        ), redirect_stdout(output):
            self.assertEqual(main(["update", "--check", "--branch", "release"]), 0)
        fetch.assert_called_once_with("release")

    def test_noninteractive_default_is_safe_and_does_not_install(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=False
        ), patch("polyglotmaker.cli.install_snapshot") as installer, patch(
            "polyglotmaker.cli.sys.stdin", io.StringIO("")
        ), redirect_stdout(output):
            result = main(["update"])

        self.assertEqual(result, 0)
        self.assertIn("non-interactive", output.getvalue())
        installer.assert_not_called()

    def test_yes_installs_and_records_the_snapshot(self):
        output = io.StringIO()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=False
        ), patch("polyglotmaker.cli.install_snapshot", return_value=completed) as installer, patch(
            "polyglotmaker.cli.record_installed_snapshot"
        ) as record, redirect_stdout(output):
            result = main(["update", "--yes"])

        self.assertEqual(result, 0)
        installer.assert_called_once_with(self.snapshot, capture_output=False)
        record.assert_called_once_with(self.snapshot)
        self.assertIn("Installed GitHub commit", output.getvalue())

    def test_json_check_is_machine_readable_and_does_not_install(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=False
        ), patch("polyglotmaker.cli.install_snapshot") as installer, redirect_stdout(output):
            result = main(["update", "--json"])

        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "update_available")
        self.assertEqual(report["commit"], self.snapshot.commit)
        installer.assert_not_called()

    def test_json_yes_installs_and_returns_structured_result(self):
        output = io.StringIO()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="installed", stderr="")
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=False
        ), patch("polyglotmaker.cli.install_snapshot", return_value=completed), patch(
            "polyglotmaker.cli.record_installed_snapshot"
        ), redirect_stdout(output):
            result = main(["update", "--yes", "--json"])

        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "installed")
        self.assertTrue(report["state_recorded"])
        self.assertEqual(report["installer_stdout"], "installed")

    def test_already_current_snapshot_does_not_install(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", return_value=self.snapshot), patch(
            "polyglotmaker.cli.is_snapshot_installed", return_value=True
        ), patch("polyglotmaker.cli.install_snapshot") as installer, redirect_stdout(output):
            result = main(["update", "--yes"])

        self.assertEqual(result, 0)
        self.assertIn("already contains the latest GitHub commit", output.getvalue())
        installer.assert_not_called()

    def test_github_error_is_returned_as_json(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_snapshot", side_effect=UpdateError("network down")), redirect_stdout(output):
            result = main(["update", "--check", "--json"])

        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()
