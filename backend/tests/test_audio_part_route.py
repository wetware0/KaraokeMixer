from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan


def _seed_track_with_parts(tmp_path) -> TestClient:
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"0123456789")
    (media_root / "Song.vocals.mp3").write_bytes(b"vocals-bytes")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client


def test_original_part_streams_the_source_file(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/1/part/original")

    assert response.status_code == 200
    assert response.content == b"0123456789"


def test_named_part_streams_the_resolved_output_file(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/1/part/vocals")

    assert response.status_code == 200
    assert response.content == b"vocals-bytes"


def test_range_header_works_on_a_named_part(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/1/part/vocals", headers={"Range": "bytes=0-2"})

    assert response.status_code == 206
    assert response.content == b"voc"


def test_missing_part_returns_404(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/1/part/drums")

    assert response.status_code == 404


def test_unknown_part_name_returns_404(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/1/part/not-a-real-part")

    assert response.status_code == 404


def test_part_for_unknown_track_returns_404(tmp_path):
    client = _seed_track_with_parts(tmp_path)

    response = client.get("/api/audio/999/part/original")

    assert response.status_code == 404
