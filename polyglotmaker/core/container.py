"""Unified make / inspect / verify / unpack pipeline."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .bundle import (
    BOOTSTRAP_MAGIC,
    BOOTSTRAP_LEN,
    MANIFEST_PATH,
    OBFUSCATION_MAGIC,
    PAYLOAD_PREFIX,
    RAW_MAGIC,
    InputFile,
    build_raw_bundle,
    build_zip_bundle,
    canonical_json,
    decode_bootstrap,
    _normalized_zip_sha,
    deobfuscate_bundle,
    new_manifest,
    locate_zip,
    obfuscate_bundle,
    parse_manifest,
    parse_raw_bundle,
    validate_zip_bundle,
)
from .detect import Detection, detect, detect_text, format_for_path, guess_host
from .errors import CorruptContainerError, IncompatibleCombinationError, IntegrityError, NotAPolyglotError, UsageError
from .util import Segment, guess_mime, resolve_output, safe_member_name, sha256_hex, unique_path, write_bytes
from ..formats.base import EMBED_MEMBERS, HostContext, HostFormat
from ..formats.registry import REGISTRY
from ..formats.images import PLGM_MARKER


@dataclass
class MakeResult:
    data: bytes
    host: str
    extension: str
    manifest: dict[str, Any]
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)
    output_path: str | None = None
    bundle_sha256: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "extension": self.extension,
            "size": len(self.data),
            "output_path": self.output_path,
            "bundle_sha256": self.bundle_sha256,
            "warnings": list(self.warnings),
            "manifest": self.manifest,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass
class UnpackResult:
    manifest: dict[str, Any] | None
    files: dict[str, bytes]
    source_store: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_data: bool = False) -> dict[str, Any]:
        records = []
        manifest_files = {r.get("name"): r for r in (self.manifest or {}).get("files", []) if isinstance(r, dict)}
        for name, data in self.files.items():
            rec = {
                "name": name,
                "size": len(data),
                "sha256": sha256_hex(data),
                "mime": manifest_files.get(name, {}).get("mime", guess_mime(name, data)),
            }
            if include_data:
                rec["data_base64"] = base64.b64encode(data).decode("ascii")
            records.append(rec)
        return {"source_store": self.source_store, "manifest": self.manifest, "files": records, "warnings": self.warnings}


def input_from_path(path: str, *, name: str | None = None, role: str = "payload") -> InputFile:
    stat_result = os.stat(path)
    with open(path, "rb") as handle:
        data = handle.read()
    relative = name or os.path.basename(path)
    return InputFile(relative, data, source=os.path.abspath(path), mtime=stat_result.st_mtime,
                     mode=stat_result.st_mode & 0o777, mime=guess_mime(relative, data), role=role)


def _dedupe_inputs(files: Sequence[InputFile]) -> list[InputFile]:
    seen: dict[str, int] = {}
    out: list[InputFile] = []
    for item in files:
        raw = item.name
        if raw not in seen:
            seen[raw] = 0
            out.append(item)
            continue
        seen[raw] += 1
        stem, ext = os.path.splitext(raw)
        candidate = f"{stem} ({seen[raw]}){ext}"
        while candidate in seen:
            seen[raw] += 1
            candidate = f"{stem} ({seen[raw]}){ext}"
        seen[candidate] = 0
        cloned = InputFile(candidate, item.data, source=item.source, mtime=item.mtime, mode=item.mode,
                           mime=item.mime, role=item.role)
        out.append(cloned)
    return out


def _detect_compatible(fmt: HostFormat, path: str, data: bytes) -> bool:
    expected = fmt.spec.key
    by_path = format_for_path(path, data)
    aliases = {expected, *fmt.spec.aliases}
    if by_path == expected or by_path in aliases:
        return True
    if fmt.spec.key in {"jpeg", "gif", "tiff", "mp3", "zip"} and by_path in aliases:
        return True
    if fmt.spec.key == "mp4" and data[4:8] == b"ftyp":
        return True
    if fmt.spec.key == "wav" and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    if fmt.spec.key == "avi" and data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return True
    if fmt.spec.key == "tiff" and data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00"):
        return True
    if fmt.spec.key == "webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if fmt.spec.key == "exe":
        return fmt.validate(data)[0]
    if fmt.spec.key == "elf" and data.startswith(b"\x7fELF"):
        return True
    if fmt.spec.key in {"docx", "xlsx", "pptx", "epub", "odt", "ods", "odp", "jar", "apk", "cbz", "zip"}:
        return data.startswith(b"PK\x03\x04")
    if fmt.spec.key == "tar":
        return len(data) > 262 and data[257:262] == b"ustar"
    if fmt.spec.key in {"gz", "bz2", "xz"}:
        return (fmt.spec.key == "gz" and data.startswith(b"\x1f\x8b")) or (fmt.spec.key == "bz2" and data.startswith(b"BZh")) or (fmt.spec.key == "xz" and data.startswith(b"\xfd7zXZ\x00"))
    return fmt.spec.magic is None or (fmt.spec.magic and data.startswith(fmt.spec.magic))


def _zip_bundle_standalone(manifest: dict[str, Any], files: Sequence[InputFile], compression: str,
                           *, offset: int = 0) -> tuple[bytes, bytes, bytes]:
    return build_zip_bundle(manifest, files, compression=compression, offset=offset)



def _layer_output(layer: HostFormat, options: Mapping[str, Any]) -> bytes:
    context = HostContext(payload=b"", payload_role="none", options=dict(options), metadata={})
    return layer.render(context).data


def make_container(
    files: Sequence[InputFile],
    *,
    host: str = "auto",
    host_file: InputFile | None = None,
    also: Sequence[str] = (),
    embed_mode: str = "append",
    store: str = "zip",
    compression: str = "deflate",
    obfuscate_key: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> MakeResult:
    """Build the final polyglot bytes in memory.

    Every supplied payload is arbitrary bytes and is restored exactly. Format
    generation is a separate concern: each selected host is a real syntax
    skeleton, while unsupported input types are simply first-class payloads.
    """
    options = dict(options or {})
    payload_files = list(files)
    if host_file is not None:
        matching_input = next((item for item in payload_files
                               if (item.source and host_file.source
                                   and os.path.abspath(item.source) == os.path.abspath(host_file.source))
                               or (item.name == host_file.name and item.data == host_file.data)), None)
        if matching_input is None:
            host_file = InputFile(host_file.name, host_file.data, source=host_file.source,
                                  mtime=host_file.mtime, mode=host_file.mode,
                                  mime=host_file.mime, role="host")
            payload_files.insert(0, host_file)
        else:
            matching_input.role = "host"
    payload_files = _dedupe_inputs(payload_files)

    if not payload_files and host_file is None:
        raise UsageError("provide at least one input file")
    if host == "auto":
        paths = [item.source or item.name for item in payload_files]
        blobs = [item.data for item in payload_files]
        if host_file is not None:
            paths = [host_file.source or host_file.name] + paths
            blobs = [host_file.data] + blobs
        host_key = guess_host(paths, blobs)
    else:
        requested_host = str(host).strip().lower().lstrip(".")
        if requested_host == "pe32":
            options.setdefault("pe_bits", 32)
        elif requested_host == "pe64":
            options.setdefault("pe_bits", 64)
        elif requested_host == "elf32":
            options.setdefault("elf_bits", 32)
        elif requested_host == "elf64":
            options.setdefault("elf_bits", 64)
        host_key = REGISTRY.canonical(host)
    host_fmt = REGISTRY.get(host_key)
    if host_file is not None and not _detect_compatible(host_fmt, host_file.name, host_file.data):
        raise IncompatibleCombinationError(
            f"--host-file {host_file.name!r} does not look like a {host_fmt.spec.label}; choose a matching --host or omit --host-file"
        )

    if embed_mode not in {"append", "native", "none"}:
        raise UsageError("embed-mode must be append, native, or none")
    if store not in {"zip", "raw"}:
        raise UsageError("store must be zip or raw")
    warnings: list[str] = []

    # Member-container hosts store the original payloads directly as files.
    # They are already self-contained, so no second archive is concatenated.
    member_host = host_fmt.spec.embed == EMBED_MEMBERS and host_file is None
    if member_host:
        if also:
            raise IncompatibleCombinationError("--also layers are not supported for archive/package hosts; use a media/document host")
        if store != "zip":
            raise IncompatibleCombinationError("archive/package hosts already store inputs as native members; --store is not applicable")
        if obfuscate_key:
            raise IncompatibleCombinationError("obfuscation is not available for native archive/package hosts")
        native_files = [] if embed_mode == "none" else payload_files
        effective_embed = "none" if embed_mode == "none" else "members"
        manifest = new_manifest(native_files, host=host_key, host_mode="generated", embed_mode=effective_embed,
                                store="members", compression=compression, obfuscated=False, options=options)
        manifest["container"]["layers"] = []
        for record in manifest["files"]:
            record["store"] = "members"
        context = HostContext(payload=b"", payload_role="none", options={**options, "zip_compression": compression},
                              metadata={"manifest": manifest}, members=[item.member() for item in native_files])
        result = host_fmt.render(context)
        valid, why = host_fmt.validate(result.data)
        if not valid:
            raise CorruptContainerError(f"generated {host_key} host failed its own validation: {why}")
        segments = list(result.segments) or [Segment(host_fmt.spec.label, 0, len(result.data), "host", host_key)]
        return MakeResult(result.data, host_key, host_fmt.spec.extension, manifest, segments, warnings)

    if host_fmt.spec.embed == EMBED_MEMBERS and host_file is not None:
        warnings.append("an original archive host file is preserved byte-for-byte; new files are stored in an appended bundle")

    # Strict formats must not have binary data appended after their terminator.
    # Use a native/base64 slot for the whole store when one exists.
    effective_embed = embed_mode
    if embed_mode == "append" and host_fmt.spec.strict:
        if host_file is not None:
            raise IncompatibleCombinationError(
                f"{host_fmt.spec.label} is strict text; an untouched --host-file cannot have a binary ZIP appended. Omit --host-file or choose another host."
            )
        if host_fmt.spec.embed == "none":
            raise IncompatibleCombinationError(f"{host_fmt.spec.label} has no legal native payload slot")
        effective_embed = "native"
        warnings.append(f"{host_key} is a strict format; using native embedding to keep the host valid")
    if effective_embed == "native" and host_fmt.spec.embed == "none":
        raise IncompatibleCombinationError(f"{host_fmt.spec.label} has no legal native payload slot; use --embed-mode append")
    if host_fmt.spec.strict and also:
        raise IncompatibleCombinationError(f"{host_fmt.spec.label} is strict; separate --also layers cannot follow it without invalidating the host")
    if host_file is not None and effective_embed == "native":
        raise IncompatibleCombinationError("native embedding changes the host file; --host-file is preserved unchanged, so use --embed-mode append")
    if obfuscate_key and store == "raw":
        # The raw store itself can be XORed, but the envelope is already a
        # custom store; avoid nested wrappers and keep the CLI semantics clear.
        warnings.append("obfuscation applies to the raw store; it is not encryption")

    manifest_store = store
    manifest_embed = effective_embed
    manifest = new_manifest(payload_files, host=host_key, host_mode="original" if host_file else "generated",
                            embed_mode=manifest_embed, store=manifest_store, compression=compression,
                            obfuscated=bool(obfuscate_key), options=options)
    manifest["container"]["layers"] = []
    # Raw stores need their manifest entries to point at names, not ZIP paths.
    if store == "raw":
        for record in manifest["files"]:
            record["store"] = "raw"
            record["entry"] = record["name"]

    layer_specs: list[tuple[str, HostFormat, bytes]] = []
    seen_layers: set[str] = set()
    for layer_name in also:
        layer_fmt = REGISTRY.get(layer_name)
        if layer_fmt.spec.key == host_key or layer_fmt.spec.key in seen_layers:
            continue
        seen_layers.add(layer_fmt.spec.key)
        layer_data = _layer_output(layer_fmt, options)
        layer_specs.append((layer_fmt.spec.key, layer_fmt, layer_data))
        manifest["container"]["layers"].append({"format": layer_fmt.spec.key, "offset": None,
                                                   "length": len(layer_data), "validity": "separate-layer"})

    host_body_original = host_file.data if host_file is not None else None
    if host_body_original is not None:
        host_segments = [Segment("original host file", 0, len(host_body_original), "host", host_key)]
        def get_host_result(_payload: bytes, _role: str):
            return None
    else:
        host_segments = []
        def get_host_result(payload: bytes, role: str):
            context = HostContext(payload=payload, payload_role=role, options=options,
                                  metadata={"manifest": manifest})
            result = host_fmt.render(context)
            valid, why = host_fmt.validate(result.data)
            if not valid:
                raise CorruptContainerError(f"generated {host_key} host failed its own validation: {why}")
            return result

    # Build archive. Appended bundles iterate because their final offsets are
    # mirrored in the manifest that the host itself embeds.
    archive_sha: str | None = None
    bundle_plain = b""
    zip_offset = 0
    if effective_embed == "none":
        manifest["bundle"].update({"size": 0, "offset": None,
                                   "sha256_scope": "payload-set; store intentionally omitted"})
        host_payload = b""
        payload_role = "none"
        host_result = get_host_result(host_payload, payload_role) if host_body_original is None else None
        host_body = host_body_original if host_body_original is not None else host_result.data
        host_segments = (host_result.segments if host_result else host_segments)
        layer_bytes = b"".join(item[2] for item in layer_specs)
        output = host_body + layer_bytes
        wrapper_len = 0
        if host_fmt.spec.key == "mp4" and layer_bytes:
            wrapped = host_fmt.wrap_trailing(layer_bytes, HostContext(options=options))
            wrapper_len = len(wrapped) - len(layer_bytes)
            output = host_body + wrapped
        segments = _assemble_segments(host_fmt, host_segments, len(host_body), layer_specs,
                                      len(host_body), wrapper_len, len(layer_bytes), None, host_file is not None)
        return MakeResult(output, host_key, host_fmt.spec.extension, manifest, segments, warnings)

    if effective_embed == "native":
        # First find a stable bundle size; offset is intentionally null because
        # the bundle is inside the host's legal native slot.
        manifest["bundle"].update({"size": None, "offset": None,
                                   "sha256_scope": "payload-set (bootstrap footer hashes normalized ZIP)"})
        # Size/hash live in the bootstrap comment, not the manifest, to avoid a
        # recursive ZIP length field whose compressed size can oscillate.
        if store == "zip":
            bundle_plain, _mb, sha = _zip_bundle_standalone(manifest, payload_files, compression, offset=0)
            archive_sha = sha.hex()
        else:
            bundle_plain = build_raw_bundle(manifest, payload_files)
            archive_sha = sha256_hex(bundle_plain)
        stored_bundle = obfuscate_bundle(bundle_plain, obfuscate_key) if obfuscate_key else bundle_plain
        host_payload = stored_bundle
        payload_role = "bundle"
        host_result = get_host_result(host_payload, payload_role) if host_body_original is None else None
        host_body = host_body_original if host_body_original is not None else host_result.data
        host_segments = host_result.segments if host_result else host_segments
        layer_bytes = b"".join(item[2] for item in layer_specs)
        trailing = layer_bytes
        wrapper_len = 0
        if host_fmt.spec.key == "mp4" and trailing:
            wrapped = host_fmt.wrap_trailing(trailing, HostContext(options=options))
            wrapper_len = len(wrapped) - len(trailing)
            output = host_body + wrapped
        else:
            output = host_body + trailing
        segments = _assemble_segments(host_fmt, host_segments, len(host_body), layer_specs,
                                      len(host_body), wrapper_len, len(layer_bytes), None, host_file is not None)
        return MakeResult(output, host_key, host_fmt.spec.extension, manifest, segments, warnings,
                          bundle_sha256=archive_sha)

    # append mode: embed only the manifest (where the host supports a native
    # slot), then put the ZIP/raw store at EOF for archive-tool interoperability.
    layer_bytes = b"".join(item[2] for item in layer_specs)
    manifest["bundle"].update({"size": None, "offset": 0,
                               "sha256_scope": "payload-set (bootstrap footer hashes normalized ZIP)"})
    for _ in range(24):
        manifest_bytes = canonical_json(manifest)
        if store == "zip":
            bundle_plain, _mb, sha = _zip_bundle_standalone(manifest, payload_files, compression, offset=zip_offset)
            archive_sha = sha.hex()
        else:
            bundle_plain = build_raw_bundle(manifest, payload_files)
            archive_sha = sha256_hex(bundle_plain)
        bundle_size = len(bundle_plain)
        if host_body_original is not None:
            host_body = host_body_original
            host_result = None
        else:
            host_result = get_host_result(manifest_bytes, "manifest")
            host_body = host_result.data
            host_segments = host_result.segments
        prefix_len = len(host_body) + len(layer_bytes)
        # MP4 wraps the *entire* suffix in a free box; the ZIP begins 8 bytes
        # (or a large-size 16-byte header) into that box.
        wrapper_header = 8 if host_fmt.spec.key == "mp4" else 0
        actual_zip_offset = prefix_len + wrapper_header
        if manifest["bundle"].get("offset") == actual_zip_offset:
            zip_offset = actual_zip_offset
            break
        manifest["bundle"]["offset"] = actual_zip_offset
        zip_offset = actual_zip_offset
    else:
        raise CorruptContainerError("append bundle manifest failed to converge")

    # Rebuild once with the final absolute offset in the ZIP bootstrap comment.
    manifest_bytes = canonical_json(manifest)
    if store == "zip":
        bundle_plain, _mb, sha = _zip_bundle_standalone(manifest, payload_files, compression, offset=zip_offset)
        archive_sha = sha.hex()
    else:
        bundle_plain = build_raw_bundle(manifest, payload_files)
        archive_sha = sha256_hex(bundle_plain)
    stored_bundle = obfuscate_bundle(bundle_plain, obfuscate_key) if obfuscate_key else bundle_plain

    if host_body_original is not None:
        host_body = host_body_original
        host_result = None
    else:
        host_result = get_host_result(manifest_bytes, "manifest")
        host_body = host_result.data
        host_segments = host_result.segments
    suffix = layer_bytes + stored_bundle
    if host_fmt.spec.key == "mp4":
        wrapped = host_fmt.wrap_trailing(suffix, HostContext(options=options))
        output = host_body + wrapped
        wrapper_len = len(wrapped) - len(suffix)
        zip_offset = len(host_body) + wrapper_len + len(layer_bytes)
    else:
        output = host_body + suffix
        wrapper_len = 0
        zip_offset = len(host_body) + len(layer_bytes)
    segments = _assemble_segments(host_fmt, host_segments, len(host_body), layer_specs,
                                  len(host_body), wrapper_len, len(layer_bytes),
                                  len(stored_bundle), host_file is not None)
    segments.append(Segment("obfuscated bundle" if obfuscate_key else f"{store.upper()} bundle",
                            zip_offset, len(stored_bundle), "bundle", store,
                            "XOR obfuscation envelope" if obfuscate_key else "lossless payload store"))
    if store == "zip" and not obfuscate_key:
        segments.append(Segment("ZIP comment bootstrap", len(output) - BOOTSTRAP_LEN, BOOTSTRAP_LEN, "footer", "zip"))
    return MakeResult(output, host_key, host_fmt.spec.extension, manifest, segments, warnings,
                      bundle_sha256=archive_sha)


def _assemble_segments(host_fmt: HostFormat, host_segments: Sequence[Segment], host_len: int,
                       layers: Sequence[tuple[str, HostFormat, bytes]], layer_start: int,
                       wrapper_len: int, layer_len: int, bundle_len: int | None,
                       original_host: bool) -> list[Segment]:
    out: list[Segment] = []
    if host_segments:
        out.extend(host_segments)
    else:
        out.append(Segment(host_fmt.spec.label, 0, host_len, "host", host_fmt.spec.key))
    if wrapper_len:
        out.append(Segment("MP4 free box wrapper", host_len, wrapper_len + layer_len + (bundle_len or 0), "host", host_fmt.spec.key,
                           "legal opaque ISO BMFF box"))
        inner_start = host_len + wrapper_len
    else:
        inner_start = host_len
    cursor = inner_start
    for key, fmt, data in layers:
        out.append(Segment(f"{fmt.spec.label} layer", cursor, len(data), "layer", key,
                           "separate format signature; scanner-visible, not offset-zero"))
        cursor += len(data)
    return sorted(out, key=lambda s: (s.offset, s.length))


def _zip_manifest_from_bytes(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]] | None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    with archive:
        try:
            raw = archive.read(MANIFEST_PATH)
        except KeyError:
            return None
        manifest = parse_manifest(raw)
        outputs: dict[str, bytes] = {}
        for record in manifest["files"]:
            name = safe_member_name(str(record.get("name", "")))
            entry = safe_member_name(str(record.get("entry", PAYLOAD_PREFIX + name)))
            try:
                content = archive.read(entry)
            except KeyError as exc:
                raise CorruptContainerError(f"payload member missing: {entry}") from exc
            if len(content) != int(record.get("size", -1)):
                raise IntegrityError(f"size mismatch for {name!r}")
            digest = str(record.get("sha256", ""))
            if digest and sha256_hex(content) != digest:
                raise IntegrityError(f"SHA-256 mismatch for {name!r}")
            if name in outputs:
                raise CorruptContainerError(f"duplicate payload name: {name!r}")
            outputs[name] = content
        return manifest, outputs


def _tar_manifest_from_bytes(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]] | None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except (tarfile.TarError, OSError, ValueError):
        return None
    with archive:
        try:
            member = archive.getmember(MANIFEST_PATH)
            handle = archive.extractfile(member)
            if handle is None:
                return None
            manifest = parse_manifest(handle.read())
        except (KeyError, tarfile.TarError):
            return None
        outputs: dict[str, bytes] = {}
        for record in manifest["files"]:
            name = safe_member_name(str(record.get("name", "")))
            entry = safe_member_name(str(record.get("entry", PAYLOAD_PREFIX + name)))
            try:
                member = archive.getmember(entry)
            except KeyError as exc:
                raise CorruptContainerError(f"payload member missing: {entry}") from exc
            handle = archive.extractfile(member)
            if handle is None:
                raise CorruptContainerError(f"payload member is not a file: {entry}")
            content = handle.read()
            if len(content) != int(record.get("size", -1)):
                raise IntegrityError(f"size mismatch for {name!r}")
            digest = str(record.get("sha256", ""))
            if digest and sha256_hex(content) != digest:
                raise IntegrityError(f"SHA-256 mismatch for {name!r}")
            outputs[name] = content
        return manifest, outputs


def _validate_raw_manifest(manifest: dict[str, Any], outputs: dict[str, bytes]) -> None:
    """Cross-check the raw-store record table against manifest file records."""
    records = manifest.get("files", [])
    if len(records) != len(outputs):
        raise CorruptContainerError("raw record count does not match manifest file count")
    for record in records:
        name = safe_member_name(str(record.get("name", "")))
        content = outputs.get(name)
        if content is None:
            raise CorruptContainerError(f"raw member missing from record table: {name!r}")
        if len(content) != int(record.get("size", -1)):
            raise IntegrityError(f"size mismatch for {name!r}")
        expected = str(record.get("sha256", ""))
        if expected and sha256_hex(content) != expected:
            raise IntegrityError(f"SHA-256 mismatch for {name!r}")


def _obfuscated_at(data: bytes, key: str | None) -> bytes | None:
    # Appended envelopes end at EOF. Native slots may have format terminators
    # after them, so those are extracted through the host parser below.
    start = len(data)
    while True:
        start = data.rfind(OBFUSCATION_MAGIC, 0, start)
        if start < 0:
            return None
        envelope = data[start:]
        if len(envelope) >= 57 and hashlib.sha256(envelope[57:]).digest() == envelope[25:57]:
            return deobfuscate_bundle(envelope, key)



def unpack_bytes(data: bytes, *, key: str | None = None, selected: Sequence[str] | None = None) -> UnpackResult:
    """Extract PolyglotMaker contents without writing to disk."""
    warnings: list[str] = []
    # An outer obfuscation envelope can live after a host or inside an MP4 free box.
    try:
        decrypted = _obfuscated_at(data, key)
    except (IntegrityError, CorruptContainerError, UsageError):
        decrypted = None
        raise
    if decrypted is None:
        # Native/text hosts may return an envelope via the legal embed slot.
        for fmt in REGISTRY.all():
            if not fmt.spec.magic:
                continue
            try:
                embedded = fmt.extract_native(data)
            except (ValueError, struct.error, IndexError):
                embedded = None
            if embedded and embedded.startswith(OBFUSCATION_MAGIC):
                decrypted = deobfuscate_bundle(embedded, key)
                break
    store_data = decrypted if decrypted is not None else data
    store_type = "obfuscated" if decrypted is not None else ""

    # Raw store may be appended to a host file or embedded natively.
    raw_idx = store_data.rfind(RAW_MAGIC)
    while raw_idx >= 0:
        raw = store_data[raw_idx:]
        try:
            manifest, outputs = parse_raw_bundle(raw)
            _validate_raw_manifest(manifest, outputs)
            store_type = "raw" if not store_type else "obfuscated raw"
            return _filter_unpack(manifest, outputs, store_type, selected, warnings)
        except CorruptContainerError:
            # A raw store can live inside a format-native chunk (with legal
            # bytes afterwards) or a payload may itself contain the signature.
            # Retry earlier signatures, then let the host parser extract it.
            raw_idx = store_data.rfind(RAW_MAGIC, 0, raw_idx)

    # ZIP bundle at EOF (bootstrap first, then central-directory search) or a
    # native ZIP/package host at offset zero.
    zip_result = _zip_manifest_from_bytes(store_data)
    if zip_result is not None:
        manifest, outputs = zip_result
        footer = decode_bootstrap(store_data)
        if footer:
            manifest_raw = canonical_json(manifest)
            if hashlib.sha256(manifest_raw).digest() != footer.manifest_sha256:
                # zipfile can normalize JSON bytes; read exact manifest bytes below.
                try:
                    archive = zipfile.ZipFile(io.BytesIO(store_data))
                    exact = archive.read(MANIFEST_PATH)
                    archive.close()
                except Exception:
                    exact = manifest_raw
                if hashlib.sha256(exact).digest() != footer.manifest_sha256:
                    raise IntegrityError("manifest checksum does not match ZIP comment footer")
            location = locate_zip(store_data)
            if location is not None:
                start, length = location
                if _normalized_zip_sha(store_data[start:start + length]) != footer.archive_sha256:
                    raise IntegrityError("ZIP checksum does not match bootstrap footer")
        if not store_type:
            store_type = "zip"
        elif store_type == "obfuscated":
            store_type = "obfuscated zip"
        return _filter_unpack(manifest, outputs, store_type, selected, warnings)

    # Native host extraction fallback. Some hosts store only the manifest in
    # append mode; if that happens, continue looking for its actual bundle.
    for fmt in REGISTRY.all():
        try:
            embedded = fmt.extract_native(data)
        except (ValueError, struct.error, IndexError):
            embedded = None
        if not embedded:
            continue
        if embedded.startswith(OBFUSCATION_MAGIC):
            embedded = deobfuscate_bundle(embedded, key)
            store_type = "obfuscated native"
        if embedded.startswith(RAW_MAGIC):
            manifest, outputs = parse_raw_bundle(embedded)
            _validate_raw_manifest(manifest, outputs)
            return _filter_unpack(manifest, outputs, "native raw", selected, warnings)
        try:
            manifest, outputs, _footer = validate_zip_bundle(embedded)
            return _filter_unpack(manifest, outputs, "native zip", selected, warnings)
        except CorruptContainerError:
            pass
        try:
            manifest = parse_manifest(embedded)
            warnings.append("native host metadata contains the manifest, but its payload store is missing")
            return _filter_unpack(manifest, {}, "manifest only", selected, warnings)
        except CorruptContainerError:
            pass

    # Native tar or tar.gz/bz2/xz package.
    tar_result = _tar_manifest_from_bytes(store_data)
    if tar_result:
        manifest, outputs = tar_result
        return _filter_unpack(manifest, outputs, "tar members", selected, warnings)

    # Do not silently extract unrelated ZIP/TAR archives. They may not be
    # PolyglotMaker files and can contain hostile paths.
    raise NotAPolyglotError("no PolyglotMaker bundle or manifest found in this file")


def _filter_unpack(manifest: dict[str, Any], outputs: dict[str, bytes], source: str,
                   selected: Sequence[str] | None, warnings: list[str]) -> UnpackResult:
    if selected:
        wanted = {safe_member_name(name) for name in selected}
        available = set(outputs)
        missing = wanted - available
        if missing:
            raise UsageError("selected file(s) not found: " + ", ".join(sorted(missing)))
        outputs = {name: blob for name, blob in outputs.items() if name in wanted}
    return UnpackResult(manifest, outputs, source, warnings)


def unpack_file(
    path: str,
    outdir: str,
    *,
    key: str | None = None,
    selected: Sequence[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    with open(path, "rb") as handle:
        data = handle.read()
    result = unpack_bytes(data, key=key, selected=selected)
    records = {r.get("name"): r for r in (result.manifest or {}).get("files", []) if isinstance(r, dict)}
    written = []
    for name, blob in result.files.items():
        target = resolve_output(outdir, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not overwrite:
            target = unique_path(target)
        with open(target, "wb") as handle:
            handle.write(blob)
        record = records.get(name, {})
        try:
            os.chmod(target, int(record.get("mode", 0o644)) & 0o777)
        except OSError:
            pass
        try:
            os.utime(target, (float(record.get("mtime", time.time())),) * 2)
        except (OSError, TypeError, ValueError):
            pass
        written.append({"name": name, "path": target, "size": len(blob), "sha256": sha256_hex(blob)})
    return {"source_store": result.source_store, "manifest": result.manifest,
            "files": written, "warnings": result.warnings}


def inspect_bytes(data: bytes) -> dict[str, Any]:
    detections = detect(data, deep=True)
    detections.extend(detect_text(data))
    # Keep duplicate detections from text+magic out of the display.
    seen, unique = set(), []
    for item in detections:
        key = (item.fmt, item.offset)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    manifest = None
    try:
        unpacked = unpack_bytes(data)
        manifest = unpacked.manifest
    except Exception:
        pass
    host_key = None
    if manifest:
        host_key = manifest.get("container", {}).get("host")
    if not host_key:
        for detection in unique:
            if detection.offset == 0:
                host_key = _host_key_from_detection(detection.fmt, data)
                if host_key:
                    break
    if not host_key and unique:
        for detection in unique:
            host_key = _host_key_from_detection(detection.fmt, data)
            if host_key:
                break
    segments: list[Segment] = []
    if host_key and REGISTRY.has(host_key):
        fmt = REGISTRY.get(host_key)
        try:
            segments = fmt.parse(data)
        except Exception:
            segments = []
    if not segments and data:
        segments = [Segment("input bytes", 0, len(data), "host", host_key)]
    # ZIP/raw bundle segment detection.
    bootstrap = decode_bootstrap(data)
    zip_location = locate_zip(data)
    if bootstrap and zip_location:
        zip_start, zip_length = zip_location
        segments.append(Segment("ZIP bundle", zip_start, zip_length, "bundle", "zip"))
        segments.append(Segment("bootstrap footer", zip_start + zip_length - BOOTSTRAP_LEN, BOOTSTRAP_LEN, "footer", "zip"))
    elif not zip_location:
        raw_pos = data.rfind(RAW_MAGIC)
        if raw_pos >= 0:
            segments.append(Segment("raw bundle", raw_pos, len(data) - raw_pos, "bundle", "raw"))
    if manifest and manifest.get("container", {}).get("embed_mode") == "append":
        layer_records = manifest.get("container", {}).get("layers", [])
        bundle_start = manifest.get("bundle", {}).get("offset")
        if layer_records and isinstance(bundle_start, int):
            cursor = bundle_start - sum(max(0, int(item.get("length", 0))) for item in layer_records)
            for item in layer_records:
                key = str(item.get("format", "layer"))
                length = max(0, int(item.get("length", 0)))
                if length:
                    label = REGISTRY.get(key).spec.label if REGISTRY.has(key) else key
                    segments.append(Segment(f"{label} layer", cursor, length, "layer", key,
                                            "separate format signature; not offset-zero"))
                    cursor += length
    grade = []
    for d in unique:
        if d.offset == 0 and _detection_matches_host(d.fmt, host_key):
            validity = "native"
        elif _detection_in_embed(segments, d.offset):
            validity = "embedded-legal"
        else:
            validity = "overlay"
        grade.append({**d.to_dict(), "validity": validity})
    return {
        "size": len(data),
        "host": host_key,
        "manifest": manifest,
        "detections": grade,
        "segments": [s.to_dict() for s in sorted(segments, key=lambda x: (x.offset, x.length))],
        "byte_map": [{"offset": s.offset, "length": s.length, "end": s.end, "kind": s.kind,
                      "format": s.fmt, "name": s.name, "detail": s.detail} for s in sorted(segments, key=lambda x: (x.offset, x.length))],
    }


def _host_key_from_detection(fmt: str, data: bytes) -> str | None:
    aliases = {
        "mz": "exe", "gif87": "gif", "gif89": "gif", "tiff_le": "tiff", "tiff_be": "tiff",
        "tiff_bigel": "tiff", "mp3_id3": "mp3", "mp3_v2": "mp3", "avi_riff": "avi",
        "zip_empty": "zip", "zip_spanned": "zip", "zip64eocd": "zip", "macho32": "macho",
        "macho64": "macho", "macho32r": "macho", "macho64r": "macho", "macho_fat": "macho",
    }
    if fmt == "mz":
        detected = format_for_path("inspect.bin", data)
        if detected in {"exe", "mz"}:
            fmt = detected
    else:
        fmt = aliases.get(fmt, fmt)
    return REGISTRY.canonical(fmt) if REGISTRY.has(fmt) else None


def _detection_matches_host(fmt: str, host: str | None) -> bool:
    if host is None:
        return REGISTRY.has(fmt)
    if fmt == "mz":
        return host in {"mz", "exe"}
    aliases = {
        "mz": "exe", "gif87": "gif", "gif89": "gif", "tiff_le": "tiff", "tiff_be": "tiff",
        "tiff_bigel": "tiff", "mp3_id3": "mp3", "mp3_v2": "mp3", "avi_riff": "avi",
        "zip_empty": "zip", "zip_spanned": "zip", "zip64eocd": "zip",
    }
    mapped = aliases.get(fmt, fmt)
    if mapped == host:
        return True
    zip_packages = {"docx", "xlsx", "pptx", "epub", "jar", "apk", "odt", "ods", "odp", "cbz"}
    return mapped == "zip" and host in zip_packages


def _detection_in_embed(segments: Sequence[Segment], offset: int) -> bool:
    return any(s.kind == "embed" and s.offset <= offset < s.end for s in segments)


def verify_bytes(data: bytes, *, key: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"valid": False, "checks": [], "errors": [], "warnings": []}
    info = inspect_bytes(data)
    result["checks"].append({"name": "format detection", "ok": bool(info["detections"]),
                             "detail": f"{len(info['detections'])} signatures detected"})
    try:
        unpacked = unpack_bytes(data, key=key)
        result["checks"].append({"name": "PolyglotMaker bundle", "ok": True,
                                 "detail": f"{len(unpacked.files)} payload(s) from {unpacked.source_store}"})
        records = (unpacked.manifest or {}).get("files", [])
        for record in records:
            name = record.get("name", "")
            blob = unpacked.files.get(name)
            okay = blob is not None and len(blob) == int(record.get("size", -1)) and sha256_hex(blob) == record.get("sha256")
            result["checks"].append({"name": f"payload {name}", "ok": bool(okay),
                                     "detail": "SHA-256/size match" if okay else "missing or checksum mismatch"})
        expected_set = (unpacked.manifest or {}).get("bundle", {}).get("sha256")
        if expected_set:
            file_set = [{"name": record.get("name"), "size": record.get("size"), "sha256": record.get("sha256")}
                        for record in records]
            aggregate = hashlib.sha256(canonical_json(file_set)).hexdigest()
            result["checks"].append({"name": "payload-set checksum", "ok": aggregate == expected_set,
                                     "detail": "manifest payload-set digest matches" if aggregate == expected_set else "manifest payload-set digest mismatch"})
        result["manifest"] = unpacked.manifest
        result["warnings"].extend(unpacked.warnings)
        host_key = (unpacked.manifest or {}).get("container", {}).get("host")
        if host_key and REGISTRY.has(host_key):
            host_valid, host_detail = REGISTRY.get(host_key).validate(data)
            result["checks"].append({"name": f"host format {host_key}", "ok": bool(host_valid), "detail": host_detail})
    except Exception as exc:
        result["errors"].append(str(exc))
        result["checks"].append({"name": "PolyglotMaker bundle", "ok": False, "detail": str(exc)})
    result["valid"] = all(check["ok"] for check in result["checks"])
    return result


def make_from_paths(
    paths: Sequence[str],
    *,
    out: str | None = None,
    host: str = "auto",
    host_file_path: str | None = None,
    also: Sequence[str] = (),
    embed_mode: str = "append",
    store: str = "zip",
    compression: str = "deflate",
    obfuscate_key: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> MakeResult:
    """CLI/API convenience wrapper: read input paths, build, and optionally write."""
    files = [input_from_path(path) for path in paths]
    host_file = input_from_path(host_file_path, role="host") if host_file_path else None
    result = make_container(files, host=host, host_file=host_file, also=also,
                            embed_mode=embed_mode, store=store, compression=compression,
                            obfuscate_key=obfuscate_key, options=options)
    if out:
        write_bytes(out, result.data)
        result.output_path = out
    return result
