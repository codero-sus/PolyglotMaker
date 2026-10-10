"""Format sniffing.

Two jobs:

1. :func:`detect` -- "what is this file?"  A broad magic table (90+ formats)
   is matched at offset 0 *and* scanned through the whole file, which is how
   ``polyglot inspect`` reports every format a container answers to.
2. :func:`guess_host` -- pick the best host format for a set of input files
   (used by ``--host auto``).

Scanning the whole file is deliberately cheap: we look for each signature with
``bytes.find`` in a loop and stop after ``max_hits`` per signature.
"""

from __future__ import annotations

import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# signature table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Signature:
    fmt: str
    magic: bytes
    category: str
    extension: str
    label: str
    offset: int = 0          # required offset (-1 == "anywhere")
    mask: bytes | None = None  # bytes that may vary (matched as wildcards)


def _sig(fmt, magic, category, extension, label, offset=0):
    return Signature(fmt=fmt, magic=magic, category=category, extension=extension, label=label, offset=offset)


SIGNATURES: tuple[Signature, ...] = (
    # ---- images -------------------------------------------------------
    _sig("png", b"\x89PNG\r\n\x1a\n", "image", ".png", "PNG image"),
    _sig("jpeg", b"\xff\xd8\xff", "image", ".jpg", "JPEG image"),
    _sig("gif87", b"GIF87a", "image", ".gif", "GIF image (87a)"),
    _sig("gif", b"GIF89a", "image", ".gif", "GIF image (89a)"),
    _sig("bmp", b"BM", "image", ".bmp", "BMP image"),
    _sig("webp", b"RIFF", "image", ".webp", "WebP image (RIFF)"),
    _sig("ico", b"\x00\x00\x01\x00", "image", ".ico", "Windows icon"),
    _sig("cur", b"\x00\x00\x02\x00", "image", ".cur", "Windows cursor"),
    _sig("tiff_le", b"II\x2a\x00", "image", ".tif", "TIFF image (little endian)"),
    _sig("tiff_be", b"MM\x00\x2a", "image", ".tif", "TIFF image (big endian)"),
    _sig("tiff_bigel", b"II\x2b\x00", "image", ".tif", "BigTIFF image"),
    _sig("psd", b"8BPS", "image", ".psd", "Photoshop document"),
    _sig("xcf", b"gimp xcf", "image", ".xcf", "GIMP image"),
    _sig("pcx", b"\x0a\x05\x01", "image", ".pcx", "PCX image"),
    _sig("tga", b"\x00\x00\x02\x00\x00\x00\x00\x00", "image", ".tga", "TGA image"),
    _sig("avif", b"\x00\x00\x00 ftypavif", "image", ".avif", "AVIF image"),
    _sig("heic", b"\x00\x00\x00\x18ftypheic", "image", ".heic", "HEIC image"),
    _sig("jxl", b"\xff\x0a", "image", ".jxl", "JPEG XL (codestream)"),
    _sig("jp2", b"\x00\x00\x00\x0cjP  ", "image", ".jp2", "JPEG 2000"),
    # ---- audio --------------------------------------------------------
    _sig("mp3_id3", b"ID3", "audio", ".mp3", "MP3 with ID3v2 tag"),
    _sig("mp3", b"\xff\xfb", "audio", ".mp3", "MPEG-1 Layer III audio"),
    _sig("mp3_v2", b"\xff\xf3", "audio", ".mp3", "MPEG-2 Layer III audio"),
    _sig("flac", b"fLaC", "audio", ".flac", "FLAC audio"),
    _sig("ogg", b"OggS", "audio", ".ogg", "Ogg container"),
    _sig("aiff", b"FORM", "audio", ".aiff", "AIFF audio (IFF)"),
    _sig("midi", b"MThd", "audio", ".mid", "Standard MIDI file"),
    _sig("xm", b"Extended Module:", "audio", ".xm", "FastTracker module"),
    _sig("s3m", b"\x00\x00\x00\x00SCRM", "audio", ".s3m", "Scream Tracker module", offset=44),
    _sig("mod", b"M.K.", "audio", ".mod", "ProTracker module", offset=1080),
    # ---- video / containers ------------------------------------------
    _sig("mp4", b"ftyp", "video", ".mp4", "ISO base media (MP4/MOV)", offset=4),
    _sig("mov", b"moov", "video", ".mov", "QuickTime movie", offset=4),
    _sig("mkv", b"\x1a\x45\xdf\xa3", "video", ".mkv", "Matroska / WebM"),
    _sig("avi_riff", b"AVI ", "video", ".avi", "AVI video", offset=8),
    _sig("wav", b"WAVE", "audio", ".wav", "WAVE audio", offset=8),
    _sig("flv", b"FLV", "video", ".flv", "Flash video"),
    _sig("asf", b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "video", ".wmv", "ASF / WMV / WMA"),
    _sig("mpg", b"\x00\x00\x01\xba", "video", ".mpg", "MPEG program stream"),
    _sig("mpegsys", b"\x00\x00\x01\xb3", "video", ".mpg", "MPEG video sequence"),
    _sig("ts", b"\x47", "video", ".ts", "MPEG transport stream"),
    # ---- documents ----------------------------------------------------
    _sig("pdf", b"%PDF-", "document", ".pdf", "PDF document"),
    _sig("rtf", b"{\\rtf", "document", ".rtf", "Rich Text Format"),
    _sig("ole2", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "document", ".doc", "OLE2 compound file (doc/xls/ppt/msi)"),
    _sig("ps", b"%!PS-", "document", ".ps", "PostScript"),
    _sig("dvi", b"\xf7\x02", "document", ".dvi", "TeX DVI"),
    _sig("epub", b"mimetypeapplication/epub+zip", "document", ".epub", "EPUB book"),
    # ---- executables --------------------------------------------------
    _sig("mz", b"MZ", "executable", ".exe", "DOS/Windows executable (MZ)"),
    _sig("elf", b"\x7fELF", "executable", ".elf", "ELF executable"),
    _sig("macho32", b"\xfe\xed\xfa\xce", "executable", ".macho", "Mach-O 32-bit"),
    _sig("macho64", b"\xfe\xed\xfa\xcf", "executable", ".macho", "Mach-O 64-bit"),
    _sig("macho32r", b"\xce\xfa\xed\xfe", "executable", ".macho", "Mach-O 32-bit (reversed)"),
    _sig("macho64r", b"\xcf\xfa\xed\xfe", "executable", ".macho", "Mach-O 64-bit (reversed)"),
    _sig("macho_fat", b"\xca\xfe\xba\xbe", "executable", ".macho", "Mach-O universal binary"),
    _sig("class", b"\xca\xfe\xba\xbe\x00\x00", "executable", ".class", "Java class file"),
    _sig("dex", b"dex\n", "executable", ".dex", "Android DEX"),
    _sig("wasm", b"\x00asm", "executable", ".wasm", "WebAssembly module"),
    _sig("pyc", b"\x0d\x0a\x1a\x0a", "executable", ".pyc", "Python bytecode"),
    _sig("shebang", b"#!", "executable", ".sh", "script with shebang"),
    _sig("aout", b"\x07\x01\x00\x00", "executable", ".out", "a.out executable"),
    # ---- archives -----------------------------------------------------
    _sig("zip", b"PK\x03\x04", "archive", ".zip", "ZIP archive"),
    _sig("zip_empty", b"PK\x05\x06", "archive", ".zip", "ZIP archive (empty)"),
    _sig("zip_spanned", b"PK\x07\x08", "archive", ".zip", "ZIP archive (spanned)"),
    _sig("rar4", b"Rar!\x1a\x07\x00", "archive", ".rar", "RAR archive (v4)"),
    _sig("rar5", b"Rar!\x1a\x07\x01\x00", "archive", ".rar", "RAR archive (v5)"),
    _sig("7z", b"7z\xbc\xaf\x27\x1c", "archive", ".7z", "7-Zip archive"),
    _sig("gz", b"\x1f\x8b", "archive", ".gz", "gzip stream"),
    _sig("bz2", b"BZh", "archive", ".bz2", "bzip2 stream"),
    _sig("xz", b"\xfd7zXZ\x00", "archive", ".xz", "xz stream"),
    _sig("zst", b"\x28\xb5\x2f\xfd", "archive", ".zst", "Zstandard frame"),
    _sig("lz4", b"\x04\x22\x4d\x18", "archive", ".lz4", "LZ4 frame"),
    _sig("lzma", b"\x5d\x00\x00", "archive", ".lzma", "LZMA alone stream"),
    _sig("cab", b"MSCF\x00\x00\x00\x00", "archive", ".cab", "Microsoft CAB"),
    _sig("iso", b"CD001", "archive", ".iso", "ISO 9660 image", offset=0x8001),
    _sig("ustar", b"ustar", "archive", ".tar", "tar archive (ustar)", offset=257),
    _sig("rpm", b"\xed\xab\xee\xdb", "archive", ".rpm", "RPM package"),
    _sig("deb", b"!<arch>\ndebian-binary", "archive", ".deb", "Debian package"),
    _sig("ar", b"!<arch>\n", "archive", ".a", "ar archive"),
    _sig("zip64eocd", b"PK\x06\x06", "archive", ".zip", "ZIP64 end of central directory"),
    # ---- data / misc --------------------------------------------------
    _sig("sqlite", b"SQLite format 3\x00", "data", ".sqlite", "SQLite database"),
    _sig("pcap", b"\xd4\xc3\xb2\xa1", "capture", ".pcap", "pcap capture"),
    _sig("pcapng", b"\x0a\x0d\x0d\x0a", "capture", ".pcapng", "pcap-ng capture"),
    _sig("pgp_msg", b"\x85\x01", "crypto", ".gpg", "OpenPGP message"),
    _sig("ssh_pk", b"---- BEGIN SSH2", "crypto", ".pem", "SSH2 key"),
    _sig("pem", b"-----BEGIN ", "crypto", ".pem", "PEM encoded data"),
    _sig("ttf", b"\x00\x01\x00\x00\x00", "font", ".ttf", "TrueType font"),
    _sig("otf", b"OTTO", "font", ".otf", "OpenType font"),
    _sig("woff", b"wOFF", "font", ".woff", "WOFF font"),
    _sig("woff2", b"wOF2", "font", ".woff2", "WOFF2 font"),
    _sig("eot", b"\x4c\x50", "font", ".eot", "Embedded OpenType font"),
    _sig("blend", b"BLENDER", "model", ".blend", "Blender file"),
    _sig("glb", b"glTF", "model", ".glb", "glTF binary model"),
    _sig("stl", b"solid ", "model", ".stl", "STL model"),
    _sig("dcm", b"DICM", "medical", ".dcm", "DICOM image", offset=128),
    # ---- our own ------------------------------------------------------
    _sig("plgm", b"PLGMK1", "polyglot", ".plgm", "PolyglotMaker bootstrap"),
    _sig("plgm_store", b"PLGMSTORE1", "polyglot", ".plgm", "PolyglotMaker raw store"),
)

_BY_FORMAT: dict[str, Signature] = {}
for _s in SIGNATURES:
    _BY_FORMAT.setdefault(_s.fmt, _s)

#: signatures that are meaningless at a fixed offset and must be searched for
_ANYWHERE = tuple(s for s in SIGNATURES if s.offset < 0)


@dataclass
class Detection:
    fmt: str
    label: str
    category: str
    extension: str
    offset: int
    magic: bytes
    confidence: str = "strong"   # strong | likely | weak
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "fmt": self.fmt,
            "label": self.label,
            "category": self.category,
            "extension": self.extension,
            "offset": self.offset,
            "magic": self.magic.hex(),
            "confidence": self.confidence,
            "note": self.note,
        }


def _match_at(data: bytes, sig: Signature, offset: int) -> bool:
    end = offset + len(sig.magic)
    if end > len(data):
        return False
    return data[offset:end] == sig.magic


def detect_at_offset(data: bytes, offset: int = 0) -> list[Detection]:
    """All signatures for a file beginning at ``offset`` (longest first).

    Some containers identify themselves with a secondary word at a fixed
    offset: RIFF/WAVE, RIFF/WEBP, AVI and ISO-BMFF/MP4. Those signatures are
    anchored relative to the beginning of the candidate file, not relative to
    the scan cursor.
    """
    hits: list[Detection] = []
    for sig in sorted(SIGNATURES, key=lambda s: -len(s.magic)):
        if sig.offset < 0:
            continue
        match_offset = offset + sig.offset
        if not _match_at(data, sig, match_offset):
            continue
        if sig.fmt == "webp" and data[match_offset + 8:match_offset + 12] != b"WEBP":
            continue
        if sig.fmt == "wav" and data[:4] != b"RIFF":
            continue
        if sig.fmt == "avi_riff" and data[:4] != b"RIFF":
            continue
        hits.append(
            Detection(
                fmt=sig.fmt,
                label=sig.label,
                category=sig.category,
                extension=sig.extension,
                offset=offset,
                magic=data[offset:offset + 16],
                confidence="strong",
            )
        )
    return _dedupe(hits)


def detect(data: bytes, *, deep: bool = True, max_hits: int = 24, scan_limit: int = 64 << 20) -> list[Detection]:
    """Detect every format ``data`` answers to.

    ``deep=True`` also scans the body for embedded signatures, which is how we
    report the extra layers of a polyglot (a JPEG hidden inside an MP4 ``free``
    box, a ZIP appended after a PNG IEND, ...).
    """
    hits = detect_at_offset(data, 0)
    # ZIP is special: it is anchored at the *end* of the file, not the start.
    hits += _detect_zip(data)
    if not deep:
        return _dedupe(hits)

    window = data[:scan_limit]
    seen_keys = {(h.fmt, h.offset) for h in hits}
    zip_bounds = _zip_bounds(data)
    for sig in SIGNATURES:
        if len(sig.magic) < 4 and sig.fmt not in {"mz", "gz", "bmp", "jpeg", "mp3", "mp3_v2"}:
            continue  # too short to be worth scanning for
        if sig.fmt in {"ts", "sh"}:
            continue
        start = 0
        found = 0
        while found < 3:
            idx = window.find(sig.magic, start)
            if idx < 0:
                break
            start = idx + 1
            candidate = idx - sig.offset if sig.offset > 0 else idx
            if candidate < 0:
                continue
            if (sig.fmt in {"zip", "zip_empty", "zip_spanned", "zip64eocd"} and zip_bounds
                    and zip_bounds[0] <= candidate < zip_bounds[1]):
                continue  # local/central ZIP records inside one archive are not separate layers
            key = (sig.fmt, candidate)
            if candidate != 0 and key not in seen_keys and not _overlaps(hits, sig.fmt, candidate):
                if sig.offset > 0 and not _match_at(window, sig, candidate + sig.offset):
                    continue
                if sig.fmt in {"wav", "avi_riff"} and window[candidate:candidate + 4] != b"RIFF":
                    continue
                if sig.fmt == "webp" and window[candidate + 8:candidate + 12] != b"WEBP":
                    continue
                seen_keys.add(key)
                hits.append(
                    Detection(
                        fmt=sig.fmt,
                        label=sig.label,
                        category=sig.category,
                        extension=sig.extension,
                        offset=candidate,
                        magic=window[candidate:candidate + 16],
                        confidence="likely" if len(sig.magic) >= 4 else "weak",
                        note="embedded / overlay",
                    )
                )
                found += 1
            if len(hits) >= max_hits * 4:
                break
    hits.sort(key=lambda h: h.offset)
    return _dedupe(hits)[: max_hits * 2]


def _overlaps(hits: Sequence[Detection], fmt: str, offset: int) -> bool:
    for hit in hits:
        if hit.fmt == fmt and abs(hit.offset - offset) <= len(hit.magic):
            return True
    return False


def _dedupe(hits: Iterable[Detection]) -> list[Detection]:
    seen: dict[tuple[str, int], Detection] = {}
    for hit in hits:
        key = (hit.fmt, hit.offset)
        if key not in seen:
            seen[key] = hit
    return list(seen.values())


def _zip_bounds(data: bytes) -> tuple[int, int] | None:
    """Return the ZIP byte range from its EOCD/central-directory offsets."""
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return None
    size = struct.unpack_from("<I", data, eocd + 12)[0]
    offset = struct.unpack_from("<I", data, eocd + 16)[0]
    comment_len = struct.unpack_from("<H", data, eocd + 20)[0]
    end = eocd + 22 + comment_len
    if end > len(data):
        return None
    start = eocd - (offset + size) if offset + size <= eocd else 0
    return max(start, 0), end


def _detect_zip(data: bytes) -> list[Detection]:
    """ZIP is located from its end-of-central-directory record, anywhere."""
    bounds = _zip_bounds(data)
    if bounds is None:
        return []
    eocd = data.rfind(b"PK\x05\x06")
    entries = struct.unpack_from("<H", data, eocd + 10)[0]
    sig = _BY_FORMAT["zip"]
    # `concat` is how many bytes precede the archive (Info-ZIP's own trick)
    concat = bounds[0]
    note = f"{entries} entries"
    if concat > 0:
        note += f", {concat} B prefix tolerated"
    return [
        Detection(
            fmt="zip",
            label=sig.label,
            category="archive",
            extension=".zip",
            offset=concat,
            magic=b"PK\x03\x04",
            confidence="strong",
            note=note,
        )
    ]


# --------------------------------------------------------------------------
# text formats: no magic, so sniff by content
# --------------------------------------------------------------------------

_TEXT_HINTS: tuple[tuple[str, bytes | re.Pattern, str, str], ...] = (
    ("html", re.compile(rb"^\s*(<!doctype html|<html|<\?xml[^>]*\?>\s*<html)", re.I), "document", ".html"),
    ("svg", re.compile(rb"^\s*(<\?xml[^>]*\?>\s*)?<svg", re.I), "image", ".svg"),
    ("xml", re.compile(rb"^\s*<\?xml", re.I), "data", ".xml"),
    ("json", re.compile(rb"^\s*[\{\[]", re.I), "data", ".json"),
    ("css", re.compile(rb"^\s*(@charset|@import|:root|html|body|\.[A-Za-z_-]+\s*\{)", re.I), "document", ".css"),
    ("javascript", re.compile(rb"^\s*(//|/\*|\"use strict\"|'use strict'|import |export |const |let |var |function )"), "document", ".js"),
    ("python", re.compile(rb"^\s*(#!.*python|import |from |def |class )", re.I), "document", ".py"),
    ("shell", re.compile(rb"^\s*#!.*/(ba|z|da)?sh", re.I), "executable", ".sh"),
    ("batch", re.compile(rb"^\s*@(echo\s+off|rem)", re.I), "executable", ".bat"),
    ("markdown", re.compile(rb"^\s*(#[^#\n]|>\s|\|\s*[-:]+|```|---\s*$)", re.M), "document", ".md"),
    ("csv", re.compile(rb"^[^,\n]+(,[^,\n]*){2,}\r?$", re.M), "data", ".csv"),
    ("ini", re.compile(rb"^\s*\[[^\]\n]+\]\s*$", re.M), "data", ".ini"),
    ("yaml", re.compile(rb"^\s*(---\s*$|[A-Za-z0-9_.-]+:\s)", re.M), "data", ".yaml"),
    ("sql", re.compile(rb"^\s*(select |insert |create |update |delete |with )", re.I), "data", ".sql"),
)


def detect_text(data: bytes) -> list[Detection]:
    """Best-effort detection of text formats (they have no magic bytes)."""
    sample = data[:4096]
    if not sample:
        return []
    printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126 or b >= 0x80)
    if printable < len(sample) * 0.90:
        return []
    hits: list[Detection] = []
    for fmt, pattern, category, ext in _TEXT_HINTS:
        if pattern.search(sample):
            hits.append(
                Detection(fmt=fmt, label=f"{fmt} text", category=category, extension=ext,
                          offset=0, magic=b"", confidence="likely", note="content heuristic")
            )
    return hits


# --------------------------------------------------------------------------
# host selection
# --------------------------------------------------------------------------

#: order in which ``--host auto`` prefers formats (real user file wins first)
AUTO_HOST_PRIORITY: tuple[str, ...] = (
    "png", "jpeg", "gif", "bmp", "webp", "tiff", "ico", "tga", "svg",
    "mp4", "mp3", "wav", "flac", "avi", "midi", "aiff",
    "pdf", "postscript", "rtf", "html", "docx", "xlsx", "pptx", "epub", "odt", "ods", "odp", "cbz",
    "exe", "elf", "mz", "wasm", "class", "macho", "apk", "jar",
    "zip", "tar", "gz", "bz2", "xz",
    "markdown", "text", "json", "csv", "xml", "yaml", "ini", "toml", "css", "sql",
    "javascript", "typescript", "python", "shell", "batch", "powershell", "java", "c", "php", "ruby", "go", "rust",
)

_EXTENSION_TO_FORMAT: dict[str, str] = {}
for _s in SIGNATURES:
    _EXTENSION_TO_FORMAT.setdefault(_s.extension.lstrip("."), _s.fmt)
_EXTENSION_TO_FORMAT.update(
    {
        "jpg": "jpeg", "jpe": "jpeg", "jfif": "jpeg", "gif": "gif", "tif": "tiff",
        "tiff": "tiff", "mpg": "mp4", "m4a": "mp4", "m4v": "mp4", "mov": "mp4",
        "wave": "wav", "mp2": "mp3", "htm": "html", "xhtml": "html",
        "txt": "text", "log": "text", "md": "markdown", "markdown": "markdown",
        "js": "javascript", "mjs": "javascript", "cjs": "javascript", "ts": "typescript", "tsx": "typescript",
        "py": "python", "sh": "shell", "bash": "shell", "zsh": "shell", "bat": "batch", "cmd": "batch",
        "ps1": "powershell", "yml": "yaml", "yaml": "yaml", "json": "json", "csv": "csv",
        "xml": "xml", "css": "css", "sql": "sql", "ini": "ini", "cfg": "ini", "conf": "ini", "toml": "toml",
        "rtf": "rtf", "doc": "doc", "xls": "ole2", "ppt": "ole2",
        "java": "java", "c": "c", "h": "c", "cc": "c", "cpp": "c", "cxx": "c", "hpp": "c",
        "php": "php", "rb": "ruby", "go": "go", "rs": "rust", "ps": "postscript",
        "exe": "exe", "dll": "exe", "so": "elf", "out": "elf", "mz": "mz",
        "apk": "apk", "jar": "jar", "odt": "odt", "ods": "ods", "odp": "odp",
        "epub": "epub", "xpi": "jar", "7z": "7z", "rar": "rar", "tar": "tar",
        "tgz": "gz", "tbz2": "bz2", "txz": "xz",
        "aif": "aiff", "aifc": "aiff", "mid": "midi", "midi": "midi", "tga": "tga", "svg": "svg",
        "ps": "postscript", "macho": "macho", "o": "macho", "wasm": "wasm",
        "class": "class", "docx": "docx", "xlsx": "xlsx", "pptx": "pptx",
        "cbz": "cbz", "m4v": "mp4", "m4a": "mp4", "mov": "mp4",
        "flv": "flv", "avi": "avi", "webp": "webp", "bmp": "bmp", "ico": "ico",
        "cur": "ico", "tif": "tiff", "tiff": "tiff", "png": "png", "gif": "gif",
        "jpg": "jpeg", "jpeg": "jpeg", "webm": "mkv", "mkv": "mkv", "mp3": "mp3",
        "flac": "flac", "wav": "wav", "ogg": "ogg", "pdf": "pdf", "rtf": "rtf",
    }
)


def format_for_path(path: str, data: bytes | None = None) -> str | None:
    """Best guess of a PolyglotMaker format key for a file path/bytes."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    extension_format = _EXTENSION_TO_FORMAT.get(ext)
    if data is not None:
        hits = detect_at_offset(data, 0)
        if hits:
            best = hits[0]
            if best.fmt == "mz":
                pe_offset = struct.unpack_from("<I", data, 0x3C)[0] if len(data) >= 0x40 else -1
                return "exe" if pe_offset >= 0 and data[pe_offset:pe_offset + 4] == b"PE\x00\x00" else "mz"
            mapped = _normalise(best.fmt)
            # OOXML, EPUB, JAR, APK and ODF are ZIP-based. Preserve the more
            # specific container identity supplied by the extension instead
            # of collapsing every such input to generic ZIP.
            if mapped == "zip" and extension_format in {"docx", "xlsx", "pptx", "epub", "jar", "apk", "odt", "ods", "odp", "cbz"}:
                return extension_format
            if mapped == "shebang" and extension_format:
                return extension_format
            if mapped:
                return mapped
        if extension_format:
            return extension_format
        text = detect_text(data)
        if text:
            return _normalise(text[0].fmt)
        return "text"
    if ext in _EXTENSION_TO_FORMAT:
        return _EXTENSION_TO_FORMAT[ext]
    return None


def _normalise(fmt: str) -> str:
    aliases = {
        "gif87": "gif", "gif89": "gif", "tiff_le": "tiff", "tiff_be": "tiff", "tiff_bigel": "tiff",
        "mp3_id3": "mp3", "mp3_v2": "mp3", "avi_riff": "avi", "mz": "exe", "ole2": "doc",
        "zip_empty": "zip", "zip_spanned": "zip", "zip64eocd": "zip", "ustar": "tar",
        "aiff": "aiff", "psd": "psd", "webp": "webp", "ps": "postscript",
        "macho32": "macho", "macho64": "macho", "macho32r": "macho", "macho64r": "macho", "macho_fat": "macho",
    }
    return aliases.get(fmt, fmt)


def guess_host(paths: Sequence[str], blobs: Sequence[bytes]) -> str:
    """Pick a host format for ``--host auto`` from the supplied inputs."""
    generic = {"text", "markdown", "json", "csv", "xml", "javascript", "python", "shell"}
    candidates = [format_for_path(path, blob) for path, blob in zip(paths, blobs)]
    for fmt in candidates:
        if fmt in AUTO_HOST_PRIORITY and fmt not in generic:
            return fmt
    for path in paths:
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext in _EXTENSION_TO_FORMAT and _EXTENSION_TO_FORMAT[ext] in AUTO_HOST_PRIORITY:
            return _EXTENSION_TO_FORMAT[ext]
    for fmt in candidates:
        if fmt in AUTO_HOST_PRIORITY:
            return fmt
    return "png"


def is_probably_text(data: bytes, sample: int = 8192) -> bool:
    head = data[:sample]
    if not head:
        return True
    printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b <= 126)
    return printable >= len(head) * 0.95


def entropy(data: bytes) -> float:
    """Shannon entropy in bits/byte -- useful to spot encrypted/compressed blobs."""
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    value = 0.0
    for count in counts:
        if count:
            prob = count / total
            value -= prob * (prob and _log2(prob))
    return round(value, 4)


def _log2(x: float) -> float:
    import math

    return math.log2(x)


def png_is_valid(data: bytes) -> bool:
    """Cheap structural PNG check (signature + walkable chunks + IEND)."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    pos = 8
    seen_iend = False
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4 : pos + 8]
        if length > len(data):
            break
        body = data[pos + 4 : pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(body) != crc:
            return False
        if ctype == b"IEND":
            seen_iend = True
            break
        pos += 12 + length
    return seen_iend
