from pathlib import Path
from unittest.mock import patch

import pytest

from open_notebook.podcasts.generation import (
    MIN_FINAL_AUDIO_BYTES,
    AudioQualityError,
    TranscriptQualityError,
    audio_file_quality_issues,
    create_podcast_with_quality_retry,
    derive_generation_stage,
)
from open_notebook.podcasts.quality import transcript_quality_issues

OUTLINE = {
    "segments": [
        {
            "name": "Specialize the stages",
            "description": (
                "Prompt chaining, model routing, outline critique, and a final "
                "writer instead of one oversized prompt."
            ),
        },
        {
            "name": "Close the quality loop",
            "description": (
                "An evaluator optimizer loop with explicit criteria, feedback, "
                "a current draft, and a finished signal."
            ),
        },
        {
            "name": "Operate the shorts factory",
            "description": (
                "Production fan out, image and video jobs, polling, aggregation, "
                "distribution, quotas, and unit costs."
            ),
        },
    ]
}


def _dialogue(text):
    return {"speaker": "Professor Sarah Kim", "dialogue": text}


def _repetitive_transcript():
    return [
        _dialogue(
            "Prompt chaining sends an outline to critique and then to the final "
            "writer. Model routing keeps each stage narrow and debuggable."
        ),
        _dialogue(
            "The evaluator optimizer loop checks explicit criteria, returns "
            "feedback, and revises the current draft until finished."
        ),
        _dialogue(
            "The shorts factory fans out image and video jobs, polls them, "
            "aggregates the result, and pays the distribution cost."
        ),
        _dialogue(
            "Prompt chaining and model routing are the boring correct answer: "
            "outline, critique, and only then the writer."
        ),
        _dialogue(
            "An evaluator optimizer repeats feedback against the latest draft "
            "until the acceptance criteria produce the finished signal."
        ),
        _dialogue(
            "Image and video generation fan out again, then polling and "
            "aggregation feed the distribution systems and their quotas."
        ),
    ]


def _distinct_transcript():
    return [
        _dialogue(
            "Prompt chaining moves one clean artifact from outline to critique "
            "and then to the final writer, which makes model routing debuggable."
        ),
        _dialogue(
            "The evaluator optimizer then owns quality: explicit criteria create "
            "feedback for the current draft until it emits finished."
        ),
        _dialogue(
            "At production scale, image and video jobs fan out, polling gathers "
            "them, and aggregation hands one result to distribution."
        ),
    ]


def test_rejects_live_failure_shape_that_restarts_the_outline():
    issues = transcript_quality_issues(
        "Aim for 60 to 80 words total across the whole episode.",
        OUTLINE,
        _repetitive_transcript(),
    )

    assert any("restarts earlier outline segments" in issue for issue in issues)


def test_accepts_distinct_segments_within_budget():
    assert (
        transcript_quality_issues(
            "Aim for 50 to 100 words total across the whole episode.",
            OUTLINE,
            _distinct_transcript(),
        )
        == []
    )


def test_empty_dialogue_is_rejected():
    assert transcript_quality_issues("Aim for 50 to 100 words.", OUTLINE, []) == [
        "transcript contains no dialogue"
    ]


def test_audio_error_string_is_rejected():
    assert audio_file_quality_issues("ERROR: ffmpeg failed")[0].startswith(
        "audio combination failed"
    )


def test_tiny_audio_file_is_rejected(tmp_path: Path):
    tiny = tmp_path / "silent.mp3"
    tiny.write_bytes(b"\x00" * 100)
    issues = audio_file_quality_issues(tiny)
    assert any("too small" in issue for issue in issues)


def test_healthy_audio_file_is_accepted(tmp_path: Path):
    """Real non-silent speech should pass size + ffmpeg volume checks."""
    import subprocess

    audio = tmp_path / "ok.mp3"
    # 1kHz tone for 3s — not silence, long enough for duration floor.
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=3",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(audio),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not audio.is_file():
        pytest.skip("ffmpeg cannot encode test tone")
    assert audio_file_quality_issues(audio) == []


def test_near_silent_audio_is_rejected(tmp_path: Path):
    import subprocess

    audio = tmp_path / "silent.mp3"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            "3",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(audio),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not audio.is_file():
        pytest.skip("ffmpeg cannot encode silent test audio")
    issues = audio_file_quality_issues(audio)
    assert any("near-silent" in issue or "too small" in issue for issue in issues)


def test_derive_generation_stage_pipeline():
    assert (
        derive_generation_stage(
            job_status="pending",
            has_outline=False,
            has_transcript=False,
            has_audio=False,
        )
        == "queued"
    )
    assert (
        derive_generation_stage(
            job_status="running",
            has_outline=True,
            has_transcript=False,
            has_audio=False,
        )
        == "transcript"
    )
    assert (
        derive_generation_stage(
            job_status="running",
            has_outline=True,
            has_transcript=True,
            has_audio=False,
        )
        == "tts"
    )
    assert (
        derive_generation_stage(
            job_status="completed",
            has_outline=True,
            has_transcript=True,
            has_audio=True,
        )
        == "completed"
    )


@pytest.mark.asyncio
async def test_quality_failure_is_repaired_once_without_human_intervention(
    tmp_path: Path,
):
    import subprocess

    briefings = []
    audio = tmp_path / "ok.mp3"
    enc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(audio),
        ],
        capture_output=True,
        check=False,
    )
    if enc.returncode != 0:
        pytest.skip("ffmpeg cannot encode test tone")

    async def fake_run(*, briefing, create_kwargs, progress_callback):
        briefings.append(briefing)
        transcript = (
            _repetitive_transcript() if len(briefings) == 1 else _distinct_transcript()
        )
        if progress_callback:
            await progress_callback("outline", OUTLINE)
            await progress_callback("transcript", transcript)
        issues = transcript_quality_issues(briefing, OUTLINE, transcript)
        if issues:
            raise TranscriptQualityError(issues)
        return {
            "outline": OUTLINE,
            "transcript": transcript,
            "final_output_file_path": str(audio),
        }

    with patch(
        "open_notebook.podcasts.generation._run_podcast_until_quality",
        new=fake_run,
    ):
        result, effective_briefing = await create_podcast_with_quality_retry(
            briefing="Aim for 50 to 100 words total across the whole episode.",
            create_kwargs={"content": "source"},
        )

    assert result["transcript"] == _distinct_transcript()
    assert len(briefings) == 2
    assert "AUTOMATIC QUALITY REPAIR" in briefings[1]
    assert effective_briefing == briefings[1]


def test_unverified_audio_fails_closed_when_probe_returns_nothing(tmp_path: Path):
    """Garbage bytes that pass the size floor must not publish without ffmpeg verify."""
    audio = tmp_path / "garbage.mp3"
    audio.write_bytes(b"\x01" * (MIN_FINAL_AUDIO_BYTES + 100))
    with patch(
        "open_notebook.podcasts.generation._probe_ffmpeg_audio",
        return_value=(None, None),
    ):
        issues = audio_file_quality_issues(audio)
    assert any("could not verify" in issue for issue in issues)


@pytest.mark.asyncio
async def test_permanent_quality_failure_raises_after_two_attempts():
    async def always_bad(*, briefing, create_kwargs, progress_callback):
        if progress_callback:
            await progress_callback("outline", OUTLINE)
            await progress_callback("transcript", _repetitive_transcript())
        raise TranscriptQualityError(
            transcript_quality_issues(briefing, OUTLINE, _repetitive_transcript())
        )

    with patch(
        "open_notebook.podcasts.generation._run_podcast_until_quality",
        new=always_bad,
    ):
        with pytest.raises(ValueError, match="automatic quality checks"):
            await create_podcast_with_quality_retry(
                briefing="Aim for 50 to 100 words total across the whole episode.",
                create_kwargs={"content": "source"},
            )


@pytest.mark.asyncio
async def test_mute_audio_fails_closed(tmp_path: Path):
    tiny = tmp_path / "mute.mp3"
    tiny.write_bytes(b"\x00" * 64)

    async def ok_transcript(*, briefing, create_kwargs, progress_callback):
        if progress_callback:
            await progress_callback("outline", OUTLINE)
            await progress_callback("transcript", _distinct_transcript())
        return {
            "outline": OUTLINE,
            "transcript": _distinct_transcript(),
            "final_output_file_path": str(tiny),
        }

    with patch(
        "open_notebook.podcasts.generation._run_podcast_until_quality",
        new=ok_transcript,
    ):
        with pytest.raises(AudioQualityError):
            await create_podcast_with_quality_retry(
                briefing="Aim for 50 to 100 words total across the whole episode.",
                create_kwargs={"content": "source"},
            )


@pytest.mark.asyncio
async def test_error_string_audio_path_fails_closed():
    async def ok_transcript(*, briefing, create_kwargs, progress_callback):
        return {
            "outline": OUTLINE,
            "transcript": _distinct_transcript(),
            "final_output_file_path": "ERROR: combine failed",
        }

    with patch(
        "open_notebook.podcasts.generation._run_podcast_until_quality",
        new=ok_transcript,
    ):
        with pytest.raises(AudioQualityError, match="audio combination failed"):
            await create_podcast_with_quality_retry(
                briefing="Aim for 50 to 100 words total across the whole episode.",
                create_kwargs={"content": "source"},
            )


@pytest.mark.asyncio
async def test_quality_fail_never_invokes_tts_node():
    """Integration: when transcript fails quality, generate_all_audio_node is not called."""
    tts_calls = []

    async def fake_outline(state, config):
        return {"outline": OUTLINE}

    async def fake_transcript(state, config):
        return {"transcript": _repetitive_transcript()}

    async def fake_tts(state, config):
        tts_calls.append(True)
        return {"audio_clips": []}

    async def fake_combine(state, config):
        return {"final_output_file_path": None}

    class _Ep:
        outline_provider = "openai"
        outline_model = "m"
        transcript_provider = "openai"
        transcript_model = "m"
        num_segments = 3
        outline_config = None
        transcript_config = None
        language = None
        speaker_config = "speakers"

    class _Sp:
        def get_speaker_names(self):
            return ["Host"]

    with (
        patch(
            "podcast_creator.nodes.generate_outline_node",
            new=fake_outline,
        ),
        patch(
            "podcast_creator.nodes.generate_transcript_node",
            new=fake_transcript,
        ),
        patch(
            "podcast_creator.nodes.generate_all_audio_node",
            new=fake_tts,
        ),
        patch(
            "podcast_creator.nodes.combine_audio_node",
            new=fake_combine,
        ),
    ):
        from open_notebook.podcasts.generation import _run_staged_podcast

        with pytest.raises(TranscriptQualityError):
            await _run_staged_podcast(
                briefing="Aim for 50 to 100 words total across the whole episode.",
                create_kwargs={
                    "content": "source",
                    "episode_name": "ep",
                    "output_dir": "/tmp/podcast-test-out",
                    "speaker_config": "speakers",
                    "episode_profile": "ep",
                    "_resolved_episode": _Ep(),
                    "_resolved_speaker": _Sp(),
                    "outline_provider": "openai",
                    "outline_model": "m",
                    "transcript_provider": "openai",
                    "transcript_model": "m",
                    "num_segments": 3,
                },
                progress_callback=None,
            )

    assert tts_calls == []


def _routing_line():
    return _dialogue(
        "Prompt chaining and model routing hand the outline to the critique "
        "stage before the final writer touches it."
    )


def test_single_backreference_is_not_a_restart():
    """Regression (episode:vwcw5u3y...): a clean 1-2-3 arc with ONE line citing
    segment-1 vocabulary mid-segment-3, then resuming, must pass — three
    structurally clean transcripts were rejected for exactly this."""
    transcript = list(_distinct_transcript())
    # Mid segment-3 cross-reference back to segment 1, then segment 3 resumes.
    transcript.insert(2, _routing_line())
    transcript.append(
        _dialogue(
            "Distribution quotas and unit costs stay the aggregation story: "
            "polling image and video jobs at production scale."
        )
    )
    issues = transcript_quality_issues(
        "Aim for 50 to 200 words total across the whole episode.",
        OUTLINE,
        transcript,
    )
    assert not any("restarts earlier" in issue for issue in issues)


def test_closer_callback_is_not_a_restart():
    """A final-line callback to the opening (briefing-mandated landing) has no
    continuation evidence and must pass."""
    transcript = list(_distinct_transcript())
    transcript.append(_routing_line())
    issues = transcript_quality_issues(
        "Aim for 50 to 200 words total across the whole episode.",
        OUTLINE,
        transcript,
    )
    assert not any("restarts earlier" in issue for issue in issues)


def test_backreference_that_continues_is_a_restart():
    """Jumping back AND continuing from the earlier segment is re-coverage."""
    transcript = list(_distinct_transcript())
    transcript.append(_routing_line())
    transcript.append(
        _dialogue(
            "The evaluator optimizer criteria then loop feedback over the "
            "current draft until the finished signal appears once more."
        )
    )
    issues = transcript_quality_issues(
        "Aim for 50 to 200 words total across the whole episode.",
        OUTLINE,
        transcript,
    )
    assert any("restarts earlier" in issue for issue in issues)
