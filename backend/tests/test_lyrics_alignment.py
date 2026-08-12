import pytest

from app.lyrics.alignment import (
    AlignmentDocument,
    ObservedWord,
    assign_word_timings,
    format_timestamp,
    line_timed_segments,
)


def test_format_timestamp_formats_minutes_seconds_centiseconds():
    assert format_timestamp(0.0) == "00:00.00"
    assert format_timestamp(61.5) == "01:01.50"


def test_parse_extracts_line_timed_lyrics():
    document = AlignmentDocument.parse("[00:01.00]hello world\n[00:03.00]goodbye")

    assert [line.text for line in document.lyric_lines] == ["hello world", "goodbye"]
    assert document.lines[0].line_start == 1.0


def test_line_timed_segments_spans_to_the_next_lines_start():
    document = AlignmentDocument.parse("[00:01.00]hello world\n[00:03.00]goodbye")

    segments = line_timed_segments(document, duration=10.0)

    assert segments == [
        {"start": 1.0, "end": 3.0, "text": "hello world"},
        {"start": 3.0, "end": 10.0, "text": "goodbye"},
    ]


def test_assign_word_timings_maps_exact_matches_directly():
    observed = [ObservedWord("hello", 1.0), ObservedWord("world", 1.5)]

    assignment = assign_word_timings(["hello", "world"], observed)

    assert assignment.starts == [1.0, 1.5]
    assert assignment.matched == 2
    assert assignment.coverage == 1.0


def test_assign_word_timings_interpolates_a_missed_word():
    observed = [ObservedWord("hello", 1.0), ObservedWord("there", 3.0)]

    assignment = assign_word_timings(["hello", "beautiful", "there"], observed)

    assert assignment.starts[0] == 1.0
    assert assignment.starts[2] == 3.0
    assert 1.0 < assignment.starts[1] < 3.0
    assert assignment.matched == 2
    assert assignment.interpolated == 1


def test_render_enhanced_inserts_word_tags_and_updates_the_line_prefix():
    document = AlignmentDocument.parse("[00:01.00]hello world")

    rendered = document.render_enhanced([1.0, 1.5])

    assert rendered == "[00:01.00]<00:01.00>hello<00:01.50> world"


def test_without_timing_removes_line_word_and_break_markers_but_preserves_structure():
    document = AlignmentDocument.parse(
        "[ar:Artist]\r\n[00:01.00]<00:01.00>hello<00:01.50> world\r\n[00:05.00]\r\n[00:10.00]again\r\n"
    )

    reset = document.without_timing()

    assert reset.newline == "\r\n"
    assert reset.ends_with_newline is True
    assert [line.raw for line in reset.lines] == ["[ar:Artist]", "hello world", "", "again"]
    assert all(line.line_start is None for line in reset.lines)


def test_assign_word_timings_raises_when_no_observed_words_but_target_exists():
    with pytest.raises(ValueError):
        assign_word_timings(["hello"], [])


def test_interpolation_treats_a_zero_end_timestamp_as_present_not_missing():
    # Regression: a naive `end or (start + 0.3)` fallback (VoiceTiming's
    # original) treats an observed word whose `end` is exactly 0.0 as "no end
    # known" (0.0 is falsy in Python), silently substituting the wrong
    # duration. Neither target word matches "zzz", so _interpolate_missing's
    # zero-known-words branch is exercised directly; with the buggy fallback
    # duration would wrongly be 0.3 (giving starts [0.0, 0.15]) instead of
    # 0.0 (giving starts [0.0, 0.0]).
    observed = [ObservedWord("zzz", 0.0, end=0.0)]

    assignment = assign_word_timings(["nope", "nada"], observed)

    assert assignment.starts == [0.0, 0.0]
