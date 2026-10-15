from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path


WEB_ONLY_DIR = Path(__file__).resolve().parents[1] / "web-only"


class LocalAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.assets.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.assets.append(str(values["href"]))


class BrowserOnlyEditionTests(unittest.TestCase):
    def test_folder_contains_all_relative_app_assets(self):
        parser = LocalAssetParser()
        parser.feed((WEB_ONLY_DIR / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(set(parser.assets), {"./styles.css", "./engine.js", "./app.js"})
        for asset in parser.assets:
            self.assertTrue((WEB_ONLY_DIR / asset.removeprefix("./")).is_file(), asset)

    def test_browser_processing_does_not_call_a_backend(self):
        for script in (WEB_ONLY_DIR / "app.js", WEB_ONLY_DIR / "engine.js"):
            source = script.read_text(encoding="utf-8")
            self.assertNotIn("fetch(", source)
            self.assertNotIn("XMLHttpRequest", source)
            self.assertNotIn("/api/", source)

    def test_author_and_website_are_credited(self):
        html = (WEB_ONLY_DIR / "index.html").read_text(encoding="utf-8")
        credits = (WEB_ONLY_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("Made with", html)
        self.assertIn("https://github.com/codero-sus", html)
        self.assertIn("https://the-codero.web.app/", html)
        self.assertIn("codero-sus", credits)
        self.assertIn("the-codero.web.app", credits)

    def test_folder_import_progress_and_selective_zip_controls_exist(self):
        html = (WEB_ONLY_DIR / "index.html").read_text(encoding="utf-8")
        app = (WEB_ONLY_DIR / "app.js").read_text(encoding="utf-8")
        for feature in (
            "webkitdirectory",
            'id="payloadSearch"',
            'id="payloadSort"',
            'id="payloadBudgetText"',
            'id="outputFilename"',
            'id="clearPayloads"',
            'id="createProgress"',
            'id="unpackProgress"',
        ):
            self.assertIn(feature, html)
        for feature in (
            "downloadSelectedZip",
            "downloadVerificationReport",
            "selectAllFiles",
            "selectNoneFiles",
            "invertFiles",
            "unpackedSort",
            "unpackedSearch",
            "copyText",
            "outputFilename",
            "AbortController",
            "MAX_VISIBLE_PAYLOADS",
            "MAX_VISIBLE_UNPACKED",
        ):
            self.assertIn(feature, app)

    def test_zip_writer_uses_chunked_checksums_and_preallocated_output(self):
        engine = (WEB_ONLY_DIR / "engine.js").read_text(encoding="utf-8")
        self.assertIn("async function crc32Chunked", engine)
        self.assertIn("function writeStoredZip", engine)
        self.assertIn("bundleSha256", engine)
        self.assertNotIn("concat([localData, centralData, endRecord])", engine)


if __name__ == "__main__":
    unittest.main()
