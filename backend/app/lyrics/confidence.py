from __future__ import annotations

from dataclasses import dataclass
import statistics

from .alignment import AlignmentDocument, TimingAssignment


AGREEMENT_LIMIT_SECONDS = 0.25
CORRECTION_THRESHOLD_SECONDS = 0.05
DIRECTIONAL_CORRECTION_MARGIN_SECONDS = 0.25
MAX_DIRECTIONAL_SPREAD_SECONDS = 2.0
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


def build_dual_audio_consensus(
    document: AlignmentDocument,
    current_starts: list[float],
    original: TimingAssignment,
    residual: TimingAssignment,
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
    lengths = {expected, len(current_starts), len(original.starts), len(residual.starts)}
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
            original_start = original.starts[flat_index]
            residual_start = residual.starts[flat_index]
            original_score = original.scores[flat_index]
            residual_score = residual.scores[flat_index]
            agreement = abs(original_start - residual_start)
            confidence = _word_confidence(agreement, original_score, residual_score)
            verified = (
                original_score is not None
                and residual_score is not None
                and agreement <= AGREEMENT_LIMIT_SECONDS
                and confidence >= 60
            )
            consensus = (original_start + residual_start) / 2.0
            directional = (
                not verified
                and original_score is not None
                and residual_score is not None
                and max(original_score, residual_score) >= 0.45
                and agreement <= MAX_DIRECTIONAL_SPREAD_SECONDS
                and min(abs(original_start - previous), abs(residual_start - previous))
                    >= DIRECTIONAL_CORRECTION_MARGIN_SECONDS
                and (original_start - previous) * (residual_start - previous) > 0
            )
            chosen = consensus if verified or directional else previous
            corrected = (
                (verified or directional)
                and abs(previous - consensus) >= CORRECTION_THRESHOLD_SECONDS
            )
            selected.append(chosen)
            confidences.append(confidence)
            details.append({
                "word_number": flat_index + 1,
                "line_index": line_index,
                "word_index": word_index,
                "word": token.text,
                "previous_seconds": round(previous, 3),
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

    verified_words = sum(row["status"] == "verified" for row in details)
    review_words = expected - verified_words
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
        details[revert_index]["corrected"] = False


def _rounded_score(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
