import math
import re
from collections import Counter
from typing import Any

_WORDS = re.compile(r"[a-z0-9']+")
_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "another",
    "because",
    "before",
    "being",
    "between",
    "could",
    "does",
    "each",
    "from",
    "have",
    "into",
    "just",
    "more",
    "most",
    "only",
    "other",
    "over",
    "same",
    "should",
    "some",
    "than",
    "that",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "through",
    "until",
    "very",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
}


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _tokens(text: str) -> set[str]:
    return {
        word
        for word in _WORDS.findall(text.lower())
        if len(word) > 3 and word not in _STOP_WORDS
    }


def _outline_segments(outline: Any) -> list[Any]:
    if isinstance(outline, dict):
        return list(outline.get("segments") or [])
    return list(getattr(outline, "segments", []) or [])


def _transcript_dialogue(transcript: Any) -> list[str]:
    if isinstance(transcript, dict):
        transcript = transcript.get("transcript") or []
    return [
        str(_value(item, "dialogue")).strip()
        for item in transcript or []
        if str(_value(item, "dialogue")).strip()
    ]


def _outline_restart(dialogue: list[str], outline: Any) -> tuple[int, int] | None:
    segments = _outline_segments(outline)
    if len(segments) < 3:
        return None

    segment_tokens = [
        _tokens(f"{_value(segment, 'name')} {_value(segment, 'description')}")
        for segment in segments
    ]
    token_frequency = Counter(token for tokens in segment_tokens for token in tokens)
    distinctive = [
        {token for token in tokens if token_frequency[token] == 1}
        for tokens in segment_tokens
    ]

    assignments: list[int] = []
    for passage in dialogue:
        passage_tokens = _tokens(passage)
        scores = [len(passage_tokens & tokens) for tokens in distinctive]
        best = max(scores, default=0)
        if best < 2 or scores.count(best) != 1:
            continue
        assignments.append(scores.index(best))

    # A restart is SUSTAINED regression — the transcript re-covering an
    # earlier segment's ground — not a lone cross-reference. A single line
    # citing an earlier segment's vocabulary (a closer callback, "the Discord
    # error notification" while discussing logs) is structure the briefing
    # itself asks for; convicting on one line failed three structurally clean
    # transcripts in a row (episode:vwcw5u3y...). Discriminator: after a
    # backward jump, a real restart CONTINUES from the jumped-back segment
    # (next assigned passage stays there or ascends from it), while a mere
    # reference snaps back to where the transcript was. A jump with no
    # continuation evidence (transcript ends on it) is a callback, not a
    # restart.
    for i in range(len(assignments) - 1):
        previous, current = assignments[i], assignments[i + 1]
        if previous - current < 2:
            continue
        if i + 2 < len(assignments) and assignments[i + 2] <= current + 1:
            return previous + 1, current + 1
    return None


def _repeated_passages(dialogue: list[str]) -> tuple[int, int] | None:
    token_sets = [_tokens(passage) for passage in dialogue]
    for left in range(len(token_sets)):
        if len(token_sets[left]) < 8:
            continue
        for right in range(left + 1, len(token_sets)):
            if len(token_sets[right]) < 8:
                continue
            overlap = len(token_sets[left] & token_sets[right])
            similarity = overlap / math.sqrt(
                len(token_sets[left]) * len(token_sets[right])
            )
            if similarity >= 0.72:
                return left + 1, right + 1
    return None


def transcript_quality_issues(
    briefing: str,
    outline: Any,
    transcript: Any,
) -> list[str]:
    """Return deterministic reasons a generated transcript should not ship."""
    dialogue = _transcript_dialogue(transcript)
    if not dialogue:
        return ["transcript contains no dialogue"]

    issues: list[str] = []
    restart = _outline_restart(dialogue, outline)
    if restart:
        issues.append(
            "transcript restarts earlier outline segments "
            f"(segment {restart[0]} back to segment {restart[1]})"
        )

    repeated = _repeated_passages(dialogue)
    if repeated:
        issues.append(
            "transcript repeats substantially similar passages "
            f"(clips {repeated[0]} and {repeated[1]})"
        )

    return issues
