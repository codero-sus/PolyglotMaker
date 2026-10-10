from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from polyglotmaker.core.bundle import InputFile, decode_bootstrap, validate_zip_bundle
from polyglotmaker.core.container import (
    inspect_bytes,
    make_container,
    unpack_bytes,
    unpack_file,
    verify_bytes,
)
from polyglotmaker.core.detect import format_for_path
from polyglotmaker.core.errors import IncompatibleCombinationError, ObfuscationKeyError
from polyglotmaker.core.util import safe_member_name
from polyglotmaker.formats import REGISTRY


class PolyglotRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.payload = InputFile(
            "nested/odd payload.bin",
            b"\x00\xffarbitrary\x00bytes\xff" * 91,
            mtime=1_700_000_000,
            mode=0o640,
            mime="application/octet-stream",
        )

    def test_registry_has_many_host_generators(self):
        self.assertGreaterEqual(len(REGISTRY), 60)
        self.assertIn("exe", REGISTRY)
        self.assertIn("mp4", REGISTRY)
        self.assertIn("png", REGISTRY)
        self.assertIn("docx", REGISTRY)
        self.assertIn("json", REGISTRY)

    def test_every_host_round_trips_arbitrary_payload_bytes(self):
        for host in REGISTRY.keys():
            with self.subTest(host=host):
                result = make_container([self.payload], host=host)
                restored = unpack_bytes(result.data)
                self.assertEqual(restored.files[self.payload.name], self.payload.data)
                self.assertEqual(restored.manifest["container"]["host"], host)
                self.assertEqual(restored.manifest["files"][0]["sha256"], self.payload.sha256)

    def test_default_zip_bundle_is_openable_at_eof(self):
        result = make_container([self.payload], host="png")
        footer = decode_bootstrap(result.data)
        self.assertIsNotNone(footer)
        self.assertEqual(result.data[footer.offset:footer.offset + 4], b"PK\x03\x04")
        manifest, outputs, parsed_footer = validate_zip_bundle(result.data)
        self.assertEqual(outputs[self.payload.name], self.payload.data)
        self.assertEqual(parsed_footer.offset, footer.offset)
        self.assertEqual(manifest["bundle"]["sha256_scope"].split()[0], "payload-set")
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            self.assertIn("POLYGLOTMAKER/manifest.json", archive.namelist())

    def test_bootstrap_archive_checksum_detects_tampering(self):
        from polyglotmaker.core.bundle import BOOTSTRAP_MAGIC
        from polyglotmaker.core.errors import IntegrityError
        result = make_container([self.payload], host="png")
        damaged = bytearray(result.data)
        footer = damaged.rfind(BOOTSTRAP_MAGIC)
        self.assertGreaterEqual(footer, 0)
        damaged[footer + 6 + 2 * 49] = ord("0") if damaged[footer + 6 + 2 * 49] != ord("0") else ord("1")
        with self.assertRaises(IntegrityError):
            unpack_bytes(bytes(damaged))

    def test_raw_store_append_and_native(self):
        for host, mode in (("png", "append"), ("png", "native"), ("json", "native"), ("mp4", "native")):
            with self.subTest(host=host, mode=mode):
                result = make_container([self.payload], host=host, embed_mode=mode, store="raw")
                restored = unpack_bytes(result.data)
                self.assertEqual(restored.files[self.payload.name], self.payload.data)

    def test_raw_signature_inside_a_payload_does_not_hide_the_store(self):
        from polyglotmaker.core.bundle import RAW_MAGIC
        noisy = InputFile("marker.bin", b"payload-prefix" + RAW_MAGIC + b"payload-suffix")
        result = make_container([noisy], host="bmp", store="raw")
        self.assertEqual(unpack_bytes(result.data).files[noisy.name], noisy.data)

    def test_strict_json_host_remains_valid(self):
        result = make_container([self.payload], host="json")
        document = json.loads(result.data.decode("utf-8"))
        self.assertIn("bundle_base64", document["polyglotmaker"])
        self.assertEqual(unpack_bytes(result.data).files[self.payload.name], self.payload.data)
        self.assertTrue(result.warnings)

    def test_obfuscation_requires_correct_key(self):
        result = make_container([self.payload], host="png", obfuscate_key="correct horse")
        with self.assertRaises(ObfuscationKeyError):
            unpack_bytes(result.data)
        with self.assertRaises(ObfuscationKeyError):
            unpack_bytes(result.data, key="wrong")
        restored = unpack_bytes(result.data, key="correct horse")
        self.assertEqual(restored.files[self.payload.name], self.payload.data)

    def test_native_obfuscation_for_strict_text(self):
        result = make_container([self.payload], host="json", obfuscate_key="short secret")
        restored = unpack_bytes(result.data, key="short secret")
        self.assertEqual(restored.files[self.payload.name], self.payload.data)

    def test_format_layers_are_separate_and_detectable(self):
        result = make_container([self.payload], host="png", also=["jpeg", "mp4"])
        info = inspect_bytes(result.data)
        self.assertEqual(info["host"], "png")
        layer_formats = {item["fmt"] for item in info["segments"] if item["kind"] == "layer"}
        self.assertEqual(layer_formats, {"jpeg", "mp4"})
        detections = {(item["fmt"], item["validity"]) for item in info["detections"]}
        self.assertIn(("jpeg", "overlay"), detections)
        self.assertIn(("mp4", "overlay"), detections)
        self.assertEqual(unpack_bytes(result.data).files[self.payload.name], self.payload.data)

    def test_strict_host_rejects_external_layers(self):
        with self.assertRaises(IncompatibleCombinationError):
            make_container([self.payload], host="json", also=["png"])

    def test_architecture_aliases_select_the_expected_bits(self):
        pe32 = make_container([self.payload], host="pe32")
        pe_offset = int.from_bytes(pe32.data[0x3C:0x40], "little")
        self.assertEqual(int.from_bytes(pe32.data[pe_offset + 4:pe_offset + 6], "little"), 0x14C)
        elf32 = make_container([self.payload], host="elf32")
        self.assertEqual(elf32.data[4], 1)

    def test_mp4_wraps_appended_store_in_free_box(self):
        result = make_container([self.payload], host="mp4")
        self.assertEqual(result.data[4:8], b"ftyp")
        self.assertIn(b"free", result.data)
        self.assertEqual(unpack_bytes(result.data).files[self.payload.name], self.payload.data)

    def test_real_host_file_is_preserved_and_restorable(self):
        png_host = REGISTRY.get("png").render(__import__("polyglotmaker.formats.base", fromlist=["HostContext"]).HostContext()).data
        host_input = InputFile("real.png", png_host, source="/tmp/real.png")
        result = make_container([self.payload], host="png", host_file=host_input)
        self.assertTrue(result.data.startswith(png_host))
        unpacked = unpack_bytes(result.data)
        self.assertEqual(unpacked.files["real.png"], png_host)
        self.assertEqual(unpacked.files[self.payload.name], self.payload.data)
        self.assertEqual(result.manifest["container"]["host_mode"], "original")

    def test_matching_host_payload_is_not_duplicated(self):
        from polyglotmaker.formats.base import HostContext
        host_data = REGISTRY.get("png").render(HostContext()).data
        host = InputFile("real.png", host_data)
        result = make_container([host], host="png", host_file=host)
        self.assertEqual(len(result.manifest["files"]), 1)
        self.assertEqual(result.manifest["files"][0]["role"], "host")
        self.assertEqual(unpack_bytes(result.data).files, {"real.png": host_data})

    def test_safe_paths_and_selective_unpack(self):
        with self.assertRaises(ValueError):
            safe_member_name("../../escape.txt")
        with self.assertRaises(ValueError):
            safe_member_name("C:\\Windows\\system.ini")
        second = InputFile("second.dat", b"other")
        result = make_container([self.payload, second], host="png")
        restored = unpack_bytes(result.data, selected=["second.dat"])
        self.assertEqual(restored.files, {"second.dat": b"other"})

    def test_output_dir_restore(self):
        result = make_container([self.payload], host="png")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "container.png"
            source.write_bytes(result.data)
            out = Path(tmp) / "restore"
            report = unpack_file(str(source), str(out))
            target = out / self.payload.name
            self.assertEqual(target.read_bytes(), self.payload.data)
            self.assertEqual(report["files"][0]["sha256"], self.payload.sha256)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks not available")
    def test_unpack_rejects_symlink_escape(self):
        result = make_container([self.payload], host="png")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "container.png"
            source.write_bytes(result.data)
            root = Path(tmp) / "restore"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "nested").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                unpack_file(str(source), str(root))
            self.assertEqual(list(outside.iterdir()), [])

    def test_verify_reports_valid_container(self):
        result = make_container([self.payload], host="png")
        report = verify_bytes(result.data)
        self.assertTrue(report["valid"], report["errors"])
        self.assertTrue(all(check["ok"] for check in report["checks"]))

    def test_extension_and_signature_selection(self):
        from polyglotmaker.formats.executables import _build_pe
        self.assertEqual(format_for_path("sound.wav", b"RIFF\x00\x00\x00\x00WAVE"), "wav")
        self.assertEqual(format_for_path("doc.docx", b"PK\x03\x04content"), "docx")
        self.assertEqual(format_for_path("icon.tga", b"not magic"), "tga")
        self.assertEqual(format_for_path("program.exe", _build_pe(64)), "exe")
        self.assertEqual(format_for_path("legacy.exe", b"MZ" + bytes(100)), "mz")


if __name__ == "__main__":
    unittest.main()
