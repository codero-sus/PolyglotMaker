from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from polyglotmaker.cli import main
from polyglotmaker.update import (
    NoReleaseError,
    Release,
    UpdateError,
    compare_versions,
    fetch_latest_release,
    install_release,
)


class VersionComparisonTests(unittest.TestCase):
    def test_release_versions_compare_numerically_and_ignore_trailing_zeroes(self):
        self.assertEqual(compare_versions("v1.2.0", "1.2"), 0)
        self.assertGreater(compare_versions("1.10.0", "1.9.9"), 0)
        self.assertLess(compare_versions("0.9.9", "1.0.0"), 0)

    def test_prereleases_sort_before_final_and_post_releases_after(self):
        self.assertGreater(compare_versions("1.0.0", "1.0.0rc1"), 0)
        self.assertGreater(compare_versions("1.0.0rc1", "1.0.0b2"), 0)
        self.assertGreater(compare_versions("1.0.0b1", "1.0.0a9"), 0)
        self.assertLess(compare_versions("1.0.0.dev1", "1.0.0a1"), 0)
        self.assertGreater(compare_versions("1.0.0.post1", "1.0.0"), 0)

    def test_unknown_version_syntax_is_rejected(self):
        with self.assertRaises(UpdateError):
            compare_versions("release-next", "1.0.0")


class ReleaseLookupTests(unittest.TestCase):
    def test_release_metadata_is_validated_and_source_url_is_constructed(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"tag_name": "v1.4.0", "draft": False, "prerelease": False}
        ).encode()
        with patch("polyglotmaker.update.urlopen", return_value=response) as opener:
            release = fetch_latest_release()

        self.assertEqual(release.version, "1.4.0")
        self.assertEqual(release.tag, "v1.4.0")
        self.assertEqual(release.url, "https://github.com/codero-sus/PolyglotMaker/releases/tag/v1.4.0")
        self.assertEqual(
            release.source_url,
            "https://github.com/codero-sus/PolyglotMaker/archive/refs/tags/v1.4.0.zip",
        )
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/codero-sus/PolyglotMaker/releases/latest")
        self.assertIn("PolyglotMaker/", request.get_header("User-agent"))

    def test_missing_stable_release_is_reported_explicitly(self):
        error = HTTPError("https://api.github.com/", 404, "Not Found", {}, None)
        with patch("polyglotmaker.update.urlopen", side_effect=error):
            with self.assertRaises(NoReleaseError):
                fetch_latest_release()

    def test_invalid_release_tag_is_rejected(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"tag_name": "main; malicious", "draft": False, "prerelease": False}
        ).encode()
        with patch("polyglotmaker.update.urlopen", return_value=response):
            with self.assertRaises(UpdateError):
                fetch_latest_release()

    def test_pip_installer_uses_an_argument_list_without_a_shell(self):
        release = Release("1.4.0", "v1.4.0", "https://github.com/release", "https://github.com/source.zip")
        with patch("polyglotmaker.update.subprocess.run") as runner:
            install_release(release, capture_output=True)

        command = runner.call_args.args[0]
        self.assertIn("--upgrade", command)
        self.assertIn(release.source_url, command)
        self.assertTrue(runner.call_args.kwargs["capture_output"])
        self.assertNotIn("shell", runner.call_args.kwargs)


class UpdateCommandTests(unittest.TestCase):
    def setUp(self):
        self.release = Release(
            version="0.2.0",
            tag="v0.2.0",
            url="https://github.com/codero-sus/PolyglotMaker/releases/tag/v0.2.0",
            source_url="https://github.com/codero-sus/PolyglotMaker/archive/refs/tags/v0.2.0.zip",
        )

    def test_check_only_never_installs(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_release", return_value=self.release), patch(
            "polyglotmaker.cli.install_release"
        ) as installer, redirect_stdout(output):
            result = main(["update", "--check"])

        self.assertEqual(result, 0)
        self.assertIn("Update available", output.getvalue())
        installer.assert_not_called()

    def test_noninteractive_default_is_safe_and_does_not_install(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_release", return_value=self.release), patch(
            "polyglotmaker.cli.install_release"
        ) as installer, redirect_stdout(output):
            result = main(["update"])

        self.assertEqual(result, 0)
        self.assertIn("non-interactive", output.getvalue())
        installer.assert_not_called()

    def test_yes_installs_the_release(self):
        output = io.StringIO()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("polyglotmaker.cli.fetch_latest_release", return_value=self.release), patch(
            "polyglotmaker.cli.install_release", return_value=completed
        ) as installer, redirect_stdout(output):
            result = main(["update", "--yes"])

        self.assertEqual(result, 0)
        installer.assert_called_once_with(self.release, capture_output=False)
        self.assertIn("installed", output.getvalue())

    def test_json_check_is_machine_readable_and_does_not_install(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_release", return_value=self.release), patch(
            "polyglotmaker.cli.install_release"
        ) as installer, redirect_stdout(output):
            result = main(["update", "--json"])

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "update_available")
        installer.assert_not_called()

    def test_json_yes_installs_and_returns_structured_result(self):
        output = io.StringIO()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="installed", stderr="")
        with patch("polyglotmaker.cli.fetch_latest_release", return_value=self.release), patch(
            "polyglotmaker.cli.install_release", return_value=completed
        ) as installer, redirect_stdout(output):
            result = main(["update", "--yes", "--json"])

        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "installed")
        self.assertEqual(report["installer_stdout"], "installed")
        installer.assert_called_once_with(self.release, capture_output=True)

    def test_no_release_is_a_non_error_status(self):
        output = io.StringIO()
        with patch("polyglotmaker.cli.fetch_latest_release", side_effect=NoReleaseError("no stable release")), redirect_stdout(output):
            result = main(["update", "--json"])

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "no_release")


if __name__ == "__main__":
    unittest.main()
