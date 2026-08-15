from app.lyrics.alignment import AlignmentDocument, ObservedWord
from app.lyrics.vocal_reference import (
    build_vocal_reference_consensus,
    coarse_vocal_segments,
    set_line_markers_to_first_word,
)


def _word(text: str, start: float, score: float = 0.9) -> ObservedWord:
    return ObservedWord(text=text, start=start, end=start + 0.2, score=score)


def test_coarse_segments_keep_repeated_lines_in_song_order():
    document = AlignmentDocument.parse(
        "[00:01.00]<00:01.00>sing <00:02.00>now\n"
        "[00:10.00]<00:10.00>sing <00:11.00>now\n"
    )
    observed = [
        _word("sing", 1.0), _word("now", 2.0),
        _word("sing", 10.0), _word("now", 11.0),
    ]

    result = coarse_vocal_segments(document, observed, duration=20.0)

    assert result.coverage == 1.0
    assert result.segments[0]["start"] == 0.25
    assert result.segments[1]["start"] == 9.25


def test_broad_vocal_disagreement_replaces_a_bad_existing_track():
    document = AlignmentDocument.parse("[00:10.00]<00:10.00>Hello <00:11.00>world\n")
    transcript = [_word("Hello", 1.0), _word("world", 2.0)]
    forced = [_word("Hello", 1.02), _word("world", 2.02)]

    outcome = build_vocal_reference_consensus(document, [10.0, 11.0], transcript, forced)

    assert outcome.preserved_existing_track is False
    assert outcome.timing.selected_starts == [1.02, 2.02]
    assert outcome.timing.corrected_words == 2
    assert outcome.timing.large_shift_words == 2


def test_already_agreeing_track_is_preserved_instead_of_rewritten():
    document = AlignmentDocument.parse(
        "[00:01.00]<00:01.00>one <00:02.00>two <00:03.00>three\n",
    )
    transcript = [_word("one", 1.02), _word("two", 2.02), _word("three", 6.0)]
    forced = [_word("one", 1.03), _word("two", 2.03), _word("three", 6.02)]

    outcome = build_vocal_reference_consensus(document, [1.0, 2.0, 3.0], transcript, forced)

    assert outcome.preserved_existing_track is True
    assert outcome.timing.selected_starts == [1.0, 2.0, 3.0]
    assert outcome.timing.corrected_words == 0
    assert outcome.timing.word_details[2]["status"] == "review"


def test_local_matching_does_not_cross_identical_choruses():
    document = AlignmentDocument.parse(
        "[00:01.00]<00:01.00>sing <00:02.00>now\n"
        "[00:10.00]<00:10.00>sing <00:11.00>now\n"
    )
    transcript = [
        _word("sing", 1.0), _word("now", 2.0),
        _word("sing", 10.0), _word("now", 11.0),
    ]
    forced = list(transcript)

    outcome = build_vocal_reference_consensus(
        document, [5.0, 6.0, 14.0, 15.0], transcript, forced,
    )

    assert outcome.timing.selected_starts == [1.0, 2.0, 10.0, 11.0]


def test_sparse_line_uses_forced_markers_but_keeps_review_on_a_bad_track():
    document = AlignmentDocument.parse(
        "[00:01.00]<00:01.00>one <00:02.00>two <00:03.00>three <00:04.00>four\n",
    )
    transcript = [_word("one", 10.0)]
    forced = [
        _word("one", 10.0), _word("two", 11.0),
        _word("three", 12.0), _word("four", 13.0),
    ]

    outcome = build_vocal_reference_consensus(
        document, [1.0, 2.0, 3.0, 4.0], transcript, forced,
    )

    assert outcome.timing.selected_starts == [10.0, 11.0, 12.0, 13.0]
    assert outcome.sparse_lines == 1
    assert all(row["correction_basis"] == "sparse_line_review" for row in outcome.timing.word_details)


def test_sparse_line_preserves_markers_when_the_existing_track_agrees():
    document = AlignmentDocument.parse(
        "[00:10.00]<00:10.00>one <00:11.00>two <00:12.00>three <00:13.00>four\n",
    )
    transcript = [_word("one", 10.02)]
    forced = [
        _word("one", 10.02), _word("two", 11.02),
        _word("three", 12.02), _word("four", 13.02),
    ]

    outcome = build_vocal_reference_consensus(
        document, [10.0, 11.0, 12.0, 13.0], transcript, forced,
    )

    assert outcome.preserved_existing_track is True
    assert outcome.timing.selected_starts == [10.0, 11.0, 12.0, 13.0]
    assert all(row["status"] == "review" for row in outcome.timing.word_details)


def test_automatic_line_marker_uses_first_word_without_preroll():
    document = AlignmentDocument.parse("[00:00.00]<00:01.00>Hello <00:02.00>world\n")

    set_line_markers_to_first_word(document, [1.25, 2.25])

    assert document.render_enhanced([1.25, 2.25]).startswith("[00:01.25]<00:01.25>Hello")
