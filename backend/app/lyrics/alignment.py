from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

LINE_TIMESTAMP_RE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
WORD_TIMESTAMP_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
METADATA_RE = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$")
TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class LyricToken:
    text: str
    start_index: int
    end_index: int


@dataclass
class AlignmentLine:
    raw: str
    text: str
    line_start: float | None
    is_lyric: bool = False

    @property
    def tokens(self) -> list[LyricToken]:
        return [LyricToken(match.group(), match.start(), match.end()) for match in TOKEN_RE.finditer(self.text)]


@dataclass
class AlignmentDocument:
    """Parsed view of an LRC file for WhisperX alignment - a distinct,
    richer parser from app.lrc.LrcDocument (which only classifies timing
    state): this one exposes per-word tokens and can render the enhanced
    <mm:ss.xx>-tagged output. Ported from
    VoiceTiming/src/vocal_timing/lrc.py."""

    lines: list[AlignmentLine]
    newline: str = "\n"
    ends_with_newline: bool = False

    @classmethod
    def parse(cls, content: str) -> "AlignmentDocument":
        newline = "\r\n" if "\r\n" in content else "\n"
        parsed: list[AlignmentLine] = []
        for raw in content.splitlines():
            timestamp = LINE_TIMESTAMP_RE.match(raw)
            if timestamp:
                text = WORD_TIMESTAMP_RE.sub("", raw[timestamp.end():]).strip()
                parsed.append(
                    AlignmentLine(raw=raw, text=text, line_start=_parse_timestamp(timestamp), is_lyric=bool(text))
                )
                continue
            if not raw.strip() or METADATA_RE.match(raw.strip()):
                parsed.append(AlignmentLine(raw=raw, text="", line_start=None))
                continue
            text = WORD_TIMESTAMP_RE.sub("", raw).strip()
            parsed.append(AlignmentLine(raw=raw, text=text, line_start=None, is_lyric=bool(text)))
        return cls(parsed, newline, content.endswith(("\n", "\r")))

    @property
    def lyric_lines(self) -> list[AlignmentLine]:
        return [line for line in self.lines if line.is_lyric]

    @property
    def tokens(self) -> list[LyricToken]:
        return [token for line in self.lyric_lines for token in line.tokens]

    def without_timing(self) -> "AlignmentDocument":
        """Return the same lyric text and file structure with every timing
        marker removed.

        Lyric lines retain only their tag-stripped text. Bare timestamp lines
        are instrumental-break markers, so resetting the whole file turns
        those into blank structural lines rather than carrying an old break
        time into the new result. Metadata and existing blank lines are
        preserved verbatim.
        """
        reset_lines: list[AlignmentLine] = []
        for line in self.lines:
            if line.is_lyric:
                reset_lines.append(AlignmentLine(raw=line.text, text=line.text, line_start=None, is_lyric=True))
            elif line.line_start is not None:
                reset_lines.append(AlignmentLine(raw="", text="", line_start=None))
            else:
                reset_lines.append(AlignmentLine(raw=line.raw, text=line.text, line_start=None, is_lyric=False))
        return AlignmentDocument(reset_lines, self.newline, self.ends_with_newline)

    def render_enhanced(self, timings: list[float]) -> str:
        expected = sum(len(line.tokens) for line in self.lyric_lines)
        if len(timings) != expected:
            raise ValueError(f"Expected {expected} word timings, received {len(timings)}")

        output: list[str] = []
        timing_index = 0
        for line in self.lines:
            if not line.is_lyric:
                output.append(line.raw)
                continue
            tokens = line.tokens
            line_timings = timings[timing_index:timing_index + len(tokens)]
            timing_index += len(tokens)
            prefix_time = line_timings[0] if line_timings else (line.line_start or 0.0)
            parts = [f"[{format_timestamp(prefix_time)}]"]
            cursor = 0
            for token, start in zip(tokens, line_timings, strict=True):
                parts.append(f"<{format_timestamp(start)}>")
                parts.append(line.text[cursor:token.start_index])
                parts.append(token.text)
                cursor = token.end_index
            parts.append(line.text[cursor:])
            output.append("".join(parts))

        trailing = self.newline if output and self.ends_with_newline else ""
        return self.newline.join(output) + trailing


@dataclass(frozen=True)
class ObservedWord:
    text: str
    start: float
    end: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class TimingAssignment:
    starts: list[float]
    matched: int
    interpolated: int

    @property
    def coverage(self) -> float:
        total = self.matched + self.interpolated
        return self.matched / total if total else 0.0


def assign_word_timings(target_words: list[str], observed_words: list[ObservedWord]) -> TimingAssignment:
    """Map ASR-observed words onto the target lyric words via difflib
    sequence matching, interpolating any gaps. Pure Python, no torch -
    ported from VoiceTiming/src/vocal_timing/lrc.py::assign_word_timings."""
    if not target_words:
        return TimingAssignment([], 0, 0)
    if not observed_words:
        raise ValueError("The aligner returned no word timings")

    target_norm = [_normalize_word(word) for word in target_words]
    observed_norm = [_normalize_word(word.text) for word in observed_words]
    matcher = SequenceMatcher(None, target_norm, observed_norm, autojunk=False)
    starts: list[float | None] = [None] * len(target_words)
    matched = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for target_index, observed_index in zip(range(i1, i2), range(j1, j2)):
                starts[target_index] = observed_words[observed_index].start
                matched += 1
        elif tag == "replace":
            available = set(range(j1, j2))
            for target_index in range(i1, i2):
                best = max(
                    available,
                    key=lambda idx: SequenceMatcher(None, target_norm[target_index], observed_norm[idx]).ratio(),
                    default=None,
                )
                if best is not None and SequenceMatcher(
                    None, target_norm[target_index], observed_norm[best]
                ).ratio() >= 0.72:
                    starts[target_index] = observed_words[best].start
                    matched += 1
                    available.remove(best)

    _interpolate_missing(starts, observed_words)
    monotonic = _make_monotonic([float(value) for value in starts])
    return TimingAssignment(monotonic, matched, len(target_words) - matched)


def line_timed_segments(document: AlignmentDocument, duration: float) -> list[dict]:
    """Forced-alignment windows for whisperx.align(): one per timed lyric
    line, spanning to the next timed line's start (or the audio's end)."""
    segments: list[dict] = []
    for index, line in enumerate(document.lines):
        if not line.is_lyric or line.line_start is None:
            continue
        start = float(line.line_start)
        later_starts = [
            other.line_start for other in document.lines[index + 1:]
            if other.line_start is not None and other.line_start > start
        ]
        end = float(later_starts[0]) if later_starts else duration
        segments.append({"start": start, "end": max(start + 0.1, end), "text": line.text})
    return segments


def format_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    minutes, remainder = divmod(centiseconds, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _parse_timestamp(match: re.Match[str]) -> float:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction_text = match.group(3) or "0"
    fraction = int(fraction_text) / (10 ** len(fraction_text))
    return minutes * 60 + seconds + fraction


def _normalize_word(word: str) -> str:
    normalized = unicodedata.normalize("NFKD", word).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _interpolate_missing(starts: list[float | None], observed_words: list[ObservedWord]) -> None:
    known = [index for index, value in enumerate(starts) if value is not None]
    if not known:
        # `is not None` (not a truthiness check): VoiceTiming's original
        # `end or (start + 0.3)` treated an observed word whose `end` is
        # exactly 0.0 as "no end known" (0.0 is falsy in Python) and silently
        # substituted the wrong fallback duration. Fixed here as a
        # deliberate correctness improvement over the reference.
        last_end = observed_words[-1].end
        duration = last_end if last_end is not None else observed_words[-1].start + 0.3
        step = duration / max(1, len(starts))
        for index in range(len(starts)):
            starts[index] = index * step
        return

    first = known[0]
    first_time = float(starts[first])
    for index in range(first - 1, -1, -1):
        starts[index] = max(0.0, first_time - 0.25 * (first - index))

    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        left_time = float(starts[left])
        right_time = float(starts[right])
        step = (right_time - left_time) / (right - left)
        for index in range(left + 1, right):
            starts[index] = left_time + step * (index - left)

    last = known[-1]
    last_time = float(starts[last])
    for index in range(last + 1, len(starts)):
        starts[index] = last_time + 0.25 * (index - last)


def _make_monotonic(starts: list[float]) -> list[float]:
    result: list[float] = []
    previous = 0.0
    for start in starts:
        value = max(previous, start)
        result.append(value)
        previous = value
    return result
