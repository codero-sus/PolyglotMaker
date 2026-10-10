# Updating PolyglotMaker from GitHub

PolyglotMaker has two GitHub-based update paths:

- **Installed Python package:** `polyglot update` downloads the active commit archive and installs it with the current interpreter's `pip`.
- **Repository checkout:** `updater.py`, `updater.sh`, and `updater.bat` fetch from GitHub and fast-forward the local checkout with Git. This updates the files in the repository itself.

Neither path depends on GitHub Releases or PyPI.

## Installed-package updater

```sh
# Check the GitHub default branch without installing
polyglot update --check

# Choose a branch explicitly
polyglot update --check --branch main

# Review and interactively confirm an install
polyglot update

# Explicitly install without a prompt (for automation)
polyglot update --yes

# Machine-readable check; add --yes to install
polyglot update --json
```

It queries GitHub for the repository's default branch and latest commit, then installs the HTTPS archive pinned to that commit using `python -m pip install --upgrade --force-reinstall`. A successful update records its commit under `~/.polyglotmaker/` for later checks. Restart running PolyglotMaker processes after installing.

## Repository updater scripts

Run from a clean checkout:

```sh
python updater.py --check       # Python entry point
./updater.sh                    # macOS/Linux: fetch and fast-forward
updater.bat                     # Windows command prompt: fetch and fast-forward
```

The scripts update the **currently checked-out local branch** from the matching branch on `https://github.com/codero-sus/PolyglotMaker`. `--branch BRANCH` selects the GitHub branch to fetch; it never switches the local branch. `--check` fetches and reports without changing files, and `--json` prints machine-readable status.

Git must be installed and the working tree must be clean. The updater refuses to overwrite local edits, create merge commits, or resolve divergent histories; it only performs a fast-forward. Commit or stash changes and resolve divergence manually before retrying.

Use `--branch BRANCH` when you intentionally want to fast-forward the current checkout from a different remote branch; the updater still never switches local branches.
