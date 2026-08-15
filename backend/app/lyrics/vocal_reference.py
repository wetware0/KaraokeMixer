from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import statistics

from .alignment import (
    AlignmentDocument,
    ObservedWord,
    _normalize_word,
    assign_word_timings,
)
from .confidence import (
    AGREEMENT_LIMIT_SECONDS,
    AUTOMATIC_HIGH_QUALITY_SCORE,
    CORRECTION_THRESHOLD_SECONDS,
    MAX_AUTOMATIC_SHIFT_SECONDS,
    TimingConfidenceResult,
    _word_confidence,
)


MIN_COARSE_ASR_COVERAGE = 0.50
LOCAL_WINDOW_SECONDS = 1.75
LOCAL_MATCH_THRESHOLD = 0.72
PRESERVE_TRACK_MEDIAN_SECONDS = 0.10
ISOLATED_OUTLIER_SECONDS = 1.0
LOCALLY_STABLE_LINE_SECONDS = 0.50
MIN_LINE_ANCHOR_COVERAGE = 0.50


@dataclass(frozen=True)
class VocalReferenceSegments:
    segments: list[dict]
    matched: int
    total: int

    @property
    def coverage(self) -> float:
        return self.matched / self.total if self.total else 0.0


@dataclass(frozen=True)
class VocalReferenceOutcome:
    timing: TimingConfidenceResult
    asr_matched: int
    asr_coverage: float
    median_input_difference: float
    preserved_existing_track: bool
    sparse_lines: int
    isolated_outliers: int


def coarse_vocal_segments(
    document: AlignmentDocument,
    observed: list[ObservedWord],
    duration: float,
) -> VocalReferenceSegments:
    """Locate lyric lines from an unconstrained isolated-vocal transcript."""
    target = [token.text for token in document.tokens]
    pairs = _global_pairs(target, observed)
    anchors: list[float | None] = [None] * len(target)
    matched = 0
    for target_index, observed_index in pairs:
        if _similarity(target[target_index], observed[observed_index].text) < LOCAL_MATCH_THRESHOLD:
            continue
        anchors[target_index] = observed[observed_index].start
        matched += 1
    if not target or matched / len(target) < MIN_COARSE_ASR_COVERAGE:
        raise ValueError(
            f"isolated-vocal transcription matched only {matched}/{len(target)} lyric words"
        )
    interpolated = _interpolate(anchors)
    first_indices: list[int] = []
    offset = 0
    for line in document.lyric_lines:
        first_indices.append(offset)
        offset += len(line.tokens)
    first_times = [interpolated[index] for index in first_indices]
    segments = _segments_from_line_starts(document, first_times, duration)
    return VocalReferenceSegments(segments=segments, matched=matched, total=len(target))


def build_vocal_reference_consensus(
    document: AlignmentDocument,
    current_starts: list[float],
    observed_transcript: list[ObservedWord],
    observed_forced: list[ObservedWord],
) -> VocalReferenceOutcome:
    """Combine free transcription and exact-text alignment of isolated vocals.

    The free transcript supplies independent word anchors. Exact-text forced
    alignment fills words that ASR misheard. Matching is repeated per placed
    line so identical choruses cannot attach to a different occurrence.
    """
    expected = len(document.tokens)
    if len(current_starts) != expected:
        raise ValueError("current lyrics and word markers have different lengths")
    forced = assign_word_timings(
        [token.text for token in document.tokens], observed_forced,
    )
    if forced.coverage < 0.80:
        raise ValueError(f"exact vocal alignment matched only {forced.coverage:.0%} of lyric words")

    anchors, anchor_scores, matched, sparse_indices = _local_line_anchors(
        document, forced.starts, observed_transcript,
    )
    # The unconstrained transcript is independent corroboration, not the
    # timing source. Human-reviewed ABBA references showed that its raw word
    # starts are materially noisier than exact-text forced alignment. Use it
    # to place lines and grade evidence, but publish the forced timings.
    candidate = list(forced.starts)
    median_difference = statistics.median(
        abs(after - before)
        for before, after in zip(current_starts, candidate, strict=True)
    )
    preserve_track = median_difference <= PRESERVE_TRACK_MEDIAN_SECONDS

    selected = list(current_starts if preserve_track else candidate)
    reasons: dict[int, str] = {}
    for index in sparse_indices:
        if preserve_track:
            selected[index] = current_starts[index]
        reasons[index] = "sparse_line_review"

    isolated_outliers = 0
    if not preserve_track:
        offset = 0
        for line in document.lyric_lines:
            count = len(line.tokens)
            deltas = [
                candidate[index] - current_starts[index]
                for index in range(offset, offset + count)
            ]
            median_delta = statistics.median(deltas)
            if abs(median_delta) <= LOCALLY_STABLE_LINE_SECONDS:
                for word_index, delta in enumerate(deltas):
                    flat_index = offset + word_index
                    if abs(delta - median_delta) > ISOLATED_OUTLIER_SECONDS:
                        selected[flat_index] = current_starts[flat_index]
                        reasons[flat_index] = "isolated_outlier_review"
                        isolated_outliers += 1
            offset += count
    selected = _monotonic(selected)

    details: list[dict] = []
    confidences: list[int] = []
    flat_index = 0
    for line_index, line in enumerate(document.lines):
        if not line.is_lyric:
            continue
        for word_index, token in enumerate(line.tokens):
            transcript_start = anchors[flat_index]
            transcript_score = anchor_scores[flat_index]
            forced_start = forced.starts[flat_index]
            forced_score = forced.scores[flat_index]
            agreement = (
                abs(transcript_start - forced_start)
                if transcript_start is not None else float("inf")
            )
            confidence = _word_confidence(agreement, transcript_score, forced_score)
            candidate_close_to_input = abs(candidate[flat_index] - current_starts[flat_index]) <= 0.25
            verified = (
                transcript_start is not None
                and transcript_score is not None
                and forced_score is not None
                and agreement <= AGREEMENT_LIMIT_SECONDS
                and confidence >= 60
                and flat_index not in reasons
                and (not preserve_track or candidate_close_to_input)
            )
            shift = abs(selected[flat_index] - current_starts[flat_index])
            large_shift = shift > MAX_AUTOMATIC_SHIFT_SECONDS
            if large_shift:
                verified = False
            corrected = shift >= CORRECTION_THRESHOLD_SECONDS
            if flat_index in reasons:
                basis = reasons[flat_index]
            elif large_shift:
                basis = "large_shift_review"
            elif preserve_track and verified:
                basis = "input_confirmed_by_vocal"
            elif corrected and verified:
                basis = "vocal_reference_verified"
            elif corrected:
                basis = "vocal_reference_review"
            else:
                basis = "retained_existing"
            confidences.append(confidence)
            details.append({
                "word_number": flat_index + 1,
                "line_index": line_index,
                "word_index": word_index,
                "word": token.text,
                "previous_seconds": round(current_starts[flat_index], 3),
                "baseline_seconds": round(current_starts[flat_index], 3),
                "selected_seconds": round(selected[flat_index], 3),
                "original_seconds": round(forced_start, 3),
                "residual_seconds": round(
                    transcript_start if transcript_start is not None else candidate[flat_index], 3,
                ),
                "asr_seconds": round(
                    transcript_start if transcript_start is not None else candidate[flat_index], 3,
                ),
                "agreement_seconds": round(agreement, 3) if agreement != float("inf") else None,
                "original_score": _rounded(forced_score),
                "residual_score": _rounded(transcript_score),
                "asr_score": _rounded(transcript_score),
                "confidence": confidence,
                "status": "verified" if verified else "review",
                "correction_basis": basis,
                "corrected": corrected,
            })
            flat_index += 1

    verified_words = sum(row["status"] == "verified" for row in details)
    review_words = len(details) - verified_words
    confidence_score = round(statistics.fmean(confidences))
    timing = TimingConfidenceResult(
        selected_starts=selected,
        word_details=details,
        confidence_score=confidence_score,
        verified_words=verified_words,
        review_words=review_words,
        corrected_words=sum(bool(row["corrected"]) for row in details),
        review_lines=len({row["line_index"] for row in details if row["status"] == "review"}),
        agreement_within_0_25=sum(
            row["agreement_seconds"] is not None and row["agreement_seconds"] <= 0.25
            for row in details
        ),
        median_agreement_seconds=round(statistics.median(
            row["agreement_seconds"]
            for row in details
            if row["agreement_seconds"] is not None
        ), 3),
        quality=(
            "high_quality"
            if review_words == 0 and confidence_score >= AUTOMATIC_HIGH_QUALITY_SCORE
            else "review"
        ),
        asr_corroborated_words=verified_words,
        large_shift_words=sum(row["correction_basis"] == "large_shift_review" for row in details),
    )
    return VocalReferenceOutcome(
        timing=timing,
        asr_matched=matched,
        asr_coverage=matched / expected,
        median_input_difference=round(median_difference, 3),
        preserved_existing_track=preserve_track,
        sparse_lines=len(_sparse_line_numbers(document, sparse_indices)),
        isolated_outliers=isolated_outliers,
    )


def set_line_markers_to_first_word(document: AlignmentDocument, timings: list[float]) -> None:
    offset = 0
    for line in document.lines:
        if not line.is_lyric:
            continue
        line.line_start = timings[offset]
        offset += len(line.tokens)


def _local_line_anchors(
    document: AlignmentDocument,
    reference: list[float],
    observed: list[ObservedWord],
) -> tuple[list[float | None], list[float | None], int, set[int]]:
    anchors: list[float | None] = [None] * len(reference)
    scores: list[float | None] = [None] * len(reference)
    sparse_indices: set[int] = set()
    matched = 0
    offset = 0
    for line in document.lyric_lines:
        count = len(line.tokens)
        line_reference = reference[offset : offset + count]
        nearby = [
            word for word in observed
            if line_reference[0] - LOCAL_WINDOW_SECONDS
            <= word.start
            <= line_reference[-1] + LOCAL_WINDOW_SECONDS
        ]
        target = [token.text for token in line.tokens]
        line_matched = 0
        for target_index, observed_index in _global_pairs(target, nearby):
            similarity = _similarity(target[target_index], nearby[observed_index].text)
            if similarity < LOCAL_MATCH_THRESHOLD:
                continue
            flat_index = offset + target_index
            anchors[flat_index] = nearby[observed_index].start
            scores[flat_index] = nearby[observed_index].score
            matched += 1
            line_matched += 1
        if line_matched / count < MIN_LINE_ANCHOR_COVERAGE:
            sparse_indices.update(range(offset, offset + count))
        offset += count
    return anchors, scores, matched, sparse_indices


def _segments_from_line_starts(
    document: AlignmentDocument,
    first_times: list[float],
    duration: float,
) -> list[dict]:
    segments: list[dict] = []
    for index, line in enumerate(document.lyric_lines):
        start = max(0.0, first_times[index] - 0.75)
        end = duration if index + 1 == len(first_times) else first_times[index + 1] - 0.05
        segments.append({"start": start, "end": max(start + 0.1, end), "text": line.text})
    return segments


def _sparse_line_numbers(document: AlignmentDocument, sparse_indices: set[int]) -> set[int]:
    result: set[int] = set()
    offset = 0
    for line_index, line in enumerate(document.lines):
        if not line.is_lyric:
            continue
        count = len(line.tokens)
        if any(index in sparse_indices for index in range(offset, offset + count)):
            result.add(line_index)
        offset += count
    return result


def _global_pairs(target: list[str], observed: list[ObservedWord]) -> list[tuple[int, int]]:
    gap = -1.1
    rows = len(target) + 1
    columns = len(observed) + 1
    scores = [[0.0] * columns for _ in range(rows)]
    moves = [[0] * columns for _ in range(rows)]
    for row in range(1, rows):
        scores[row][0] = row * gap
        moves[row][0] = 2
    for column in range(1, columns):
        scores[0][column] = column * gap
        moves[0][column] = 3
    for row in range(1, rows):
        for column in range(1, columns):
            diagonal = scores[row - 1][column - 1] + _pair_score(
                target[row - 1], observed[column - 1].text,
            )
            up = scores[row - 1][column] + gap
            left = scores[row][column - 1] + gap
            best = max(diagonal, up, left)
            scores[row][column] = best
            moves[row][column] = 1 if best == diagonal else 2 if best == up else 3
    row = len(target)
    column = len(observed)
    pairs: list[tuple[int, int]] = []
    while row or column:
        move = moves[row][column]
        if move == 1:
            pairs.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif move == 2:
            row -= 1
        else:
            column -= 1
    pairs.reverse()
    return pairs


def _pair_score(left: str, right: str) -> float:
    similarity = _similarity(left, right)
    if similarity == 1.0:
        return 4.0
    if similarity >= 0.86:
        return 3.0
    if similarity >= LOCAL_MATCH_THRESHOLD:
        return 1.5
    if similarity >= 0.55:
        return 0.25
    return -2.0


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_word(left), _normalize_word(right)).ratio()


def _interpolate(starts: list[float | None]) -> list[float]:
    known = [index for index, value in enumerate(starts) if value is not None]
    if not known:
        raise ValueError("vocal transcript contains no usable lyric anchors")
    first = known[0]
    for index in range(first - 1, -1, -1):
        starts[index] = max(0.0, float(starts[first]) - 0.25 * (first - index))
    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        step = (float(starts[right]) - float(starts[left])) / (right - left)
        for index in range(left + 1, right):
            starts[index] = float(starts[left]) + step * (index - left)
    last = known[-1]
    for index in range(last + 1, len(starts)):
        starts[index] = float(starts[last]) + 0.25 * (index - last)
    return _monotonic([float(value) for value in starts])


def _monotonic(timings: list[float]) -> list[float]:
    result: list[float] = []
    previous = 0.0
    for timing in timings:
        previous = max(previous, timing)
        result.append(previous)
    return result


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
