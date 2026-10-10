#!/bin/sh
# GitHub checkout updater for macOS and Linux.
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" >/dev/null 2>&1 && pwd) || {
    echo "updater.sh: could not locate its directory" >&2
    exit 1
}

# A digit-leading environment variable is accessed through env rather than
# shell parameter expansion (for example, ${2PY2} is invalid in POSIX shells).
PYTHON_EXE=$(env | sed -n 's/^2PY2=//p' | sed -n '1p')
# dash may discard digit-leading names while importing its environment. On Linux,
# recover the original value from procfs if the shell's environment no longer has it.
if [ -z "$PYTHON_EXE" ] && [ -r "/proc/$$/environ" ]; then
    PYTHON_EXE=$(tr '\000' '\n' < "/proc/$$/environ" | sed -n 's/^2PY2=//p' | sed -n '1p')
fi

if [ -z "$PYTHON_EXE" ] && [ -f "$SCRIPT_DIR/python.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(printf '%s\n' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case "$line" in
            ''|\#*) continue ;;
        esac
        case "$line" in
            PYTHON=*|PYTHON_EXE=*|PYTHON_PATH=*|2PY2=*) line=${line#*=} ;;
        esac
        line=$(printf '%s\n' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case "$line" in
            \"*\") line=${line#\"}; line=${line%\"} ;;
        esac
        if [ -n "$line" ]; then
            PYTHON_EXE=$line
            break
        fi
    done < "$SCRIPT_DIR/python.env"
fi

if [ -z "$PYTHON_EXE" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_EXE=$(command -v python3)
    elif command -v python >/dev/null 2>&1; then
        PYTHON_EXE=$(command -v python)
    else
        echo "updater.sh: Python 3.10 or newer is required" >&2
        exit 127
    fi
fi

case "$PYTHON_EXE" in
    '~/'*) PYTHON_EXE="$HOME/${PYTHON_EXE#\~/}" ;;
esac
case "$PYTHON_EXE" in
    /*) ;;
    *)
        if [ -f "$SCRIPT_DIR/$PYTHON_EXE" ]; then
            PYTHON_EXE="$SCRIPT_DIR/$PYTHON_EXE"
        else
            RESOLVED=$(command -v "$PYTHON_EXE" 2>/dev/null || true)
            if [ -n "$RESOLVED" ]; then PYTHON_EXE=$RESOLVED; fi
        fi
        ;;
esac

if [ ! -x "$PYTHON_EXE" ]; then
    echo "updater.sh: Python executable not found or not executable: $PYTHON_EXE" >&2
    exit 127
fi
if ! "$PYTHON_EXE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "updater.sh: Python 3.10 or newer is required (selected: $PYTHON_EXE)" >&2
    exit 2
fi

exec "$PYTHON_EXE" "$SCRIPT_DIR/updater.py" "$@"
