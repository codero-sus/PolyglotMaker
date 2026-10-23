"""Byte-level primitives: packing, checksums, segments, obfuscation, safe paths.

Everything here is standard library only.  PolyglotMaker's runtime has **no
third-party dependencies** -- optional libraries (Pillow, pypdf, pefile,
mutagen) are used by :mod:`polyglotmaker.core.validators` purely to *prove*
that a generated host file really parses, and are skipped when absent.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
import struct
import time
import unicodedata
import zlib
from dataclasses import asdict, dataclass, field
from typing import Iterable, Iterator, Sequence

# --------------------------------------------------------------------------
# integer packing
# --------------------------------------------------------------------------


def u8(value: int) -> bytes:
    return struct.pack("<B", value & 0xFF)


def u16le(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def u32le(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def u64le(value: int) -> bytes:
    return struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def u16be(value: int) -> bytes:
    return struct.pack(">H", value & 0xFFFF)


def u24be(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFF)[1:]


def u32be(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFFFF)


def u64be(value: int) -> bytes:
    return struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF)


def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return ((value + alignment - 1) // alignment) * alignment


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def adler32(data: bytes) -> int:
    return zlib.adler32(data) & 0xFFFFFFFF


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def dos_datetime(when: float | None = None) -> tuple[int, int]:
    """Return (dos_date, dos_time) as used by ZIP local file headers."""
    stamp = time.localtime(when if when is not None else time.time())
    year = max(stamp.tm_year, 1980)
    date = ((year - 1980) << 9) | (stamp.tm_mon << 5) | stamp.tm_mday
    dos_time = (stamp.tm_hour << 11) | (stamp.tm_min << 5) | (stamp.tm_sec // 2)
    return date, dos_time


# --------------------------------------------------------------------------
# segments -- the byte map that `polyglot inspect` renders
# --------------------------------------------------------------------------

#: recognised segment kinds, in the order they normally appear
SEGMENT_KINDS = (
    "host",      # bytes that make the host format itself valid
    "embed",     # payload stored in a slot the host format legally defines
    "layer",     # a second, complete, valid file of another format
    "bundle",    # the payload archive (ZIP or raw store)
    "footer",    # bootstrap metadata used by the unpacker
    "padding",   # alignment / filler
    "meta",      # descriptive structures (manifest mirrors, comments)
)


@dataclass
class Segment:
    """One labelled region of the output file."""

    name: str
    offset: int
    length: int
    kind: str = "host"
    fmt: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SEGMENT_KINDS:
            raise ValueError(f"unknown segment kind {self.kind!r}")

    @property
    def end(self) -> int:
        return self.offset + self.length

    def to_dict(self) -> dict:
        data = asdict(self)
        data["end"] = self.end
        return data

    def label(self) -> str:
        span = f"0x{self.offset:08x}..0x{self.end:08x}"
        fmt = f" [{self.fmt}]" if self.fmt else ""
        return f"{self.name}{fmt} {span} ({self.length} B)"


class ByteWriter:
    """Append-only writer that records a :class:`Segment` map and supports
    back-patching of sizes/offsets (needed by every binary container)."""

    def __init__(self, initial: bytes = b"") -> None:
        self._buf = bytearray(initial)
        self.segments: list[Segment] = []

    # -- basic IO ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self._buf)

    @property
    def tell(self) -> int:
        return len(self._buf)

    @property
    def value(self) -> bytes:
        return bytes(self._buf)

    def write(
        self,
        data: bytes,
        *,
        name: str | None = None,
        kind: str = "host",
        fmt: str | None = None,
        detail: str = "",
    ) -> int:
        """Append ``data``; return the offset it was written at."""
        offset = len(self._buf)
        self._buf += data
        if name:
            self.segments.append(
                Segment(name=name, offset=offset, length=len(data), kind=kind, fmt=fmt, detail=detail)
            )
        return offset

    def patch(self, offset: int, data: bytes) -> None:
        """Overwrite bytes already written (used to fill in lengths)."""
        end = offset + len(data)
        if end > len(self._buf):
            self._buf.extend(b"\x00" * (end - len(self._buf)))
        self._buf[offset:end] = data

    def patch_u32be(self, offset: int, value: int) -> None:
        self.patch(offset, u32be(value))

    def patch_u32le(self, offset: int, value: int) -> None:
        self.patch(offset, u32le(value))

    def reserve(self, size: int, *, name: str | None = None, kind: str = "host", fill: bytes = b"\x00") -> int:
        offset = len(self._buf)
        self._buf += (fill * size)[:size] if fill else b"\x00" * size
        if name:
            self.segments.append(Segment(name=name, offset=offset, length=size, kind=kind))
        return offset

    def align(self, alignment: int, fill: bytes = b"\x00", *, name: str = "alignment", kind: str = "padding") -> None:
        pad = align_up(len(self._buf), alignment) - len(self._buf)
        if pad:
            self.write(fill[:1] * pad, name=f"{name} ({pad} B)", kind=kind)

    def sorted_segments(self) -> list[Segment]:
        return sorted(self.segments, key=lambda s: (s.offset, -s.length))


# --------------------------------------------------------------------------
# obfuscation (NOT encryption -- documented everywhere)
# --------------------------------------------------------------------------


def keystream(key: bytes, salt: bytes, length: int) -> bytes:
    """Counter-mode SHA-256 keystream.

    This is *obfuscation*, not cryptography: it hides payload bytes from
    casual inspection and stops signature scanners, but the key sits in the
    same command line / manifest.  Use real encryption (age, GPG) first if you
    need confidentiality.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + salt + struct.pack("<Q", counter)).digest()
        counter += 1
    return bytes(out[:length])


def xor_stream(key: bytes, salt: bytes, data: bytes) -> bytes:
    if not key:
        return data
    stream = keystream(key, salt, len(data))
    return bytes(a ^ b for a, b in zip(data, stream))


# --------------------------------------------------------------------------
# base64 helpers (used by strict text hosts)
# --------------------------------------------------------------------------

_B64_RE = re.compile(rb"[A-Za-z0-9+/=\r\n]+")


def b64_encode(data: bytes, wrap: int = 76) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    if wrap <= 0:
        return encoded
    return "\n".join(encoded[i : i + wrap] for i in range(0, len(encoded), wrap))


def b64_decode(text: str | bytes) -> bytes:
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", text)
    pad = (-len(cleaned)) % 4
    return base64.b64decode(cleaned + "=" * pad)


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def portable_member_key(name: str) -> str:
    """Return the cross-platform collision key used for archive member names."""
    return unicodedata.normalize("NFC", str(name)).lower()


def safe_member_name(name: str) -> str:
    """Normalize an untrusted archive path for ZIP-slip and Windows safety."""
    raw = str(name).strip()
    if raw.startswith(("/", chr(92))) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"unsafe absolute member name: {name!r}")
    normalized = raw.replace("\\", "/")
    parts: list[str] = []
    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError(f"unsafe member name (path traversal): {normalized!r}")
        part = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", part).rstrip(". ")
        if not part:
            part = "_"
        if re.match(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", part, re.IGNORECASE):
            part = "_" + part
        parts.append(part[:200])
    if not parts:
        raise ValueError(f"unsafe member name: {name!r}")
    safe_name = "/".join(parts)[:2000].rstrip(". ")
    if not safe_name:
        raise ValueError(f"unsafe member name: {name!r}")
    return safe_name


def resolve_output(root: str, name: str) -> str:
    """Join ``name`` onto ``root`` and reject traversal through symlinks."""
    root_real = os.path.realpath(os.path.abspath(root))
    target = os.path.realpath(os.path.join(root_real, safe_member_name(name)))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError(f"member {name!r} escapes the output directory")
    return target


def unique_path(path: str) -> str:
    """``a.txt`` -> ``a.1.txt`` if ``a.txt`` already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 1
    while True:
        candidate = f"{stem}.{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def dedupe_names(names: Iterable[str]) -> list[str]:
    """Make portable archive paths unique without losing any of them."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        clean = safe_member_name(raw)
        if portable_member_key(clean) not in seen:
            name = clean
        else:
            stem, ext = os.path.splitext(clean)
            index = 1
            name = safe_member_name(f"{stem} ({index}){ext}")
            while portable_member_key(name) in seen:
                index += 1
                name = safe_member_name(f"{stem} ({index}){ext}")
        seen.add(portable_member_key(name))
        out.append(name)
    return out


def guess_mime(name: str, data: bytes | None = None) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------

_SIZE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def human_size(num: float | int) -> str:
    value = float(num)
    for unit in _SIZE_UNITS:
        if abs(value) < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PiB"


def human_hex(num: int) -> str:
    return f"0x{num:08x}"


def chunked(data: bytes, size: int) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def read_bytes(path: str | os.PathLike[str]) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def to_hex(data: bytes, limit: int = 64) -> str:
    clipped = data[:limit]
    text = binascii.hexlify(clipped, " ").decode("ascii")
    return text + (" ..." if len(data) > limit else "")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def flatten(items: Sequence[Iterable[str] | str | None]) -> list[str]:
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if isinstance(item, str):
            out.extend(p.strip() for p in item.split(",") if p.strip())
        else:
            out.extend(str(p).strip() for p in item if str(p).strip())
    return out
