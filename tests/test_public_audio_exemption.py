"""Auth exemption and filename behavior for the episode-audio route."""

import pytest

from api.auth import is_public_audio_request, public_audio_enabled
from api.routers.podcasts import _episode_download_filename

AUDIO_PATH = "/api/podcasts/episodes/podcast_episode:abc123/audio"


class TestPublicAudioFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("OPEN_NOTEBOOK_PUBLIC_AUDIO", raising=False)
        assert public_audio_enabled() is False
        assert is_public_audio_request("GET", AUDIO_PATH) is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
    def test_enabled_values(self, monkeypatch, value):
        monkeypatch.setenv("OPEN_NOTEBOOK_PUBLIC_AUDIO", value)
        assert public_audio_enabled() is True

    def test_get_and_head_only(self, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_PUBLIC_AUDIO", "true")
        assert is_public_audio_request("GET", AUDIO_PATH) is True
        assert is_public_audio_request("HEAD", AUDIO_PATH) is True
        assert is_public_audio_request("POST", AUDIO_PATH) is False
        assert is_public_audio_request("DELETE", AUDIO_PATH) is False

    def test_audio_path_only(self, monkeypatch):
        monkeypatch.setenv("OPEN_NOTEBOOK_PUBLIC_AUDIO", "true")
        assert is_public_audio_request("GET", "/api/podcasts/episodes") is False
        assert is_public_audio_request("GET", "/api/sources") is False
        assert (
            is_public_audio_request(
                "GET", "/api/podcasts/episodes/x/audio/../../../sources"
            )
            is False
        )
        # Sub-paths beyond /audio stay guarded
        assert is_public_audio_request("GET", AUDIO_PATH + "/extra") is False


class TestDownloadFilename:
    def test_sanitizes_episode_name(self):
        assert (
            _episode_download_filename('My Episode: The "Best" One?', "e:1")
            == "My Episode The Best One.mp3"
        )

    def test_collapses_whitespace(self):
        assert (
            _episode_download_filename("  spaced   out\tname ", "e:1")
            == "spaced out name.mp3"
        )

    def test_fallback_to_id_suffix(self):
        assert (
            _episode_download_filename("///???", "podcast_episode:xyz789")
            == "episode-xyz789.mp3"
        )
        assert _episode_download_filename(None, "e:2") == "episode-2.mp3"
