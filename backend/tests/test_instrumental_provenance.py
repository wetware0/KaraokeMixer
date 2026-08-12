import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.db import (
    backfill_known_instrumental_provenance,
    confirm_instrumental_quality_for_artists,
    create_job,
    get_connection,
    get_job,
    list_tracks,
    record_instrumental_provenance,
    replace_tracks,
    set_item_stages,
)
from app.instrumental_provenance import build_instrumental_provenance


def _track(source_path: Path, output_path: Path | None, *, artist: str = "Artist") -> SimpleNamespace:
    parts = {
        "instrumental": output_path is not None,
        "vocals": False,
        "lead_vocals": False,
        "backing_vocals": False,
        "drums": False,
        "bass": False,
        "guitar": False,
        "piano": False,
        "other": False,
        "lrc": False,
    }
    stat = output_path.stat() if output_path else None
    return SimpleNamespace(
        media_root=str(source_path.parent),
        relative_path=source_path.name,
        absolute_path=str(source_path),
        artist=artist,
        title=source_path.stem,
        outputs=SimpleNamespace(**parts),
        lrc_state=None,
        stem_count=0,
        album=None,
        year=None,
        duration_seconds=180.0,
        instrumental_output_path=str(output_path) if output_path else None,
        instrumental_mtime_ns=stat.st_mtime_ns if stat else None,
        instrumental_size=stat.st_size if stat else None,
    )


def _files(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "Song.flac"
    output = tmp_path / "Song.instrumental.mp3"
    source.write_bytes(b"source")
    output.write_bytes(b"instrumental")
    return source, output


def test_recorded_provenance_is_returned_with_the_track_and_survives_an_unchanged_rescan(tmp_path):
    source, output = _files(tmp_path)
    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    track_id = list_tracks(conn)[0]["id"]
    job_id = create_job(conn, "karaoke", {}, [{"track_id": track_id, "source_path": str(source)}])
    item_id = get_job(conn, job_id)["items"][0]["id"]
    base = build_instrumental_provenance(
        {"processing_profile": "balanced", "device": "cuda"},
        output,
        engine="demucs",
        model="htdemucs",
        backing_vocal_mode="stripped",
    )

    updated = record_instrumental_provenance(
        conn, track_id, job_id, item_id, "karaoke_instrumental", base
    )
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    rescanned = list_tracks(conn)[0]

    assert updated["instrumental_provenance"]["quality"] == "balanced"
    assert rescanned["instrumental_provenance"]["engine"] == "demucs"
    assert rescanned["instrumental_provenance"]["job_id"] == job_id
    assert rescanned["instrumental_provenance"]["attribution"] == "confirmed"
    assert "output_path" not in rescanned["instrumental_provenance"]
    stored = json.loads(conn.execute(
        "SELECT instrumental_provenance_json FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()["instrumental_provenance_json"])
    assert stored["output_path"] == str(output)
    assert stored["output_mtime_ns"] == output.stat().st_mtime_ns


def test_rescan_clears_provenance_when_the_instrumental_was_replaced_or_removed(tmp_path):
    source, output = _files(tmp_path)
    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    track_id = list_tracks(conn)[0]["id"]
    base = build_instrumental_provenance(
        {"processing_profile": "fast"}, output,
        engine="demucs", model="mdx", backing_vocal_mode="stripped",
    )
    record_instrumental_provenance(conn, track_id, 1, 1, "karaoke_instrumental", base)

    output.write_bytes(b"externally replaced instrumental")
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    replaced = list_tracks(conn)[0]

    assert replaced["outputs"]["instrumental"] is True
    assert replaced["instrumental_provenance"] is None

    output.unlink()
    replace_tracks(conn, str(tmp_path), [_track(source, None)])
    removed = list_tracks(conn)[0]
    assert removed["outputs"]["instrumental"] is False
    assert removed["instrumental_provenance"] is None


def test_startup_transformation_attributes_only_a_timestamp_matching_known_output(tmp_path):
    source, output = _files(tmp_path)
    completed_at = datetime(2026, 8, 10, 2, 10, 30, tzinfo=timezone.utc)
    os.utime(output, (completed_at.timestamp(), completed_at.timestamp()))
    db_path = tmp_path / "library.db"
    conn = get_connection(db_path)
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    track_id = list_tracks(conn)[0]["id"]
    options = {
        "processing_profile": "high_quality",
        "model": "htdemucs_ft",
        "backing_vocal_mode": "best",
        "device": "cuda",
        "output_mode": "beside",
    }
    job_id = create_job(conn, "karaoke", options, [{"track_id": track_id, "source_path": str(source)}])
    item = get_job(conn, job_id)["items"][0]
    set_item_stages(conn, item["id"], [{
        "name": "karaoke_instrumental",
        "status": "completed",
        "started_at": "2026-08-10T02:00:00+00:00",
        "finished_at": completed_at.isoformat(),
        "error": None,
    }])
    # Simulate upgrading a database which has not run this transformation.
    conn.execute("DELETE FROM app_migrations WHERE name = '2026-08-10-instrumental-provenance-v1'")
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    provenance = list_tracks(migrated)[0]["instrumental_provenance"]
    detail = json.loads(migrated.execute(
        "SELECT detail_json FROM app_migrations WHERE name = '2026-08-10-instrumental-provenance-v1'"
    ).fetchone()["detail_json"])

    assert provenance["quality"] == "high_quality"
    assert provenance["engine"] == "uvr_karaoke_ensemble"
    assert provenance["model"] == "karaoke"
    assert provenance["job_id"] == job_id
    assert provenance["attribution"] == "inferred"
    assert detail == {"instrumentals_attributed": 1}


def test_backfill_leaves_an_externally_changed_output_unknown(tmp_path):
    source, output = _files(tmp_path)
    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, str(tmp_path), [_track(source, output)])
    track_id = list_tracks(conn)[0]["id"]
    job_id = create_job(
        conn,
        "karaoke",
        {"processing_profile": "balanced", "backing_vocal_mode": "stripped"},
        [{"track_id": track_id, "source_path": str(source)}],
    )
    item = get_job(conn, job_id)["items"][0]
    set_item_stages(conn, item["id"], [{
        "name": "karaoke_instrumental", "status": "completed",
        "started_at": "2026-08-10T01:00:00+00:00",
        "finished_at": "2026-08-10T01:01:00+00:00", "error": None,
    }])

    assert backfill_known_instrumental_provenance(conn) == 0
    assert list_tracks(conn)[0]["instrumental_provenance"] is None


def test_manual_artist_confirmation_is_exact_audited_and_signature_bound(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    records = []
    for filename, artist in (
        ("Dancing Queen.flac", "ABBA"),
        ("Bohemian Rhapsody.flac", "Queen"),
        ("Paranoid.flac", "Black Sabbath"),
    ):
        source = media_root / filename
        output = source.with_name(f"{source.stem}.instrumental.mp3")
        source.write_bytes(b"source")
        output.write_bytes(b"instrumental")
        records.append(_track(source, output, artist=artist))
    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, str(media_root), records)

    detail = confirm_instrumental_quality_for_artists(
        conn,
        ["ABBA", "Queen"],
        "high_quality",
        confirmed_by="Peter",
        audit_name="test-manual-quality",
    )

    tracks = {track["artist"]: track for track in list_tracks(conn)}
    assert detail["tracks_updated"] == 2
    assert detail["counts_by_artist"] == {"ABBA": 1, "Queen": 1}
    assert tracks["ABBA"]["instrumental_provenance"]["quality"] == "high_quality"
    assert tracks["Queen"]["instrumental_provenance"]["attribution"] == "manual"
    assert tracks["Queen"]["instrumental_provenance"]["confirmed_by"] == "Peter"
    assert tracks["Queen"]["instrumental_provenance"]["job_id"] is None
    assert tracks["Black Sabbath"]["instrumental_provenance"] is None
    audit = json.loads(conn.execute(
        "SELECT detail_json FROM app_migrations WHERE name = 'test-manual-quality'"
    ).fetchone()["detail_json"])
    assert audit == detail

    queen_output = media_root / "Bohemian Rhapsody.instrumental.mp3"
    queen_output.write_bytes(b"replaced")
    refreshed_records = [
        _track(
            Path(record.absolute_path),
            Path(record.instrumental_output_path),
            artist=record.artist,
        )
        for record in records
    ]
    replace_tracks(conn, str(media_root), refreshed_records)
    assert {track["artist"]: track for track in list_tracks(conn)}["Queen"]["instrumental_provenance"] is None
