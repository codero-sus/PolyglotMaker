"""Archive and package hosts: ZIP, TAR, tar.gz/bz2/xz, JAR/APK, office, EPUB.

All user inputs are real archive members, not merely signatures pasted into
another file. ZIP-family formats are generated with :mod:`zipfile`; tar-family
formats use :mod:`tarfile`. The runtime has no external dependencies.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..core.util import Segment, safe_member_name
from .base import EMBED_MEMBERS, HostContext, HostFormat, HostSpec, RenderResult, register

MANIFEST_PATH = "POLYGLOTMAKER/manifest.json"
PAYLOAD_PREFIX = "payloads/"


def _member_values(ctx: HostContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in ctx.members:
        name = str(item.get("name", "payload.bin"))
        data = item.get("data", b"")
        if not isinstance(data, bytes):
            data = bytes(data)
        out.append({
            "name": safe_member_name(name),
            "data": data,
            "mtime": float(item.get("mtime", time.time())),
            "mode": int(item.get("mode", 0o644)),
            "mime": str(item.get("mime", "application/octet-stream")),
        })
    return out


def _manifest_bytes(ctx: HostContext) -> bytes:
    manifest = ctx.metadata.get("manifest", {})
    if isinstance(manifest, bytes):
        return manifest
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_zip(
    entries: Sequence[tuple[str, bytes, float, int, int]],
    *,
    compression: int,
    comment: bytes = b"",
    first_stored: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression, allowZip64=True) as archive:
        for index, (name, data, mtime, mode, force_store) in enumerate(entries):
            safe = safe_member_name(name)
            info = zipfile.ZipInfo(safe)
            stamp = time.localtime(max(mtime, 315532800))
            info.date_time = (stamp.tm_year, stamp.tm_mon, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec - stamp.tm_sec % 2)
            info.create_system = 3
            info.external_attr = ((mode & 0xFFFF) << 16) | (0x10 if safe.endswith("/") else 0)
            info.compress_type = zipfile.ZIP_STORED if force_store or (first_stored and index == 0) else compression
            archive.writestr(info, data)
        archive.comment = comment[:65535]
    return buffer.getvalue()


class ZipHost(HostFormat):
    spec = HostSpec(
        key="zip", label="ZIP archive", category="archive", extension=".zip",
        aliases=("jar-like", "application/zip", "pkzip", "zipfile"), magic=b"PK\x03\x04",
        embed=EMBED_MEMBERS, tolerates_prefix=True, tolerates_suffix=True,
        store="members", tier=1,
        notes="input files are stored as first-class payloads/ entries with a PolyglotMaker manifest",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        files = _member_values(ctx)
        manifest = _manifest_bytes(ctx)
        method = str(ctx.opt("zip_compression", "deflate")).lower()
        compression = {
            "none": zipfile.ZIP_STORED,
            "store": zipfile.ZIP_STORED,
            "stored": zipfile.ZIP_STORED,
            "deflate": zipfile.ZIP_DEFLATED,
            "bzip2": zipfile.ZIP_BZIP2,
            "lzma": zipfile.ZIP_LZMA,
        }.get(method, zipfile.ZIP_DEFLATED)
        entries = [(MANIFEST_PATH, manifest, time.time(), 0o644, True)]
        for item in files:
            entries.append((PAYLOAD_PREFIX + item["name"], item["data"], item["mtime"], item["mode"], False))
        data = _write_zip(entries, compression=compression)
        segments = [Segment("ZIP local/central records", 0, len(data), "host", "zip")]
        return RenderResult(data, segments, True, f"ZIP archive with {len(files)} payload member(s), {method} compression")

    def parse(self, data: bytes) -> list[Segment]:
        # A prefix-tolerant archive may start after a layer. Find the central directory end.
        eocd = data.rfind(b"PK\x05\x06")
        if eocd >= 0:
            start = data.find(b"PK\x03\x04")
            if start < 0 or start > eocd:
                start = 0
            return [Segment("ZIP archive (prefix tolerated)", start, len(data) - start, "host", "zip")]
        return []


class TarHost(HostFormat):
    spec = HostSpec(
        key="tar", label="TAR archive", category="archive", extension=".tar",
        aliases=("application/x-tar",), magic=None,
        embed=EMBED_MEMBERS, tolerates_prefix=False, tolerates_suffix=True,
        store="members", tier=1,
        notes="POSIX ustar archive containing manifest and all input files as members",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        files = _member_values(ctx)
        manifest = _manifest_bytes(ctx)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            entries = [(MANIFEST_PATH, manifest, 0o644, time.time())]
            entries.extend((PAYLOAD_PREFIX + item["name"], item["data"], item["mode"], item["mtime"]) for item in files)
            for name, data, mode, mtime in entries:
                info = tarfile.TarInfo(safe_member_name(name))
                info.size = len(data)
                info.mode = mode & 0o777
                info.mtime = mtime
                archive.addfile(info, io.BytesIO(data))
        data = buffer.getvalue()
        return RenderResult(data, [Segment("TAR archive", 0, len(data), "host", "tar")], True,
                            f"TAR archive with {len(files)} payload member(s)")


class CompressedTarHost(HostFormat):
    def __init__(self, key: str, label: str, extension: str, alias: tuple[str, ...], mode: str, magic: bytes):
        self.mode = mode
        self.spec = HostSpec(
            key=key, label=label, category="archive", extension=extension,
            aliases=alias, magic=magic, embed=EMBED_MEMBERS,
            tolerates_prefix=False, tolerates_suffix=False, store="members", tier=1,
            notes=f"real tar.{mode} stream; all user files are first-class tar members",
        )

    def render(self, ctx: HostContext) -> RenderResult:
        files = _member_values(ctx)
        manifest = _manifest_bytes(ctx)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:" + self.mode, format=tarfile.PAX_FORMAT) as archive:
            entries = [(MANIFEST_PATH, manifest, 0o644, time.time())]
            entries.extend((PAYLOAD_PREFIX + item["name"], item["data"], item["mode"], item["mtime"]) for item in files)
            for name, data, mode, mtime in entries:
                info = tarfile.TarInfo(safe_member_name(name))
                info.size = len(data)
                info.mode = mode & 0o777
                info.mtime = mtime
                archive.addfile(info, io.BytesIO(data))
        data = buffer.getvalue()
        return RenderResult(data, [Segment(f"tar.{self.mode} archive", 0, len(data), "host", self.spec.key)], True,
                            f"tar.{self.mode} archive with {len(files)} payload member(s)")


@dataclass(frozen=True)
class ZipPackage:
    key: str
    label: str
    extension: str
    aliases: tuple[str, ...]
    files: tuple[tuple[str, bytes, bool], ...]  # (path, contents, force stored)
    note: str


class ZipPackageHost(HostFormat):
    def __init__(self, package: ZipPackage):
        self.package = package
        self.spec = HostSpec(
            key=package.key, label=package.label, category="document" if package.key not in {"jar", "apk"} else "executable",
            extension=package.extension, aliases=package.aliases, magic=b"PK\x03\x04",
            embed=EMBED_MEMBERS, tolerates_prefix=True, tolerates_suffix=True,
            store="members", tier=1, notes=package.note,
        )

    def _shell_files(self, ctx: HostContext) -> list[tuple[str, bytes, float, int, int]]:
        # Base package files have fixed legal skeletons. Every payload is
        # included as a separate member under payloads/, with original names.
        entries = [(name, data, time.time(), 0o644, stored) for name, data, stored in self.package.files]
        entries.append((MANIFEST_PATH, _manifest_bytes(ctx), time.time(), 0o644, False))
        for item in _member_values(ctx):
            entries.append((PAYLOAD_PREFIX + item["name"], item["data"], item["mtime"], item["mode"], False))
        return entries

    def render(self, ctx: HostContext) -> RenderResult:
        files = self._shell_files(ctx)
        method = str(ctx.opt("zip_compression", "deflate")).lower()
        compression = {"none": zipfile.ZIP_STORED, "store": zipfile.ZIP_STORED,
                       "stored": zipfile.ZIP_STORED, "deflate": zipfile.ZIP_DEFLATED,
                       "bzip2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA}.get(method, zipfile.ZIP_DEFLATED)
        data = _write_zip(files, compression=compression, first_stored=self.package.key == "epub")
        return RenderResult(data, [Segment(f"{self.package.label} ZIP package", 0, len(data), "host", self.spec.key)], True,
                            f"{self.package.label} ZIP package with {len(_member_values(ctx))} payload member(s)")


# Minimal legal package skeletons. Payload files are external resources, not
# injected into XML source, so their names and bytes remain exactly as given.
_DOCX_FILES = (
    ("[Content_Types].xml", b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>', False),
    ("_rels/.rels", b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>', False),
    ("word/document.xml", b'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>PolyglotMaker document</w:t></w:r></w:p><w:sectPr/></w:body></w:document>', False),
)
_XLSX_FILES = (
    ("[Content_Types].xml", b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>', False),
    ("_rels/.rels", b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>', False),
    ("xl/workbook.xml", b'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="PolyglotMaker" sheetId="1" r:id="rId1"/></sheets></workbook>', False),
    ("xl/_rels/workbook.xml.rels", b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>', False),
    ("xl/worksheets/sheet1.xml", b'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>PolyglotMaker</t></is></c></row></sheetData></worksheet>', False),
)
_PPTX_FILES = (
    ("[Content_Types].xml", b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>', False),
    ("_rels/.rels", b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>', False),
    ("ppt/presentation.xml", b'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst/><p:sldSz cx="9144000" cy="5143500" type="wide"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>', False),
)
_EPUB_FILES = (
    ("mimetype", b"application/epub+zip", True),
    ("META-INF/container.xml", b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>', False),
    ("OEBPS/content.opf", b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:polyglotmaker:bundle</dc:identifier><dc:title>PolyglotMaker</dc:title><dc:language>en</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>', False),
    ("OEBPS/nav.xhtml", b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol><li><a href="chapter.xhtml">PolyglotMaker</a></li></ol></nav></body></html>', False),
    ("OEBPS/chapter.xhtml", b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>PolyglotMaker</title></head><body><h1>PolyglotMaker</h1></body></html>', False),
)
_ODT_FILES = (
    ("mimetype", b"application/vnd.oasis.opendocument.text", True),
    ("META-INF/manifest.xml", b'<?xml version="1.0"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>', False),
    ("content.xml", b'<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.3"><office:body><office:text><text:p>PolyglotMaker</text:p></office:text></office:body></office:document-content>', False),
)

_PACKAGES = (
    ZipPackage("jar", "Java archive", ".jar", ("java-archive", "application/java-archive"),
               (("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\r\nCreated-By: PolyglotMaker\r\n\r\n", False),),
               "JAR with a manifest and payloads/ entries"),
    ZipPackage("apk", "Android package", ".apk", ("android-package", "application/vnd.android.package-archive"),
               (("AndroidManifest.xml", b'<?xml version="1.0" encoding="utf-8"?><manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.polyglotmaker.bundle" android:versionCode="1" android:versionName="1.0"><uses-sdk android:minSdkVersion="21"/><application android:label="PolyglotMaker"/></manifest>', False),),
               "APK-like ZIP package skeleton; unsigned and intended for format demos, not installation"),
    ZipPackage("docx", "Word document (DOCX)", ".docx", ("word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), _DOCX_FILES,
               "minimal OOXML Word package plus payloads/ resources"),
    ZipPackage("xlsx", "Excel workbook (XLSX)", ".xlsx", ("excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), _XLSX_FILES,
               "minimal OOXML Spreadsheet package plus payloads/ resources"),
    ZipPackage("pptx", "PowerPoint presentation (PPTX)", ".pptx", ("powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"), _PPTX_FILES,
               "minimal OOXML Presentation package plus payloads/ resources"),
    ZipPackage("epub", "EPUB ebook", ".epub", ("application/epub+zip",), _EPUB_FILES,
               "EPUB 3 package (mimetype is first and stored) plus payloads/ resources"),
    ZipPackage("odt", "OpenDocument text", ".odt", ("application/vnd.oasis.opendocument.text",), _ODT_FILES,
               "OpenDocument Text package (mimetype is first and stored) plus payloads/ resources"),
    ZipPackage("ods", "OpenDocument spreadsheet", ".ods", ("application/vnd.oasis.opendocument.spreadsheet",),
               (("mimetype", b"application/vnd.oasis.opendocument.spreadsheet", True),
                ("content.xml", b'<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.3"><office:body><office:spreadsheet><table:table table:name="PolyglotMaker"/></office:spreadsheet></office:body></office:document-content>', False)),
               "OpenDocument Spreadsheet package plus payloads/ resources"),
    ZipPackage("odp", "OpenDocument presentation", ".odp", ("application/vnd.oasis.opendocument.presentation",),
               (("mimetype", b"application/vnd.oasis.opendocument.presentation", True),
                ("content.xml", b'<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" office:version="1.3"><office:body><office:presentation/></office:body></office:document-content>', False)),
               "OpenDocument Presentation package plus payloads/ resources"),
    ZipPackage("cbz", "Comic book ZIP", ".cbz", ("comic-zip",), (),
               "CBZ comic archive with all input images/resources as members"),
)


register(ZipHost())
register(TarHost())
register(CompressedTarHost("gz", "gzip-compressed TAR", ".tar.gz", ("gzip", "tgz", "application/gzip"), "gz", b"\x1f\x8b"))
register(CompressedTarHost("bz2", "bzip2-compressed TAR", ".tar.bz2", ("bzip2", "tbz2", "application/x-bzip2"), "bz2", b"BZh"))
register(CompressedTarHost("xz", "xz-compressed TAR", ".tar.xz", ("txz", "application/x-xz"), "xz", b"\xfd7zXZ\x00"))
for _package in _PACKAGES:
    register(ZipPackageHost(_package))
