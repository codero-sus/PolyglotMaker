"""Release checking and opt-in self-updates for PolyglotMaker.

Updates are fetched only from this project's GitHub Releases. Merely checking
for an update never installs code; installation is always confirmed by the user
or explicitly requested with ``--yes``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import __version__
from .core.errors import PolyglotError

GITHUB_REPOSITORY = "codero-sus/PolyglotMaker"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 8
MAX_RELEASE_RESPONSE_BYTES = 1024 * 1024


class UpdateError(PolyglotError):
    """An update check or install could not be completed safely."""


class NoReleaseError(UpdateError):
    """The project has not published a stable GitHub release yet."""


@dataclass(frozen=True)
class Release:
    """Metadata for a validated stable release."""

    version: str
    tag: str
    url: str
    source_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "tag": self.tag,
            "url": self.url,
            "source_url": self.source_url,
        }


_VERSION_RE = re.compile(
    r"^[vV]?(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?:[-._]?)(?P<pre>alpha|a|beta|b|preview|pre|rc|c)(?:[-._]?(?P<pre_num>\d+))?)?"
    r"(?:(?:[-._]?)(?P<post>post|rev|r)(?:[-._]?(?P<post_num>\d+))?)?"
    r"(?:(?:[-._]?)(?P<dev>dev)(?:[-._]?(?P<dev_num>\d+))?)?"
    r"(?:\+[0-9a-z.-]+)?$",
    re.IGNORECASE,
)
_PRE_RELEASE_ORDER = {
    "a": 0,
    "alpha": 0,
    "b": 1,
    "beta": 1,
    "c": 2,
    "rc": 2,
    "pre": 2,
    "preview": 2,
}


def _version_key(value: str) -> tuple[object, ...]:
    """Return an ordering key for common PEP 440 release tags.

    This intentionally supports the numeric, alpha/beta/rc, post, dev, and
    local-version forms used by Python releases. Unknown tag schemes fail
    closed instead of accidentally offering a downgrade.
    """

    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(
            f"cannot compare version {value!r}; use a PEP 440-style release tag such as v1.2.3"
        )

    release = [int(part) for part in match.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()

    pre = match.group("pre")
    dev_num = match.group("dev_num")
    if pre:
        pre_key = (_PRE_RELEASE_ORDER[pre.lower()], int(match.group("pre_num") or 0))
    elif dev_num is not None and not match.group("post"):
        # A dev-only release sorts before alpha/beta/rc releases.
        pre_key = (-1, 0)
    else:
        # A final release sorts after its prereleases.
        pre_key = (3, 0)

    post = match.group("post")
    post_key = (1, int(match.group("post_num") or 0)) if post else (0, 0)
    dev_key = (0, int(dev_num or 0)) if dev_num is not None else (1, 0)
    return tuple(release), pre_key, post_key, dev_key


def compare_versions(left: str, right: str) -> int:
    """Compare two supported release versions (1, 0, or -1)."""

    left_key, right_key = _version_key(left), _version_key(right)
    return (left_key > right_key) - (left_key < right_key)


def _version_from_tag(tag: str) -> str:
    version = tag[1:] if tag[:1].lower() == "v" else tag
    _version_key(version)  # validate before using the value in any install URL
    return version


def _source_archive_url(tag: str) -> str:
    encoded_tag = quote(tag, safe="")
    return f"https://github.com/{GITHUB_REPOSITORY}/archive/refs/tags/{encoded_tag}.zip"


def fetch_latest_release() -> Release:
    """Fetch and validate the latest stable release from GitHub's API."""

    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PolyglotMaker/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RELEASE_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise NoReleaseError(
                f"no stable GitHub release has been published for {GITHUB_REPOSITORY} yet"
            ) from exc
        raise UpdateError(f"GitHub update check failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"could not reach GitHub to check for updates: {reason}") from exc

    if len(raw) > MAX_RELEASE_RESPONSE_BYTES:
        raise UpdateError("GitHub returned an unexpectedly large release response")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release metadata") from exc
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned an invalid release record")

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag or len(tag) > 128:
        raise UpdateError("GitHub release is missing a valid tag name")
    version = _version_from_tag(tag)
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("GitHub's latest release endpoint returned a draft or prerelease")

    tag_path = quote(tag, safe="")
    release_url = f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{tag_path}"
    return Release(
        version=version,
        tag=tag,
        url=release_url,
        source_url=_source_archive_url(tag),
    )


def install_release(release: Release, *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    """Install the selected release with the active Python interpreter's pip."""

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-input",
        "--disable-pip-version-check",
        release.source_url,
    ]
    return subprocess.run(command, check=False, capture_output=capture_output, text=True)
