from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
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
            'class="skip-link"',
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
            "selectMatchesFiles",
            "invertMatchesFiles",
            "unpackedZipFilename",
            "matchingUnpackedIndexes",
            "unpackedZipStem",
            "renderRenameNotice",
            "renamedFiles",
        ):
            self.assertIn(feature, app)

    def test_browser_engine_uses_portable_safe_archive_names(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = r"""
const engine = require('./web-only/engine.js');
const input = (name, content) => {
  const bytes = Buffer.from(content);
  return { name, type: 'application/octet-stream', size: bytes.length, lastModified: 1700000000000,
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
};
function localNames(bytes) {
  const zip = Buffer.from(bytes);
  const names = [];
  let offset = 0;
  while (offset + 30 <= zip.length && zip.readUInt32LE(offset) === 0x04034b50) {
    const size = zip.readUInt32LE(offset + 18);
    const nameLength = zip.readUInt16LE(offset + 26);
    const extraLength = zip.readUInt16LE(offset + 28);
    names.push(zip.subarray(offset + 30, offset + 30 + nameLength).toString('utf8'));
    offset += 30 + nameLength + extraLength + size;
  }
  return names;
}
(async () => {
  const result = await engine.createPolyglot({ payloads: [
    input('Folder/CON.txt', 'a'), input('folder/_con.TXT', 'b'),
    input('Cafe\u0301.txt', 'c'), input('Café.txt', 'd'), input('trailing. ', 'e'),
  ] });
  const restored = await engine.unpackPolyglot(result.bytes);
  const zip = await engine.downloadZip([
    { name: 'Readme.txt', data: Uint8Array.of(1) },
    { name: 'README.TXT', data: Uint8Array.of(2) },
  ]);
  console.log(JSON.stringify({ created: result.files.map((file) => file.name), restored: restored.files.map((file) => file.name), renamed: result.renamedFiles, zip: localNames(zip) }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            [node, "-e", script], cwd=WEB_ONLY_DIR.parent, check=True, capture_output=True, text=True, timeout=30
        )
        output = json.loads(result.stdout)
        expected = ["Folder/_CON.txt", "folder/_con (1).TXT", "Cafe\u0301.txt", "Café (1).txt", "trailing"]
        self.assertEqual(output["created"], expected)
        self.assertEqual(output["restored"], expected)
        self.assertEqual(output["renamed"], [
            {"from": "Folder/CON.txt", "to": "Folder/_CON.txt"},
            {"from": "folder/_con.TXT", "to": "folder/_con (1).TXT"},
            {"from": "Café.txt", "to": "Café (1).txt"},
            {"from": "trailing. ", "to": "trailing"},
        ])
        self.assertEqual(output["zip"], ["Readme.txt", "README (1).TXT"])

    def test_installable_offline_app_shell_uses_local_assets_only(self):
        html = (WEB_ONLY_DIR / "index.html").read_text(encoding="utf-8")
        app = (WEB_ONLY_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn("serviceWorker.register('./sw.js'", app)
        manifest = json.loads((WEB_ONLY_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["start_url"], "./")
        self.assertEqual(manifest["display"], "standalone")
        for icon in manifest["icons"]:
            self.assertTrue((WEB_ONLY_DIR / icon["src"].removeprefix("./")).is_file())

        worker = (WEB_ONLY_DIR / "sw.js").read_text(encoding="utf-8")
        assets = re.findall(r"^\s+'(\./[^']+)',?$", worker, re.MULTILINE)
        self.assertGreaterEqual(len(assets), 8)
        self.assertIn("if (url.origin !== self.location.origin || !APP_PATHS.has(url.pathname)) return;", worker)
        self.assertIn("cache.addAll(APP_ASSETS)", worker)
        self.assertNotIn("state.payloads", worker)
        for asset in assets:
            self.assertTrue((WEB_ONLY_DIR / asset.removeprefix("./")).is_file(), asset)

    def test_raster_install_icons_are_valid_pngs_at_manifest_sizes(self):
        for size in (192, 512):
            data = (WEB_ONLY_DIR / f"icon-{size}.png").read_bytes()
            self.assertTrue(data.startswith(bytes.fromhex("89504e470d0a1a0a")))
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))

    def test_user_preferences_exclude_selected_file_data(self):
        app = (WEB_ONLY_DIR / "app.js").read_text(encoding="utf-8")
        preferences = app.split("function savePreferences()", 1)[1].split("function setTheme", 1)[0]
        for preference in ("state.theme", "state.payloadSort", "state.unpackedSort", "unpackedZipStem", "outputStem", "includeHost"):
            self.assertIn(preference, preferences)
        self.assertNotIn("state.payloads", preferences)
        self.assertNotIn("state.unpackedFiles", preferences)
        self.assertIn("localStorage", app)

    def test_theme_and_keyboard_help_controls_are_accessible(self):
        html = (WEB_ONLY_DIR / "index.html").read_text(encoding="utf-8")
        css = (WEB_ONLY_DIR / "styles.css").read_text(encoding="utf-8")
        app = (WEB_ONLY_DIR / "app.js").read_text(encoding="utf-8")
        for control in ('id="themeToggle"', 'id="shortcutHelp"', 'id="shortcutDialog"', 'id="installApp"'):
            self.assertIn(control, html)
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn('class="skip-link" href="#mainTools"', html)
        self.assertIn('id="mainTools" tabindex="-1"', html)
        for feature in ("prefers-color-scheme: dark", "event.altKey", "openShortcutDialog", "Escape", "beforeinstallprompt"):
            self.assertIn(feature, app)

    def test_zip_writer_uses_chunked_checksums_and_preallocated_output(self):
        engine = (WEB_ONLY_DIR / "engine.js").read_text(encoding="utf-8")
        self.assertIn("async function crc32Chunked", engine)
        self.assertIn("function writeStoredZip", engine)
        self.assertIn("bundleSha256", engine)
        self.assertNotIn("concat([localData, centralData, endRecord])", engine)


if __name__ == "__main__":
    unittest.main()
