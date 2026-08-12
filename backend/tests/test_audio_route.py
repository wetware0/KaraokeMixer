from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan


def _seed_one_track(tmp_path, content: bytes) -> TestClient:
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(content)

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client


def test_get_audio_returns_full_file_without_range_header(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    response = client.get("/api/audio/1")
    assert response.status_code == 200
    assert response.content == b"0123456789"


def test_get_audio_returns_partial_content_for_range_header(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    response = client.get("/api/audio/1", headers={"Range": "bytes=2-4"})
    assert response.status_code == 206
    assert response.content == b"234"
    assert response.headers["Content-Range"] == "bytes 2-4/10"
    assert response.headers["Accept-Ranges"] == "bytes"


def test_get_audio_returns_404_for_unknown_track(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    response = client.get("/api/audio/999")
    assert response.status_code == 404


def test_get_audio_suffix_range_returns_last_bytes(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    response = client.get("/api/audio/1", headers={"Range": "bytes=-3"})
    assert response.status_code == 206
    assert response.content == b"789"
    assert response.headers["Content-Range"] == "bytes 7-9/10"


def test_get_audio_malformed_range_returns_416(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    response = client.get("/api/audio/1", headers={"Range": "bytes=abc-def"})
    assert response.status_code == 416


def test_get_audio_returns_404_when_file_missing_on_disk(tmp_path):
    client = _seed_one_track(tmp_path, b"0123456789")
    (tmp_path / "Media" / "Song.flac").unlink()
    response = client.get("/api/audio/1")
    assert response.status_code == 404
