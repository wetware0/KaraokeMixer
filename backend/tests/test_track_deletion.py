from pathlib import Path

from fastapi.testclient import TestClient

import app.routes.tracks as tracks_routes
from app.db import create_job
from app.main import create_app
from app.track_deletion import related_output_paths
from tests.scan_test_helpers import run_rescan


def _seed_track(tmp_path) -> tuple[TestClient, int, Path, Path]:
    media_root = tmp_path / "Media"
    mirror_root = tmp_path / "Mirror"
    media_root.mkdir()
    mirror_root.mkdir()
    source = media_root / "Song.flac"
    source.write_bytes(b"audio")
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [str(mirror_root)], "device_preference": "auto"},
    )
    run_rescan(client)
    track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
    return client, track_id, source, mirror_root


def test_delete_moves_source_and_generated_outputs_to_recycle_bin_then_removes_row(tmp_path, monkeypatch):
    client, track_id, source, mirror_root = _seed_track(tmp_path)
    beside_stem = source.with_name("Song.instrumental.mp3")
    beside_lrc_variant = source.with_name("Song.review.lrc")
    mirror_lrc = mirror_root / "Song.lrc"
    unrelated = source.with_name("Other.instrumental.mp3")
    for path in (beside_stem, beside_lrc_variant, mirror_lrc, unrelated):
        path.write_bytes(b"generated")

    recycled: list[Path] = []
    monkeypatch.setattr(tracks_routes, "send2trash", lambda value: recycled.append(Path(value)))

    response = client.request("DELETE", f"/api/tracks/{track_id}", json={"include_outputs": True})

    assert response.status_code == 200
    assert recycled[-1] == source
    assert set(recycled[:-1]) == {beside_stem, beside_lrc_variant, mirror_lrc}
    assert unrelated not in recycled
    assert client.get("/api/tracks").json()["tracks"] == []


def test_delete_can_keep_generated_outputs(tmp_path, monkeypatch):
    client, track_id, source, _mirror_root = _seed_track(tmp_path)
    stem = source.with_name("Song.vocals.mp3")
    stem.write_bytes(b"generated")
    recycled: list[Path] = []
    monkeypatch.setattr(tracks_routes, "send2trash", lambda value: recycled.append(Path(value)))

    response = client.request("DELETE", f"/api/tracks/{track_id}", json={"include_outputs": False})

    assert response.status_code == 200
    assert recycled == [source]
    assert stem not in recycled


def test_delete_refuses_a_track_with_an_active_job(tmp_path, monkeypatch):
    client, track_id, source, _mirror_root = _seed_track(tmp_path)
    create_job(
        client.app.state.db_conn,
        "fake",
        {},
        [{"track_id": track_id, "source_path": str(source)}],
    )
    recycled: list[Path] = []
    monkeypatch.setattr(tracks_routes, "send2trash", lambda value: recycled.append(Path(value)))

    response = client.request("DELETE", f"/api/tracks/{track_id}", json={"include_outputs": True})

    assert response.status_code == 409
    assert "queued or processing" in response.json()["detail"]
    assert recycled == []
    assert client.get("/api/tracks").json()["tracks"][0]["id"] == track_id


def test_output_discovery_treats_wildcard_characters_in_track_names_literally(tmp_path):
    source = tmp_path / "Song[Live].flac"
    source.write_bytes(b"audio")
    intended = tmp_path / "Song[Live].review.lrc"
    other = tmp_path / "SongL.review.lrc"
    intended.write_text("lyrics", encoding="utf-8")
    other.write_text("other lyrics", encoding="utf-8")

    found = related_output_paths(source, tmp_path, [])

    assert intended in found
    assert other not in found


def test_delete_refuses_to_race_an_active_library_rescan(tmp_path, monkeypatch):
    client, track_id, _source, _mirror_root = _seed_track(tmp_path)
    monkeypatch.setattr(client.app.state.library_scan, "status", lambda: {"status": "running"})
    recycled: list[str] = []
    monkeypatch.setattr(tracks_routes, "send2trash", lambda value: recycled.append(value))

    response = client.request("DELETE", f"/api/tracks/{track_id}", json={"include_outputs": True})

    assert response.status_code == 409
    assert "rescan is active" in response.json()["detail"]
    assert recycled == []
