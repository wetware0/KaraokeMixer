from __future__ import annotations

from dataclasses import dataclass
import statistics

from .alignment import AlignmentDocument, TimingAssignment


AGREEMENT_LIMIT_SECONDS = 0.25
CORRECTION_THRESHOLD_SECONDS = 0.05
DIRECTIONAL_CORRECTION_MARGIN_SECONDS = 0.25
MAX_DIRECTIONAL_SPREAD_SECONDS = 2.0
MAX_AUTOMATIC_SHIFT_SECONDS = 2.0
AUTOMATIC_HIGH_QUALITY_SCORE = 85


@dataclass(frozen=True)
class TimingConfidenceResult:
    selected_starts: list[float]
    word_details: list[dict]
    confidence_score: int
    verified_words: int
    review_words: int
    corrected_words: int
    review_lines: int
    agreement_within_0_25: int
    median_agreement_seconds: float
    quality: str
    asr_corroborated_words: int = 0
    large_shift_words: int = 0


def build_dual_audio_consensus(
    document: AlignmentDocument,
    current_starts: list[float],
    original: TimingAssignment,
    residual: TimingAssignment,
    *,
    baseline_starts: list[float] | None = None,
) -> TimingConfidenceResult:
    """Apply only word corrections supported by two acoustic views.

    Both assignments use the same line-constrained CTC aligner, but one hears
    the original mix and the other hears original-minus-instrumental. Their
    agreement is useful evidence against accompaniment masking; it is not
    treated as independent-model proof. Disputed words retain their current
    timing and are explicitly returned for review.
    """
    words = document.tokens
    expected = len(words)
    baseline_starts = baseline_starts or current_starts
    lengths = {
        expected, len(current_starts), len(baseline_starts),
        len(original.starts), len(residual.starts),
    }
    if len(lengths) != 1:
        raise ValueError("current lyrics and timing evidence have different word counts")
    if expected == 0:
        raise ValueError("lyrics contain no words to audit")

    details: list[dict] = []
    selected: list[float] = []
    confidences: list[int] = []
    line_index = 0
    word_index = 0
    flat_index = 0
    for line_index, line in enumerate(document.lines):
        if not line.is_lyric:
            continue
        for word_index, token in enumerate(line.tokens):
            previous = current_starts[flat_index]
            baseline = baseline_starts[flat_index]
            original_start = original.starts[flat_index]
            residual_start = residual.starts[flat_index]
            original_score = original.scores[flat_index]
            residual_score = residual.scores[flat_index]
            agreement = abs(original_start - residual_start)
            confidence = _word_confidence(agreement, original_score, residual_score)
            acoustically_verified = (
                original_score is not None
                and residual_score is not None
                and agreement <= AGREEMENT_LIMIT_SECONDS
                and confidence >= 60
            )
            consensus = (original_start + residual_start) / 2.0
            large_shift = abs(consensus - baseline) > MAX_AUTOMATIC_SHIFT_SECONDS
            verified = acoustically_verified and not large_shift
            directional = (
                not acoustically_verified
                and original_score is not None
                and residual_score is not None
                and max(original_score, residual_score) >= 0.45
                and agreement <= MAX_DIRECTIONAL_SPREAD_SECONDS
                and min(abs(original_start - previous), abs(residual_start - previous))
                    >= DIRECTIONAL_CORRECTION_MARGIN_SECONDS
                and (original_start - previous) * (residual_start - previous) > 0
            )
            chosen = consensus if acoustically_verified or directional else previous
            corrected = (
                (acoustically_verified or directional)
                and abs(baseline - consensus) >= CORRECTION_THRESHOLD_SECONDS
            )
            selected.append(chosen)
            confidences.append(confidence)
            details.append({
                "word_number": flat_index + 1,
                "line_index": line_index,
                "word_index": word_index,
                "word": token.text,
                "previous_seconds": round(previous, 3),
                "baseline_seconds": round(baseline, 3),
                "selected_seconds": round(chosen, 3),
                "original_seconds": round(original_start, 3),
                "residual_seconds": round(residual_start, 3),
                "agreement_seconds": round(agreement, 3),
                "original_score": _rounded_score(original_score),
                "residual_score": _rounded_score(residual_score),
                "confidence": confidence,
                "status": "verified" if verified else "review",
                "correction_basis": (
                    "verified_agreement" if verified
                    else "large_shift_review" if acoustically_verified and large_shift
                    else "gross_directional" if directional
                    else "retained_existing"
                ),
                "corrected": corrected,
            })
            flat_index += 1

    # Mixing retained current timings with corrected consensus timings can
    # theoretically invert neighbouring words. Revert only the conflicting
    # proposed corrections until the original monotonic ordering is restored.
    _revert_order_conflicts(selected, current_starts, details)

    return _build_result(selected, details, confidences)


def build_three_way_consensus(
    document: AlignmentDocument,
    current_starts: list[float],
    original: TimingAssignment,
    residual: TimingAssignment,
    asr: TimingAssignment,
    *,
    baseline_starts: list[float] | None = None,
) -> TimingConfidenceResult:
    """Add independently discovered ASR words to the dual-audio audit.

    ASR is allowed to promote a disputed word only when it directly matched
    that lyric word, agrees with one line-constrained acoustic view, and the
    resulting marker remains close to both the input and pre-audit baseline.
    This recovers evidence lost to a noisy subtraction residual without
    allowing repeated choruses to create automatically trusted large jumps.
    """
    baseline_starts = baseline_starts or current_starts
    expected = len(document.tokens)
    lengths = {
        expected, len(current_starts), len(baseline_starts), len(original.starts),
        len(residual.starts), len(asr.starts),
    }
    if len(lengths) != 1:
        raise ValueError("current lyrics and timing evidence have different word counts")

    dual = build_dual_audio_consensus(
        document, current_starts, original, residual, baseline_starts=baseline_starts,
    )
    selected = list(dual.selected_starts)
    details = [dict(row) for row in dual.word_details]
    confidences = [int(row["confidence"]) for row in details]

    for index, row in enumerate(details):
        row["asr_seconds"] = round(asr.starts[index], 3)
        row["asr_score"] = _rounded_score(asr.scores[index])
        if row["status"] == "verified" or asr.scores[index] is None:
            continue

        candidates: list[tuple[int, float, float, str]] = []
        for source_name, source_start, source_score in (
            ("original", original.starts[index], original.scores[index]),
            ("residual", residual.starts[index], residual.scores[index]),
        ):
            if source_score is None:
                continue
            agreement = abs(source_start - asr.starts[index])
            confidence = _word_confidence(agreement, source_score, asr.scores[index])
            if agreement <= AGREEMENT_LIMIT_SECONDS and confidence >= 60:
                candidates.append((
                    confidence,
                    agreement,
                    (source_start + asr.starts[index]) / 2.0,
                    source_name,
                ))
        if not candidates:
            continue

        confidence, agreement, candidate, source_name = max(
            candidates, key=lambda value: (value[0], -value[1]),
        )
        baseline_shift = abs(candidate - baseline_starts[index])
        input_shift = abs(candidate - current_starts[index])
        if (
            baseline_shift > MAX_AUTOMATIC_SHIFT_SECONDS
            or input_shift > MAX_AUTOMATIC_SHIFT_SECONDS
        ):
            row["correction_basis"] = "large_shift_review"
            continue

        selected[index] = candidate
        confidences[index] = confidence
        row.update({
            "selected_seconds": round(candidate, 3),
            "agreement_seconds": round(agreement, 3),
            "confidence": confidence,
            "status": "verified",
            "correction_basis": f"asr_corroborated_{source_name}",
            "corrected": abs(candidate - baseline_starts[index]) >= CORRECTION_THRESHOLD_SECONDS,
        })

    _revert_order_conflicts(selected, current_starts, details)
    return _build_result(selected, details, confidences)


def _build_result(
    selected: list[float], details: list[dict], confidences: list[int],
) -> TimingConfidenceResult:
    verified_words = sum(row["status"] == "verified" for row in details)
    review_words = len(details) - verified_words
    corrected_words = sum(bool(row["corrected"]) for row in details)
    review_lines = len({row["line_index"] for row in details if row["status"] == "review"})
    agreements = [float(row["agreement_seconds"]) for row in details]
    confidence_score = round(statistics.fmean(confidences))
    quality = (
        "high_quality"
        if review_words == 0 and confidence_score >= AUTOMATIC_HIGH_QUALITY_SCORE
        else "review"
    )
    return TimingConfidenceResult(
        selected_starts=selected,
        word_details=details,
        confidence_score=confidence_score,
        verified_words=verified_words,
        review_words=review_words,
        corrected_words=corrected_words,
        review_lines=review_lines,
        agreement_within_0_25=sum(value <= AGREEMENT_LIMIT_SECONDS for value in agreements),
        median_agreement_seconds=round(statistics.median(agreements), 3),
        quality=quality,
        asr_corroborated_words=sum(
            str(row.get("correction_basis", "")).startswith("asr_corroborated_")
            for row in details
        ),
        large_shift_words=sum(row.get("correction_basis") == "large_shift_review" for row in details),
    )


def _word_confidence(
    agreement_seconds: float,
    original_score: float | None,
    residual_score: float | None,
) -> int:
    if agreement_seconds <= 0.05:
        agreement_score = 100.0
    elif agreement_seconds <= 0.10:
        agreement_score = 100.0 - (agreement_seconds - 0.05) * 200.0
    elif agreement_seconds <= 0.25:
        agreement_score = 90.0 - (agreement_seconds - 0.10) * 100.0
    elif agreement_seconds <= 0.50:
        agreement_score = 75.0 - (agreement_seconds - 0.25) * 200.0
    else:
        agreement_score = max(0.0, 25.0 - (agreement_seconds - 0.50) * 25.0)
    if original_score is None or residual_score is None:
        return min(35, round(agreement_score * 0.35))
    acoustic_score = max(0.0, min(1.0, (original_score + residual_score) / 2.0)) * 100.0
    return max(0, min(100, round(agreement_score * 0.80 + acoustic_score * 0.20)))


def _revert_order_conflicts(
    selected: list[float], current: list[float], details: list[dict]
) -> None:
    while True:
        conflict = next(
            (index for index in range(1, len(selected)) if selected[index] < selected[index - 1]),
            None,
        )
        if conflict is None:
            return
        candidates = [index for index in (conflict - 1, conflict) if details[index]["corrected"]]
        if not candidates:
            # Existing enhanced files are expected to be monotonic, but keep
            # the result safe even when importing a malformed external file.
            selected[conflict] = selected[conflict - 1]
            details[conflict]["selected_seconds"] = round(selected[conflict], 3)
            details[conflict]["status"] = "review"
            return
        revert_index = min(candidates, key=lambda index: details[index]["confidence"])
        selected[revert_index] = current[revert_index]
        details[revert_index]["selected_seconds"] = round(current[revert_index], 3)
        details[revert_index]["status"] = "review"
        baseline = float(details[revert_index].get(
            "baseline_seconds", details[revert_index]["previous_seconds"],
        ))
        details[revert_index]["corrected"] = (
            abs(current[revert_index] - baseline) >= CORRECTION_THRESHOLD_SECONDS
        )


def _rounded_score(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
