"""Dependency-free HTTP server for the drag-and-drop web UI."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .. import __version__
from ..core.bundle import InputFile
from ..core.container import inspect_bytes, make_container, unpack_bytes, verify_bytes
from ..core.errors import PolyglotError
from ..core.util import flatten, guess_mime, safe_member_name
from ..formats import REGISTRY

MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_DOWNLOADS = 32
MAX_DOWNLOAD_CACHE_BYTES = 512 * 1024 * 1024
STATIC_DIR = Path(__file__).with_name("static")
_DOWNLOADS: dict[str, tuple[bytes, str, str, float]] = {}
_DOWNLOADS_LOCK = threading.Lock()


@dataclass
class Upload:
    name: str
    data: bytes
    field: str


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, list[str]], list[Upload]]:
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type, re.I)
    if not match:
        raise ValueError("multipart request is missing a boundary")
    boundary = (match.group(1) or match.group(2)).strip().encode("ascii", "strict")
    fields: dict[str, list[str]] = {}
    uploads: list[Upload] = []
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--\r\n", b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if part.endswith(b"--"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, value = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("latin-1", "replace")
        disposition = next((line for line in headers.split("\r\n") if line.lower().startswith("content-disposition:")), "")
        name_match = re.search(r'\bname="([^"]*)"', disposition, re.I)
        if not name_match:
            continue
        field = name_match.group(1)
        filename_match = re.search(r'\bfilename="([^"]*)"', disposition, re.I)
        if filename_match and filename_match.group(1):
            filename = unquote(filename_match.group(1).replace("+", "%2B"))
            # Strip directory components supplied by a browser/client.
            filename = filename.replace("\\", "/").split("/")[-1] or "upload.bin"
            uploads.append(Upload(filename, value, field))
        else:
            fields.setdefault(field, []).append(value.decode("utf-8", "replace"))
    return fields, uploads


def _parse_request(handler: BaseHTTPRequestHandler) -> tuple[dict[str, list[str]], list[Upload]]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > MAX_REQUEST_BYTES:
        raise ValueError(f"request exceeds {MAX_REQUEST_BYTES // (1024 * 1024)} MiB limit")
    body = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" in content_type.lower():
        return _parse_multipart(content_type, body)
    if "application/json" in content_type.lower():
        payload = json.loads(body.decode("utf-8"))
        fields: dict[str, list[str]] = {}
        uploads: list[Upload] = []
        for key, value in payload.items():
            if key == "files":
                for item in value or []:
                    data = base64.b64decode(item.get("data_base64", ""), validate=True)
                    uploads.append(Upload(item.get("name", "upload.bin"), data, "files"))
            elif key == "host_file" and isinstance(value, dict):
                data = base64.b64decode(value.get("data_base64", ""), validate=True)
                uploads.append(Upload(value.get("name", "host.bin"), data, "host_file"))
            elif isinstance(value, list):
                fields[key] = [str(v) for v in value]
            elif value is not None:
                fields[key] = [str(value)]
        return fields, uploads
    raise ValueError("use multipart/form-data or application/json")


def _field(fields: dict[str, list[str]], name: str, default: str = "") -> str:
    values = fields.get(name, [])
    return values[-1] if values else default


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _remember_download(data: bytes, filename: str, content_type: str = "application/octet-stream") -> str:
    if len(data) > MAX_DOWNLOAD_CACHE_BYTES:
        raise ValueError("generated file exceeds the 512 MiB web download cache limit; use the CLI")
    token = secrets.token_urlsafe(18)
    with _DOWNLOADS_LOCK:
        _DOWNLOADS[token] = (data, filename, content_type, time.time())
        while len(_DOWNLOADS) > MAX_DOWNLOADS or sum(len(item[0]) for item in _DOWNLOADS.values()) > MAX_DOWNLOAD_CACHE_BYTES:
            oldest = min(_DOWNLOADS, key=lambda key: _DOWNLOADS[key][3])
            _DOWNLOADS.pop(oldest, None)
    return token


def _extract_inputs(uploads: list[Upload]) -> tuple[list[InputFile], InputFile | None]:
    files, host = [], None
    for upload in uploads:
        # Preserve nested filenames when they are present in client-side JSON,
        # but never trust a multipart path from a browser.
        name = safe_member_name(upload.name)
        item = InputFile(name, upload.data, mtime=time.time(), mode=0o644,
                         mime=guess_mime(name, upload.data), role="host" if upload.field == "host_file" else "payload")
        if upload.field == "host_file":
            host = item
        else:
            files.append(item)
    return files, host


class PolyglotRequestHandler(BaseHTTPRequestHandler):
    server_version = f"PolyglotMaker/{__version__}"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep logs useful but avoid printing uploaded data or user-supplied names.
        print(f"[PolyglotMaker] {self.address_string()} - {fmt % args}")

    def _send_asset(self, name: str) -> None:
        allowed = {"index.html", "app.js", "styles.css"}
        if name not in allowed:
            self.send_error(404)
            return
        path = STATIC_DIR / name
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".js"):
            mime = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        path = urlsplit(self.path).path
        if path == "/" or path == "/index.html":
            self._send_asset("index.html")
        elif path.startswith("/static/"):
            self._send_asset(path.rsplit("/", 1)[-1])
        elif path == "/api/formats":
            formats = [fmt.spec.to_dict() for fmt in REGISTRY.all()]
            _json_response(self, {"version": __version__, "count": len(formats), "formats": formats})
        elif path == "/api/health":
            _json_response(self, {"ok": True, "version": __version__})
        elif path.startswith("/api/download/"):
            token = path.rsplit("/", 1)[-1]
            with _DOWNLOADS_LOCK:
                item = _DOWNLOADS.get(token)
            if not item:
                self.send_error(404, "download expired or unknown")
                return
            data, filename, mime, _created = item
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        path = urlsplit(self.path).path
        if path not in {"/api/make", "/api/unpack", "/api/inspect", "/api/verify"}:
            self.send_error(404)
            return
        try:
            fields, uploads = _parse_request(self)
            files, host_file = _extract_inputs(uploads)
            if path == "/api/make":
                if not files:
                    raise ValueError("add at least one payload file")
                also = flatten(fields.get("also", []))
                options = {
                    "width": _bounded_int(fields, "width", 8, 1, 1024),
                    "height": _bounded_int(fields, "height", 8, 1, 1024),
                    "pe_bits": _bounded_int(fields, "pe_bits", 64, 32, 64, {32, 64}),
                    "elf_bits": _bounded_int(fields, "elf_bits", 64, 32, 64, {32, 64}),
                    "sample_rate": _bounded_int(fields, "sample_rate", 8000, 8000, 192000),
                    "duration_ms": _bounded_int(fields, "duration_ms", 100, 20, 10000),
                }
                result = make_container(
                    files,
                    host=_field(fields, "host", "auto"),
                    host_file=host_file,
                    also=also,
                    embed_mode=_field(fields, "embed_mode", "append"),
                    store=_field(fields, "store", "zip"),
                    compression=_field(fields, "compress", "deflate"),
                    obfuscate_key=_field(fields, "obfuscate") or None,
                    options=options,
                )
                filename = "polyglotmaker" + result.extension
                token = _remember_download(result.data, filename)
                payload = result.to_dict()
                payload["data"] = None
                payload["download_url"] = f"/api/download/{token}"
                payload["download_name"] = filename
                _json_response(self, payload)
                return
            if path == "/api/unpack":
                upload = next((item for item in uploads if item.field in {"file", "files"}), None)
                if upload is None:
                    raise ValueError("select a PolyglotMaker file")
                selected = flatten(fields.get("select", [])) or None
                result = unpack_bytes(upload.data, key=_field(fields, "key") or None, selected=selected)
                _json_response(self, result.to_dict(include_data=True))
                return
            upload = next((item for item in uploads if item.field in {"file", "files"}), None)
            if upload is None:
                raise ValueError("select a file to inspect")
            if path == "/api/inspect":
                _json_response(self, inspect_bytes(upload.data))
            else:
                _json_response(self, verify_bytes(upload.data, key=_field(fields, "key") or None))
        except (PolyglotError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            _json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:  # keep browser API errors readable, no traceback
            _json_response(self, {"error": f"internal error: {type(exc).__name__}: {exc}"}, 500)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


def _bounded_int(fields: dict[str, list[str]], name: str, default: int, low: int, high: int,
                 allowed: set[int] | None = None) -> int:
    raw = _field(fields, name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    value = max(low, min(high, value))
    if allowed and value not in allowed:
        value = default
    return value


def serve(bind: str = "0.0.0.0", port: int = 8765) -> None:
    """Run until interrupted. The server accepts preview proxy hosts/origins."""
    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((bind, int(port)), PolyglotRequestHandler) as httpd:
        try:
            httpd.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
