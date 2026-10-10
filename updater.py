#!/usr/bin/env python3
"""Fast-forward this checkout to its latest commit on GitHub."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from polyglotmaker.update import UpdateError, update_repository_checkout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update this clean Git checkout from the matching GitHub branch."
    )
    parser.add_argument(
        "--branch",
        help="target branch (default: the currently checked-out branch)",
    )
    parser.add_argument("--check", action="store_true", help="fetch and report only; do not update files")
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args(argv)

    try:
        result = update_repository_checkout(
            Path(__file__).resolve().parent,
            branch=args.branch,
            check_only=args.check,
        )
    except UpdateError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"updater: {exc}", file=sys.stderr)
        return 2

    if result.updated:
        status = "updated"
        message = f"Updated {result.branch} from {result.previous_commit[:12]} to {result.latest_commit[:12]}."
    elif result.update_available:
        status = "update_available"
        message = f"GitHub has {result.branch}@{result.latest_commit[:12]}; run updater.py to fast-forward."
    else:
        status = "up_to_date"
        message = f"This checkout already contains the latest GitHub commit for {result.branch}."

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
