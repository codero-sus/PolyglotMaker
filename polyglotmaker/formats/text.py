"""Text/document host formats (JSON, XML, HTML, code, markup, config, ...).

For strict text formats the binary ZIP bundle is stored as base64 inside a
legal comment/property.  The make pipeline automatically uses native mode for
these hosts, because appending a binary ZIP would make JSON, XML, scripts, and
code invalid.  This preserves both the host syntax and every payload byte.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from typing import Callable

from ..core.util import Segment
from .base import EMBED_TEXT, HostContext, HostFormat, HostSpec, RenderResult, register
from .images import PLGM_MARKER

BEGIN = "PLGMK1-BUNDLE-BEGIN"
END = "PLGMK1-BUNDLE-END"


@dataclass(frozen=True)
class TextDescriptor:
    key: str
    label: str
    extension: str
    aliases: tuple[str, ...]
    category: str = "document"
    template: str = "text"


DESCRIPTORS = (
    TextDescriptor("text", "Plain text", ".txt", ("txt", "log", "text/plain"), "data", "text"),
    TextDescriptor("markdown", "Markdown", ".md", ("md", "markdown", "text/markdown"), "document", "markdown"),
    TextDescriptor("html", "HTML document", ".html", ("htm", "xhtml", "text/html"), "document", "html"),
    TextDescriptor("svg", "SVG vector image", ".svg", ("image/svg+xml",), "image", "svg"),
    TextDescriptor("json", "JSON data", ".json", ("application/json",), "data", "json"),
    TextDescriptor("xml", "XML document", ".xml", ("application/xml", "text/xml"), "data", "xml"),
    TextDescriptor("yaml", "YAML document", ".yaml", ("yml", "application/yaml"), "data", "yaml"),
    TextDescriptor("csv", "CSV spreadsheet", ".csv", ("text/csv",), "data", "csv"),
    TextDescriptor("ini", "INI configuration", ".ini", ("cfg", "conf", "config"), "data", "ini"),
    TextDescriptor("toml", "TOML configuration", ".toml", (), "data", "toml"),
    TextDescriptor("css", "CSS stylesheet", ".css", ("text/css",), "document", "css"),
    TextDescriptor("javascript", "JavaScript source", ".js", ("js", "mjs", "cjs", "ecmascript"), "document", "javascript"),
    TextDescriptor("typescript", "TypeScript source", ".ts", ("tsx",), "document", "typescript"),
    TextDescriptor("python", "Python source", ".py", ("py3",), "document", "python"),
    TextDescriptor("shell", "POSIX shell script", ".sh", ("bash", "zsh"), "executable", "shell"),
    TextDescriptor("batch", "Windows batch script", ".bat", ("cmd",), "executable", "batch"),
    TextDescriptor("powershell", "PowerShell script", ".ps1", ("ps1",), "executable", "powershell"),
    TextDescriptor("rtf", "Rich Text Format", ".rtf", ("application/rtf",), "document", "rtf"),
    TextDescriptor("sql", "SQL script", ".sql", ("application/sql",), "data", "sql"),
    TextDescriptor("java", "Java source", ".java", (), "document", "java"),
    TextDescriptor("c", "C source", ".c", ("h", "cpp", "cc", "hpp", "cxx"), "document", "c"),
    TextDescriptor("php", "PHP source", ".php", (), "document", "php"),
    TextDescriptor("ruby", "Ruby source", ".rb", (), "document", "ruby"),
    TextDescriptor("go", "Go source", ".go", (), "document", "go"),
    TextDescriptor("rust", "Rust source", ".rs", (), "document", "rust"),
)


def _payload_comment(template: str, b64: str) -> str:
    token = f"{BEGIN}\n{b64}\n{END}"
    if template in {"html", "markdown"}:
        return "<!-- " + token + " -->"
    if template in {"xml", "svg"}:
        return "<!-- " + token + " -->"
    if template in {"css", "javascript", "typescript", "c", "java", "go", "rust", "php", "sql"}:
        return "/* " + token + " */"
    if template in {"python", "shell", "ruby", "yaml", "ini", "toml"}:
        return "# " + BEGIN + ":" + b64 + ":" + END
    if template == "batch":
        return "REM " + BEGIN + ":" + b64 + ":" + END
    if template == "rtf":
        return "{\\*\\comment " + token.replace("\n", " ") + "}"
    if template == "csv":
        return f'PolyglotMaker Bundle,"{b64}"'
    return token


class TextHost(HostFormat):
    def __init__(self, descriptor: TextDescriptor):
        self.descriptor = descriptor
        self.spec = HostSpec(
            key=descriptor.key,
            label=descriptor.label,
            category=descriptor.category,
            extension=descriptor.extension,
            aliases=descriptor.aliases,
            magic=None,
            embed=EMBED_TEXT,
            tolerates_prefix=False,
            tolerates_suffix=False,
            store="b64",
            strict=True,
            notes="valid UTF-8 text; ZIP bundle is embedded as base64 in a format-safe comment/property",
        )

    def _body(self, payload_b64: str) -> bytes:
        t = self.descriptor.template
        if t == "json":
            obj = {"polyglotmaker": {"format": "PLGMK1", "bundle_base64": payload_b64},
                   "message": "PolyglotMaker multi-format container"}
            return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if t == "html":
            escaped = html.escape(payload_b64, quote=False)
            return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>PolyglotMaker</title>"
                    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
                    "<body><main><h1>PolyglotMaker</h1><p>Multi-format file container.</p></main>"
                    f"<script type=\"application/octet-stream\" id=\"polyglotmaker-bundle\">{escaped}</script>"
                    "</body></html>\n").encode("utf-8")
        if t == "svg":
            return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"120\" viewBox=\"0 0 320 120\">"
                    "<rect width=\"320\" height=\"120\" rx=\"12\" fill=\"#172033\"/>"
                    "<text x=\"20\" y=\"66\" fill=\"white\" font-family=\"sans-serif\" font-size=\"24\">PolyglotMaker</text>"
                    f"<metadata id=\"polyglotmaker-bundle\">{payload_b64}</metadata></svg>\n").encode("utf-8")
        if t == "xml":
            return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                    f"<polyglotmaker format=\"PLGMK1\"><bundle encoding=\"base64\">{payload_b64}</bundle></polyglotmaker>\n").encode("utf-8")
        if t == "csv":
            # CSV with a payload record; RFC 4180 quotes fields that contain commas/newlines.
            return ("kind,value\r\nPolyglotMaker,PLGMK1\r\n" +
                    'bundle_base64,"' + payload_b64 + '"\r\n').encode("utf-8")
        if t == "yaml":
            return ("polyglotmaker:\n  format: PLGMK1\n  bundle_encoding: base64\n  bundle: \"" + payload_b64 + "\"\n").encode("utf-8")
        if t == "toml":
            return ("[polyglotmaker]\nformat = \"PLGMK1\"\nbundle_encoding = \"base64\"\nbundle = \"" + payload_b64 + "\"\n").encode("utf-8")
        if t == "ini":
            return ("[polyglotmaker]\nformat=PLGMK1\nbundle_encoding=base64\nbundle=" + payload_b64 + "\n").encode("utf-8")
        if t == "rtf":
            # RTF uses an escaped, text-only comment group; bundle base64 needs no escaping.
            return ("{\\rtf1\\ansi\\deff0 {\\fonttbl{\\f0 Arial;}}\\f0\n"
                    "PolyglotMaker\\par\n" + _payload_comment(t, payload_b64) + "\n}").encode("ascii")
        if t == "markdown":
            return ("# PolyglotMaker\n\nMulti-format file container.\n\n" + _payload_comment(t, payload_b64) + "\n").encode("utf-8")
        if t == "css":
            return ("/* PolyglotMaker multi-format container */\n:root { --polyglotmaker: 1; }\n" +
                    _payload_comment(t, payload_b64) + "\n").encode("utf-8")
        if t == "javascript":
            return ("// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) + "\nexport {};\n").encode("utf-8")
        if t == "typescript":
            return ("// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) + "\nexport {};\n").encode("utf-8")
        if t == "python":
            return ("#!/usr/bin/env python3\n\"\"\"PolyglotMaker multi-format bundle carrier.\"\"\"\n" +
                    _payload_comment(t, payload_b64) + "\nif __name__ == '__main__':\n    print('PolyglotMaker bundle')\n").encode("utf-8")
        if t == "shell":
            return ("#!/bin/sh\n# PolyglotMaker multi-format bundle carrier\n" + _payload_comment(t, payload_b64) +
                    "\nprintf '%s\\n' 'PolyglotMaker bundle'\n").encode("utf-8")
        if t == "batch":
            return ("@echo off\r\nREM PolyglotMaker multi-format bundle carrier\r\n" +
                    _payload_comment(t, payload_b64) + "\r\necho PolyglotMaker bundle\r\n").encode("utf-8")
        if t == "powershell":
            return ("# PolyglotMaker multi-format bundle carrier\n" + _payload_comment(t, payload_b64) +
                    "\nWrite-Output 'PolyglotMaker bundle'\n").encode("utf-8")
        if t == "sql":
            return ("-- PolyglotMaker multi-format container\nCREATE TABLE IF NOT EXISTS polyglotmaker_info (format TEXT);\n" +
                    _payload_comment(t, payload_b64) + "\n").encode("utf-8")
        if t == "java":
            return ("// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) +
                    "\nfinal class PolyglotMakerBundle { public static void main(String[] args) {} }\n").encode("utf-8")
        if t == "c":
            return ("/* PolyglotMaker multi-format container */\n" + _payload_comment(t, payload_b64) +
                    "\nint main(void) { return 0; }\n").encode("utf-8")
        if t == "php":
            return ("<?php\n// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) +
                    "\necho 'PolyglotMaker bundle';\n").encode("utf-8")
        if t == "ruby":
            return ("#!/usr/bin/env ruby\n# PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) +
                    "\nputs 'PolyglotMaker bundle'\n").encode("utf-8")
        if t == "go":
            return ("package main\n// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) +
                    "\nfunc main() {}\n").encode("utf-8")
        if t == "rust":
            return ("// PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) +
                    "\nfn main() {}\n").encode("utf-8")
        if t == "xml":
            return ("<?xml version=\"1.0\"?><polyglotmaker>" + payload_b64 + "</polyglotmaker>\n").encode("utf-8")
        if t == "text":
            return ("PolyglotMaker multi-format container\n" + _payload_comment(t, payload_b64) + "\n").encode("utf-8")
        # Fallback for future simple text descriptors.
        return (f"PolyglotMaker {self.descriptor.label}\n" + _payload_comment(t, payload_b64) + "\n").encode("utf-8")

    def render(self, ctx: HostContext) -> RenderResult:
        # In native mode ctx.payload is the complete ZIP bundle. If a caller
        # explicitly requests append mode, this still embeds what was provided,
        # but the orchestrator selects native automatically for strict hosts.
        payload = ctx.payload
        encoded = base64.b64encode(payload).decode("ascii") if payload else ""
        data = self._body(encoded)
        embedded = bool(payload)
        return RenderResult(data, [Segment(f"{self.descriptor.label} document", 0, len(data),
                                           "embed" if embedded else "host", self.spec.key)],
                            embedded, f"UTF-8 {self.descriptor.label}; base64 native payload={len(payload)} B")

    def parse(self, data: bytes) -> list[Segment]:
        if not data:
            return []
        kind = "embed" if BEGIN.encode() in data or b"bundle_base64" in data or b"polyglotmaker-bundle" in data else "host"
        return [Segment(f"{self.descriptor.label} text", 0, len(data), kind, self.spec.key)]

    def extract_native(self, data: bytes) -> bytes | None:
        # JSON/XML/etc use direct base64 properties, while comment-based formats
        # share the pair of sentinels. Requiring a full ZIP magic after decoding
        # prevents arbitrary matching strings from being treated as a bundle.
        candidates: list[str] = []
        if self.descriptor.template == "json":
            try:
                obj = json.loads(data.decode("utf-8"))
                value = obj.get("polyglotmaker", {}).get("bundle_base64")
                if isinstance(value, str):
                    candidates.append(value)
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
        elif self.descriptor.template in {"yaml", "toml", "ini", "csv"}:
            for marker in ("bundle: ", "bundle=", "bundle = \"", "bundle_base64,\"", "bundle_base64,\r\n\""):
                idx = data.find(marker.encode())
                if idx >= 0:
                    value = data[idx + len(marker):].split(b"\n", 1)[0].strip().strip(b'"')
                    if value:
                        candidates.append(value.decode("ascii", "ignore"))
        else:
            raw = data.decode("utf-8", "ignore")
            match = re.search(re.escape(BEGIN) + r":([A-Za-z0-9+/=]+):" + re.escape(END), raw)
            if match:
                candidates.append(match.group(1))
            else:
                match = re.search(re.escape(BEGIN) + r"\s*(.*?)\s*" + re.escape(END), raw, re.S)
                if match:
                    candidates.append(re.sub(r"\s+", "", match.group(1)))
            if self.descriptor.template == "svg":
                match = re.search(r'<metadata[^>]*id="polyglotmaker-bundle"[^>]*>(.*?)</metadata>', raw, re.S)
                if match:
                    candidates.append(match.group(1))
            if self.descriptor.template == "xml":
                match = re.search(r'<bundle[^>]*encoding="base64"[^>]*>(.*?)</bundle>', raw, re.S)
                if match:
                    candidates.append(match.group(1))
            if self.descriptor.template == "html":
                match = re.search(r'<script[^>]*id="polyglotmaker-bundle"[^>]*>(.*?)</script>', raw, re.S)
                if match:
                    candidates.append(match.group(1))
        for value in candidates:
            try:
                decoded = base64.b64decode(re.sub(r"\s+", "", value), validate=True)
                if decoded.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PLGMSTORE1", b"PLGMOBF1")):
                    return decoded
            except (ValueError, base64.binascii.Error):
                continue
        return None


for _descriptor in DESCRIPTORS:
    register(TextHost(_descriptor))
