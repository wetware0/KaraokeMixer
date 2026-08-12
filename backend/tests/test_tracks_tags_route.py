from pathlib import Path

from fastapi.testclient import TestClient
from pydub import AudioSegment

from app.main import create_app
from tests.scan_test_helpers import run_rescan
from app.metadata.providers import TagsMatch


def _seed_track(tmp_path, suffix=".flac"):
    media_root = tmp_path / "Media"
    media_root.mkdir(parents=True)
    path = media_root / f"Song{suffix}"
    fmt = {"flac": "flac", "mp3": "mp3", "m4a": "ipod"}[suffix.lstrip(".")]
    AudioSegment.silent(duration=100, frame_rate=8000).export(str(path), format=fmt)

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client, path


def test_get_artwork_returns_404_when_the_track_has_none(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.get("/api/tracks/1/artwork")

    assert response.status_code == 404


def test_tag_suggestion_returns_reviewable_fields_and_validated_artwork(tmp_path, monkeypatch):
    client, _ = _seed_track(tmp_path)
    match = TagsMatch(
        artist="ABBA", title="Dancing Queen", album="Arrival", year=1976,
        artwork_url="https://example.test/cover.jpg",
    )
    monkeypatch.setattr("app.routes.tracks.search_tags_providers", lambda artist, title, providers: (match, "stub"))
    monkeypatch.setattr("app.routes.tracks.download_artwork", lambda url: (b"\xff\xd8\xe0", "image/jpeg"))

    response = client.post(
        "/api/tracks/1/tags/suggest",
        json={"artist": "Abba", "title": "Dancing queen", "include_artwork": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "artist": "ABBA", "title": "Dancing Queen", "album": "Arrival", "year": 1976,
        "provider": "stub", "artwork_data_url": "data:image/jpeg;base64,/9jg",
    }


def test_tag_suggestion_never_writes_and_reports_no_confident_match(tmp_path, monkeypatch):
    client, path = _seed_track(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr("app.routes.tracks.search_tags_providers", lambda artist, title, providers: None)

    response = client.post(
        "/api/tracks/1/tags/suggest",
        json={"artist": None, "title": "Unknown Song", "include_artwork": False},
    )

    assert response.status_code == 404
    assert "No confident tag match" in response.json()["detail"]
    assert path.read_bytes() == before


def test_get_artwork_returns_404_for_an_unknown_track(tmp_path):
    client, _ = _seed_track(tmp_path)

    assert client.get("/api/tracks/999/artwork").status_code == 404


def test_put_then_get_artwork_round_trips(tmp_path):
    client, _ = _seed_track(tmp_path)
    data = b"\xff\xd8\xff\xe0FAKEJPEGBYTES"

    put_response = client.put(
        "/api/tracks/1/artwork", content=data, headers={"Content-Type": "image/jpeg"}
    )
    assert put_response.status_code == 200

    get_response = client.get("/api/tracks/1/artwork")
    assert get_response.status_code == 200
    assert get_response.content == data
    assert get_response.headers["content-type"] == "image/jpeg"
    assert get_response.headers["cache-control"] == "no-store, max-age=0"


def test_put_artwork_rejects_an_empty_body(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put("/api/tracks/1/artwork", content=b"")

    assert response.status_code == 422


def test_put_artwork_normalizes_content_type_with_params(tmp_path):
    client, _ = _seed_track(tmp_path)
    data = b"\xff\xd8\xff\xe0FAKEJPEGBYTES"

    put_response = client.put(
        "/api/tracks/1/artwork",
        content=data,
        headers={"Content-Type": "image/jpeg; charset=binary"},
    )
    assert put_response.status_code == 200

    get_response = client.get("/api/tracks/1/artwork")
    assert get_response.status_code == 200
    assert get_response.content == data


def test_put_artwork_rejects_unsupported_mime_type(tmp_path):
    client, _ = _seed_track(tmp_path)
    data = b"GIF89a"

    response = client.put(
        "/api/tracks/1/artwork",
        content=data,
        headers={"Content-Type": "image/gif"},
    )

    assert response.status_code == 422


def test_put_artwork_rejects_content_that_does_not_match_its_declared_type(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/1/artwork", content=b"GIF89a", headers={"Content-Type": "image/jpeg"}
    )

    assert response.status_code == 422


def test_put_artwork_rejects_unreasonably_large_files(tmp_path, monkeypatch):
    client, _ = _seed_track(tmp_path)
    monkeypatch.setattr("app.routes.tracks.MAX_ARTWORK_BYTES", 8)

    response = client.put(
        "/api/tracks/1/artwork", content=b"\xff\xd8" + b"x" * 7, headers={"Content-Type": "image/jpeg"}
    )

    assert response.status_code == 413


def test_put_tags_writes_into_the_original_file_and_returns_the_fresh_track(tmp_path):
    client, path = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/1/tags",
        json={"artist": "ABBA", "title": "Dancing Queen", "album": "Arrival", "year": 1976},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["artist"] == "ABBA"
    assert body["title"] == "Dancing Queen"
    assert body["album"] == "Arrival"
    assert body["year"] == 1976

    from app.scanner import read_extended_tags
    on_disk = read_extended_tags(path)
    assert on_disk.artist == "ABBA"
    assert on_disk.album == "Arrival"
    assert on_disk.year == 1976


def test_put_tags_clears_album_and_year_when_given_null(tmp_path):
    client, _ = _seed_track(tmp_path)
    client.put(
        "/api/tracks/1/tags",
        json={"artist": "ABBA", "title": "Song", "album": "Old Album", "year": 1999},
    )

    response = client.put(
        "/api/tracks/1/tags", json={"artist": "ABBA", "title": "Song", "album": None, "year": None}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["album"] is None
    assert body["year"] is None


def test_put_tags_rejects_an_empty_title(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/1/tags", json={"artist": "ABBA", "title": "   ", "album": None, "year": None}
    )

    assert response.status_code == 422


def test_put_tags_rejects_a_non_four_digit_year(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/1/tags", json={"artist": "ABBA", "title": "Song", "album": None, "year": 76}
    )

    assert response.status_code == 422


def test_put_tags_rejects_an_implausible_four_digit_year(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/1/tags", json={"artist": "ABBA", "title": "Song", "album": None, "year": 1125}
    )

    assert response.status_code == 422
    assert "plausible release year" in response.json()["detail"]


def test_put_tags_returns_404_for_an_unknown_track(tmp_path):
    client, _ = _seed_track(tmp_path)

    response = client.put(
        "/api/tracks/999/tags", json={"artist": "A", "title": "T", "album": None, "year": None}
    )

    assert response.status_code == 404


def test_put_tags_never_touches_the_audio_payload_for_an_unsupported_format(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    path = media_root / "Song.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)

    response = client.put(
        "/api/tracks/1/tags", json={"artist": "A", "title": "T", "album": None, "year": None}
    )

    assert response.status_code == 422
