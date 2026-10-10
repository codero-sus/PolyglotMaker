"""Check and install the latest PolyglotMaker source directly from GitHub.

The updater follows one fixed GitHub branch, pins installation to the commit
fetched from GitHub, and records that commit locally after a successful install.
It does not depend on GitHub Releases being published.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import __version__
from .core.errors import PolyglotError

GITHUB_REPOSITORY = "codero-sus/PolyglotMaker"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
GITHUB_CLONE_URL = f"https://github.com/{GITHUB_REPOSITORY}.git"
GITHUB_UPDATE_BRANCH = "arena/a02bfaaf-polyglotmaker"
REQUEST_TIMEOUT_SECONDS = 8
MAX_GITHUB_RESPONSE_BYTES = 1024 * 1024
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpdateError(PolyglotError):
    """A GitHub update check or install could not be completed safely."""


def _strip_path_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _python_setting(root: Path) -> str | None:
    override = os.environ.get("2PY2", "").strip()
    if override:
        return _strip_path_quotes(override)

    config = root / "python.env"
    if not config.is_file():
        return None
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise UpdateError(f"could not read {config}: {exc}") from exc
    for raw_line in lines:
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if "=" in value:
            key, candidate = value.split("=", 1)
            if key.strip().upper() in {"PYTHON", "PYTHON_EXE", "PYTHON_PATH", "2PY2"}:
                value = candidate.strip()
        value = _strip_path_quotes(value)
        if value:
            return value
    return None


def configured_python(root: str | os.PathLike[str] | None = None) -> str:
    """Resolve the interpreter override (``2PY2`` or root ``python.env``)."""

    roots = [Path(root).resolve()] if root is not None else [Path.cwd().resolve()]
    module_root = Path(__file__).resolve().parents[1]
    if module_root not in roots:
        roots.append(module_root)

    selected = None
    for candidate_root in roots:
        selected = _python_setting(candidate_root)
        if selected:
            break
    if not selected:
        return sys.executable

    candidate = Path(selected).expanduser()
    if not candidate.is_absolute():
        for candidate_root in roots:
            relative_path = candidate_root / candidate
            if relative_path.is_file():
                candidate = relative_path
                break
    if candidate.is_file():
        executable = str(candidate.absolute())
    else:
        executable = shutil.which(selected) or ""
    if not executable:
        raise UpdateError(f"configured Python executable was not found: {selected}")

    try:
        probe = subprocess.run(
            [executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"could not start configured Python {executable!r}: {exc}") from exc
    if probe.returncode != 0:
        raise UpdateError(f"configured Python failed its version check: {executable}")
    try:
        version = tuple(int(part) for part in probe.stdout.strip().split(".", 1))
    except ValueError as exc:
        raise UpdateError(f"configured Python returned an invalid version: {probe.stdout.strip()!r}") from exc
    if len(version) != 2 or version < (3, 10):
        raise UpdateError(f"configured Python must be 3.10 or newer (found {probe.stdout.strip()})")
    return executable


@dataclass(frozen=True)
class GitHubSnapshot:
    """A commit-pinned snapshot from a GitHub branch."""

    branch: str
    commit: str
    url: str
    source_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "branch": self.branch,
            "commit": self.commit,
            "url": self.url,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class CheckoutUpdate:
    """Result of fast-forwarding a local Git checkout from GitHub."""

    branch: str
    previous_commit: str
    latest_commit: str
    update_available: bool
    updated: bool


def _github_json(url: str) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PolyglotMaker/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("GitHub could not find the repository or branch requested") from exc
        raise UpdateError(f"GitHub update check failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"could not reach GitHub to check for updates: {reason}") from exc

    if len(raw) > MAX_GITHUB_RESPONSE_BYTES:
        raise UpdateError("GitHub returned an unexpectedly large update response")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid update metadata") from exc


def fetch_latest_snapshot() -> GitHubSnapshot:
    """Resolve the pinned updater branch to its latest commit."""

    branch_path = quote(GITHUB_UPDATE_BRANCH, safe="")
    commit_payload = _github_json(f"{GITHUB_API}/commits/{branch_path}")
    if not isinstance(commit_payload, dict):
        raise UpdateError("GitHub returned invalid branch commit metadata")
    commit = commit_payload.get("sha")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise UpdateError("GitHub returned an invalid commit identifier")
    commit = commit.lower()
    return GitHubSnapshot(
        branch=GITHUB_UPDATE_BRANCH,
        commit=commit,
        url=f"https://github.com/{GITHUB_REPOSITORY}/commit/{commit}",
        source_url=f"https://github.com/{GITHUB_REPOSITORY}/archive/{commit}.zip",
    )


def _local_checkout() -> tuple[Path, str] | None:
    """Return this checkout's HEAD when the package is running from a Git repo."""

    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=False, capture_output=True, text=True
        )
    except OSError:
        return None
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not _COMMIT_RE.fullmatch(commit):
        return None
    return root, commit


def _state_path() -> Path:
    return Path.home() / ".polyglotmaker" / "github-update-state.json"


def _python_identity() -> str:
    return os.path.normcase(os.path.abspath(configured_python()))


def is_snapshot_installed(snapshot: GitHubSnapshot) -> bool:
    """Check Git ancestry or the per-user marker written after a GitHub update."""

    checkout = _local_checkout()
    if checkout:
        root, local_commit = checkout
        if local_commit == snapshot.commit:
            return True
        # A development checkout may already contain the pinned updater commit
        # while being on a newer feature branch. Avoid offering a downgrade.
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", snapshot.commit, local_commit],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    try:
        marker = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("repository") == GITHUB_REPOSITORY
        and marker.get("branch") == snapshot.branch
        and marker.get("commit") == snapshot.commit
        and marker.get("python_executable") == _python_identity()
    )


def record_installed_snapshot(snapshot: GitHubSnapshot) -> None:
    """Remember a successfully installed GitHub commit for future checks."""

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    payload = {
        "repository": GITHUB_REPOSITORY,
        "branch": snapshot.branch,
        "commit": snapshot.commit,
        "python_executable": _python_identity(),
    }
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def install_snapshot(
    snapshot: GitHubSnapshot, *, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """Install a commit-pinned GitHub archive with the selected Python's pip."""

    command = [
        configured_python(),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "--no-input",
        "--disable-pip-version-check",
        snapshot.source_url,
    ]
    return subprocess.run(command, check=False, capture_output=capture_output, text=True)


def _git_result(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=False, capture_output=True, text=True
        )
    except OSError as exc:
        raise UpdateError(f"could not run Git: {exc}") from exc


def _git_output(root: Path, *args: str) -> str:
    result = _git_result(root, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise UpdateError(f"Git command failed: {detail}")
    return result.stdout.strip()


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = _git_result(root, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or "commit history is unavailable"
    raise UpdateError(f"could not compare local and GitHub history: {detail}")


def update_repository_checkout(
    repo_root: str | os.PathLike[str], *, check_only: bool = False
) -> CheckoutUpdate:
    """Fetch a GitHub branch and optionally fast-forward this clean checkout.

    The updater never switches branches, overwrites local changes, or performs
    a merge commit. It refuses dirty or diverged checkouts.
    """

    root = Path(repo_root).resolve()
    top_level = Path(_git_output(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise UpdateError(f"run the updater from the repository root ({top_level})")

    status = _git_output(root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise UpdateError("working tree has uncommitted changes; commit or stash them before updating")

    current_branch = _git_output(root, "branch", "--show-current")
    if not current_branch:
        raise UpdateError("cannot update a detached HEAD; check out a branch first")
    target_branch = GITHUB_UPDATE_BRANCH
    fetch = _git_result(
        root,
        "fetch",
        "--no-tags",
        GITHUB_CLONE_URL,
        f"refs/heads/{target_branch}",
    )
    if fetch.returncode != 0:
        detail = fetch.stderr.strip() or fetch.stdout.strip() or "unknown Git fetch error"
        raise UpdateError(f"could not fetch GitHub branch {target_branch!r}: {detail}")

    previous_commit = _git_output(root, "rev-parse", "--verify", "HEAD^{commit}").lower()
    latest_commit = _git_output(root, "rev-parse", "--verify", "FETCH_HEAD^{commit}").lower()
    if not _COMMIT_RE.fullmatch(previous_commit) or not _COMMIT_RE.fullmatch(latest_commit):
        raise UpdateError("Git returned an invalid commit identifier")

    if previous_commit == latest_commit or _is_ancestor(root, latest_commit, previous_commit):
        return CheckoutUpdate(target_branch, previous_commit, latest_commit, False, False)
    if not _is_ancestor(root, previous_commit, latest_commit):
        raise UpdateError(
            "local and GitHub histories have diverged; update manually after reviewing the changes"
        )
    if check_only:
        return CheckoutUpdate(target_branch, previous_commit, latest_commit, True, False)

    merge = _git_result(root, "merge", "--ff-only", "FETCH_HEAD")
    if merge.returncode != 0:
        detail = merge.stderr.strip() or merge.stdout.strip() or "unknown Git merge error"
        raise UpdateError(f"could not fast-forward from GitHub: {detail}")
    final_commit = _git_output(root, "rev-parse", "--verify", "HEAD^{commit}").lower()
    return CheckoutUpdate(target_branch, previous_commit, final_commit, False, final_commit != previous_commit)
