from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

LINE_TIMESTAMP_RE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
WORD_TIMESTAMP_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
METADATA_RE = re.compile(r"^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$")


class TimingState(str, Enum):
    ENHANCED = "enhanced"
    LINE_TIMED = "line_timed"
    UNTIMED = "untimed"
    EMPTY = "empty"
    UNKNOWN = "unknown"


def _parse_timestamp(match: re.Match[str]) -> float:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction_text = match.group(3) or "0"
    fraction = int(fraction_text) / (10 ** len(fraction_text))
    return minutes * 60 + seconds + fraction


@dataclass
class LrcLine:
    raw: str
    text: str
    line_start: float | None
    is_lyric: bool = False


@dataclass
class LrcDocument:
    lines: list[LrcLine]

    @classmethod
    def parse(cls, content: str) -> "LrcDocument":
        parsed: list[LrcLine] = []
        for raw in content.splitlines():
            timestamp = LINE_TIMESTAMP_RE.match(raw)
            if timestamp:
                text = WORD_TIMESTAMP_RE.sub("", raw[timestamp.end():]).strip()
                parsed.append(
                    LrcLine(
                        raw=raw,
                        text=text,
                        line_start=_parse_timestamp(timestamp),
                        is_lyric=bool(text),
                    )
                )
                continue

            if not raw.strip() or METADATA_RE.match(raw.strip()):
                parsed.append(LrcLine(raw=raw, text="", line_start=None))
                continue

            text = WORD_TIMESTAMP_RE.sub("", raw).strip()
            parsed.append(LrcLine(raw=raw, text=text, line_start=None, is_lyric=bool(text)))
        return cls(parsed)

    @property
    def state(self) -> TimingState:
        lyric_lines = [line for line in self.lines if line.is_lyric]

        orphaned_word_tag = any(
            WORD_TIMESTAMP_RE.search(line.raw) and line.line_start is None
            for line in lyric_lines
        )
        if orphaned_word_tag:
            return TimingState.UNKNOWN

        if any(WORD_TIMESTAMP_RE.search(line.raw) for line in lyric_lines):
            return TimingState.ENHANCED
        if not lyric_lines:
            return TimingState.EMPTY
        if all(line.line_start is not None for line in lyric_lines):
            return TimingState.LINE_TIMED
        return TimingState.UNTIMED


def read_lrc_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def classify_lrc_file(path: Path) -> TimingState:
    try:
        content = read_lrc_text(path)
    except OSError:
        return TimingState.UNKNOWN
    return LrcDocument.parse(content).state
