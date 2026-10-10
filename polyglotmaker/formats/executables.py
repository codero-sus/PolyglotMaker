"""Executable/object hosts: Windows PE32/PE32+, Linux ELF32/ELF64, DOS MZ,
WebAssembly, Java class, and a minimal Mach-O object.

PE and ELF outputs are minimal, structurally valid executable stubs. The
Windows PE calls kernel32!ExitProcess(0); the ELF stubs exit(0) on x86/x86-64.
All arbitrary user data is carried in the loader-ignored overlay / WebAssembly
custom section / Java class attribute. This module does not execute payloads.
"""

from __future__ import annotations

import base64
import struct
from typing import Iterable

from ..core.util import ByteWriter, Segment, align_up, u16be, u16le, u32be, u32le, u64be, u64le
from .base import EMBED_CHUNK, EMBED_OVERLAY, EMBED_NONE, HostContext, HostFormat, HostSpec, RenderResult, register
from .images import PLGM_MARKER

OVERLAY_MARKER = b"PLGMK1"
FILE_ALIGN = 0x200
SECTION_ALIGN = 0x1000


def _section_header(name: bytes, virtual_size: int, virtual_address: int, raw_size: int,
                    raw_offset: int, characteristics: int) -> bytes:
    return struct.pack("<8sIIIIIIHHI", name.ljust(8, b"\x00"), virtual_size, virtual_address,
                       raw_size, raw_offset, 0, 0, 0, 0, characteristics)


def _build_pe(arch: int, overlay: bytes = b"") -> bytes:
    """Return a minimal PE32/PE32+ GUI-independent console executable."""
    if arch not in (32, 64):
        raise ValueError("PE architecture must be 32 or 64")
    pe_offset = 0x80
    dos = bytearray(pe_offset)
    dos[0:2] = b"MZ"
    struct.pack_into("<HHHHHHHHHHHHHH", dos, 2,
                     0x0090, 0x0003, 0, 0x0004, 0, 0xFFFF, 0, 0x00B8,
                     0, 0, 0, 0, 0x0040, 0)
    dos[0x3C:0x40] = u32le(pe_offset)
    stub = b"\x0e\x1f\xba\x0e\x00\xb4\x09\xcd\x21\xb8\x01\x4c\xcd\x21"
    dos[0x40:0x40 + len(stub)] = stub
    dos[0x4E:0x4E + len(b"This program requires Windows.\r\n$")] = b"This program requires Windows.\r\n$"

    text_rva, idata_rva = 0x1000, 0x2000
    text_raw, idata_raw = FILE_ALIGN, FILE_ALIGN * 2
    image_base = 0x140000000 if arch == 64 else 0x400000
    # .idata starts with ILT, then IAT; import descriptor/name/hint follow.
    ilt_rva = idata_rva
    iat_rva = idata_rva + (16 if arch == 64 else 8)
    import_rva = idata_rva + 40
    dll_name_rva = idata_rva + 88
    hint_name_rva = idata_rva + 112
    if arch == 64:
        entry = struct.pack("<Q", hint_name_rva) + u64le(0)
        iat_slot = struct.pack("<Q", hint_name_rva) + u64le(0)
        code = b"\x48\x83\xec\x28\x31\xc9\xff\x15" + struct.pack("<i", iat_rva - (text_rva + 12)) + b"\x0f\x0b"
        machine, magic, optional_size = 0x8664, 0x20B, 240
    else:
        entry = u32le(hint_name_rva) + u32le(0)
        iat_slot = u32le(hint_name_rva) + u32le(0)
        code = b"\x6a\x00\xff\x15" + u32le(image_base + iat_rva) + b"\xcc"
        machine, magic, optional_size = 0x014C, 0x10B, 224

    idata = bytearray(FILE_ALIGN)
    idata[:len(entry)] = entry
    iat_offset = iat_rva - idata_rva
    idata[iat_offset:iat_offset + len(iat_slot)] = iat_slot
    idata[40:60] = (u32le(ilt_rva) + u32le(0) + u32le(0) + u32le(dll_name_rva) + u32le(iat_rva))
    idata[88:100] = b"kernel32.dll\x00"
    idata[112:114] = u16le(0)
    idata[114:126] = b"ExitProcess\x00"

    text_size = align_up(len(code), FILE_ALIGN)
    idata_size = FILE_ALIGN
    size_of_image = align_up(idata_rva + FILE_ALIGN, SECTION_ALIGN)

    directories = [(0, 0)] * 16
    directories[1] = (import_rva, 40)
    if arch == 64:
        optional = bytearray()
        optional += u16le(magic) + bytes([14, 0])
        optional += u32le(text_size) + u32le(idata_size) + u32le(0)
        optional += u32le(text_rva) + u32le(text_rva)
        optional += u64le(image_base)
        optional += u32le(SECTION_ALIGN) + u32le(FILE_ALIGN)
        optional += u16le(6) + u16le(0) + u16le(0) + u16le(0) + u16le(6) + u16le(0)
        optional += u32le(0) + u32le(size_of_image) + u32le(FILE_ALIGN) + u32le(0)
        optional += u16le(3) + u16le(0x0100)
        optional += u64le(0x100000) + u64le(0x1000) + u64le(0x100000) + u64le(0x1000)
        optional += u32le(0) + u32le(16)
    else:
        optional = bytearray()
        optional += u16le(magic) + bytes([14, 0])
        optional += u32le(text_size) + u32le(idata_size) + u32le(0)
        optional += u32le(text_rva) + u32le(text_rva) + u32le(idata_rva)
        optional += u32le(image_base)
        optional += u32le(SECTION_ALIGN) + u32le(FILE_ALIGN)
        optional += u16le(6) + u16le(0) + u16le(0) + u16le(0) + u16le(6) + u16le(0)
        optional += u32le(0) + u32le(size_of_image) + u32le(FILE_ALIGN) + u32le(0)
        optional += u16le(3) + u16le(0x0100)
        optional += u32le(0x100000) + u32le(0x1000) + u32le(0x100000) + u32le(0x1000)
        optional += u32le(0) + u32le(16)
    for rva, size in directories:
        optional += u32le(rva) + u32le(size)
    if len(optional) != optional_size:
        raise AssertionError(f"PE{arch} optional header has {len(optional)} bytes, expected {optional_size}")

    coff = struct.pack("<HHIIIHH", machine, 2, 0, 0, 0, optional_size, 0x0022)
    sections = (_section_header(b".text", len(code), text_rva, text_size, text_raw, 0x60000020) +
                _section_header(b".idata", FILE_ALIGN, idata_rva, idata_size, idata_raw, 0xC0000040))
    headers = bytes(dos) + b"PE\x00\x00" + coff + bytes(optional) + sections
    headers = headers.ljust(FILE_ALIGN, b"\x00")
    out = headers + code.ljust(text_size, b"\x00") + bytes(idata)
    return out + overlay


class PeHost(HostFormat):
    spec = HostSpec(
        key="exe", label="Windows PE executable", category="executable", extension=".exe",
        aliases=("pe", "pe32", "pe64", "windows", "windows-exe", "dll"), magic=b"MZ",
        embed=EMBED_OVERLAY, tolerates_suffix=True,
        notes="minimal PE32+ executable importing kernel32!ExitProcess; all payload bytes are loader-ignored overlay",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        arch = int(ctx.opt("pe_bits", 64))
        if arch not in (32, 64):
            raise ValueError("--pe-bits must be 32 or 64")
        overlay = (OVERLAY_MARKER + ctx.payload) if ctx.payload else b""
        data = _build_pe(arch, overlay)
        section_end = FILE_ALIGN * 3
        segments = [Segment("DOS header/stub", 0, 0x80, "host", "exe"),
                    Segment("PE headers", 0x80, FILE_ALIGN - 0x80, "host", "exe"),
                    Segment(".text", FILE_ALIGN, FILE_ALIGN, "host", "exe"),
                    Segment(".idata", FILE_ALIGN * 2, FILE_ALIGN, "host", "exe")]
        if overlay:
            segments.append(Segment("PE overlay payload", section_end, len(overlay), "embed", "exe"))
        return RenderResult(data, segments, bool(ctx.payload), f"PE{arch} console stub with ExitProcess import")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"MZ") or len(data) < 0x40:
            return []
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\x00\x00":
            return [Segment("DOS MZ file", 0, len(data), "host", "exe")]
        count = struct.unpack_from("<H", data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe + 20)[0]
        sec_base = pe + 24 + opt_size
        sections = [Segment("DOS header/stub", 0, pe, "host", "exe"),
                    Segment("PE/COFF headers", pe, 24 + opt_size, "host", "exe")]
        last = sec_base + count * 40
        for i in range(count):
            start = sec_base + i * 40
            if start + 40 > len(data):
                break
            name = data[start:start + 8].rstrip(b"\x00").decode("latin1")
            raw_size, raw_ptr = struct.unpack_from("<II", data, start + 16)
            if raw_size and raw_ptr + raw_size <= len(data):
                sections.append(Segment(name, raw_ptr, raw_size, "host", "exe"))
                last = max(last, raw_ptr + raw_size)
        if last < len(data):
            sections.append(Segment("PE overlay", last, len(data) - last, "embed" if OVERLAY_MARKER in data[last:] else "bundle", "exe"))
        return sorted(sections, key=lambda s: s.offset)

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"MZ"):
            return None
        idx = data.find(OVERLAY_MARKER)
        return data[idx + len(OVERLAY_MARKER):] if idx >= 0 else None

    def validate(self, data: bytes) -> tuple[bool, str]:
        if len(data) < 0x100 or not data.startswith(b"MZ"):
            return False, "missing PE DOS signature/header"
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\x00\x00":
            return False, "missing PE signature"
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        if machine not in (0x14C, 0x8664):
            return False, f"unexpected PE machine 0x{machine:04x}"
        return True, f"PE signature and machine 0x{machine:04x} present"


class ElfHost(HostFormat):
    spec = HostSpec(
        key="elf", label="Linux ELF executable", category="executable", extension=".elf",
        aliases=("linux", "elf64", "elf32", "application/x-elf", "so"), magic=b"\x7fELF",
        embed=EMBED_OVERLAY, tolerates_suffix=True,
        notes="static x86-64 ELF exits with status 0; payload is outside the PT_LOAD segment",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        bits = int(ctx.opt("elf_bits", 64))
        if bits == 64:
            eh_size, ph_size = 64, 56
            code = bytes.fromhex("48c7c03c0000004831ff0f05")  # exit(0)
            entry = 0x401000 + eh_size + ph_size
            ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
            eh = ident + struct.pack("<HHIQQQIHHHHHH", 2, 0x3E, 1, entry, eh_size, 0, 0,
                                     eh_size, ph_size, 1, 0, 0, 0)
            total = eh_size + ph_size + len(code)
            ph = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x401000, 0, total, total, 0x1000)
        elif bits == 32:
            eh_size, ph_size = 52, 32
            code = bytes.fromhex("b801000000bb00000000cd80")  # Linux i386 exit(0)
            entry = 0x8048000 + eh_size + ph_size
            ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8)
            eh = ident + struct.pack("<HHIIIIIHHHHHH", 2, 3, 1, entry, eh_size, 0, 0,
                                     eh_size, ph_size, 1, 0, 0, 0)
            total = eh_size + ph_size + len(code)
            ph = struct.pack("<IIIIIIII", 1, 0, 0x8048000, 0, total, total, 5, 0x1000)
        else:
            raise ValueError("--elf-bits must be 32 or 64")
        overlay = (OVERLAY_MARKER + ctx.payload) if ctx.payload else b""
        data = eh + ph + code + overlay
        body_len = eh_size + ph_size + len(code)
        segments = [Segment("ELF header", 0, eh_size, "host", "elf"),
                    Segment("PT_LOAD program header", eh_size, ph_size, "host", "elf"),
                    Segment("entry code (exit 0)", eh_size + ph_size, len(code), "host", "elf")]
        if overlay:
            segments.append(Segment("ELF overlay", body_len, len(overlay), "embed", "elf"))
        return RenderResult(data, segments, bool(ctx.payload), f"ELF{bits} static exit(0) executable")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"\x7fELF") or len(data) < 52:
            return []
        bits = 64 if data[4] == 2 else 32
        if bits == 64:
            entry, phoff = struct.unpack_from("<QQ", data, 24)
            phentsize, phnum = struct.unpack_from("<HH", data, 54)
            header_len, phfmt = 64, "<IIQQQQQQ"
        else:
            entry, phoff = struct.unpack_from("<II", data, 24)
            phentsize, phnum = struct.unpack_from("<HH", data, 42)
            header_len, phfmt = 52, "<IIIIIIII"
        out = [Segment(f"ELF{bits} header", 0, header_len, "host", "elf")]
        maxend = header_len
        for i in range(min(phnum, 128)):
            pos = phoff + i * phentsize
            if pos + phentsize > len(data):
                break
            values = struct.unpack_from(phfmt, data, pos)
            if bits == 64:
                typ, _flags, off, _vaddr, _paddr, filesz = values[:6]
            else:
                typ, off, _vaddr, _paddr, filesz, _memsz, _flags, _align = values
            if typ == 1 and off + filesz <= len(data):
                out.append(Segment("PT_LOAD", off, filesz, "host", "elf"))
                maxend = max(maxend, off + filesz)
        if maxend < len(data):
            out.append(Segment("ELF overlay", maxend, len(data) - maxend,
                               "embed" if OVERLAY_MARKER in data[maxend:] else "bundle", "elf"))
        return sorted(out, key=lambda s: s.offset)

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"\x7fELF"):
            return None
        idx = data.find(OVERLAY_MARKER)
        return data[idx + len(OVERLAY_MARKER):] if idx >= 0 else None

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not data.startswith(b"\x7fELF") or len(data) < 52:
            return False, "missing ELF header"
        return True, f"ELF class {data[4]} header present"


class MzHost(HostFormat):
    spec = HostSpec(
        key="mz", label="DOS MZ executable", category="executable", extension=".mz",
        aliases=("dos", "dos-exe", "mz-exe"), magic=b"MZ", embed=EMBED_OVERLAY,
        tolerates_suffix=True, notes="minimal DOS MZ executable exits through INT 21h; payload is an overlay",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        code = b"\xb8\x00\x4c\xcd\x21"  # mov ax,4c00h; int 21h
        body_len = 32 + len(code)
        cp = (body_len + 511) // 512
        cblp = body_len % 512
        if body_len and cblp == 0:
            cblp = 0
        header = bytearray(32)
        header[0:2] = b"MZ"
        struct.pack_into("<13H", header, 2,
                         cblp, cp, 0, 2, 0, 0xFFFF, 0, 0x100, 0, 0, 0, 0x1C, 0)
        overlay = (OVERLAY_MARKER + ctx.payload) if ctx.payload else b""
        data = bytes(header) + code + overlay
        segments = [Segment("MZ header", 0, 32, "host", "mz"), Segment("DOS exit code", 32, len(code), "host", "mz")]
        if overlay:
            segments.append(Segment("DOS overlay", body_len, len(overlay), "embed", "mz"))
        return RenderResult(data, segments, bool(ctx.payload), "DOS MZ stub exits with code 0")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"MZ") or len(data) < 32:
            return []
        last = struct.unpack_from("<H", data, 2)[0]
        pages = struct.unpack_from("<H", data, 4)[0]
        length = (pages - 1) * 512 + (last or 512)
        out = [Segment("MZ executable", 0, min(length, len(data)), "host", "mz")]
        if length < len(data):
            out.append(Segment("DOS overlay", length, len(data) - length, "embed" if OVERLAY_MARKER in data[length:] else "bundle", "mz"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"MZ"):
            return None
        idx = data.find(OVERLAY_MARKER)
        return data[idx + len(OVERLAY_MARKER):] if idx >= 0 else None


# Minimal valid wasm v1 module with one custom section.
def _leb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


class WasmHost(HostFormat):
    spec = HostSpec(
        key="wasm", label="WebAssembly module", category="executable", extension=".wasm",
        aliases=("application/wasm",), magic=b"\x00asm", embed=EMBED_CHUNK,
        tolerates_suffix=False, strict=True,
        notes="valid WebAssembly v1 empty module; a custom section carries the native bundle",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        base = b"\x00asm\x01\x00\x00\x00"
        custom = b"polyglotmaker\x00" + PLGM_MARKER + ctx.payload if ctx.payload else b""
        section = _leb128(len(custom)) + custom
        data = base + (b"\x00" + section if custom else b"")
        segments = [Segment("Wasm header", 0, 8, "host", "wasm")]
        if custom:
            segments.append(Segment("polyglotmaker custom section", 8, len(data) - 8, "embed", "wasm"))
        return RenderResult(data, segments, bool(ctx.payload), "WebAssembly v1 module")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"\x00asm\x01\x00\x00\x00"):
            return []
        out = [Segment("Wasm header", 0, 8, "host", "wasm")]
        if b"polyglotmaker" in data:
            out.append(Segment("custom section", 8, len(data) - 8, "embed", "wasm"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"\x00asm\x01\x00\x00\x00"):
            return None
        idx = data.find(PLGM_MARKER)
        return data[idx + len(PLGM_MARKER):] if idx >= 0 else None


class JavaClassHost(HostFormat):
    spec = HostSpec(
        key="class", label="Java class file", category="executable", extension=".class",
        aliases=("java-class", "application/java-vm"), magic=b"\xca\xfe\xba\xbe",
        embed=EMBED_CHUNK, tolerates_suffix=False, strict=True,
        notes="minimal Java class with a custom class attribute containing the native bundle as base64",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        # Constant pool: this class, java/lang/Object, and a custom attribute name.
        pool = [
            b"\x01" + u16be(len(b"PolyglotMaker")) + b"PolyglotMaker",  # #1 Utf8
            b"\x07" + u16be(1),                                          # #2 Class #1
            b"\x01" + u16be(len(b"java/lang/Object")) + b"java/lang/Object", # #3 Utf8
            b"\x07" + u16be(3),                                          # #4 Class #3
            b"\x01" + u16be(len(b"PolyglotMakerBundle")) + b"PolyglotMakerBundle", # #5
        ]
        prefix = b"\xca\xfe\xba\xbe" + u16be(0) + u16be(52) + u16be(len(pool) + 1) + b"".join(pool)
        # public super class, no interfaces/fields/methods.
        body = u16be(0x0021) + u16be(2) + u16be(4) + u16be(0) + u16be(0) + u16be(0)
        attr = b""
        if ctx.payload:
            encoded = base64.b64encode(PLGM_MARKER + ctx.payload)
            attr = u16be(5) + u32be(len(encoded)) + encoded
        data = prefix + body + u16be(1 if attr else 0) + attr
        segments = [Segment("Java class structure", 0, len(prefix) + len(body) + 2, "host", "class")]
        if attr:
            segments.append(Segment("PolyglotMakerBundle attribute", len(prefix) + len(body) + 2, len(attr), "embed", "class"))
        return RenderResult(data, segments, bool(attr), "Java 8 class (no methods), custom bundle attribute")

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"\xca\xfe\xba\xbe"):
            return None
        # Base64 payload sits after the custom UTF-8 CP entry #5. Locate its
        # UTF-8 content, then skip the class fields/methods-free header to attr.
        marker = b"PolyglotMakerBundle"
        cpname = data.find(marker)
        if cpname < 0:
            return None
        # This builder's constant pool immediately follows that UTF-8 entry;
        # the class body is fixed-size and carries one attribute at EOF.
        # The last class-level attribute begins with name index 5 and u32 size.
        idx = data.rfind(u16be(5))
        if idx < 0 or idx + 6 > len(data):
            return None
        size = struct.unpack_from(">I", data, idx + 2)[0]
        try:
            raw = base64.b64decode(data[idx + 6:idx + 6 + size], validate=True)
        except Exception:
            return None
        return raw[len(PLGM_MARKER):] if raw.startswith(PLGM_MARKER) else None


class MachOHost(HostFormat):
    spec = HostSpec(
        key="macho", label="Mach-O 64-bit object", category="executable", extension=".o",
        aliases=("mach-o", "macho64", "apple-object"), magic=b"\xcf\xfa\xed\xfe",
        embed=EMBED_OVERLAY, tolerates_suffix=True,
        notes="minimal 64-bit Mach-O relocatable object header; payload follows as a data overlay",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        # Little-endian MH_MAGIC_64, x86_64, MH_OBJECT, no load commands.
        header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 1, 0, 0, 0, 0)
        overlay = (OVERLAY_MARKER + ctx.payload) if ctx.payload else b""
        data = header + overlay
        segments = [Segment("Mach-O 64 header", 0, len(header), "host", "macho")]
        if overlay:
            segments.append(Segment("Mach-O trailing payload", len(header), len(overlay), "embed", "macho"))
        return RenderResult(data, segments, bool(ctx.payload), "x86_64 MH_OBJECT, no load commands")


for _format in (PeHost(), ElfHost(), MzHost(), WasmHost(), JavaClassHost(), MachOHost()):
    register(_format)
