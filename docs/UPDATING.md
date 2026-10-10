# Updating PolyglotMaker from GitHub

Every updater in this project is pinned to one source branch: **`arena/a02bfaaf-polyglotmaker`**. It does not follow `main`, another branch, GitHub Releases, or PyPI.

There are two GitHub update paths:

- **Installed Python package:** `polyglot update` downloads a commit-pinned archive from the fixed branch and installs it with the selected Python's `pip`.
- **Repository checkout:** `updater.py`, `updater.sh`, and `updater.bat` fetch that same fixed branch and fast-forward the local checkout with Git.

## Python selection

Interpreter selection is shared by the CLI and root launchers, in this order:

1. Environment variable `2PY2` (the name means “second Python”; it does **not** mean Python 2).
2. The first active line in the project-root `python.env` file.
3. The platform's Python 3 default (`python3` on macOS/Linux, `py -3` on Windows, with a `python` fallback).

`python.env` is optional and ignored by Git. Set it to the executable path; paths may contain spaces, but do not surround them with quotes. A `PYTHON=...` or `2PY2=...` assignment is also accepted. Relative paths resolve from the project root.

```text
# python.env examples — use one active line
.venv/bin/python
# or on Windows: C:\PortablePython\python.exe
# or: PYTHON=/absolute/path/to/python
```

The selected interpreter must be Python 3.10 or newer. Portable or embeddable Python executables can be selected the same way; an embeddable interpreter needs `pip` installed for the installed-package updater, while the checkout updater does not use `pip`. `python.env.example` is a starter template. The environment variable takes precedence over the file. On POSIX shells, a digit-leading name cannot be assigned with normal shell syntax, so pass it for a command with `env '2PY2=/path/to/python' polyglot update --check` or `env '2PY2=/path/to/python' ./updater.sh`. On Windows, use `set "2PY2=C:\PortablePython\python.exe"` before running `updater.bat`.

## Installed-package updater

```sh
polyglot update --check       # check the pinned GitHub branch
polyglot update               # prompt before installing
polyglot update --yes         # install without prompting
polyglot update --json        # machine-readable check; add --yes to install
```

The updater verifies the GitHub commit SHA, then runs `-m pip install --upgrade --force-reinstall` using the selected Python. After a successful install it records the commit in `~/.polyglotmaker/` for later checks. Restart running PolyglotMaker processes after installation.

## Repository updater scripts

Run from a clean Git checkout:

```sh
python updater.py --check       # check only
./updater.sh                    # macOS/Linux: fetch and fast-forward
updater.bat                     # Windows command prompt: fetch and fast-forward
```

These scripts always fetch `refs/heads/arena/a02bfaaf-polyglotmaker` from the official GitHub repository. They never switch branches, overwrite local edits, create merge commits, or resolve divergent histories. The local checkout must have a clean working tree and Git installed. `--json` prints machine-readable status.
