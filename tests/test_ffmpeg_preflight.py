"""ffmpeg preflight before TTS spend."""

from open_notebook.podcasts.generation import (
    AudioQualityError,
    require_ffmpeg_for_publish,
)


def test_require_ffmpeg_raises_when_missing(monkeypatch):
    monkeypatch.setattr(
        "open_notebook.podcasts.generation.ffmpeg_available",
        lambda: False,
    )
    try:
        require_ffmpeg_for_publish()
        assert False, "expected AudioQualityError"
    except AudioQualityError as e:
        assert "ffmpeg" in str(e).lower()


def test_require_ffmpeg_ok_when_present(monkeypatch):
    monkeypatch.setattr(
        "open_notebook.podcasts.generation.ffmpeg_available",
        lambda: True,
    )
    require_ffmpeg_for_publish()  # no raise
