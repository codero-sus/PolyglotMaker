"""Image hosts: PNG, JPEG, GIF, BMP, WebP, TIFF, ICO, TGA.

Every generator here is hand-written against the format specification using
only the standard library, and every one of them is checked in the test-suite
against independent parsers (Pillow / ImageMagick) when those are installed.

Payload embedding strategy per format
-------------------------------------
PNG     private ancillary chunk ``plGm`` (safe-to-copy) before IDAT  -> *legal*
JPEG    chained ``COM`` comment segments after APP0                  -> *legal*
GIF     chained comment extensions (0x21 0xFE) before the image      -> *legal*
WebP    unknown RIFF chunk ``PLGM`` (RIFF allows unknown chunks)     -> *legal*
TIFF    private tag 0x83d0 (SubIFD-style, ignored by readers)        -> *legal*
ICO     extra ICONDIRENTRY-less trailing resource                    -> tolerated
BMP     trailing bytes after pixel data                              -> tolerated
TGA     trailing bytes after pixel data                              -> tolerated
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable

from ..core.util import ByteWriter, Segment, u16be, u16le, u32be, u32le, chunked
from .base import EMBED_CHUNK, EMBED_COMMENT, EMBED_NONE, HostContext, HostFormat, HostSpec, RenderResult, register

PLGM_MARKER = b"PLGMK1"          # prefix used inside every native embed slot
MAX_JPEG_SEGMENT = 0xFFFF - 2
MAX_GIF_SUBBLOCK = 255


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


class BitWriterMSB:
    """JPEG-style bit writer (most significant bit first)."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def finish(self, fill: int = 1) -> bytes:
        bits = list(self._bits)
        while len(bits) % 8:
            bits.append(fill)
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for bit in bits[i : i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        # byte stuffing: 0xFF must be followed by 0x00
        stuffed = bytearray()
        for byte in out:
            stuffed.append(byte)
            if byte == 0xFF:
                stuffed.append(0x00)
        return bytes(stuffed)


class BitWriterLSB:
    """RIFF/VP8L-style bit writer (least significant bit first)."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, count: int) -> None:
        for i in range(count):
            self._bits.append((value >> i) & 1)

    def finish(self) -> bytes:
        bits = list(self._bits)
        while len(bits) % 8:
            bits.append(0)
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for j, bit in enumerate(bits[i : i + 8]):
                byte |= bit << j
            out.append(byte)
        return bytes(out)


def riff_chunk(fourcc: bytes, body: bytes) -> bytes:
    """A RIFF chunk, padded to an even length (pad byte excluded from size)."""
    out = fourcc + u32le(len(body)) + body
    if len(body) % 2:
        out += b"\x00"
    return out


def split_payload(payload: bytes, max_size: int) -> Iterable[bytes]:
    yield from chunked(payload, max_size)


def gif_solid_lzw(count: int) -> bytes:
    """Encode a solid palette-index-0 GIF row using clear/literal pairs.

    A clear code before every literal keeps the LZW dictionary at its initial
    size, so every code is exactly 3 bits and no code-size transition is needed.
    """
    codes = [4, 0] * max(1, count) + [5]  # minimum code size 2: clear=4, end=5
    output = bytearray()
    accumulator = bit_count = 0
    for code in codes:
        accumulator |= code << bit_count
        bit_count += 3
        while bit_count >= 8:
            output.append(accumulator & 0xFF)
            accumulator >>= 8
            bit_count -= 8
    if bit_count:
        output.append(accumulator & 0xFF)
    return bytes(output)


# ==========================================================================
# PNG
# ==========================================================================


class PngHost(HostFormat):
    spec = HostSpec(
        key="png",
        label="PNG image",
        category="image",
        extension=".png",
        aliases=("apng", "image/png"),
        magic=b"\x89PNG\r\n\x1a\n",
        embed=EMBED_CHUNK,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="payload lives in private chunk 'plGm'; decoders ignore data after IEND",
    )

    CHUNK = b"plGm"

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(8))
        height = max(1, ctx.height(8))
        writer = ByteWriter()
        writer.write(b"\x89PNG\r\n\x1a\n", name="PNG signature", kind="host")

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour RGB
        self._chunk(writer, b"IHDR", ihdr, "IHDR")

        title = str(ctx.meta("title", "") or "")
        if title:
            self._chunk(writer, b"tEXt", b"Title\x00" + title.encode("utf-8", "replace"), "tEXt (title)", kind="meta")

        embedded = False
        if ctx.payload:
            for index, block in enumerate(split_payload(ctx.payload, 0x40000000)):
                self._chunk(writer, self.CHUNK, PLGM_MARKER + block if index == 0 else block,
                            f"{self.CHUNK.decode()} payload chunk {index}", kind="embed")
            embedded = True

        raw = bytearray()
        for y in range(height):
            raw.append(0)  # filter type 0
            for x in range(width):
                raw += bytes(((x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 128))
        self._chunk(writer, b"IDAT", zlib.compress(bytes(raw), 9), "IDAT (pixel data)")
        self._chunk(writer, b"IEND", b"", "IEND")

        detail = f"{width}x{height} RGB8"
        if embedded:
            detail += f", {len(ctx.payload)} B in private chunk"
        return RenderResult(data=writer.value, segments=writer.segments, embedded=embedded, detail=detail)

    @staticmethod
    def _chunk(writer: ByteWriter, ctype: bytes, data: bytes, name: str, kind: str = "host") -> None:
        body = ctype + data
        writer.write(u32be(len(data)) + body + u32be(zlib.crc32(body)), name=name, kind=kind, fmt="png")

    def parse(self, data: bytes) -> list[Segment]:
        out: list[Segment] = []
        if not data.startswith(self.spec.magic):
            return out
        out.append(Segment("PNG signature", 0, 8, "host", "png"))
        pos = 8
        while pos + 12 <= len(data):
            length = struct.unpack_from(">I", data, pos)[0]
            ctype = data[pos + 4 : pos + 8]
            if length > len(data) - pos - 8:
                break
            kind = "embed" if ctype == self.CHUNK else ("meta" if ctype in (b"tEXt", b"iTXt", b"zTXt") else "host")
            out.append(Segment(ctype.decode("latin1"), pos, 12 + length, kind, "png"))
            if ctype == b"IEND":
                pos += 12 + length
                break
            pos += 12 + length
        if pos < len(data):
            out.append(Segment("trailing overlay", pos, len(data) - pos, "bundle", "png",
                               "ignored by PNG decoders"))
        return out

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not data.startswith(self.spec.magic):
            return False, "missing PNG signature"
        pos, seen_ihdr, seen_idat, seen_iend = 8, False, False, False
        while pos + 12 <= len(data):
            length = struct.unpack_from(">I", data, pos)[0]
            if length > len(data) - pos - 12:
                return False, "truncated PNG chunk"
            ctype = data[pos + 4:pos + 8]
            body = data[pos + 4:pos + 8 + length]
            checksum = struct.unpack_from(">I", data, pos + 8 + length)[0]
            if zlib.crc32(body) & 0xFFFFFFFF != checksum:
                return False, f"PNG CRC mismatch in {ctype!r}"
            if not seen_ihdr:
                if ctype != b"IHDR" or length != 13:
                    return False, "PNG must begin with a 13-byte IHDR"
                seen_ihdr = True
            if ctype == b"IDAT":
                seen_idat = True
            if ctype == b"IEND":
                seen_iend = length == 0
                break
            pos += 12 + length
        return (True, "PNG signature, CRCs, IDAT, and IEND are valid") if seen_ihdr and seen_idat and seen_iend else (False, "PNG is missing IHDR/IDAT/IEND")

    def extract_native(self, data: bytes) -> bytes | None:
        parts: list[bytes] = []
        pos = 8
        if not data.startswith(self.spec.magic):
            return None
        while pos + 12 <= len(data):
            length = struct.unpack_from(">I", data, pos)[0]
            ctype = data[pos + 4 : pos + 8]
            if length > len(data) - pos - 8:
                break
            if ctype == self.CHUNK:
                parts.append(data[pos + 8 : pos + 8 + length])
            if ctype == b"IEND":
                break
            pos += 12 + length
        if not parts:
            return None
        blob = b"".join(parts)
        return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else blob


# ==========================================================================
# JPEG
# ==========================================================================


class JpegHost(HostFormat):
    spec = HostSpec(
        key="jpeg",
        label="JPEG image",
        category="image",
        extension=".jpg",
        aliases=("jpg", "jpe", "jfif", "image/jpeg"),
        magic=b"\xff\xd8\xff",
        embed=EMBED_COMMENT,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="payload split across chained COM segments; data after EOI is ignored",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(8))
        height = max(1, ctx.height(8))
        writer = ByteWriter()
        writer.write(b"\xff\xd8", name="SOI", kind="host", fmt="jpeg")
        app0 = b"JFIF\x00" + bytes([1, 2, 0]) + u16be(1) + u16be(1) + bytes([0, 0, 0])
        self._seg(writer, b"\xff\xe0", app0, "APP0 (JFIF)")

        embedded = False
        if ctx.payload:
            blocks = list(split_payload(PLGM_MARKER + ctx.payload, MAX_JPEG_SEGMENT))
            for index, block in enumerate(blocks):
                self._seg(writer, b"\xff\xfe", block, f"COM payload {index}", kind="embed")
            embedded = True

        dqt = bytes([0x00]) + bytes([16] * 64)
        self._seg(writer, b"\xff\xdb", dqt, "DQT (quant table)")
        sof = bytes([8]) + struct.pack(">HH", height, width) + bytes([1, 1, 0x11, 0])
        self._seg(writer, b"\xff\xc0", sof, "SOF0 (baseline 1 component)")

        # Huffman tables with a single 1-bit code each: DC symbol 0, AC EOB.
        counts = bytes([1]) + bytes(15)
        self._seg(writer, b"\xff\xc4", bytes([0x00]) + counts + bytes([0x00]), "DHT (DC)")
        self._seg(writer, b"\xff\xc4", bytes([0x10]) + counts + bytes([0x00]), "DHT (AC)")

        self._seg(writer, b"\xff\xda", bytes([1, 1, 0x00, 0, 0x3F, 0]), "SOS")
        bits = BitWriterMSB()
        bits.write(0b0, 1)      # DC category 0
        bits.write(0b0, 1)      # AC EOB
        for _ in range((width * height + 63) // 64 - 1):
            bits.write(0b0, 1)
            bits.write(0b0, 1)
        writer.write(bits.finish(1), name="entropy data", kind="host", fmt="jpeg")
        writer.write(b"\xff\xd9", name="EOI", kind="host", fmt="jpeg")

        detail = f"{width}x{height} grayscale baseline"
        if embedded:
            detail += f", {len(ctx.payload)} B in {len(blocks)} COM segment(s)"
        return RenderResult(data=writer.value, segments=writer.segments, embedded=embedded, detail=detail)

    @staticmethod
    def _seg(writer: ByteWriter, marker: bytes, body: bytes, name: str, kind: str = "host") -> None:
        writer.write(marker + struct.pack(">H", len(body) + 2) + body, name=name, kind=kind, fmt="jpeg")

    def parse(self, data: bytes) -> list[Segment]:
        out: list[Segment] = []
        if not data.startswith(b"\xff\xd8"):
            return out
        out.append(Segment("SOI", 0, 2, "host", "jpeg"))
        pos = 2
        while pos + 1 < len(data):
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            if marker == 0xD9:  # EOI
                out.append(Segment("EOI", pos, 2, "host", "jpeg"))
                pos += 2
                break
            if pos + 4 > len(data):
                break
            length = struct.unpack_from(">H", data, pos + 2)[0]
            name = {0xE0: "APP0", 0xE1: "APP1 (EXIF)", 0xFE: "COM", 0xDB: "DQT",
                    0xC0: "SOF0", 0xC4: "DHT", 0xDA: "SOS", 0xDD: "DRI"}.get(marker, f"segment FF{marker:02X}")
            kind = "embed" if marker == 0xFE and PLGM_MARKER in data[pos + 4 : pos + 4 + length] else "host"
            out.append(Segment(name, pos, 2 + length, kind, "jpeg"))
            pos += 2 + length
            if marker == 0xDA:  # scan data runs to the next marker
                end = data.find(b"\xff\xd9", pos)
                end = len(data) if end < 0 else end + 2
                out.append(Segment("entropy scan data", pos, end - pos, "host", "jpeg"))
                pos = end
                break
        if pos < len(data):
            out.append(Segment("trailing overlay", pos, len(data) - pos, "bundle", "jpeg",
                               "ignored by JPEG decoders"))
        return out

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not data.startswith(b"\xff\xd8"):
            return False, "missing JPEG SOI marker"
        if data.rfind(b"\xff\xd9") < 2:
            return False, "missing JPEG EOI marker"
        return True, "JPEG SOI and EOI markers present"

    def extract_native(self, data: bytes) -> bytes | None:
        parts: list[bytes] = []
        started = False
        pos = 2
        if not data.startswith(b"\xff\xd8"):
            return None
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            length = struct.unpack_from(">H", data, pos + 2)[0]
            if marker == 0xFE:
                body = data[pos + 4 : pos + 2 + length]
                if not started:
                    if body.startswith(PLGM_MARKER):
                        started = True
                        parts.append(body[len(PLGM_MARKER):])
                    else:
                        parts = []
                else:
                    parts.append(body)
            elif started:
                break
            pos += 2 + length
        return b"".join(parts) if started else None


# ==========================================================================
# GIF
# ==========================================================================


class GifHost(HostFormat):
    spec = HostSpec(
        key="gif",
        label="GIF image",
        category="image",
        extension=".gif",
        aliases=("gif89a", "gif87a", "image/gif"),
        magic=b"GIF8",
        embed=EMBED_COMMENT,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="payload in comment extensions; data after the 0x3B trailer is ignored",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(8))
        height = max(1, ctx.height(8))
        writer = ByteWriter()
        writer.write(b"GIF89a", name="GIF header", kind="host", fmt="gif")
        writer.write(struct.pack("<HH", width, height) + bytes([0x80, 0, 0]),
                     name="logical screen descriptor", kind="host", fmt="gif")
        writer.write(b"\x00\x00\x00\xff\xff\xff", name="global colour table", kind="host", fmt="gif")

        embedded = False
        if ctx.payload:
            body = bytearray()
            for sub in split_payload(PLGM_MARKER + ctx.payload, MAX_GIF_SUBBLOCK):
                body += bytes([len(sub)]) + sub
            body += b"\x00"
            writer.write(b"\x21\xfe" + bytes(body), name="comment extension (payload)", kind="embed", fmt="gif")
            embedded = True

        writer.write(b"\x2c" + struct.pack("<HHHH", 0, 0, width, height) + bytes([0x00]),
                     name="image descriptor", kind="host", fmt="gif")
        compressed = gif_solid_lzw(width * height)
        writer.write(b"\x02", name="LZW minimum code size", kind="host", fmt="gif")
        for block_index, block in enumerate(split_payload(compressed, MAX_GIF_SUBBLOCK)):
            writer.write(bytes([len(block)]) + block, name=f"LZW sub-block {block_index}", kind="host", fmt="gif")
        writer.write(b"\x00", name="LZW data terminator", kind="host", fmt="gif")
        writer.write(b"\x3b", name="trailer", kind="host", fmt="gif")
        detail = f"{width}x{height}"
        if embedded:
            detail += f", {len(ctx.payload)} B in comment extensions"
        return RenderResult(data=writer.value, segments=writer.segments, embedded=embedded, detail=detail)

    def parse(self, data: bytes) -> list[Segment]:
        out: list[Segment] = []
        if not data[:3] == b"GIF":
            return out
        out.append(Segment("GIF header", 0, 6, "host", "gif"))
        flags = data[10]
        pos = 13
        gct_size = 3 * (2 << (flags & 0x07)) if flags & 0x80 else 0
        if gct_size:
            out.append(Segment("global colour table", pos, gct_size, "host", "gif"))
            pos += gct_size
        while pos < len(data):
            marker = data[pos]
            if marker == 0x3B:
                out.append(Segment("trailer", pos, 1, "host", "gif"))
                pos += 1
                break
            if marker == 0x21:
                label = data[pos + 1]
                end = pos + 2
                while end < len(data) and data[end]:
                    end += data[end] + 1
                end = min(end + 1, len(data))
                name = {0xFE: "comment extension", 0xF9: "graphic control", 0xFF: "application extension"}.get(
                    label, f"extension 0x{label:02x}")
                kind = "embed" if label == 0xFE and PLGM_MARKER in data[pos:end] else "host"
                out.append(Segment(name, pos, end - pos, kind, "gif"))
                pos = end
            elif marker == 0x2C:
                flags2 = data[pos + 9]
                end = pos + 10
                if flags2 & 0x80:
                    end += 3 * (2 << (flags2 & 0x07))
                end += 1
                while end < len(data) and data[end]:
                    end += data[end] + 1
                end = min(end + 1, len(data))
                out.append(Segment("image block", pos, end - pos, "host", "gif"))
                pos = end
            else:
                break
        if pos < len(data):
            out.append(Segment("trailing overlay", pos, len(data) - pos, "bundle", "gif",
                               "ignored by GIF decoders"))
        return out

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
            return False, "missing GIF header"
        trailer = data.find(b"\x3b", 13)
        if trailer < 0:
            return False, "missing GIF trailer"
        return True, "GIF header and trailer present"

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"GIF"):
            return None
        flags = data[10] if len(data) > 10 else 0
        pos = 13 + (3 * (2 << (flags & 0x07)) if flags & 0x80 else 0)
        parts: list[bytes] = []
        started = False
        while pos + 2 < len(data):
            if data[pos] != 0x21 or data[pos + 1] != 0xFE:
                break
            end = pos + 2
            blob = bytearray()
            while end < len(data) and data[end]:
                size = data[end]
                blob += data[end + 1 : end + 1 + size]
                end += size + 1
            end += 1
            body = bytes(blob)
            if not started:
                if body.startswith(PLGM_MARKER):
                    started = True
                    parts.append(body[len(PLGM_MARKER):])
                else:
                    return None
            else:
                parts.append(body)
            pos = end
        return b"".join(parts) if started else None


# ==========================================================================
# WebP
# ==========================================================================


class WebpHost(HostFormat):
    spec = HostSpec(
        key="webp",
        label="WebP image",
        category="image",
        extension=".webp",
        aliases=("image/webp",),
        magic=b"RIFF",
        embed=EMBED_CHUNK,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="payload in an unknown RIFF chunk 'PLGM' (RIFF permits unknown chunks)",
    )

    @staticmethod
    def vp8l_1x1(width: int = 1, height: int = 1) -> bytes:
        """Return a valid VP8L constant-black image bitstream.

        The entropy-coded suffix is a known-valid single-colour WebP lossless
        stream; only its packed 14-bit dimensions vary.
        """
        width = max(1, min(16384, width))
        height = max(1, min(16384, height))
        dimension_bits = (width - 1) | ((height - 1) << 14)
        return b"\x2f" + struct.pack("<I", dimension_bits) + bytes.fromhex("071011fd0f4444ff03")


    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(1))
        height = max(1, ctx.height(1))
        writer = ByteWriter()
        writer.write(b"RIFF", name="RIFF id", kind="host", fmt="webp")
        size_at = writer.tell
        writer.write(u32le(0), name="RIFF size (patched)", kind="host", fmt="webp")
        writer.write(b"WEBP", name="WEBP form type", kind="host", fmt="webp")
        writer.write(riff_chunk(b"VP8L", self.vp8l_1x1(width, height)),
                     name="VP8L (lossless image)", kind="host", fmt="webp")
        embedded = False
        if ctx.payload:
            for index, block in enumerate(split_payload(PLGM_MARKER + ctx.payload, 0x3FFFFFFF)):
                writer.write(riff_chunk(b"PLGM", block), name=f"PLGM payload chunk {index}",
                             kind="embed", fmt="webp")
            embedded = True
        writer.patch_u32le(size_at, writer.tell - size_at - 4)
        detail = f"{width}x{height} VP8L lossless"
        if embedded:
            detail += f", {len(ctx.payload)} B in RIFF chunk"
        return RenderResult(data=writer.value, segments=writer.segments, embedded=embedded, detail=detail)

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return False, "missing RIFF/WEBP header"
        declared = struct.unpack_from("<I", data, 4)[0] + 8
        if declared > len(data) or declared < 20:
            return False, "invalid RIFF/WEBP size"
        pos = 12
        while pos + 8 <= declared:
            size = struct.unpack_from("<I", data, pos + 4)[0]
            pos += 8 + size + (size & 1)
        return (True, "RIFF/WEBP chunks are structurally valid") if pos == declared else (False, "truncated RIFF/WEBP chunk")

    def parse(self, data: bytes) -> list[Segment]:
        out: list[Segment] = []
        if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
            return out
        out.append(Segment("RIFF/WEBP header", 0, 12, "host", "webp"))
        pos = 12
        while pos + 8 <= len(data):
            fourcc = data[pos : pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            total = 8 + size + (size & 1)
            kind = "embed" if fourcc == b"PLGM" else "host"
            out.append(Segment(fourcc.decode("latin1").strip(), pos, min(total, len(data) - pos), kind, "webp"))
            pos += total
        if pos < len(data):
            out.append(Segment("trailing overlay", pos, len(data) - pos, "bundle", "webp"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        parts: list[bytes] = []
        pos = 12
        if not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
            return None
        while pos + 8 <= len(data):
            fourcc = data[pos : pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if fourcc == b"PLGM":
                parts.append(data[pos + 8 : pos + 8 + size])
            pos += 8 + size + (size & 1)
        if not parts:
            return None
        blob = b"".join(parts)
        return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else blob


# ==========================================================================
# BMP
# ==========================================================================


class BmpHost(HostFormat):
    spec = HostSpec(
        key="bmp",
        label="BMP image",
        category="image",
        extension=".bmp",
        aliases=("bitmap", "dib", "image/bmp"),
        magic=b"BM",
        embed=EMBED_NONE,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="24-bit uncompressed bitmap; trailing bytes are ignored by decoders",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(8))
        height = max(1, ctx.height(8))
        row = bytearray()
        for x in range(width):
            row += bytes([255 - (x * 255) // max(width - 1, 1), 96, (x * 255) // max(width - 1, 1)])
        row += b"\x00" * ((-len(row)) % 4)
        pixels = bytes(row) * height
        dif = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
        offset = 14 + len(dif)
        writer = ByteWriter()
        writer.write(b"BM" + u32le(offset + len(pixels)) + u16le(0) + u16le(0) + u32le(offset),
                     name="BITMAPFILEHEADER", kind="host", fmt="bmp")
        writer.write(dif, name="BITMAPINFOHEADER", kind="host", fmt="bmp")
        writer.write(pixels, name="pixel data", kind="host", fmt="bmp")
        return RenderResult(data=writer.value, segments=writer.segments, embedded=False,
                            detail=f"{width}x{height} 24-bit uncompressed")

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 54 or not data.startswith(b"BM"):
            return False, "missing BMP file/DIB header"
        file_size = struct.unpack_from("<I", data, 2)[0]
        pixel_offset = struct.unpack_from("<I", data, 10)[0]
        return (True, "BMP header and pixel offset are valid") if 54 <= pixel_offset <= file_size <= len(data) else (False, "invalid BMP sizes")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"BM") or len(data) < 54:
            return []
        pixel_offset = struct.unpack_from("<I", data, 10)[0]
        out = [Segment("BITMAPFILEHEADER", 0, 14, "host", "bmp"),
               Segment("DIB header", 14, pixel_offset - 14, "host", "bmp"),
               Segment("pixel data", pixel_offset, min(len(data), struct.unpack_from("<I", data, 2)[0]) - pixel_offset,
                       "host", "bmp")]
        end = struct.unpack_from("<I", data, 2)[0]
        if end < len(data):
            out.append(Segment("trailing overlay", end, len(data) - end, "bundle", "bmp"))
        return out


# ==========================================================================
# TIFF
# ==========================================================================


class TiffHost(HostFormat):
    spec = HostSpec(
        key="tiff",
        label="TIFF image",
        category="image",
        extension=".tif",
        aliases=("tif", "image/tiff"),
        magic=b"II\x2a\x00",
        embed=EMBED_CHUNK,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="payload stored in private IFD tag 0x83d0; readers skip unknown tags",
    )

    PRIVATE_TAG = 0x83D0
    TYPE_UNDEFINED = 7
    TYPE_ASCII = 2

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, ctx.width(8))
        height = max(1, ctx.height(8))
        pixels = bytes(((x * 255) // max(width - 1, 1) for _ in range(height) for x in range(width)))

        entries: list[tuple[int, int, int, bytes]] = []

        def value16(v: int) -> bytes:
            return u16le(v) + b"\x00\x00"

        def value32(v: int) -> bytes:
            return u32le(v)

        entries.append((256, 4, 1, value32(width)))          # ImageWidth
        entries.append((257, 4, 1, value32(height)))         # ImageLength
        entries.append((258, 3, 1, value16(8)))              # BitsPerSample
        entries.append((259, 3, 1, value16(1)))              # Compression = none
        entries.append((262, 3, 1, value16(1)))              # Photometric = black is zero
        strip_offset_placeholder = len(entries)
        entries.append((273, 4, 1, value32(0)))              # StripOffsets (patched)
        entries.append((277, 3, 1, value16(1)))              # SamplesPerPixel
        entries.append((278, 4, 1, value32(height)))         # RowsPerStrip
        entries.append((279, 4, 1, value32(len(pixels))))    # StripByteCounts
        entries.append((282, 5, 1, b"\x00" * 4))             # XResolution (patched)
        entries.append((283, 5, 1, b"\x00" * 4))             # YResolution (patched)
        entries.append((296, 3, 1, value16(2)))              # ResolutionUnit = inch

        extra = b""
        embedded = False
        if ctx.payload:
            blob = PLGM_MARKER + ctx.payload
            extra = blob
            entries.append((self.PRIVATE_TAG, self.TYPE_UNDEFINED, len(blob), b"\x00" * 4))  # patched
            embedded = True

        ifd_offset = 8
        ifd_size = 2 + 12 * len(entries) + 4
        out_of_line = ifd_offset + ifd_size
        rational_offset = out_of_line
        payload_offset = rational_offset + 16
        strip_offset = payload_offset + len(extra)
        strip_offset += (-strip_offset) % 2

        writer = ByteWriter()
        writer.write(b"II" + u16le(42) + u32le(ifd_offset), name="TIFF header", kind="host", fmt="tiff")
        writer.write(u16le(len(entries)), name="IFD entry count", kind="host", fmt="tiff")
        for tag, typ, count, value in entries:
            if tag == 273:
                value = value32(strip_offset)
            elif tag in (282, 283):
                value = u32le(rational_offset + (0 if tag == 282 else 8))
            elif tag == self.PRIVATE_TAG:
                value = value32(payload_offset) if len(extra) > 4 else extra.ljust(4, b"\x00")
            writer.write(u16le(tag) + u16le(typ) + u32le(count) + value,
                         name=f"IFD tag 0x{tag:04x}", kind="embed" if tag == self.PRIVATE_TAG else "host",
                         fmt="tiff")
        writer.write(u32le(0), name="next IFD = none", kind="host", fmt="tiff")
        writer.write(u32le(72) + u32le(1) + u32le(72) + u32le(1), name="resolution rationals", kind="host", fmt="tiff")
        if extra:
            writer.write(extra, name="private tag payload", kind="embed", fmt="tiff")
        if writer.tell < strip_offset:
            writer.reserve(strip_offset - writer.tell, name="padding", kind="padding")
        writer.write(pixels, name="strip data", kind="host", fmt="tiff")
        detail = f"{width}x{height} 8-bit grayscale"
        if embedded:
            detail += f", {len(ctx.payload)} B in private tag"
        return RenderResult(data=writer.value, segments=writer.segments, embedded=embedded, detail=detail)

    def parse(self, data: bytes) -> list[Segment]:
        if data[:4] not in (b"II\x2a\x00", b"MM\x00\x2a"):
            return []
        little = data[:2] == b"II"
        fmt = "<" if little else ">"
        ifd = struct.unpack_from(fmt + "I", data, 4)[0]
        if ifd + 2 > len(data):
            return []
        count = struct.unpack_from(fmt + "H", data, ifd)[0]
        out = [Segment("TIFF header", 0, 8, "host", "tiff"), Segment("IFD", ifd, 2 + 12 * count + 4, "host", "tiff")]
        for index in range(count):
            base = ifd + 2 + 12 * index
            tag = struct.unpack_from(fmt + "H", data, base)[0]
            if tag == self.PRIVATE_TAG:
                size = struct.unpack_from(fmt + "I", data, base + 4)[0]
                off = struct.unpack_from(fmt + "I", data, base + 8)[0]
                out.append(Segment("private tag payload", off, size, "embed", "tiff"))
        out.sort(key=lambda s: s.offset)
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if data[:4] not in (b"II\x2a\x00", b"MM\x00\x2a"):
            return None
        fmt = "<" if data[:2] == b"II" else ">"
        ifd = struct.unpack_from(fmt + "I", data, 4)[0]
        count = struct.unpack_from(fmt + "H", data, ifd)[0]
        for index in range(count):
            base = ifd + 2 + 12 * index
            if base + 12 > len(data):
                break
            tag = struct.unpack_from(fmt + "H", data, base)[0]
            if tag == self.PRIVATE_TAG:
                size = struct.unpack_from(fmt + "I", data, base + 4)[0]
                off = struct.unpack_from(fmt + "I", data, base + 8)[0]
                blob = data[off : off + size]
                return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else blob
        return None


# ==========================================================================
# ICO
# ==========================================================================


class IcoHost(HostFormat):
    spec = HostSpec(
        key="ico",
        label="Windows icon",
        category="image",
        extension=".ico",
        aliases=("icon", "image/x-icon", "image/vnd.microsoft.icon"),
        magic=b"\x00\x00\x01\x00",
        embed=EMBED_NONE,
        tolerates_prefix=False,
        tolerates_suffix=True,
        notes="a PNG-compressed icon resource; trailing bytes are ignored",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        png = PngHost().render(HostContext(metadata=ctx.metadata, options={**ctx.options, "width": 1, "height": 1})).data
        writer = ByteWriter()
        entry = bytes([1, 1, 0, 0]) + u16le(1) + u16le(32) + u32le(len(png)) + u32le(6 + 16)
        writer.write(u16le(0) + u16le(1) + u16le(1), name="ICONDIR", kind="host", fmt="ico")
        writer.write(entry, name="ICONDIRENTRY", kind="host", fmt="ico")
        writer.write(png, name="PNG resource", kind="host", fmt="ico")
        return RenderResult(data=writer.value, segments=writer.segments, embedded=False,
                            detail="1x1 PNG-compressed icon")

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 22 or data[:4] != b"\x00\x00\x01\x00":
            return []
        count = struct.unpack_from("<H", data, 4)[0]
        out = [Segment("ICONDIR", 0, 6, "host", "ico")]
        for index in range(min(count, 64)):
            base = 6 + 16 * index
            if base + 16 > len(data):
                break
            size = struct.unpack_from("<I", data, base + 8)[0]
            offset = struct.unpack_from("<I", data, base + 12)[0]
            out.append(Segment(f"ICONDIRENTRY[{index}]", base, 16, "host", "ico"))
            out.append(Segment(f"icon image {index}", offset, size, "host", "ico"))
        return sorted(out, key=lambda s: s.offset)


# ==========================================================================
# TGA
# ==========================================================================


class TgaHost(HostFormat):
    spec = HostSpec(
        key="tga",
        label="Targa image",
        category="image",
        extension=".tga",
        aliases=("targa", "image/x-tga"),
        magic=None,
        embed=EMBED_COMMENT,
        tolerates_prefix=False,
        tolerates_suffix=False,
        strict=True,
        notes="uncompressed truecolour TGA; payload is stored in a legal TGA 2.0 developer directory, with footer at EOF",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        width = max(1, min(ctx.width(8), 1024))
        height = max(1, min(ctx.height(8), 1024))
        writer = ByteWriter()
        header = bytes([0, 0, 2, 0, 0, 0, 0, 0]) + u16le(0) + u16le(0) + u16le(width) + u16le(height) + bytes([24, 0])
        writer.write(header, name="TGA header", kind="host", fmt="tga")
        pixels = bytearray()
        for y in range(height):
            for x in range(width):
                pixels += bytes([255 - (y * 255) // max(height - 1, 1), 128,
                                 (x * 255) // max(width - 1, 1)])
        writer.write(bytes(pixels), name="pixel data (BGR)", kind="host", fmt="tga")
        if ctx.payload:
            payload = PLGM_MARKER + ctx.payload
            directory_offset = writer.tell
            data_offset = directory_offset + 12
            writer.write(u16le(1) + u16le(0xC0DE) + u32le(data_offset) + u32le(len(payload)),
                         name="TGA developer directory", kind="embed", fmt="tga")
            writer.write(payload, name="TGA developer data", kind="embed", fmt="tga")
        else:
            directory_offset = 0
        writer.write(u32le(0) + u32le(directory_offset) + b"TRUEVISION-XFILE.\x00",
                     name="TGA 2.0 footer", kind="host", fmt="tga")
        return RenderResult(writer.value, writer.segments, embedded=bool(ctx.payload),
                            detail=f"{width}x{height} 24-bit truecolour")

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 18:
            return False, "truncated TGA header"
        width, height = struct.unpack_from("<HH", data, 12)
        depth = data[16]
        if width < 1 or height < 1 or depth not in (8, 16, 24, 32):
            return False, "invalid TGA dimensions or pixel depth"
        pixel_end = 18 + data[0] + width * height * (depth // 8)
        if pixel_end > len(data):
            return False, "truncated TGA pixel data"
        return True, "TGA header and pixel data are present"

    def parse(self, data: bytes) -> list[Segment]:
        if len(data) < 18:
            return []
        id_len = data[0]
        width, height = struct.unpack_from("<HH", data, 12)
        bpp = data[16]
        out = [Segment("TGA header", 0, 18, "host", "tga")]
        if id_len:
            out.append(Segment("image ID", 18, id_len, "meta", "tga"))
        pixel_start = 18 + id_len
        pixel_end = pixel_start + (bpp // 8 or 3) * width * height
        out.append(Segment("pixel data", pixel_start, max(0, pixel_end - pixel_start), "host", "tga"))
        footer_start = len(data) - 26 if len(data) >= 26 and data[-18:] == b"TRUEVISION-XFILE.\x00" else len(data)
        if footer_start < pixel_end:
            footer_start = len(data)
        if footer_start > pixel_end:
            out.append(Segment("TGA developer data", pixel_end, footer_start - pixel_end, "embed", "tga"))
        if footer_start < len(data):
            out.append(Segment("TGA 2.0 footer", footer_start, len(data) - footer_start, "host", "tga"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if len(data) < 26 or data[-18:] != b"TRUEVISION-XFILE.\x00":
            return None
        directory = struct.unpack_from("<I", data, len(data) - 22)[0]
        if not directory or directory + 2 > len(data) - 26:
            return None
        count = struct.unpack_from("<H", data, directory)[0]
        for index in range(min(count, 64)):
            entry = directory + 2 + index * 10
            if entry + 10 > len(data) - 26:
                break
            tag = struct.unpack_from("<H", data, entry)[0]
            offset, length = struct.unpack_from("<II", data, entry + 2)
            if tag == 0xC0DE and offset + length <= len(data) - 26:
                blob = data[offset:offset + length]
                return blob[len(PLGM_MARKER):] if blob.startswith(PLGM_MARKER) else blob
        return None


register(PngHost())
register(JpegHost())
register(GifHost())
register(WebpHost())
register(BmpHost())
register(TiffHost())
register(IcoHost())
register(TgaHost())
