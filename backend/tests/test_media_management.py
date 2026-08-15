from pathlib import Path

from fastapi.testclient import TestClient

import app.routes.tracks as tracks_routes
from app.db import create_job
from app.main import create_app
from tests.scan_test_helpers import run_rescan


def _client(tmp_path: Path) -> tuple[TestClient, Path, Path, Path]:
    media = tmp_path / "Media"
    second = tmp_path / "Second"
    mirror = tmp_path / "Mirror"
    media.mkdir()
    second.mkdir()
    mirror.mkdir()
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "media_roots": [str(media), str(second)],
            "mirror_roots": [str(mirror)],
            "device_preference": "auto",
        },
    )
    return client, media, second, mirror


def _track_id(client: TestClient) -> int:
    return client.get("/api/tracks").json()["tracks"][0]["id"]


def test_create_folder_is_listed_even_while_empty(tmp_path):
    client, media, _second, _mirror = _client(tmp_path)

    created = client.post("/api/folders", json={"parent_path": str(media), "name": "New songs"})

    assert created.status_code == 200
    assert (media / "New songs").is_dir()
    listed = client.get("/api/folders").json()["folders"]
    assert any(folder["path"] == (media / "New songs").resolve().as_posix() for folder in listed)


def test_move_track_carries_beside_and_mirror_outputs_and_keeps_track_id(tmp_path):
    client, media, second, mirror = _client(tmp_path)
    source = media / "Song.flac"
    source.write_bytes(b"audio")
    beside = media / "Song.instrumental.mp3"
    beside.write_bytes(b"instrumental")
    mirrored = mirror / "Song.lrc"
    mirrored.write_text("[00:01.00]Song", encoding="utf-8")
    destination = second / "Imported"
    destination.mkdir()
    run_rescan(client)
    track_id = _track_id(client)

    response = client.put(
        f"/api/tracks/{track_id}/location",
        json={"destination_folder": str(destination)},
    )

    assert response.status_code == 200, response.text
    assert (destination / "Song.flac").read_bytes() == b"audio"
    assert (destination / "Song.instrumental.mp3").read_bytes() == b"instrumental"
    assert (mirror / "Imported" / "Song.lrc").read_text(encoding="utf-8") == "[00:01.00]Song"
    assert not source.exists()
    assert response.json()["track"]["id"] == track_id
    assert response.json()["track"]["media_root"] == str(second)
    assert Path(response.json()["track"]["relative_path"]) == Path("Imported/Song.flac")


def test_rename_track_renames_all_managed_companions(tmp_path):
    client, media, _second, mirror = _client(tmp_path)
    source = media / "Old name.m4a"
    source.write_bytes(b"audio")
    (media / "Old name.vocals.mp3").write_bytes(b"vocals")
    (media / "Old name.review.lrc").write_text("review", encoding="utf-8")
    (media / "Old name.lyrics-quality.json").write_text("{}", encoding="utf-8")
    (media / "Old name.lyrics-quality-details.json").write_text("{}", encoding="utf-8")
    (media / "Old name.before-confidence.lrc").write_text("backup", encoding="utf-8")
    (mirror / "Old name.lrc").write_text("lyrics", encoding="utf-8")
    run_rescan(client)
    track_id = _track_id(client)

    response = client.put(
        f"/api/tracks/{track_id}/location",
        json={"destination_folder": str(media), "filename_stem": "New name"},
    )

    assert response.status_code == 200, response.text
    assert (media / "New name.m4a").exists()
    assert (media / "New name.vocals.mp3").exists()
    assert (media / "New name.review.lrc").exists()
    assert (media / "New name.lyrics-quality.json").exists()
    assert (media / "New name.lyrics-quality-details.json").exists()
    assert (media / "New name.before-confidence.lrc").exists()
    assert (mirror / "New name.lrc").exists()
    assert not source.exists()
    assert Path(response.json()["track"]["relative_path"]).name == "New name.m4a"


def test_move_and_rename_refuse_active_tracks(tmp_path):
    client, media, second, _mirror = _client(tmp_path)
    source = media / "Busy.flac"
    source.write_bytes(b"audio")
    run_rescan(client)
    track_id = _track_id(client)
    create_job(client.app.state.db_conn, "fake", {}, [{"track_id": track_id, "source_path": str(source)}])

    response = client.put(
        f"/api/tracks/{track_id}/location",
        json={"destination_folder": str(second)},
    )

    assert response.status_code == 409
    assert "queued or processing" in response.json()["detail"]
    assert source.exists()


def test_move_refuses_to_separate_outputs_shared_by_same_stem_sources(tmp_path):
    client, media, second, _mirror = _client(tmp_path)
    (media / "Song.flac").write_bytes(b"flac")
    (media / "Song.mp3").write_bytes(b"mp3")
    shared_lrc = media / "Song.lrc"
    shared_lrc.write_text("lyrics", encoding="utf-8")
    run_rescan(client)
    track_id = next(
        track["id"] for track in client.get("/api/tracks").json()["tracks"]
        if track["relative_path"].endswith("Song.flac")
    )

    response = client.put(
        f"/api/tracks/{track_id}/location",
        json={"destination_folder": str(second)},
    )

    assert response.status_code == 422
    assert "same filename stem" in response.json()["detail"]
    assert shared_lrc.exists()
    assert (media / "Song.flac").exists()


def test_rename_folder_moves_sources_and_mirror_outputs_and_preserves_ids(tmp_path):
    client, media, _second, mirror = _client(tmp_path)
    album = media / "Artist" / "Old album"
    album.mkdir(parents=True)
    source = album / "Song.flac"
    source.write_bytes(b"audio")
    mirror_album = mirror / "Artist" / "Old album"
    mirror_album.mkdir(parents=True)
    (mirror_album / "Song.lrc").write_text("lyrics", encoding="utf-8")
    run_rescan(client)
    track_id = _track_id(client)

    response = client.put("/api/folders/rename", json={"path": str(album), "name": "New album"})

    assert response.status_code == 200, response.text
    assert (media / "Artist" / "New album" / "Song.flac").exists()
    assert (mirror / "Artist" / "New album" / "Song.lrc").exists()
    track = client.get("/api/tracks").json()["tracks"][0]
    assert track["id"] == track_id
    assert Path(track["relative_path"]) == Path("Artist/New album/Song.flac")


def test_delete_folder_recycles_folder_and_external_outputs_then_removes_rows(tmp_path, monkeypatch):
    client, media, _second, mirror = _client(tmp_path)
    album = media / "Artist" / "Album"
    album.mkdir(parents=True)
    source = album / "Song.flac"
    source.write_bytes(b"audio")
    mirror_album = mirror / "Artist" / "Album"
    mirror_album.mkdir(parents=True)
    mirror_lrc = mirror_album / "Song.lrc"
    mirror_lrc.write_text("lyrics", encoding="utf-8")
    run_rescan(client)
    recycled: list[Path] = []
    monkeypatch.setattr(tracks_routes, "send2trash", lambda value: recycled.append(Path(value)))

    response = client.delete("/api/folders", params={"path": str(album)})

    assert response.status_code == 200, response.text
    assert recycled == [mirror_lrc, album.resolve()]
    assert client.get("/api/tracks").json()["tracks"] == []


def test_folder_operations_refuse_media_root(tmp_path):
    client, media, _second, _mirror = _client(tmp_path)

    rename = client.put("/api/folders/rename", json={"path": str(media), "name": "Renamed"})
    delete = client.delete("/api/folders", params={"path": str(media)})

    assert rename.status_code == 422
    assert delete.status_code == 422


def test_folder_operations_refuse_a_folder_containing_another_configured_root(tmp_path):
    media = tmp_path / "Media"
    parent = media / "Managed parent"
    nested_root = parent / "Nested root"
    nested_root.mkdir(parents=True)
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "media_roots": [str(media), str(nested_root)],
            "mirror_roots": [],
            "device_preference": "auto",
        },
    )

    rename = client.put("/api/folders/rename", json={"path": str(parent), "name": "Renamed"})
    delete = client.delete("/api/folders", params={"path": str(parent)})

    assert rename.status_code == 422
    assert "configured media folder" in rename.json()["detail"]
    assert delete.status_code == 422
    assert parent.exists()
