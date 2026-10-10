"""Host format contract.

A *host* is the format that owns offset 0 of the container -- the one that a
double-click, a browser, an image viewer or the OS loader will actually open.
Every host answers three questions:

``render(ctx)``
    Produce a **complete, standalone-valid** file of that format, embedding as
    much of our payload as the format legally allows (PNG private chunks, JPEG
    COM segments, MP4 ``free`` boxes, PE overlay, ZIP members, base64 in JSON...).

``wrap_trailing(trailing)``
    Given the bytes that will be appended *after* the host body, return bytes
    that keep the host valid.  MP4 wraps them in a ``free`` box; most formats
    simply tolerate them and return them untouched.

``parse(data)`` / ``extract_native(data)``
    Structural map for ``inspect`` and payload recovery when the appended
    bundle is missing or damaged.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from ..core.util import Segment

#: how a host can legally carry bytes
EMBED_NONE = "none"          # no spec-defined slot: payload goes to the appended bundle
EMBED_CHUNK = "chunk"        # format has first-class extensible chunks (PNG/RIFF/MP4/ID3/FLAC)
EMBED_COMMENT = "comment"    # format has a comment/text slot (JPEG COM, GIF comment, PDF, RTF)
EMBED_OVERLAY = "overlay"    # loaders ignore trailing bytes (PE/ELF/tar/gz)
EMBED_MEMBERS = "members"    # host *is* an archive: payloads become members (zip/tar/docx/epub)
EMBED_TEXT = "text"          # strict text format: base64 inside a legal token (json/js/py/xml)


@dataclass(frozen=True)
class HostSpec:
    """Static description of a host format."""

    key: str
    label: str
    category: str                      # image|audio|video|document|executable|archive|data
    extension: str                     # default output extension, with dot
    aliases: tuple[str, ...] = ()
    magic: bytes | None = None
    embed: str = EMBED_NONE
    tolerates_prefix: bool = False     # valid if bytes are prepended
    tolerates_suffix: bool = True      # valid if bytes are appended
    store: str = "zip"                 # default payload store: zip|raw|b64|members
    strict: bool = False               # True => nothing may follow the host body
    tier: int = 1                      # 1 full, 2 native-store only, 3 archive-document
    playable: bool = True              # does the generated stub "work" in real apps
    notes: str = ""

    def to_dict(self) -> dict:
        data = {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "extension": self.extension,
            "aliases": list(self.aliases),
            "magic": self.magic.hex() if self.magic else None,
            "embed": self.embed,
            "tolerates_prefix": self.tolerates_prefix,
            "tolerates_suffix": self.tolerates_suffix,
            "store": self.store,
            "strict": self.strict,
            "tier": self.tier,
            "playable": self.playable,
            "notes": self.notes,
        }
        return data


@dataclass
class HostContext:
    """Everything a host needs to render itself."""

    payload: bytes = b""               # bytes to embed natively (manifest or whole bundle)
    payload_role: str = "none"         # none | manifest | bundle
    suffix_len: int = 0                # how many bytes will follow the host body
    metadata: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    rng: random.Random = field(default_factory=lambda: random.Random(0xC0FFEE))
    members: Sequence[Mapping[str, Any]] = ()   # for EMBED_MEMBERS hosts

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def meta(self, key: str, default: Any = "") -> Any:
        return self.metadata.get(key, default)

    def sized(self, width: int, height: int) -> "HostContext":
        return replace(self, options={**self.options, "width": width, "height": height})

    def width(self, default: int = 8) -> int:
        return int(self.opt("width", default))

    def height(self, default: int = 8) -> int:
        return int(self.opt("height", default))


@dataclass
class RenderResult:
    data: bytes
    segments: list[Segment] = field(default_factory=list)
    embedded: bool = False             # was `ctx.payload` stored natively?
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "size": len(self.data),
            "embedded": self.embedded,
            "detail": self.detail,
            "segments": [s.to_dict() for s in self.segments],
        }


class HostFormat(ABC):
    """Base class for every host format generator."""

    spec: HostSpec

    # -- construction ----------------------------------------------------
    @abstractmethod
    def render(self, ctx: HostContext) -> RenderResult:
        """Build a complete, valid file of this format."""

    def wrap_trailing(self, trailing: bytes, ctx: HostContext) -> bytes:
        """Return ``trailing`` (possibly re-wrapped) so the host stays valid."""
        return trailing

    # -- introspection ---------------------------------------------------
    def parse(self, data: bytes) -> list[Segment]:
        """Structural map of an existing file of this format (best effort)."""
        return []

    def extract_native(self, data: bytes) -> bytes | None:
        """Recover the natively embedded payload, if any."""
        return None

    def validate(self, data: bytes) -> tuple[bool, str]:
        """Cheap self-check used by ``polyglot verify`` / ``doctor``."""
        if self.spec.magic and not data.startswith(self.spec.magic):
            return False, f"missing {self.spec.key} magic at offset 0"
        return True, "magic present"

    # -- helpers ---------------------------------------------------------
    def _seg(self, offset: int, length: int, name: str, kind: str = "host", detail: str = "") -> Segment:
        return Segment(name=name, offset=offset, length=length, kind=kind, fmt=self.spec.key, detail=detail)


class Registry:
    """Format registry with alias resolution."""

    def __init__(self) -> None:
        self._formats: dict[str, HostFormat] = {}
        self._aliases: dict[str, str] = {}

    def register(self, fmt: HostFormat) -> HostFormat:
        key = fmt.spec.key
        if key in self._formats:
            raise ValueError(f"duplicate host format {key!r}")
        self._formats[key] = fmt
        for alias in fmt.spec.aliases:
            self._aliases.setdefault(alias, key)
        self._aliases.setdefault(key.lstrip("."), key)
        return fmt

    def canonical(self, key: str) -> str:
        key = (key or "").strip().lower().lstrip(".")
        if key in self._formats:
            return key
        if key in self._aliases:
            return self._aliases[key]
        raise KeyError(key)

    def get(self, key: str) -> HostFormat:
        return self._formats[self.canonical(key)]

    def has(self, key: str) -> bool:
        try:
            self.canonical(key)
            return True
        except KeyError:
            return False

    def keys(self) -> list[str]:
        return sorted(self._formats)

    def all(self) -> list[HostFormat]:
        return [self._formats[k] for k in self.keys()]

    def by_category(self) -> dict[str, list[HostFormat]]:
        out: dict[str, list[HostFormat]] = {}
        for fmt in self.all():
            out.setdefault(fmt.spec.category, []).append(fmt)
        return dict(sorted(out.items()))

    def __len__(self) -> int:
        return len(self._formats)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.has(key)


REGISTRY = Registry()


def register(fmt: HostFormat) -> HostFormat:
    return REGISTRY.register(fmt)
