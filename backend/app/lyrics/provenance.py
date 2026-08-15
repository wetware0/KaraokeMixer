from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..pipeline import atomic_publish

LYRIC_TIMING_QUALITIES = ("review", "high_quality")
LYRIC_TIMING_SIDECAR_SUFFIX = ".lyrics-quality.json"
LYRIC_TIMING_DETAILS_SUFFIX = ".lyrics-quality-details.json"
MAX_LYRIC_TIMING_DETAILS_BYTES = 10 * 1024 * 1024
MAX_LYRIC_TIMING_DETAIL_WORDS = 20_000


def lyric_timing_sidecar_path(lrc_path: Path) -> Path:
    return _fixed_lrc_sibling(lrc_path, LYRIC_TIMING_SIDECAR_SUFFIX)


def lyric_timing_details_path(lrc_path: Path) -> Path:
    return _fixed_lrc_sibling(lrc_path, LYRIC_TIMING_DETAILS_SUFFIX)


def _fixed_lrc_sibling(lrc_path: Path, suffix: str) -> Path:
    """Return a fixed-name companion beside a canonical LRC only.

    Callers obtain ``lrc_path`` from the catalogue's configured media or
    mirror roots. Restricting the extension and deriving the sibling from the
    normalized existing parent prevents a supplied filename from redirecting
    a report to another directory.
    """
    if lrc_path.suffix.casefold() != ".lrc":
        raise ValueError("lyric timing provenance requires a canonical .lrc path")
    resolved_parent = lrc_path.parent.resolve()
    sibling = resolved_parent / f"{lrc_path.stem}{suffix}"
    if sibling.parent != resolved_parent:
        raise ValueError("lyric timing provenance path escaped its LRC directory")
    return sibling


def lrc_sha256(lrc_path: Path) -> str:
    digest = hashlib.sha256()
    with lrc_path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_lyric_timing_provenance(provenance: dict) -> dict | None:
    if provenance.get("schema_version") not in {1, 2} or provenance.get("part") != "lyrics":
        return None
    if provenance.get("quality") not in LYRIC_TIMING_QUALITIES:
        return None
    signature = provenance.get("lrc_sha256")
    if not isinstance(signature, str) or len(signature) != 64:
        return None
    try:
        int(signature, 16)
    except ValueError:
        return None
    if provenance.get("timing_state") != "enhanced":
        return None
    if provenance.get("attribution") not in {"automatic", "manual"}:
        return None
    for key in ("engine", "model", "method", "device", "confirmed_by", "recorded_at"):
        value = provenance.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            return None
    for key in (
        "words", "matched", "interpolated", "low_confidence_words",
        "verified_words", "review_words", "corrected_words", "review_lines",
        "agreement_within_0_25", "asr_matched", "asr_corroborated_words",
        "large_shift_words",
    ):
        value = provenance.get(key)
        if value is not None and (not isinstance(value, int) or value < 0):
            return None
    for key in ("coverage", "median_confidence", "asr_coverage"):
        value = provenance.get(key)
        if value is not None and (not isinstance(value, (int, float)) or not 0 <= float(value) <= 1):
            return None
    confidence_score = provenance.get("confidence_score")
    if confidence_score is not None and (
        not isinstance(confidence_score, (int, float)) or not 0 <= float(confidence_score) <= 100
    ):
        return None
    median_agreement = provenance.get("median_agreement_seconds")
    if median_agreement is not None and (
        not isinstance(median_agreement, (int, float)) or float(median_agreement) < 0
    ):
        return None
    return {
        key: provenance.get(key)
        for key in (
            "schema_version", "part", "quality", "timing_state", "lrc_sha256",
            "engine", "model", "method", "device", "words", "matched",
            "interpolated", "coverage", "median_confidence", "low_confidence_words",
            "confidence_score", "verified_words", "review_words", "corrected_words",
            "review_lines", "agreement_within_0_25", "median_agreement_seconds",
            "asr_matched", "asr_coverage", "asr_corroborated_words", "large_shift_words",
            "attribution", "confirmed_by", "recorded_at",
        )
    }


def read_lyric_timing_provenance(lrc_path: Path) -> dict | None:
    sidecar = lyric_timing_sidecar_path(lrc_path)
    try:
        decoded = json.loads(sidecar.read_text(encoding="utf-8"))
        portable = portable_lyric_timing_provenance(decoded) if isinstance(decoded, dict) else None
        if portable is None or portable["lrc_sha256"] != lrc_sha256(lrc_path):
            return None
        return portable
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def write_lyric_timing_provenance(lrc_path: Path, provenance: dict) -> dict:
    if not lrc_path.is_file():
        raise ValueError("cannot record lyric timing quality without an LRC file")
    candidate = dict(provenance)
    candidate.update({
        "schema_version": candidate.get("schema_version", 1),
        "part": "lyrics",
        "timing_state": "enhanced",
        "lrc_sha256": lrc_sha256(lrc_path),
        "recorded_at": candidate.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
    })
    portable = portable_lyric_timing_provenance(candidate)
    if portable is None:
        raise ValueError("invalid lyric timing provenance")
    sidecar = lyric_timing_sidecar_path(lrc_path)
    encoded = json.dumps(portable, sort_keys=True, separators=(",", ":"))
    atomic_publish(
        sidecar,
        # The destination is a fixed sibling produced by _fixed_lrc_sibling.
        # codeql[py/path-injection]
        lambda part: part.write_text(encoded, encoding="utf-8", newline=""),
    )
    return portable


def write_lyric_timing_report(lrc_path: Path, provenance: dict, word_details: list[dict]) -> dict:
    summary = write_lyric_timing_provenance(lrc_path, {**provenance, "schema_version": 2})
    report = {
        "schema_version": 1,
        "part": "lyrics_timing_details",
        "lrc_sha256": summary["lrc_sha256"],
        "recorded_at": summary["recorded_at"],
        "words": word_details,
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    atomic_publish(
        lyric_timing_details_path(lrc_path),
        # The destination is a fixed sibling produced by _fixed_lrc_sibling.
        # codeql[py/path-injection]
        lambda part: part.write_text(encoded, encoding="utf-8", newline=""),
    )
    return summary


def read_lyric_timing_report(lrc_path: Path) -> dict | None:
    summary = read_lyric_timing_provenance(lrc_path)
    if summary is None:
        return None
    try:
        details_path = lyric_timing_details_path(lrc_path)
        if details_path.stat().st_size > MAX_LYRIC_TIMING_DETAILS_BYTES:
            return None
        decoded = json.loads(details_path.read_text(encoding="utf-8"))
        if (
            not isinstance(decoded, dict)
            or decoded.get("schema_version") != 1
            or decoded.get("part") != "lyrics_timing_details"
            or decoded.get("lrc_sha256") != summary["lrc_sha256"]
            or not isinstance(decoded.get("words"), list)
            or len(decoded["words"]) > MAX_LYRIC_TIMING_DETAIL_WORDS
            or not all(isinstance(word, dict) for word in decoded["words"])
        ):
            return None
        return {"summary": summary, "words": decoded["words"]}
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def remove_lyric_timing_provenance(lrc_path: Path) -> None:
    lyric_timing_sidecar_path(lrc_path).unlink(missing_ok=True)
    lyric_timing_details_path(lrc_path).unlink(missing_ok=True)


def confirm_lyric_timing_quality(lrc_path: Path, confirmed_by: str = "user") -> dict:
    previous = read_lyric_timing_provenance(lrc_path) or {}
    return write_lyric_timing_provenance(lrc_path, {
        **previous,
        "quality": "high_quality",
        "engine": "human_review",
        "model": "manual",
        "method": "listening_review",
        "device": None,
        "recorded_at": None,
        "attribution": "manual",
        "confirmed_by": confirmed_by,
    })
