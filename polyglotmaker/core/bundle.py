"""Payload records, manifests, and interoperable ZIP/raw bundles.

A generated file is a host format plus a bundled set of arbitrary input bytes.
ZIP is used by default because it is openable by common archive tools. The
PolyglotMaker bootstrap footer is stored in the ZIP comment, so the EOCD stays
at the end of the archive and no extra marker breaks ZIP readers.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import stat
import struct
import time
import zipfile
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping, Sequence

from .errors import CorruptContainerError, IntegrityError, ObfuscationKeyError
from .util import dos_datetime, guess_mime, safe_member_name, sha256_hex, xor_stream

MANIFEST_PATH = "POLYGLOTMAKER/manifest.json"
PAYLOAD_PREFIX = "payloads/"
BOOTSTRAP_MAGIC = b"PLGMK1"
BOOTSTRAP_VERSION = 1
# ASCII-hex footer: literal magic followed by version/offset/length/hashes/flags.
# Hex encoding prevents random checksum bytes from accidentally creating a later PK\x05\x06 EOCD signature.
BOOTSTRAP_FIELDS_LEN = 1 + 8 + 8 + 32 + 32 + 2
BOOTSTRAP_LEN = 6 + 2 * BOOTSTRAP_FIELDS_LEN
RAW_MAGIC = b"PLGMSTORE1"
RAW_VERSION = 1
OBFUSCATION_MAGIC = b"PLGMOBF1"
OBFUSCATION_VERSION = 1


@dataclass
class InputFile:
    """In-memory representation of one input file."""

    name: str
    data: bytes
    source: str = ""
    mtime: float = 0.0
    mode: int = 0o644
    mime: str = "application/octet-stream"
    role: str = "payload"  # payload | host | layer

    def __post_init__(self) -> None:
        self.name = safe_member_name(self.name)
        if self.mtime <= 0:
            self.mtime = time.time()
        self.mode &= 0o777
        if not self.mime:
            self.mime = guess_mime(self.name, self.data)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)

    def manifest_entry(self, *, archive_name: str | None = None) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": len(self.data),
            "sha256": self.sha256,
            "mtime": self.mtime,
            "mode": self.mode,
            "mime": self.mime,
            "role": self.role,
            "store": "zip",
            "entry": archive_name or (PAYLOAD_PREFIX + self.name),
        }

    def member(self) -> dict[str, Any]:
        return {"name": self.name, "data": self.data, "mtime": self.mtime, "mode": self.mode, "mime": self.mime}


@dataclass
class Bootstrap:
    offset: int
    length: int
    manifest_sha256: bytes
    archive_sha256: bytes
    flags: int = 0
    version: int = BOOTSTRAP_VERSION

    def encode(self) -> bytes:
        fields = (bytes([self.version]) + struct.pack("<QQ", self.offset, self.length)
                  + self.manifest_sha256[:32].ljust(32, b"\x00")
                  + self.archive_sha256[:32].ljust(32, b"\x00")
                  + struct.pack("<H", self.flags & 0xFFFF))
        return BOOTSTRAP_MAGIC + fields.hex().encode("ascii")


def new_manifest(
    files: Sequence[InputFile],
    *,
    host: str,
    host_mode: str = "generated",
    embed_mode: str = "append",
    store: str = "zip",
    compression: str = "deflate",
    obfuscated: bool = False,
    created_utc: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create schema-v1 manifest. File hashes always cover original bytes."""
    records = [item.manifest_entry() for item in files]
    file_set = [{"name": item["name"], "size": item["size"], "sha256": item["sha256"]}
                for item in records]
    payload_set_sha = hashlib.sha256(canonical_json(file_set)).hexdigest()
    return {
        "tool": "PolyglotMaker",
        "format_version": 1,
        "created_utc": created_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "container": {
            "host": host,
            "host_mode": host_mode,
            "embed_mode": embed_mode,
            "store": store,
            "compression": compression,
            "obfuscated": bool(obfuscated),
            "layers": [],
        },
        "files": records,
        "bundle": {"sha256": payload_set_sha, "size": None, "offset": None,
                   "sha256_scope": "payload-set (footer separately hashes normalized ZIP bytes)"},
        "options": dict(options or {}),
    }


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_manifest(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptContainerError(f"invalid PolyglotMaker manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("tool") != "PolyglotMaker":
        raise CorruptContainerError("not a PolyglotMaker manifest")
    version = value.get("format_version")
    if version != 1:
        raise CorruptContainerError(f"unsupported manifest version {version!r}")
    if not isinstance(value.get("files"), list):
        raise CorruptContainerError("manifest has no files list")
    return value


def _zip_info(name: str, mtime: float, mode: int, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(safe_member_name(name))
    stamp = time.localtime(max(mtime, 315532800))
    info.date_time = (stamp.tm_year, stamp.tm_mon, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec - stamp.tm_sec % 2)
    info.create_system = 3
    info.external_attr = (mode & 0xFFFF) << 16
    info.compress_type = compress_type
    return info


def compression_method(method: str) -> int:
    key = (method or "deflate").lower()
    mapping = {"none": zipfile.ZIP_STORED, "store": zipfile.ZIP_STORED,
               "stored": zipfile.ZIP_STORED, "deflate": zipfile.ZIP_DEFLATED,
               "bzip2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA}
    if key not in mapping:
        raise ValueError(f"unknown ZIP compression {method!r}")
    return mapping[key]


def _zip_bytes(manifest: dict[str, Any], files: Sequence[InputFile], method: str,
               comment: bytes = b"") -> tuple[bytes, bytes]:
    manifest_bytes = canonical_json(manifest)
    buffer = io.BytesIO()
    compression = compression_method(method)
    with zipfile.ZipFile(buffer, "w", compression=compression, allowZip64=True) as archive:
        # The manifest is first for people using generic archive tools.
        archive.writestr(_zip_info(MANIFEST_PATH, time.time(), 0o644, zipfile.ZIP_DEFLATED), manifest_bytes)
        for item in files:
            name = safe_member_name(item.name)
            archive.writestr(_zip_info(PAYLOAD_PREFIX + name, item.mtime, item.mode, compression), item.data)
        archive.comment = comment[:65535]
    return buffer.getvalue(), manifest_bytes


def _normalized_zip_sha(data: bytes) -> bytes:
    """Hash all ZIP bytes up through the EOCD but normalize away its comment.

    The bootstrap comment contains this checksum itself, so it cannot be a
    recursive hash of the final bytes. Normalizing to an empty comment makes
    the checksum stable while still detecting any changed records/metadata.
    """
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return hashlib.sha256(data).digest()
    normalized = data[:eocd + 20] + b"\x00\x00"
    return hashlib.sha256(normalized).digest()


def build_zip_bundle(
    manifest: dict[str, Any],
    files: Sequence[InputFile],
    *,
    compression: str = "deflate",
    offset: int = 0,
    flags: int = 0,
) -> tuple[bytes, bytes, bytes]:
    """Build a ZIP with manifest first and a fixed-size bootstrap comment.

    Returns ``(zip_bytes, manifest_bytes, normalized_sha256)``. ``offset`` is
    the ZIP's absolute offset in the final polyglot file (which may be inside a
    legal MP4 ``free`` box).
    """
    provisional, manifest_bytes = _zip_bytes(manifest, files, compression)
    archive_sha = _normalized_zip_sha(provisional)
    manifest_sha = hashlib.sha256(manifest_bytes).digest()
    comment_prefix = b"PolyglotMaker v1\x00"
    footer = Bootstrap(offset=offset, length=len(provisional) + len(comment_prefix) + BOOTSTRAP_LEN,
                       manifest_sha256=manifest_sha, archive_sha256=archive_sha,
                       flags=flags).encode()
    # ZIP file comment length is a u16; include a short descriptive prefix.
    comment = comment_prefix + footer
    result, manifest_bytes_2 = _zip_bytes(manifest, files, compression, comment)
    if manifest_bytes_2 != manifest_bytes:
        raise AssertionError("manifest changed while building ZIP")
    if len(result) != len(provisional) + len(comment):
        raise AssertionError("unexpected ZIP comment length accounting")
    return result, manifest_bytes, archive_sha


def decode_bootstrap(data: bytes) -> Bootstrap | None:
    """Read the comment footer from a ZIP at EOF (prefix tolerant)."""
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return None
    try:
        comment_len = struct.unpack_from("<H", data, eocd + 20)[0]
    except struct.error:
        return None
    comment_start, comment_end = eocd + 22, eocd + 22 + comment_len
    if comment_end > len(data):
        return None
    comment = data[comment_start:comment_end]
    idx = comment.rfind(BOOTSTRAP_MAGIC)
    if idx < 0 or idx + BOOTSTRAP_LEN > len(comment):
        return None
    footer = comment[idx:idx + BOOTSTRAP_LEN]
    try:
        fields = bytes.fromhex(footer[6:].decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if len(fields) != BOOTSTRAP_FIELDS_LEN:
        return None
    version = fields[0]
    offset, length = struct.unpack_from("<QQ", fields, 1)
    manifest_hash = fields[17:49]
    archive_hash = fields[49:81]
    flags = struct.unpack_from("<H", fields, 81)[0]
    return Bootstrap(offset, length, manifest_hash, archive_hash, flags, version)


def validate_zip_bundle(data: bytes, *, verify_footer: bool = True) -> tuple[dict[str, Any], dict[str, bytes], Bootstrap | None]:
    """Open a prefix-tolerant ZIP, validate manifest and file hashes."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise CorruptContainerError(f"not a readable ZIP bundle: {exc}") from exc
    with archive:
        try:
            manifest_raw = archive.read(MANIFEST_PATH)
        except KeyError as exc:
            raise CorruptContainerError("ZIP archive has no PolyglotMaker manifest") from exc
        manifest = parse_manifest(manifest_raw)
        footer = decode_bootstrap(data)
        if verify_footer and footer:
            if footer.version != BOOTSTRAP_VERSION:
                raise CorruptContainerError(f"unsupported bootstrap footer version {footer.version}")
            expected = hashlib.sha256(manifest_raw).digest()
            if expected != footer.manifest_sha256:
                raise IntegrityError("manifest SHA-256 does not match bootstrap footer")
            location = locate_zip(data)
            if location is None:
                raise CorruptContainerError("ZIP bundle location cannot be determined")
            start, length = location
            if _normalized_zip_sha(data[start:start + length]) != footer.archive_sha256:
                raise IntegrityError("ZIP bundle SHA-256 does not match bootstrap footer")
        outputs: dict[str, bytes] = {}
        for record in manifest["files"]:
            if not isinstance(record, dict):
                raise CorruptContainerError("malformed file record in manifest")
            name = safe_member_name(str(record.get("name", "")))
            entry = safe_member_name(str(record.get("entry", PAYLOAD_PREFIX + name)))
            try:
                content = archive.read(entry)
            except KeyError as exc:
                raise CorruptContainerError(f"payload member missing: {entry}") from exc
            if len(content) != int(record.get("size", -1)):
                raise IntegrityError(f"size mismatch for {name!r}")
            expected_hash = str(record.get("sha256", ""))
            if expected_hash and hashlib.sha256(content).hexdigest() != expected_hash:
                raise IntegrityError(f"SHA-256 mismatch for {name!r}")
            if name in outputs:
                raise CorruptContainerError(f"duplicate output name in manifest: {name!r}")
            outputs[name] = content
    return manifest, outputs, footer


def build_raw_bundle(manifest: dict[str, Any], files: Sequence[InputFile]) -> bytes:
    """Build an uncompressed framed store for `--store raw`.

    The record table is length-prefixed and each payload has an independent
    SHA-256; extraction never trusts a path or size from the file blindly.
    """
    manifest_bytes = canonical_json(manifest)
    out = bytearray(RAW_MAGIC + bytes([RAW_VERSION]) + struct.pack(">I", len(manifest_bytes)) + manifest_bytes)
    out += struct.pack(">I", len(files))
    for item in files:
        name = safe_member_name(item.name).encode("utf-8")
        out += struct.pack(">H", len(name))
        out += struct.pack(">Q", len(item.data))
        out += struct.pack(">d", item.mtime)
        out += struct.pack(">I", item.mode & 0xFFFFFFFF)
        out += hashlib.sha256(item.data).digest()
        out += name
        out += item.data
    return bytes(out)


def parse_raw_bundle(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not data.startswith(RAW_MAGIC) or len(data) < len(RAW_MAGIC) + 9:
        raise CorruptContainerError("not a PolyglotMaker raw store")
    pos = len(RAW_MAGIC)
    version = data[pos]
    pos += 1
    if version != RAW_VERSION:
        raise CorruptContainerError(f"unsupported raw store version {version}")
    manifest_len = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    if manifest_len > len(data) - pos:
        raise CorruptContainerError("truncated raw manifest")
    manifest = parse_manifest(data[pos:pos + manifest_len])
    pos += manifest_len
    if pos + 4 > len(data):
        raise CorruptContainerError("truncated raw record count")
    count = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    if count > 100000:
        raise CorruptContainerError("unreasonable raw record count")
    outputs = {}
    for _ in range(count):
        if pos + 54 > len(data):
            raise CorruptContainerError("truncated raw record header")
        name_len = struct.unpack_from(">H", data, pos)[0]
        size = struct.unpack_from(">Q", data, pos + 2)[0]
        _mtime = struct.unpack_from(">d", data, pos + 10)[0]
        _mode = struct.unpack_from(">I", data, pos + 18)[0]
        digest = data[pos + 22:pos + 54]
        pos += 54
        if name_len > 4096 or name_len > len(data) - pos:
            raise CorruptContainerError("invalid raw member name length")
        name = safe_member_name(data[pos:pos + name_len].decode("utf-8", "replace"))
        pos += name_len
        if size > len(data) - pos:
            raise CorruptContainerError(f"truncated raw payload {name!r}")
        content = data[pos:pos + size]
        pos += size
        if hashlib.sha256(content).digest() != digest:
            raise IntegrityError(f"SHA-256 mismatch for {name!r}")
        if name in outputs:
            raise CorruptContainerError(f"duplicate raw member name: {name!r}")
        outputs[name] = content
    if pos != len(data):
        raise CorruptContainerError("unexpected bytes after raw-store records")
    return manifest, outputs


def obfuscate_bundle(data: bytes, key: str | bytes, salt: bytes | None = None) -> bytes:
    """Obfuscate bundle bytes using SHA-256 counter-mode XOR (not encryption)."""
    if isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key
    if not key_bytes:
        raise ValueError("obfuscation key must not be empty")
    salt = salt or secrets.token_bytes(16)
    cipher = xor_stream(hashlib.sha256(key_bytes).digest(), salt, data)
    return (OBFUSCATION_MAGIC + bytes([OBFUSCATION_VERSION]) + salt
            + hashlib.sha256(cipher).digest() + cipher)


def deobfuscate_bundle(envelope: bytes, key: str | bytes | None) -> bytes:
    if not envelope.startswith(OBFUSCATION_MAGIC):
        return envelope
    if key is None:
        raise ObfuscationKeyError("this PolyglotMaker bundle is obfuscated; provide --key")
    if len(envelope) < 8 + 1 + 16 + 32:
        raise CorruptContainerError("truncated obfuscated bundle envelope")
    version = envelope[8]
    if version != OBFUSCATION_VERSION:
        raise CorruptContainerError(f"unsupported obfuscation version {version}")
    salt = envelope[9:25]
    expected = envelope[25:57]
    cipher = envelope[57:]
    if hashlib.sha256(cipher).digest() != expected:
        raise IntegrityError("obfuscated bundle ciphertext checksum mismatch")
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    plain = xor_stream(hashlib.sha256(key_bytes).digest(), salt, cipher)
    if not plain.startswith((b"PK\x03\x04", b"PK\x05\x06", RAW_MAGIC)):
        raise ObfuscationKeyError("incorrect obfuscation key (decrypted store signature is invalid)")
    return plain


def locate_zip(data: bytes) -> tuple[int, int] | None:
    """Locate a ZIP using its EOCD and bootstrap metadata.

    A valid archive can have an arbitrary prefix. When no PolyglotMaker footer
    is available, compute the prefix from central-directory coordinates.
    """
    footer = decode_bootstrap(data)
    if footer and 0 <= footer.offset < len(data) and data[footer.offset:footer.offset + 4] == b"PK\x03\x04":
        if 0 < footer.length <= len(data) - footer.offset:
            return footer.offset, footer.length
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + 22 > len(data):
        return None
    try:
        cd_size = struct.unpack_from("<I", data, eocd + 12)[0]
        cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
        comment_len = struct.unpack_from("<H", data, eocd + 20)[0]
        prefix = eocd - cd_size - cd_offset
        if prefix < 0:
            prefix = 0
        end = eocd + 22 + comment_len
        if end > len(data):
            return None
        return prefix, end - prefix
    except struct.error:
        return None


def decode_bootstrap_at_eof(data: bytes) -> Bootstrap | None:
    """Alias retained for older callers."""
    return decode_bootstrap(data)
