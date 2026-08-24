"""Single choke point for podcast episode audio file paths (#1030, #1609).

``PodcastEpisode.audio_file`` stores a path RELATIVE to ``PODCASTS_FOLDER``
(e.g. ``episodes/<uuid>/audio/<uuid>.mp3``). Thin wrappers over the shared
contained-path helpers in :mod:`open_notebook.utils.contained_paths`.
"""

from pathlib import Path
from typing import Optional, Union

from open_notebook.config import PODCASTS_FOLDER
from open_notebook.utils.contained_paths import (
    resolve_contained_path,
    resolved_root,
    to_relative_path,
)


def podcasts_root() -> Path:
    """Real (symlink-resolved, absolute) path of the podcasts output root.

    Computed on every call rather than at import time so tests can
    monkeypatch ``PODCASTS_FOLDER`` on this module.
    """
    return resolved_root(PODCASTS_FOLDER)


def to_relative_audio_path(audio_path: Union[str, Path]) -> str:
    """Convert a generated audio file path to the DB storage form.

    Raises:
        ValueError: if the path resolves outside the podcasts root.
    """
    try:
        return to_relative_path(audio_path, PODCASTS_FOLDER, kind="audio file")
    except ValueError as e:
        # Preserve historical message substring used by tests/callers.
        raise ValueError(
            f"Generated audio file path is outside the podcasts folder: {audio_path}"
        ) from e


def resolve_contained_audio_path(audio_file: Optional[str]) -> Optional[Path]:
    """Resolve a stored ``audio_file`` value to a real filesystem path."""
    return resolve_contained_path(audio_file, PODCASTS_FOLDER)
