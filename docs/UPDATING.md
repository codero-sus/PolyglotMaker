# Updating PolyglotMaker

PolyglotMaker includes an opt-in CLI updater that checks the project's latest **stable GitHub Release** and, when requested, installs its source archive with the active Python interpreter's `pip`.

```sh
# Check availability without changing the installation
polyglot update --check

# Review and interactively confirm an update
polyglot update

# Explicitly install without a prompt (useful in automation)
polyglot update --yes

# Machine-readable check; add --yes to install and return installer output as JSON
polyglot update --json
```

Repository-root launchers are also provided for environments where invoking the installed CLI is inconvenient:

```sh
python updater.py --check       # cross-platform Python entry point
./updater.sh --check            # macOS/Linux shell launcher
updater.bat --check             # Windows command prompt launcher
```

The launchers forward all arguments to `polyglot update`; use `--yes` to explicitly install from automation.

A non-interactive `polyglot update` only reports the available update; it never installs unless `--yes` is explicitly passed. `--check` and `--yes` are mutually exclusive. The command uses HTTPS to query the fixed repository `codero-sus/PolyglotMaker`; it does not run a web endpoint or automatically update the browser server. Restart running PolyglotMaker processes after installing.

## Release requirements

The updater follows the latest non-draft, non-prerelease entry returned by GitHub's Releases API. A release needs a tag in a version format the updater can compare (for example `v0.2.0`, or a PEP 440 alpha/beta/RC/post/dev version). The updater compares versions before offering installation, so it won't downgrade a newer installed version. If there are no stable releases, it reports that status without failing or changing the installation. No stable release is currently published, so checks report `no_release` until the first stable GitHub Release is created.

The update is installed from the official tag archive using `python -m pip install --upgrade`. As with any package update, this downloads and installs code; only accept updates from the trusted repository, and follow your environment's Python package-management policy. If pip reports that the environment is externally managed, use the environment's documented update method or reinstall in a virtual environment.

## Maintainer checklist

1. Bump `project.version` in `pyproject.toml` and `__version__` in `polyglotmaker/__init__.py` to the same value.
2. Tag the release with the matching version (a leading `v` is accepted), then publish it as a stable GitHub Release.
3. Confirm its source archive contains `pyproject.toml` at the repository root and run `polyglot update --check` against an installed prior version.
