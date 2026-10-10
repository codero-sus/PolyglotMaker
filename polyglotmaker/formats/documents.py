"""Document hosts whose native syntax is not a ZIP package (PDF/PostScript)."""

from __future__ import annotations

import re
import struct
from typing import Any

from ..core.util import Segment, u16be, u32be
from .base import EMBED_COMMENT, EMBED_NONE, HostContext, HostFormat, HostSpec, RenderResult, register
from .images import PLGM_MARKER


class PdfHost(HostFormat):
    spec = HostSpec(
        key="pdf", label="PDF document", category="document", extension=".pdf",
        aliases=("application/pdf",), magic=b"%PDF-", embed=EMBED_COMMENT,
        tolerates_prefix=False, tolerates_suffix=True,
        notes="PDF 1.7 with a valid EmbeddedFiles name tree attachment for the PolyglotMaker manifest/bundle",
    )

    @staticmethod
    def _stream(data: bytes, extra: bytes = b"") -> bytes:
        return (b"<< /Length " + str(len(data)).encode("ascii") + extra + b" >>\nstream\n"
                + data + b"\nendstream")

    def render(self, ctx: HostContext) -> RenderResult:
        payload = PLGM_MARKER + ctx.payload if ctx.payload else b""
        content = b"BT /F1 24 Tf 72 720 Td (PolyglotMaker multi-format bundle) Tj ET"
        objects: list[bytes] = [
            b"",  # object 0 unused
            b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 6 0 R >> /AF [4 0 R] >>" if payload
            else b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
            b"<< /Type /Filespec /F (PolyglotMaker.bundle) /UF (PolyglotMaker.bundle) /EF << /F 8 0 R >> >>" if payload
            else b"<< /Type /Filespec /F (PolyglotMaker.bundle) >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Names [(PolyglotMaker bundle) 4 0 R] >>" if payload else b"<< /Names [] >>",
            self._stream(content),
            self._stream(payload, b" /Type /EmbeddedFile /Subtype /application#2Foctet-stream") if payload
            else b"<< /Length 0 >>\nstream\n\nendstream",
        ]
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * len(objects)
        for obj_num in range(1, len(objects)):
            offsets[obj_num] = len(out)
            out += f"{obj_num} 0 obj\n".encode("ascii") + objects[obj_num] + b"\nendobj\n"
        xref_offset = len(out)
        out += f"xref\n0 {len(objects)}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for obj_num in range(1, len(objects)):
            out += f"{offsets[obj_num]:010d} 00000 n \n".encode("ascii")
        out += (f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
        data = bytes(out)
        segments = [Segment("PDF header", 0, 15, "host", "pdf"),
                    Segment("PDF objects/xref", 15, len(data) - 15, "embed" if payload else "host", "pdf")]
        return RenderResult(data, segments, bool(payload), f"PDF 1.7, one page, embedded attachment={len(payload)} B")

    def parse(self, data: bytes) -> list[Segment]:
        if not data.startswith(b"%PDF-"):
            return []
        eof = data.rfind(b"%%EOF")
        end = eof + 5 if eof >= 0 else len(data)
        out = [Segment("PDF document", 0, end, "host", "pdf")]
        if b"/EmbeddedFile" in data[:end] or b"PolyglotMaker" in data[:end]:
            out.append(Segment("EmbeddedFiles attachment (PDF objects)", 0, end, "embed", "pdf"))
        if end < len(data):
            out.append(Segment("trailing ZIP/overlay", end, len(data) - end, "bundle", "pdf",
                               "PDF readers normally tolerate trailing data"))
        return out

    def extract_native(self, data: bytes) -> bytes | None:
        if not data.startswith(b"%PDF-"):
            return None
        # Our generator writes object 8 as /EmbeddedFile with a direct /Length.
        # Search every EmbeddedFile dictionary, honour its exact stream length.
        pos = 0
        while True:
            idx = data.find(b"/Type /EmbeddedFile", pos)
            if idx < 0:
                return None
            stream_idx = data.find(b"stream\n", idx)
            if stream_idx < 0:
                return None
            header = data[idx:stream_idx]
            match = re.search(rb"/Length\s+(\d+)", header)
            if not match:
                pos = stream_idx + 7
                continue
            length = int(match.group(1))
            start = stream_idx + len(b"stream\n")
            blob = data[start:start + length]
            if blob.startswith(PLGM_MARKER):
                return blob[len(PLGM_MARKER):]
            pos = start + length

    def validate(self, data: bytes) -> tuple[bool, str]:
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data:
            return False, "missing PDF header or EOF marker"
        return True, "PDF header and EOF marker present"


class PostScriptHost(HostFormat):
    spec = HostSpec(
        key="postscript", label="PostScript document", category="document", extension=".ps",
        aliases=("ps", "application/postscript"), magic=b"%!PS-", embed="text",
        tolerates_suffix=False, strict=True,
        notes="valid PostScript Level 2 prolog; native bundle encoded as a DSC comment",
    )

    def render(self, ctx: HostContext) -> RenderResult:
        import base64
        payload = base64.b64encode(PLGM_MARKER + ctx.payload).decode("ascii") if ctx.payload else ""
        lines = ["%!PS-Adobe-3.0", "%%Title: PolyglotMaker", "%%Creator: PolyglotMaker"]
        if payload:
            payload_lines = ["%%+ " + payload[i:i + 72] for i in range(0, len(payload), 72)]
            data_bytes = sum(len(line.encode("ascii")) + 1 for line in payload_lines)
            lines += ["%%BeginResource: file PolyglotMaker.bundle", "%%PolyglotMaker: PLGMK1",
                      "%%BeginData: " + str(data_bytes) + " ASCII Bytes"]
            lines.extend(payload_lines)
            lines += ["%%EndData", "%%EndResource"]
        lines += ["%%Pages: 1", "%%Page: 1 1", "/Helvetica findfont 24 scalefont setfont", "72 720 moveto", "(PolyglotMaker) show", "showpage", "%%EOF"]
        data = ("\n".join(lines) + "\n").encode("ascii")
        return RenderResult(data, [Segment("PostScript document", 0, len(data), "embed" if payload else "host", "postscript")],
                            bool(payload), "PostScript Level 2 one-page document")

    def extract_native(self, data: bytes) -> bytes | None:
        import base64
        match = re.search(rb"%%BeginData: [0-9]+ ASCII Bytes\r?\n((?:%%\+ ?[A-Za-z0-9+/=]+\r?\n)+)%%EndData", data)
        if not match:
            return None
        encoded = b"".join(line[4:].strip() if line.startswith(b"%%+ ") else line[3:].strip()
                             for line in match.group(1).splitlines())
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        return raw[len(PLGM_MARKER):] if raw.startswith(PLGM_MARKER) else None


register(PdfHost())
register(PostScriptHost())
