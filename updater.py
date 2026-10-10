#!/usr/bin/env python3
"""Fast-forward this checkout to the updater branch hosted on GitHub."""

# Keep this small bootstrap free of modern annotations so it can switch to a
# configured portable Python before importing the Python 3.10+ application.
import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def _clean_setting(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _find_on_path(command):
    locate = getattr(shutil, "which", None)
    if locate:
        return locate(command)

    extensions = [""]
    if os.name == "nt" and not os.path.splitext(command)[1]:
        extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        for extension in extensions:
            candidate = os.path.join(directory or os.curdir, command + extension)
            if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return candidate
    return None


def _configured_python():
    selected = _clean_setting(os.environ.get("2PY2", ""))
    if not selected:
        config_path = os.path.join(ROOT, "python.env")
        if os.path.isfile(config_path):
            try:
                with io.open(config_path, "r", encoding="utf-8") as config:
                    for raw_line in config:
                        value = raw_line.strip()
                        if not value or value.startswith("#"):
                            continue
                        if "=" in value:
                            key, candidate = value.split("=", 1)
                            if key.strip().upper() in ("PYTHON", "PYTHON_EXE", "PYTHON_PATH", "2PY2"):
                                value = candidate.strip()
                        selected = _clean_setting(value)
                        if selected:
                            break
            except (IOError, UnicodeError) as exc:
                sys.stderr.write("updater: could not read python.env: %s\n" % exc)
                raise SystemExit(2)

    if not selected:
        return None

    candidate = os.path.expanduser(selected)
    if not os.path.isabs(candidate):
        project_candidate = os.path.join(ROOT, candidate)
        if os.path.isfile(project_candidate):
            candidate = project_candidate
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)

    resolved = _find_on_path(selected)
    if resolved:
        return os.path.abspath(resolved)
    sys.stderr.write("updater: configured Python executable was not found: %s\n" % selected)
    raise SystemExit(2)


def _switch_to_configured_python():
    selected = _configured_python()
    if not selected:
        return
    current = os.path.normcase(os.path.realpath(sys.executable))
    target = os.path.normcase(os.path.realpath(selected))
    if current == target:
        return
    try:
        os.execv(selected, [selected, os.path.abspath(__file__)] + sys.argv[1:])
    except OSError as exc:
        sys.stderr.write("updater: could not start configured Python %s: %s\n" % (selected, exc))
        raise SystemExit(2)


_switch_to_configured_python()
if sys.version_info < (3, 10):
    sys.stderr.write("updater: Python 3.10 or newer is required\n")
    raise SystemExit(2)

# Embeddable Python may use an isolated sys.path; make the checkout importable.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import argparse
import json
from pathlib import Path

from polyglotmaker.update import UpdateError, update_repository_checkout


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Update this clean checkout from the fixed GitHub updater branch."
    )
    parser.add_argument("--check", action="store_true", help="fetch and report only; do not update files")
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args(argv)

    try:
        result = update_repository_checkout(Path(__file__).resolve().parent, check_only=args.check)
    except UpdateError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print("updater: %s" % exc, file=sys.stderr)
        return 2

    if result.updated:
        status = "updated"
        message = "Updated %s from %s to %s." % (
            result.branch,
            result.previous_commit[:12],
            result.latest_commit[:12],
        )
    elif result.update_available:
        status = "update_available"
        message = "GitHub has %s@%s; run updater.py to fast-forward." % (
            result.branch,
            result.latest_commit[:12],
        )
    else:
        status = "up_to_date"
        message = "This checkout already contains the latest GitHub commit for %s." % result.branch

    report = {
        "status": status,
        "branch": result.branch,
        "previous_commit": result.previous_commit,
        "latest_commit": result.latest_commit,
        "updated": result.updated,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
