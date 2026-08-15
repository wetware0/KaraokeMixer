import json

import pytest

from app.lyrics.provenance import (
    confirm_lyric_timing_quality,
    lyric_timing_details_path,
    lyric_timing_sidecar_path,
    read_lyric_timing_provenance,
    read_lyric_timing_report,
    remove_lyric_timing_provenance,
    write_lyric_timing_report,
    write_lyric_timing_provenance,
)
from app.db import get_connection, list_tracks, replace_tracks
from app.scanner import scan_media_root


def test_quality_record_is_bound_to_the_exact_lrc_content(tmp_path):
    lrc = tmp_path / "Song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8")

    written = confirm_lyric_timing_quality(lrc, "Peter")

    assert written["quality"] == "high_quality"
    assert written["confirmed_by"] == "Peter"
    assert read_lyric_timing_provenance(lrc) == written

    lrc.write_text("[00:02.00]<00:02.00>Hello\n", encoding="utf-8")
    assert read_lyric_timing_provenance(lrc) is None


def test_provenance_refuses_a_non_lrc_destination(tmp_path):
    audio = tmp_path / "Song.flac"
    audio.write_bytes(b"audio")

    with pytest.raises(ValueError, match="canonical .lrc"):
        write_lyric_timing_provenance(audio, {
            "quality": "review", "attribution": "automatic",
        })


def test_automatic_review_metrics_round_trip_and_invalid_data_is_rejected(tmp_path):
    lrc = tmp_path / "Song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8")
    written = write_lyric_timing_provenance(lrc, {
        "quality": "review",
        "engine": "whisperx",
        "model": "wav2vec2",
        "method": "line_constrained_ctc",
        "device": "cuda",
        "words": 1,
        "matched": 1,
        "interpolated": 0,
        "coverage": 1.0,
        "median_confidence": 0.42,
        "low_confidence_words": 1,
        "attribution": "automatic",
        "confirmed_by": None,
    })

    assert read_lyric_timing_provenance(lrc) == written
    sidecar = lyric_timing_sidecar_path(lrc)
    sidecar.write_text(json.dumps({"schema_version": 1, "part": "lyrics", "quality": "invented"}))
    assert read_lyric_timing_provenance(lrc) is None


def test_remove_quality_record_is_idempotent(tmp_path):
    lrc = tmp_path / "Song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8")
    write_lyric_timing_report(lrc, {
        "quality": "review", "engine": "whisperx", "model": "wav2vec2",
        "method": "dual_audio_consensus_v1", "device": "cuda", "words": 1,
        "matched": 1, "interpolated": 0, "coverage": 1.0,
        "median_confidence": 0.8, "low_confidence_words": 0,
        "confidence_score": 88, "verified_words": 1, "review_words": 0,
        "corrected_words": 1, "review_lines": 0, "agreement_within_0_25": 1,
        "median_agreement_seconds": 0.02, "attribution": "automatic", "confirmed_by": None,
    }, [{"word": "Hello", "line_index": 0, "word_index": 0, "confidence": 88}])

    report = read_lyric_timing_report(lrc)
    assert report is not None
    assert report["summary"]["confidence_score"] == 88
    assert report["words"][0]["word"] == "Hello"

    remove_lyric_timing_provenance(lrc)
    remove_lyric_timing_provenance(lrc)

    assert not lyric_timing_sidecar_path(lrc).exists()
    assert not lyric_timing_details_path(lrc).exists()


def test_manual_confirmation_records_its_own_time_while_retaining_audit_metrics(tmp_path):
    lrc = tmp_path / "Song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8")
    write_lyric_timing_provenance(lrc, {
        "quality": "review", "engine": "whisperx", "model": "align",
        "method": "dual_audio_consensus_v1", "device": "cuda",
        "confidence_score": 82, "verified_words": 1, "review_words": 0,
        "recorded_at": "2000-01-01T00:00:00+00:00", "attribution": "automatic",
        "confirmed_by": None,
    })

    confirmed = confirm_lyric_timing_quality(lrc, "Peter")

    assert confirmed["recorded_at"] != "2000-01-01T00:00:00+00:00"
    assert confirmed["confidence_score"] == 82
    assert confirmed["quality"] == "high_quality"


def test_scan_restores_quality_in_a_fresh_catalogue_and_rejects_a_stale_hash(tmp_path):
    source = tmp_path / "Song.flac"
    source.write_bytes(b"not-real-audio")
    lrc = tmp_path / "Song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8")
    confirm_lyric_timing_quality(lrc, "Peter")

    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, str(tmp_path), scan_media_root(tmp_path, []))
    assert list_tracks(conn)[0]["lyric_timing_provenance"]["quality"] == "high_quality"

    lrc.write_text("[00:02.00]<00:02.00>Hello\n", encoding="utf-8")
    replace_tracks(conn, str(tmp_path), scan_media_root(tmp_path, []))
    assert list_tracks(conn)[0]["lyric_timing_provenance"] is None
