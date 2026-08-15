from app.lyrics.alignment import AlignmentDocument, TimingAssignment
from app.lyrics.confidence import build_dual_audio_consensus


def _assignment(starts, scores) -> TimingAssignment:
    return TimingAssignment(starts=list(starts), matched=len(starts), interpolated=0, scores=list(scores))


def test_dual_consensus_corrects_supported_words_and_keeps_disputed_timing():
    document = AlignmentDocument.parse("[00:01.00]<00:01.00>one<00:01.40> two<00:02.00> three")
    result = build_dual_audio_consensus(
        document,
        [1.0, 1.4, 2.0],
        _assignment([1.2, 1.6, 2.7], [0.8, 0.7, 0.9]),
        _assignment([1.24, 1.7, 1.6], [0.9, 0.8, 0.9]),
    )

    assert result.selected_starts == [1.22, 1.65, 2.0]
    assert result.verified_words == 2
    assert result.review_words == 1
    assert result.corrected_words == 2
    assert result.word_details[2]["status"] == "review"
    assert result.word_details[2]["selected_seconds"] == 2.0
    assert result.quality == "review"


def test_missing_acoustic_score_cannot_be_automatically_verified():
    document = AlignmentDocument.parse("[00:01.00]<00:01.00>one")
    result = build_dual_audio_consensus(
        document,
        [1.0],
        _assignment([1.2], [None]),
        _assignment([1.2], [0.9]),
    )

    assert result.verified_words == 0
    assert result.selected_starts == [1.0]
    assert result.word_details[0]["confidence"] <= 35


def test_gross_same_direction_error_is_corrected_but_still_marked_for_review():
    document = AlignmentDocument.parse("[00:01.00]<00:01.00>Hello\n")
    result = build_dual_audio_consensus(
        document,
        [1.0],
        _assignment([2.0], [0.55]),
        _assignment([3.2], [0.70]),
    )

    assert result.selected_starts == [2.6]
    assert result.corrected_words == 1
    assert result.review_words == 1
    assert result.word_details[0]["status"] == "review"
    assert result.word_details[0]["correction_basis"] == "gross_directional"


def test_conflicting_correction_is_reverted_instead_of_reordering_words():
    document = AlignmentDocument.parse("[00:01.00]<00:01.00>one<00:02.00> two")
    result = build_dual_audio_consensus(
        document,
        [1.0, 2.0],
        _assignment([2.2, 1.7], [0.9, 0.9]),
        _assignment([2.2, 1.7], [0.9, 0.9]),
    )

    assert result.selected_starts[0] <= result.selected_starts[1]
    assert result.review_words >= 1


def test_perfect_high_confidence_evidence_can_be_automatically_high_quality():
    document = AlignmentDocument.parse("[00:01.00]<00:01.00>one<00:02.00> two")
    result = build_dual_audio_consensus(
        document,
        [1.0, 2.0],
        _assignment([1.1, 2.1], [0.95, 0.95]),
        _assignment([1.1, 2.1], [0.95, 0.95]),
    )

    assert result.review_words == 0
    assert result.confidence_score >= 85
    assert result.quality == "high_quality"
