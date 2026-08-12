from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan


def _seed_track_with_parts(tmp_path) -> TestClient:
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"0123456789")
    (media_root / "Song.vocals.mp3").write_bytes(b"vocals-bytes")
    (media_root / "Song.instrumental.mp3").write_bytes(b"instrumental-bytes")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client


def test_parts_lists_all_nine_part_names_plus_original(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/tracks/1/parts")

    assert response.status_code == 200
    parts = response.json()["parts"]
    names = [p["part"] for p in parts]
    assert names == [
        "instrumental", "vocals", "lead_vocals", "backing_vocals",
        "drums", "bass", "guitar", "piano", "other", "original",
    ]


def test_parts_reports_exists_true_only_for_files_present_on_disk(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    parts = {p["part"]: p for p in client.get("/api/tracks/1/parts").json()["parts"]}

    assert parts["vocals"]["exists"] is True
    assert parts["instrumental"]["exists"] is True
    assert parts["original"]["exists"] is True
    assert parts["drums"]["exists"] is False


def test_parts_duration_is_none_for_missing_or_unparseable_files(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    parts = {p["part"]: p for p in client.get("/api/tracks/1/parts").json()["parts"]}

    assert parts["drums"]["duration"] is None
    assert parts["vocals"]["duration"] is None  # fixture bytes aren't real audio


def test_parts_returns_404_for_unknown_track(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/tracks/999/parts")

    assert response.status_code == 404
