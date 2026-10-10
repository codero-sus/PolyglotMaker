"""Exception hierarchy for PolyglotMaker."""

from __future__ import annotations


class PolyglotError(Exception):
    """Base class for every error raised by PolyglotMaker."""


class UsageError(PolyglotError):
    """The user asked for something contradictory or impossible."""


class UnsupportedFormatError(UsageError):
    """A requested host/layer format is unknown or cannot be generated."""

    def __init__(self, key: str, kind: str = "format", known: list[str] | None = None):
        self.key = key
        self.kind = kind
        self.known = known or []
        msg = f"unknown {kind} {key!r}"
        if known:
            msg += f" (known: {', '.join(known)})"
        super().__init__(msg)


class IncompatibleCombinationError(UsageError):
    """Two requested options cannot be combined into a valid file."""


class NotAPolyglotError(PolyglotError):
    """The given file does not look like a PolyglotMaker container."""


class CorruptContainerError(PolyglotError):
    """A container was recognised but its data is damaged."""


class IntegrityError(PolyglotError):
    """A payload failed its checksum / size verification."""


class ObfuscationKeyError(PolyglotError):
    """A wrong (or missing) key was supplied for an obfuscated container."""
