"""Import all format modules and expose the host registry."""

from __future__ import annotations

# Importing these modules registers every host class. Keep the import list
# explicit so `python -m polyglotmaker formats` is deterministic and easy to
# audit.
from . import images as _images
from . import media as _media
from . import text as _text
from . import archives as _archives
from . import executables as _executables
from . import documents as _documents
from .base import REGISTRY


def get_format(key: str):
    return REGISTRY.get(key)


def list_formats():
    return [fmt.spec for fmt in REGISTRY.all()]


__all__ = ["REGISTRY", "get_format", "list_formats"]
