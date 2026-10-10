from __future__ import annotations

import io
import os
import tempfile
from importlib.util import find_spec
import subprocess
import unittest

from polyglotmaker.core.bundle import InputFile
from polyglotmaker.core.container import make_container


PAYLOAD = InputFile("payload.dat", b"PolyglotMaker optional parser smoke test\x00\xff")


class OptionalValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PIL import Image  # noqa: F401
            cls.pillow = True
        except ImportError:
            cls.pillow = False
        try:
            import pypdf  # noqa: F401
            cls.pypdf = True
        except ImportError:
            cls.pypdf = False
        try:
            import pefile  # noqa: F401
            cls.pefile = True
        except ImportError:
            cls.pefile = False
        try:
            import mutagen  # noqa: F401
            cls.mutagen = True
        except ImportError:
            cls.mutagen = False

    @unittest.skipUnless(find_spec("PIL"), "optional Pillow not installed")
    def test_generated_image_hosts_open_with_pillow(self):
        from PIL import Image
        for host in ("png", "jpeg", "gif", "bmp", "webp", "tiff", "ico", "tga"):
            with self.subTest(host=host):
                data = make_container([PAYLOAD], host=host).data
                image = Image.open(io.BytesIO(data))
                image.load()
                self.assertGreaterEqual(image.width, 1)
                self.assertGreaterEqual(image.height, 1)

    @unittest.skipUnless(find_spec("pypdf"), "optional pypdf not installed")
    def test_generated_pdf_and_appended_zip_open_with_pypdf(self):
        from pypdf import PdfReader
        data = make_container([PAYLOAD], host="pdf").data
        reader = PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), 1)
        self.assertIn("PolyglotMaker", reader.pages[0].extract_text())
        self.assertTrue(reader.attachments)

    @unittest.skipUnless(find_spec("pefile"), "optional pefile not installed")
    def test_pe32_and_pe64_import_tables_open_with_pefile(self):
        import pefile
        for bits in (32, 64):
            with self.subTest(bits=bits):
                data = make_container([PAYLOAD], host="exe", options={"pe_bits": bits}).data
                pe = pefile.PE(data=data)
                self.assertEqual(pe.FILE_HEADER.Machine, 0x8664 if bits == 64 else 0x14C)
                imports = {entry.dll.lower(): {item.name.lower() for item in entry.imports} for entry in pe.DIRECTORY_ENTRY_IMPORT}
                self.assertIn(b"exitprocess", imports[b"kernel32.dll"])
                self.assertTrue(pe.get_overlay())
                pe.close()

    @unittest.skipUnless(find_spec("mutagen"), "optional mutagen not installed")
    def test_generated_audio_containers_are_recognized(self):
        import mutagen
        extensions = {"mp3": ".mp3", "flac": ".flac", "wav": ".wav", "aiff": ".aiff", "midi": ".mid"}
        with tempfile.TemporaryDirectory() as tmp:
            for host, extension in extensions.items():
                with self.subTest(host=host):
                    data = make_container([PAYLOAD], host=host).data
                    path = os.path.join(tmp, "output" + extension)
                    with open(path, "wb") as handle:
                        handle.write(data)
                    parsed = mutagen.File(path)
                    self.assertIsNotNone(parsed)

    @unittest.skipUnless(os.name == "posix", "ELF execution requires POSIX")
    def test_generated_elf_exits_successfully(self):
        data = make_container([PAYLOAD], host="elf").data
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.elf")
            with open(path, "wb") as handle:
                handle.write(data)
            os.chmod(path, 0o755)
            result = subprocess.run([path], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
