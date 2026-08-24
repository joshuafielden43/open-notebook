"""Relative-to-root path storage + containment resolution (#1609).

Any durable path stored in the DB (podcast audio and future artifacts)
must be relative to a DATA_FOLDER sub-root. Absolute and escaping values are
rejected on write and refused on read.

Podcast audio uses :mod:`open_notebook.podcasts.audio_paths` (thin wrappers
around these helpers). New artifact types should call these helpers directly
with their own root rather than inventing absolute-path storage.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union
from urllib.parse import unquote, urlparse


def resolved_root(root: Union[str, Path]) -> Path:
    """Real (symlink-resolved, absolute) path of a storage root."""
    return Path(os.path.realpath(str(root)))


def to_relative_path(
    path: Union[str, Path],
    root: Union[str, Path],
    *,
    kind: str = "file",
) -> str:
    """Convert a generated filesystem path to the DB storage form.

    Accepts absolute, CWD-relative, or ``file://`` paths produced by workers
    and returns a POSIX path relative to ``root``.

    Raises:
        ValueError: if the path resolves outside ``root``.
    """
    raw = str(path)
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    resolved = Path(os.path.realpath(raw))
    base = resolved_root(root)
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError(
            f"Generated {kind} path is outside the storage root: {path}"
        )
    return resolved.relative_to(base).as_posix()


def resolve_contained_path(
    stored: Optional[str],
    root: Union[str, Path],
) -> Optional[Path]:
    """Resolve a stored relative path to a real filesystem path under root.

    Returns ``None`` for empty, absolute, URI, or root-escaping values so
    callers can keep fail-closed 403/404 behaviour.
    """
    if not stored:
        return None
    if "://" in stored:
        return None
    candidate = Path(stored)
    if candidate.is_absolute():
        return None
    base = resolved_root(root)
    resolved = Path(os.path.realpath(base / candidate))
    if resolved == base or not resolved.is_relative_to(base):
        return None
    return resolved
