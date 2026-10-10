"""Audio and video host generators: WAV, AIFF, MIDI, MP3, FLAC, MP4, AVI.

All generators are pure Python and stdlib-only. WAV/AIFF/MIDI/MP4/AVI have
fully structured containers. MP3 emits a standards-shaped ID3v2.4 tag and
silent MPEG-1 Layer III frames. FLAC emits a STREAMINFO + Vorbis-comment tag
and one fixed-subframe silence frame. Real media inputs are always preserved
byte-for-byte as payloads; these generated media hosts are lightweight,
playable demos, not transcoding tools.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from typing import Iterable

from ..core.util import ByteWriter, Segment, chunked, u16be, u16le, u24be, u32be, u32le, u64be
from .base import EMBED_CHUNK, EMBED_COMMENT, EMBED_NONE, HostContext, HostFormat, HostSpec, RenderResult, register
from .images import PLGM_MARKER, riff_chunk


def _synchsafe(value: int) -> bytes:
    return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])


def _id3_frame(frame_id: bytes, body: bytes) -> bytes:
    return frame_id + _synchsafe(len(body)) + b"\x00\x00" + body


def _crc8_flac(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16_flac(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _utf8_uint(value: int) -> bytes:
    """FLAC's UTF-8-like unsigned integer encoding used for frame numbers."""
    if value < 0x80:
        return bytes([value])
    if value < 0x800:
        return bytes([0xC0 | (value >> 6), 0x80 | (value & 0x3F)])
    raise ValueError("frame number too large for minimal FLAC header")


class WavHost(HostFormat):
    spec = HostSpec(
        key="wav", label="WAVE audio", category="audio", extension=".wav",
        aliases=("wave", "audio/wav", "audio/x-wav"), magic=b"RIFF",
        embed=EMBED_CHUNK, tolerates_suffix=True,
        notes="PCM silence plus a private PLGM RIFF chunk; any input file can be a payload",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        rate = int(ctx.opt("sample_rate", 8000))
        duration_ms = int(ctx.opt("duration_ms", 100))
        frames = max(1, min(rate * duration_ms // 1000, rate * 10))
        samples = bytes(frames * 2)  # mono PCM16 silence
        writer = ByteWriter()
        writer.write(b"RIFF", name="RIFF", kind="host", fmt="wav")
        size_at = writer.tell
        writer.write(u32le(0), name="RIFF size (patched)", kind="host", fmt="wav")
        writer.write(b"WAVE", name="WAVE", kind="host", fmt="wav")
        fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
        writer.write(riff_chunk(b"fmt ", fmt), name="fmt (PCM)", kind="host", fmt="wav")
        if ctx.payload:
            for i, block in enumerate(chunked(PLGM_MARKER + ctx.payload, 0x3FFFFFFF)):
                writer.write(riff_chunk(b"PLGM", block), name=f"PLGM payload chunk {i}", kind="embed", fmt="wav")
        writer.write(riff_chunk(b"data", samples), name="data (silence)", kind="host", fmt="wav")
        writer.patch_u32le(size_at, writer.tell - 8)
        detail = f"PCM16 mono {rate} Hz, {frames / rate:.3f} s silence"
        if ctx.payload:
            detail += f", {len(ctx.payload)} B in PLGM chunk"
        return RenderResult(writer.value, writer.segments, bool(ctx.payload), detail)

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return False, "missing RIFF/WAVE header"
        end = struct.unpack_from("<I", data, 4)[0] + 8
        if end > len(data) or end < 12:
            return False, "invalid RIFF/WAVE size"
        pos, has_fmt, has_data = 12, False, False
        while pos + 8 <= end:
            name = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if size > end - pos - 8:
                return False, "truncated RIFF/WAVE chunk"
            has_fmt |= name == b"fmt "
            has_data |= name == b"data"
            pos += 8 + size + (size & 1)
        okay = pos == end and has_fmt and has_data
        return (True, "RIFF/WAVE fmt and data chunks are valid") if okay else (False, "RIFF/WAVE is missing fmt/data chunks")

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return []
        out = [Segment("RIFF/WAVE header", 0, 12, "host", "wav")]
        pos = 12
        end = min(len(data), struct.unpack_from("<I", data, 4)[0] + 8)
        while pos + 8 <= end:
            name = data[pos:pos + 4].decode("latin1")
            size = struct.unpack_from("<I", data, pos + 4)[0]
            total = 8 + size + (size & 1)
            out.append(Segment(name, pos, min(total, end - pos), "embed" if name == "PLGM" else "host", "wav"))
            pos += total
        if end < len(data):
            out.append(Segment("trailing overlay", end, len(data) - end, "bundle", "wav"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None
        parts, pos = [], 12
        end = min(len(data), struct.unpack_from("<I", data, 4)[0] + 8)
        while pos + 8 <= end:
            name = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if name == b"PLGM":
                parts.append(data[pos + 8:pos + 8 + size])
            pos += 8 + size + (size & 1)
        if not parts:
            return None
        blob = b"".join(parts)
        return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else blob


class AiffHost(HostFormat):
    spec = HostSpec(
        key="aiff", label="AIFF audio", category="audio", extension=".aiff",
        aliases=("aif", "audio/aiff", "audio/x-aiff"), magic=b"FORM",
        embed=EMBED_COMMENT, tolerates_suffix=True,
        notes="PCM silence with an ANNO annotation carrying the embedded manifest",
    )

    @staticmethod
    def _extended80(value: int) -> bytes:
        # 44100 = 1.3458 * 2^15, stored as 1.fraction with exponent 16383+15
        exponent = 16383 + (value.bit_length() - 1)
        significand = (value << 63) // (1 << (value.bit_length() - 1))
        return u16be(exponent) + u64be(significand)

    @staticmethod
    def _chunk(kind: bytes, body: bytes) -> bytes:
        # AIFF, unlike RIFF/WAVE, stores chunk sizes in big-endian order.
        result = kind + u32be(len(body)) + body
        return result + (b"\x00" if len(body) & 1 else b"")

    def render(self, ctx: HostContext) -> RenderResult:
        rate = int(ctx.opt("sample_rate", 8000))
        frames = max(1, min(rate // 10, rate * 10))
        comm = u16be(1) + u32be(frames) + u16be(16) + self._extended80(rate)
        writer = ByteWriter()
        writer.write(b"FORM", name="FORM", kind="host", fmt="aiff")
        size_at = writer.tell
        writer.write(u32be(0), name="FORM size (patched)", kind="host", fmt="aiff")
        writer.write(b"AIFF", name="AIFF form type", kind="host", fmt="aiff")
        writer.write(self._chunk(b"COMM", comm), name="COMM", kind="host", fmt="aiff")
        if ctx.payload:
            annotation = PLGM_MARKER + b":" + base64.b64encode(ctx.payload)
            writer.write(self._chunk(b"ANNO", annotation), name="ANNO (PolyglotMaker payload)", kind="embed", fmt="aiff")
        sound = u32be(0) + u32be(0) + bytes(frames * 2)
        writer.write(self._chunk(b"SSND", sound), name="SSND (silent PCM)", kind="host", fmt="aiff")
        writer.patch(size_at, u32be(writer.tell - 8))
        detail = f"PCM16 mono {rate} Hz, {frames} silent samples"
        if ctx.payload:
            detail += f", {len(ctx.payload)} B in ANNO"
        return RenderResult(writer.value, writer.segments, bool(ctx.payload), detail)

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
            return False, "missing FORM/AIFF header"
        end = struct.unpack_from(">I", data, 4)[0] + 8
        if end > len(data) or end < 12:
            return False, "invalid FORM/AIFF size"
        pos, has_comm, has_ssnd = 12, False, False
        while pos + 8 <= end:
            name = data[pos:pos + 4]
            size = struct.unpack_from(">I", data, pos + 4)[0]
            if size > end - pos - 8:
                return False, "truncated AIFF chunk"
            has_comm |= name == b"COMM"
            has_ssnd |= name == b"SSND"
            pos += 8 + size + (size & 1)
        okay = pos == end and has_comm and has_ssnd
        return (True, "AIFF COMM and SSND chunks are valid") if okay else (False, "AIFF is missing COMM/SSND chunks")

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
            return []
        out, pos = [Segment("FORM/AIFF header", 0, 12, "host", "aiff")], 12
        end = min(len(data), struct.unpack_from(">I", data, 4)[0] + 8)
        while pos + 8 <= end:
            name = data[pos:pos + 4].decode("latin1")
            size = struct.unpack_from(">I", data, pos + 4)[0]
            total = 8 + size + (size & 1)
            out.append(Segment(name, pos, total, "embed" if name == "ANNO" and PLGM_MARKER in data[pos:pos + total] else "host", "aiff"))
            pos += total
        if end < len(data):
            out.append(Segment("trailing overlay", end, len(data) - end, "bundle", "aiff"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if len(data) < 12 or data[:4] != b"FORM":
            return None
        pos, end = 12, min(len(data), struct.unpack_from(">I", data, 4)[0] + 8)
        while pos + 8 <= end:
            name = data[pos:pos + 4]
            size = struct.unpack_from(">I", data, pos + 4)[0]
            if name == b"ANNO":
                blob = data[pos + 8:pos + 8 + size]
                prefix = PLGM_MARKER + b":"
                if blob.startswith(prefix):
                    try:
                        return base64.b64decode(blob[len(prefix):], validate=True)
                    except ValueError:
                        return None
            pos += 8 + size + (size & 1)
        return None


class MidiHost(HostFormat):
    spec = HostSpec(
        key="midi", label="Standard MIDI file", category="audio", extension=".mid",
        aliases=("mid", "smf", "audio/midi", "audio/x-midi"), magic=b"MThd",
        embed=EMBED_COMMENT, tolerates_suffix=False, strict=True,
        notes="format-0 MIDI sequence; embedded payload is a legal sequencer-specific meta event",
    )

    @staticmethod
    def _vlq(value: int) -> bytes:
        value = max(0, value)
        out = [value & 0x7F]
        value >>= 7
        while value:
            out.insert(0, 0x80 | (value & 0x7F))
            value >>= 7
        return bytes(out)

    def render(self, ctx: HostContext) -> RenderResult:
        track = bytearray()
        track += b"\x00\xff\x51\x03\x07\xa1\x20"  # 120 BPM
        if ctx.payload:
            body = PLGM_MARKER + u32be(len(ctx.payload)) + ctx.payload
            track += b"\x00\xff\x7f" + self._vlq(len(body)) + body
        track += b"\x00\xff\x2f\x00"
        data = b"MThd" + u32be(6) + u16be(0) + u16be(1) + u16be(96) + b"MTrk" + u32be(len(track)) + bytes(track)
        return RenderResult(data, [Segment("MThd", 0, 14, "host", "midi"),
                                   Segment("MTrk", 14, len(data) - 14, "embed" if ctx.payload else "host", "midi")],
                            bool(ctx.payload), "format 0, one tempo track")

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 14 or data[:4] != b"MThd":
            return False, "missing MIDI MThd header"
        header_size = struct.unpack_from(">I", data, 4)[0]
        if header_size < 6 or 8 + header_size > len(data):
            return False, "invalid MIDI header length"
        tracks = struct.unpack_from(">H", data, 10)[0]
        pos, seen = 8 + header_size, 0
        while pos + 8 <= len(data) and data[pos:pos + 4] == b"MTrk":
            size = struct.unpack_from(">I", data, pos + 4)[0]
            if size > len(data) - pos - 8:
                return False, "truncated MIDI track"
            pos += 8 + size
            seen += 1
        okay = seen == tracks and pos == len(data)
        return (True, "MIDI header and track lengths are valid") if okay else (False, "MIDI track count/length mismatch")

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 14 or data[:4] != b"MThd":
            return []
        header_len = struct.unpack_from(">I", data, 4)[0]
        pos = 8 + header_len
        out = [Segment("MThd", 0, pos, "host", "midi")]
        while pos + 8 <= len(data) and data[pos:pos + 4] == b"MTrk":
            size = struct.unpack_from(">I", data, pos + 4)[0]
            out.append(Segment("MTrk", pos, 8 + size, "embed" if PLGM_MARKER in data[pos + 8:pos + 8 + size] else "host", "midi"))
            pos += 8 + size
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"MThd"):
            return None
        idx = data.find(PLGM_MARKER)
        if idx < 0:
            return None
        # Event payload ends at the next End-of-Track event; strip the marker.
        start = idx + len(PLGM_MARKER)
        if start + 4 > len(data):
            return None
        length = struct.unpack_from(">I", data, start)[0]
        return data[start + 4:start + 4 + length] if length <= len(data) - start - 4 else None


class Mp3Host(HostFormat):
    spec = HostSpec(
        key="mp3", label="MP3 audio", category="audio", extension=".mp3",
        aliases=("mpeg", "mpeg3", "audio/mpeg", "audio/mp3"), magic=b"ID3",
        embed=EMBED_CHUNK, tolerates_suffix=True,
        notes="ID3v2.4 PRIV frame carries the manifest; appends silent MPEG Layer III frames",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        owner = b"PolyglotMaker\x00"
        body = owner + PLGM_MARKER + ctx.payload if ctx.payload else owner + b""
        frames = _id3_frame(b"PRIV", body) if ctx.payload else _id3_frame(b"TIT2", b"\x03Silent polyglot host")
        tag = b"ID3\x04\x00\x00" + _synchsafe(len(frames)) + frames
        # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, stereo. Side info and main data
        # are all zero; this is a standards-shaped silent frame (417 bytes).
        frame = bytes.fromhex("fffb9000") + bytes(413)
        data = tag + frame * 4
        segments = [Segment("ID3v2.4 tag", 0, len(tag), "embed" if ctx.payload else "meta", "mp3"),
                    Segment("MPEG-1 Layer III frames", len(tag), len(frame) * 4, "host", "mp3")]
        return RenderResult(data, segments, bool(ctx.payload), "4 silent MPEG-1 Layer III frames at 128 kb/s")

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not data.startswith(b"ID3") or len(data) < 10:
            return False, "missing ID3v2 header"
        tag_size = sum((data[6 + i] & 0x7F) << (7 * (3 - i)) for i in range(4))
        audio = 10 + tag_size
        if audio + 4 > len(data) or data[audio] != 0xFF or data[audio + 1] & 0xE0 != 0xE0:
            return False, "missing MPEG audio frame after ID3 tag"
        return True, "ID3 tag and MPEG frame sync are present"

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"ID3") or len(data) < 10:
            return []
        size = sum((data[6 + i] & 0x7F) << (7 * (3 - i)) for i in range(4))
        tag_end = min(len(data), 10 + size)
        out = [Segment("ID3v2 tag", 0, tag_end, "embed" if PLGM_MARKER in data[:tag_end] else "meta", "mp3")]
        if tag_end < len(data):
            out.append(Segment("MPEG audio data / trailing overlay", tag_end, len(data) - tag_end, "host", "mp3"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"ID3") or len(data) < 10:
            return None
        idx = data.find(PLGM_MARKER)
        if idx < 0:
            return None
        start = idx + len(PLGM_MARKER)
        # The payload is bounded by the PRIV frame size; our tag ends at the first MPEG frame.
        tag_size = sum((data[6 + i] & 0x7F) << (7 * (3 - i)) for i in range(4))
        return data[start:min(10 + tag_size, len(data))]


class FlacHost(HostFormat):
    spec = HostSpec(
        key="flac", label="FLAC audio", category="audio", extension=".flac",
        aliases=("audio/flac", "audio/x-flac"), magic=b"fLaC",
        embed=EMBED_COMMENT, tolerates_suffix=True,
        notes="Vorbis-comment field carries a base64 manifest; includes a constant-silence FLAC frame",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        sample_rate, channels, bps, block_size = 44100, 1, 16, 192
        # STREAMINFO: min/max block, min/max frame size (unknown), and packed stream parameters.
        packed = (sample_rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | block_size
        streaminfo = u16be(block_size) + u16be(block_size) + b"\x00" * 6 + u64be(packed) + b"\x00" * 16
        metadata = bytearray()
        metadata += bytes([0]) + u24be(34) + streaminfo
        if ctx.payload:
            vendor = b"PolyglotMaker"
            comment = b"POLYGLOTMAKER=" + base64.b64encode(PLGM_MARKER + ctx.payload)
            body = u32le(len(vendor)) + vendor + u32le(1) + u32le(len(comment)) + comment
            metadata += bytes([0x84]) + u24be(len(body)) + body  # last block, VORBIS_COMMENT
        else:
            metadata[0] = 0x80  # STREAMINFO is last metadata block
        # Fixed-block FLAC frame: one mono constant subframe containing 192 zero samples.
        header = b"\xff\xf8\x10\x00" + _utf8_uint(0)
        header += bytes([_crc8_flac(header)])
        subframe = b"\x02\x00\x00"  # constant subframe, signed 16-bit sample 0
        frame_no_crc = header + subframe
        frame = frame_no_crc + u16be(_crc16_flac(frame_no_crc))
        data = b"fLaC" + bytes(metadata) + frame
        segments = [Segment("fLaC marker", 0, 4, "host", "flac"),
                    Segment("metadata blocks", 4, len(metadata), "embed" if ctx.payload else "meta", "flac"),
                    Segment("audio frame", 4 + len(metadata), len(frame), "host", "flac")]
        return RenderResult(data, segments, bool(ctx.payload), "mono 44.1 kHz FLAC silence frame")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"fLaC"):
            return []
        pos, out = 4, [Segment("fLaC marker", 0, 4, "host", "flac")]
        last = False
        while not last and pos + 4 <= len(data):
            header = data[pos]
            last = bool(header & 0x80)
            block_type = header & 0x7F
            length = int.from_bytes(data[pos + 1:pos + 4], "big")
            name = {0: "STREAMINFO", 1: "PADDING", 4: "VORBIS_COMMENT", 6: "PICTURE"}.get(block_type, f"metadata {block_type}")
            out.append(Segment(name, pos, 4 + length, "embed" if block_type == 4 and PLGM_MARKER in data[pos:pos + 4 + length] else "meta", "flac"))
            pos += 4 + length
        if pos < len(data):
            out.append(Segment("audio frames / trailing overlay", pos, len(data) - pos, "host", "flac"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"fLaC"):
            return None
        pos, last = 4, False
        while not last and pos + 4 <= len(data):
            header = data[pos]
            last = bool(header & 0x80)
            block_type = header & 0x7F
            length = int.from_bytes(data[pos + 1:pos + 4], "big")
            body = data[pos + 4:pos + 4 + length]
            if block_type == 4:
                marker = b"POLYGLOTMAKER="
                idx = body.find(marker)
                if idx >= 0:
                    value = body[idx + len(marker):].split(b"\x00", 1)[0]
                    try:
                        raw = base64.b64decode(value, validate=True)
                        return raw[len(PLGM_MARKER):] if raw.startswith(PLGM_MARKER) else raw
                    except ValueError:
                        pass
            pos += 4 + length
        return None


class Mp4Host(HostFormat):
    spec = HostSpec(
        key="mp4", label="MP4 / ISO base media", category="video", extension=".mp4",
        aliases=("m4v", "m4a", "mov", "qt", "video/mp4", "quicktime"), magic=None,
        embed=EMBED_CHUNK, tolerates_suffix=True,
        notes="valid ISO BMFF ftyp+moov host; appended content is wrapped in a 'free' box",
    )

    @staticmethod
    def box(kind: bytes, payload: bytes) -> bytes:
        if len(payload) + 8 <= 0xFFFFFFFF:
            return u32be(len(payload) + 8) + kind + payload
        return u32be(1) + kind + u64be(len(payload) + 16) + payload

    def render(self, ctx: HostContext) -> RenderResult:
        ftyp = self.box(b"ftyp", b"isom" + u32be(0x200) + b"isomiso2mp41")
        # ISO/IEC 14496-12 movie header (version 0, duration zero, no tracks).
        mvhd = (u32be(0) + u32be(0) + u32be(0) + u32be(1000) + u32be(0)
                + u32be(0x00010000) + u16be(0x0100) + b"\x00" * 10
                + u32be(0x00010000) + u32be(0) + u32be(0)
                + u32be(0) + u32be(0x00010000) + u32be(0)
                + u32be(0) + u32be(0) + u32be(0x40000000)
                + b"\x00" * 24 + u32be(1))
        moov = self.box(b"moov", self.box(b"mvhd", mvhd))
        chunks = [ftyp, moov]
        segments = [Segment("ftyp", 0, len(ftyp), "host", "mp4"),
                    Segment("moov/mvhd", len(ftyp), len(moov), "host", "mp4")]
        if ctx.payload:
            # Legal opaque ISO BMFF box for our native metadata or whole bundle.
            embedded = self.box(b"free", PLGM_MARKER + ctx.payload)
            segments.append(Segment("free (PolyglotMaker payload)", sum(map(len, chunks)), len(embedded), "embed", "mp4"))
            chunks.append(embedded)
        data = b"".join(chunks)
        return RenderResult(data, segments, bool(ctx.payload),
                            "ISO base media file with an empty movie header" + (", private free box" if ctx.payload else ""))

    def wrap_trailing(self, trailing: bytes, ctx: HostContext) -> bytes:
        if not trailing:
            return b""
        # `free` is a legal top-level ISO BMFF box. ZIP readers treat the bytes
        # inside it as an arbitrary prefix, and native MP4 readers skip it.
        return self.box(b"free", trailing)

    def validate(self, data: bytes) -> tuple[bool, str]:
        pos, kinds = 0, []
        while pos + 8 <= len(data):
            size = struct.unpack_from(">I", data, pos)[0]
            kind = data[pos + 4:pos + 8]
            header = 8
            if size == 1:
                if pos + 16 > len(data):
                    return False, "truncated large-size ISO BMFF box"
                size = struct.unpack_from(">Q", data, pos + 8)[0]
                header = 16
            elif size == 0:
                size = len(data) - pos
            if size < header or size > len(data) - pos:
                return False, "invalid ISO BMFF box size"
            kinds.append(kind)
            pos += size
        okay = pos == len(data) and bool(kinds) and kinds[0] == b"ftyp" and b"moov" in kinds
        return (True, "ISO BMFF ftyp/moov boxes are valid") if okay else (False, "missing or malformed ISO BMFF ftyp/moov")

    def parse(self, data: bytes) -> list[Segment]:
        out, pos = [], 0
        while pos + 8 <= len(data):
            size = struct.unpack_from(">I", data, pos)[0]
            kind = data[pos + 4:pos + 8]
            header = 8
            if size == 1 and pos + 16 <= len(data):
                size = struct.unpack_from(">Q", data, pos + 8)[0]
                header = 16
            elif size == 0:
                size = len(data) - pos
            if size < header or pos + size > len(data):
                break
            name = kind.decode("latin1")
            out.append(Segment(name, pos, size, "embed" if kind == b"free" and PLGM_MARKER in data[pos + header:pos + size] else "host", "mp4"))
            pos += size
        if pos < len(data):
            out.append(Segment("trailing data", pos, len(data) - pos, "bundle", "mp4"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        pos = 0
        while pos + 8 <= len(data):
            size = struct.unpack_from(">I", data, pos)[0]
            header = 8
            if size == 1 and pos + 16 <= len(data):
                size = struct.unpack_from(">Q", data, pos + 8)[0]
                header = 16
            elif size == 0:
                size = len(data) - pos
            if size < header or pos + size > len(data):
                break
            if data[pos + 4:pos + 8] == b"free":
                body = data[pos + header:pos + size]
                idx = body.find(PLGM_MARKER)
                if idx >= 0:
                    return body[idx + len(PLGM_MARKER):]
            pos += size
        return None


class AviHost(HostFormat):
    spec = HostSpec(
        key="avi", label="AVI video", category="video", extension=".avi",
        aliases=("audio/video-interleaved", "video/x-msvideo"), magic=b"RIFF",
        embed=EMBED_CHUNK, tolerates_suffix=True,
        notes="RIFF AVI with a single uncompressed 1x1 video frame and optional PLGM metadata list",
    )

    @staticmethod
    def _chunk(name: bytes, body: bytes) -> bytes:
        out = name + u32le(len(body)) + body
        return out + (b"\x00" if len(body) & 1 else b"")

    @classmethod
    def _list(cls, kind: bytes, body: bytes) -> bytes:
        return b"LIST" + u32le(4 + len(body)) + kind + body

    def render(self, ctx: HostContext) -> RenderResult:
        avih = struct.pack("<14I", 33333, 0, 0, 0, 1, 0, 1, 4, 1, 1, 0, 0, 0, 0)
        avih_chunk = self._chunk(b"avih", avih)
        # AVIStreamHeader (56 bytes): DIB video, 30 fps, one frame.
        strh = b"vids" + b"DIB " + struct.pack("<IHHIIIIIIII", 0, 0, 0, 0, 1, 30, 0, 1, 4, 0xFFFFFFFF, 0) + struct.pack("<hhhh", 0, 0, 1, 1)
        if len(strh) != 56:
            raise AssertionError(f"AVI stream header must be 56 bytes, got {len(strh)}")
        strh_chunk = self._chunk(b"strh", strh)
        strf = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
        strl = self._list(b"strl", strh_chunk + self._chunk(b"strf", strf))
        hdrl = self._list(b"hdrl", avih_chunk + strl)
        extras = self._chunk(b"PLGM", PLGM_MARKER + ctx.payload) if ctx.payload else b""
        # A single padded 24-bit BGR black pixel.
        movi = self._list(b"movi", self._chunk(b"00db", b"\x00\x00\x00\x00"))
        data_body = b"AVI " + hdrl + extras + movi
        data = b"RIFF" + u32le(len(data_body)) + data_body
        segments = [Segment("RIFF/AVI header", 0, 12, "host", "avi"),
                    Segment("LIST hdrl", 12, len(hdrl), "host", "avi")]
        if extras:
            segments.append(Segment("PLGM metadata chunk", 12 + len(hdrl), len(extras), "embed", "avi"))
        segments.append(Segment("LIST movi", len(data) - len(movi), len(movi), "host", "avi"))
        return RenderResult(data, segments, bool(ctx.payload), "uncompressed DIB video, 1x1 black frame")

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"AVI ":
            return False, "missing RIFF/AVI header"
        declared = struct.unpack_from("<I", data, 4)[0] + 8
        if declared > len(data) or declared < 12:
            return False, "invalid RIFF/AVI size"
        return True, "RIFF/AVI header and declared length are valid"

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"AVI ":
            return []
        end = min(len(data), struct.unpack_from("<I", data, 4)[0] + 8)
        out = [Segment("RIFF/AVI header", 0, 12, "host", "avi")]
        pos = 12
        while pos + 8 <= end:
            name = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            total = 8 + size + (size & 1)
            out.append(Segment(name.decode("latin1"), pos, total, "embed" if name == b"PLGM" else "host", "avi"))
            pos += total
        if end < len(data):
            out.append(Segment("trailing overlay", end, len(data) - end, "bundle", "avi"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        idx = data.find(b"PLGM" + PLGM_MARKER)
        if idx < 0 or idx + 8 > len(data):
            return None
        size = struct.unpack_from("<I", data, idx + 4)[0]
        blob = data[idx + 8:idx + 8 + size]
        return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else None


for _format in (WavHost(), AiffHost(), MidiHost(), Mp3Host(), FlacHost(), Mp4Host(), AviHost()):
    register(_format)
