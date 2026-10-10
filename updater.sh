#!/bin/sh
# Repository-root launcher for PolyglotMaker's update command.
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" >/dev/null 2>&1 && pwd) || {
    echo "updater.sh: could not locate its directory" >&2
    exit 1
}

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/updater.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/updater.py" "$@"
else
    echo "updater.sh: Python 3 is required" >&2
    exit 127
fi
