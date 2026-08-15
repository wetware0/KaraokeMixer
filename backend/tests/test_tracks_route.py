from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan


def _seed_two_tracks(tmp_path) -> TestClient:
    media_root = tmp_path / "Media"
    (media_root / "ABBA").mkdir(parents=True)
    (media_root / "ABBA" / "Dancing Queen.flac").write_bytes(b"")
    (media_root / "ABBA" / "Waterloo.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    return client


def test_get_tracks_returns_all_tracks_with_expected_shape(tmp_path):
    client = _seed_two_tracks(tmp_path)

    response = client.get("/api/tracks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["tracks"]) == 2
    track = body["tracks"][0]
    assert set(track.keys()) == {
        "id", "media_root", "relative_path", "artist", "title",
            "outputs", "lrc_state", "stem_count", "album", "year", "duration_seconds",
            "has_artwork", "instrumental_provenance", "lyric_timing_provenance",
    }
    assert track["instrumental_provenance"] is None
    assert set(track["outputs"].keys()) == {
        "instrumental", "vocals", "lead_vocals", "backing_vocals",
        "drums", "bass", "guitar", "piano", "other", "lrc",
    }


def test_get_tracks_filters_by_search_query(tmp_path):
    client = _seed_two_tracks(tmp_path)

    response = client.get("/api/tracks", params={"query": "waterloo"})

    titles = [track["title"] for track in response.json()["tracks"]]
    assert titles == ["Waterloo"]


def test_reconcile_visible_track_lyrics_repairs_stale_timing_state(tmp_path):
    client = _seed_two_tracks(tmp_path)
    track = client.get("/api/tracks").json()["tracks"][0]
    source = Path(track["media_root"]) / track["relative_path"]
    source.with_suffix(".lrc").write_text(
        "[00:01.00]<00:01.00>Hello <00:01.40>world\n",
        encoding="utf-8",
    )

    response = client.post("/api/tracks/reconcile-lyrics", json={"track_ids": [track["id"]]})

    assert response.status_code == 200
    assert response.json()["tracks"][0]["lrc_state"] == "enhanced"
    listed = next(row for row in client.get("/api/tracks").json()["tracks"] if row["id"] == track["id"])
    assert listed["outputs"]["lrc"] is True
    assert listed["lrc_state"] == "enhanced"


def test_reconcile_visible_track_lyrics_is_bounded(tmp_path):
    client = _seed_two_tracks(tmp_path)

    response = client.post("/api/tracks/reconcile-lyrics", json={"track_ids": list(range(65))})

    assert response.status_code == 422
