"""Command line interface for PolyglotMaker."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Sequence

from . import __version__
from .core.container import (
    input_from_path,
    inspect_bytes,
    make_from_paths,
    make_container,
    unpack_file,
    unpack_bytes,
    verify_bytes,
)
from .core.errors import PolyglotError
from .core.util import flatten, human_size
from .formats import REGISTRY
from .update import (
    UpdateError,
    fetch_latest_snapshot,
    install_snapshot,
    is_snapshot_installed,
    record_installed_snapshot,
)


def _json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _add_global_version(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", action="version", version=f"PolyglotMaker {__version__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polyglot",
        description="Create, inspect, verify, and unpack multi-format files. Any input bytes can be bundled losslessly.",
        epilog="Responsible use only: distribution, research, and CTFs. Not an AV-evasion tool.",
    )
    _add_global_version(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_make = sub.add_parser("make", help="build one file from one or more arbitrary inputs")
    p_make.add_argument("files", nargs="+", help="input files of any type")
    p_make.add_argument("-o", "--out", help="output path (default: polyglotmaker<host-extension>)")
    p_make.add_argument("--host", default="auto", help="host format key (default: auto)")
    p_make.add_argument("--host-file", help="use an existing valid file as the byte-exact host body")
    p_make.add_argument("--also", action="append", default=[], metavar="FORMAT[,FORMAT...]")
    p_make.add_argument("--embed-mode", choices=("append", "native", "none"), default="append")
    p_make.add_argument("--store", choices=("zip", "raw"), default="zip")
    p_make.add_argument("--compress", choices=("none", "deflate", "bzip2", "lzma"), default="deflate")
    p_make.add_argument("--obfuscate", metavar="KEY", help="obfuscate the bundle (SHA-256 XOR; NOT encryption)")
    p_make.add_argument("--width", type=int, default=8, help="generated image width (1-1024; default 8)")
    p_make.add_argument("--height", type=int, default=8, help="generated image height (1-1024; default 8)")
    p_make.add_argument("--pe-bits", type=int, choices=(32, 64), default=64)
    p_make.add_argument("--elf-bits", type=int, choices=(32, 64), default=64)
    p_make.add_argument("--sample-rate", type=int, default=8000)
    p_make.add_argument("--duration-ms", type=int, default=100)
    p_make.add_argument("--json", action="store_true", help="print machine-readable result")

    p_unpack = sub.add_parser("unpack", help="restore payloads from a PolyglotMaker file")
    p_unpack.add_argument("file")
    p_unpack.add_argument("-d", "--outdir", default="unpacked", help="output directory")
    p_unpack.add_argument("--key", help="obfuscation key, if used when making")
    p_unpack.add_argument("--list", action="store_true", help="list files without writing them")
    p_unpack.add_argument("--select", action="append", default=[], help="extract only this member name (repeatable)")
    p_unpack.add_argument("--overwrite", action="store_true", help="overwrite existing files")
    p_unpack.add_argument("--json", action="store_true")

    p_inspect = sub.add_parser("inspect", help="detect formats and show byte offsets/segments")
    p_inspect.add_argument("file")
    p_inspect.add_argument("--json", action="store_true")

    p_verify = sub.add_parser("verify", help="validate the container and every payload SHA-256")
    p_verify.add_argument("file")
    p_verify.add_argument("--key", help="obfuscation key, if used when making")
    p_verify.add_argument("--json", action="store_true")

    p_formats = sub.add_parser("formats", help="list every host format generator")
    p_formats.add_argument("--category", help="filter by category")
    p_formats.add_argument("--json", action="store_true")

    p_doctor = sub.add_parser("doctor", help="round-trip self-test every host format")
    p_doctor.add_argument("--json", action="store_true")

    p_serve = sub.add_parser("serve", help="run the browser UI and JSON API")
    p_serve.add_argument("--bind", default="0.0.0.0", help="interface to bind (default 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8765)

    p_update = sub.add_parser("update", help="check for and install the latest source from GitHub")
    update_action = p_update.add_mutually_exclusive_group()
    update_action.add_argument("--check", action="store_true", help="check only; do not install")
    update_action.add_argument("-y", "--yes", action="store_true", help="install without prompting")
    p_update.add_argument("--branch", help="GitHub branch to update from (default: repository's default branch)")
    p_update.add_argument("--json", action="store_true", help="print machine-readable status")

    return parser


def _read_input_files(paths: Sequence[str]):
    return [input_from_path(path) for path in paths]


def _do_make(args: argparse.Namespace) -> int:
    options = {
        "width": max(1, min(args.width, 1024)),
        "height": max(1, min(args.height, 1024)),
        "pe_bits": args.pe_bits,
        "elf_bits": args.elf_bits,
        "sample_rate": max(8000, min(args.sample_rate, 192000)),
        "duration_ms": max(20, min(args.duration_ms, 10000)),
    }
    also = flatten(args.also)
    result = make_from_paths(
        args.files,
        out=args.out,
        host=args.host,
        host_file_path=args.host_file,
        also=also,
        embed_mode=args.embed_mode,
        store=args.store,
        compression=args.compress,
        obfuscate_key=args.obfuscate,
        options=options,
    )
    if result.output_path is None:
        result.output_path = os.path.abspath("polyglotmaker" + result.extension)
        from .core.util import write_bytes
        write_bytes(result.output_path, result.data)
    if args.json:
        _json(result.to_dict())
    else:
        print(f"Created {result.output_path}")
        print(f"Host: {result.host} ({result.extension}) | {human_size(result.size)} | {len(result.manifest['files'])} payload(s)")
        if result.bundle_sha256:
            print(f"Bundle checksum: {result.bundle_sha256}")
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        print("Use `polyglot inspect` and `polyglot unpack` to review/restore the contents.")
    return 0


def _do_unpack(args: argparse.Namespace) -> int:
    if args.list:
        result = unpack_bytes(_read(args.file), key=args.key, selected=args.select or None)
        report = result.to_dict()
        if args.json:
            _json(report)
        else:
            print(f"{len(result.files)} file(s) in {result.source_store}:")
            for name, data in result.files.items():
                print(f"  {name}  ({human_size(len(data))})")
            for warning in result.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
        return 0
    report = unpack_file(args.file, args.outdir, key=args.key, selected=args.select or None, overwrite=args.overwrite)
    if args.json:
        _json(report)
    else:
        print(f"Extracted from {report['source_store']} to {os.path.abspath(args.outdir)}")
        for item in report["files"]:
            print(f"  {item['name']}  ({human_size(item['size'])})  -> {item['path']}")
        for warning in report.get("warnings", []):
            print(f"Warning: {warning}", file=sys.stderr)
    return 0


def _do_inspect(args: argparse.Namespace) -> int:
    data = _read(args.file)
    result = inspect_bytes(data)
    result["file"] = os.path.abspath(args.file)
    if args.json:
        _json(result)
    else:
        print(f"File: {args.file} ({human_size(result['size'])})")
        print(f"Host: {result['host'] or 'unknown'}")
        if result["manifest"]:
            files = result["manifest"].get("files", [])
            print(f"PolyglotMaker bundle: {len(files)} payload(s)")
        print("Detected formats:")
        for item in result["detections"]:
            print(f"  {item['fmt']:14} @ 0x{item['offset']:08x}  {item['validity']:14} {item['label']}")
        print("Byte map:")
        for segment in result["segments"]:
            print(f"  {segment['kind']:8}  0x{segment['offset']:08x} + {segment['length']:8}  {segment['name']}")
    return 0


def _do_verify(args: argparse.Namespace) -> int:
    result = verify_bytes(_read(args.file), key=args.key)
    if args.json:
        _json(result)
    else:
        print("VALID" if result["valid"] else "INVALID")
        for check in result["checks"]:
            print(f"  {'PASS' if check['ok'] else 'FAIL'}  {check['name']}: {check['detail']}")
        for error in result["errors"]:
            print(f"  ERROR {error}", file=sys.stderr)
    return 0 if result["valid"] else 1


def _do_formats(args: argparse.Namespace) -> int:
    formats = [fmt.spec for fmt in REGISTRY.all()]
    if args.category:
        formats = [spec for spec in formats if spec.category == args.category]
    if args.json:
        _json([spec.to_dict() for spec in formats])
    else:
        print(f"{len(formats)} host generators (runtime dependencies: Python standard library only)\n")
        categories = {}
        for spec in formats:
            categories.setdefault(spec.category, []).append(spec)
        for category, specs in sorted(categories.items()):
            print(f"{category.title()} ({len(specs)}):")
            for spec in specs:
                native = "native" if spec.embed != "none" else "append only"
                print(f"  {spec.key:14} {spec.extension:10} {spec.label} [{native}]")
    return 0


def _do_doctor(args: argparse.Namespace) -> int:
    test_file = input_from_path(__file__, name="diagnostic/cli.py")
    results = []
    for fmt in REGISTRY.all():
        try:
            made = make_container([test_file], host=fmt.spec.key)
            unpacked = unpack_bytes(made.data)
            ok = unpacked.files.get(test_file.name) == test_file.data
            valid, detail = fmt.validate(made.data)
            results.append({"format": fmt.spec.key, "ok": bool(ok and valid), "bytes": len(made.data),
                            "roundtrip": ok, "host_valid": valid, "detail": detail})
        except Exception as exc:
            results.append({"format": fmt.spec.key, "ok": False, "bytes": 0,
                            "roundtrip": False, "host_valid": False, "detail": str(exc)})
    total = len(results)
    passed = sum(1 for item in results if item["ok"])
    report = {"version": __version__, "passed": passed, "total": total, "results": results}
    if args.json:
        _json(report)
    else:
        for item in results:
            print(f"{'PASS' if item['ok'] else 'FAIL'} {item['format']:14} {item['detail']}")
        print(f"\n{passed}/{total} host round-trips passed")
    return 0 if passed == total else 1


def _do_serve(args: argparse.Namespace) -> int:
    from .web.server import serve
    print(f"PolyglotMaker web UI: http://{args.bind}:{args.port}/ (Ctrl+C to stop)")
    serve(args.bind, args.port)
    return 0


def _do_update(args: argparse.Namespace) -> int:
    current_version = __version__
    try:
        snapshot = fetch_latest_snapshot(args.branch)
        already_current = is_snapshot_installed(snapshot)
    except UpdateError as exc:
        report = {"status": "error", "current_version": current_version, "error": str(exc)}
        if args.json:
            _json(report)
        else:
            print(f"polyglot: GitHub update check failed: {exc}", file=sys.stderr)
        return 2

    report = {
        "status": "up_to_date" if already_current else "update_available",
        "current_version": current_version,
        **snapshot.to_dict(),
    }
    if already_current:
        message = (
            f"PolyglotMaker already contains the latest GitHub commit for {snapshot.branch} "
            f"({snapshot.commit[:12]})."
        )
    else:
        message = (
            f"GitHub update available: {snapshot.branch}@{snapshot.commit[:12]} "
            f"({snapshot.url})"
        )

    if already_current or args.check or (args.json and not args.yes):
        if args.json:
            _json(report)
        else:
            print(message)
            if not already_current:
                print("Run `polyglot update` to review and install, or `polyglot update --yes` to install directly.")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print(message)
            print("No install was started in this non-interactive session. Re-run with `polyglot update --yes` to install.")
            return 0
        print(message)
        try:
            answer = input(f"Install GitHub commit {snapshot.commit[:12]} now? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Update cancelled.")
            return 0

    if not args.json:
        print(f"Installing GitHub commit {snapshot.commit[:12]} from branch {snapshot.branch}...")
    try:
        result = install_snapshot(snapshot, capture_output=args.json)
    except OSError as exc:
        report.update({"status": "install_failed", "error": str(exc)})
        if args.json:
            _json(report)
        else:
            print(f"polyglot: GitHub update install failed: {exc}", file=sys.stderr)
        return 2

    report["status"] = "installed" if result.returncode == 0 else "install_failed"
    report["installer_exit_code"] = result.returncode
    if result.returncode == 0:
        try:
            record_installed_snapshot(snapshot)
            report["state_recorded"] = True
        except OSError as exc:
            report["state_recorded"] = False
            report["state_warning"] = str(exc)
    if args.json:
        if result.stdout:
            report["installer_stdout"] = result.stdout
        if result.stderr:
            report["installer_stderr"] = result.stderr
        _json(report)
    elif result.returncode == 0:
        print(f"Installed GitHub commit {snapshot.commit[:12]}. Restart any running PolyglotMaker process to use it.")
        if not report.get("state_recorded", False):
            print(f"Warning: could not save update state: {report.get('state_warning', 'unknown error')}", file=sys.stderr)
    else:
        print(f"polyglot: pip could not install the GitHub update (exit code {result.returncode}).", file=sys.stderr)
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "make":
            return _do_make(args)
        if args.command == "unpack":
            return _do_unpack(args)
        if args.command == "inspect":
            return _do_inspect(args)
        if args.command == "verify":
            return _do_verify(args)
        if args.command == "formats":
            return _do_formats(args)
        if args.command == "doctor":
            return _do_doctor(args)
        if args.command == "serve":
            return _do_serve(args)
        if args.command == "update":
            return _do_update(args)
    except (PolyglotError, ValueError, OSError, KeyError) as exc:
        print(f"polyglot: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
