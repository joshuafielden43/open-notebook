from pathlib import Path


def test_transcript_prompt_treats_segments_as_exclusive_scopes():
    template = (
        Path(__file__).parent.parent / "prompts" / "podcast" / "transcript.jinja"
    ).read_text()

    assert "Segments are just markers" not in template
    assert "segment is an exclusive scope" in template
    assert "Do not recap earlier segments" in template
    assert "<context>" not in template
    assert "{{ outline }}" not in template
