#!/usr/bin/env python3
"""Repository-root launcher for PolyglotMaker's update command."""

from __future__ import annotations

import sys

from polyglotmaker.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["update", *sys.argv[1:]]))
