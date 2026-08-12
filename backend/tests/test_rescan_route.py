from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.scan_test_helpers import run_rescan, scan_counts


def test_rescan_populates_tracks_from_configured_media_root(tmp_path):
    media_root = tmp_path / "Media"
    (media_root / "ABBA").mkdir(parents=True)
    (media_root / "ABBA" / "Dancing Queen.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )

    response = run_rescan(client)

    assert response.status_code == 200
    assert scan_counts(response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [],
        "tracks_purged": 0,
    }


def test_rescan_replaces_stale_rows_for_the_same_media_root(tmp_path):
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song One.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)

    (media_root / "Song One.flac").unlink()
    (media_root / "Song Two.flac").write_bytes(b"")

    second_response = run_rescan(client)

    assert scan_counts(second_response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [],
        "tracks_purged": 0,
    }


def test_rescan_reports_unavailable_root_and_preserves_other_roots_tracks(tmp_path):
    good_root = tmp_path / "Media"
    good_root.mkdir()
    (good_root / "Song One.flac").write_bytes(b"")

    missing_root = tmp_path / "DoesNotExist"

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(good_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)

    client.put(
        "/api/settings",
        json={
            "media_roots": [str(missing_root), str(good_root)],
            "mirror_roots": [],
            "device_preference": "auto",
        },
    )
    response = run_rescan(client)

    assert scan_counts(response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [str(missing_root)],
        "tracks_purged": 0,
    }

    tracks_response = client.get("/api/tracks")
    titles = {track["title"] for track in tracks_response.json()["tracks"]}
    assert titles == {"Song One"}


def test_rescan_with_only_unavailable_root_preserves_previously_scanned_rows(tmp_path):
    """A root that is still configured but temporarily unreachable (e.g. an
    unmounted drive) must not lose its previously-scanned rows just because
    this particular rescan could not reach it - purging is keyed off
    `settings["media_roots"]`, not off which roots were scannable this time,
    so the root stays 'configured' and its rows survive."""
    good_root = tmp_path / "Media"
    good_root.mkdir()
    (good_root / "Song One.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(good_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)

    missing_root = tmp_path / "DoesNotExist"
    client.put(
        "/api/settings",
        json={"media_roots": [str(good_root), str(missing_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    response = run_rescan(client)

    assert scan_counts(response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [str(missing_root)],
        "tracks_purged": 0,
    }

    tracks_response = client.get("/api/tracks")
    titles = {track["title"] for track in tracks_response.json()["tracks"]}
    assert titles == {"Song One"}


def test_rescan_purges_tracks_when_their_media_root_is_removed_from_settings(tmp_path):
    """Regression test for the reported bug: removing a media root from
    settings must purge its tracks on the next rescan instead of leaving them
    in the database forever."""
    root_a = tmp_path / "RootA"
    root_a.mkdir()
    (root_a / "Song A.flac").write_bytes(b"")

    root_b = tmp_path / "RootB"
    root_b.mkdir()
    (root_b / "Song B.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "media_roots": [str(root_a), str(root_b)],
            "mirror_roots": [],
            "device_preference": "auto",
        },
    )
    run_rescan(client)

    # Root B is removed from settings entirely.
    client.put(
        "/api/settings",
        json={"media_roots": [str(root_a)], "mirror_roots": [], "device_preference": "auto"},
    )
    response = run_rescan(client)

    assert scan_counts(response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [],
        "tracks_purged": 1,
    }

    tracks_response = client.get("/api/tracks")
    titles = {track["title"] for track in tracks_response.json()["tracks"]}
    assert titles == {"Song A"}


def test_rescan_twice_with_forward_slash_root_does_not_duplicate_or_crash(tmp_path):
    """Regression test for the IntegrityError: on Windows, str(tmp_path) is
    backslash-spelled, but a configured media root can be forward-slash
    spelled (as it commonly is in settings.json). scan_media_root() stamps
    TrackRecord.media_root = str(Path(root)), which normalizes back to
    backslashes - so replace_tracks() must key rows by the raw configured
    string (forward slashes), not by that scanner-normalized spelling, or a
    second rescan collides with leftover rows on UNIQUE(media_root,
    relative_path)."""
    media_root_path = tmp_path / "Media"
    media_root_path.mkdir()
    (media_root_path / "Song One.flac").write_bytes(b"")
    forward_slash_root = str(media_root_path).replace("\\", "/")

    db_path = tmp_path / "library.db"
    app = create_app(db_path=db_path)
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [forward_slash_root], "mirror_roots": [], "device_preference": "auto"},
    )

    first_response = run_rescan(client)
    assert first_response.status_code == 200
    first_track_id = client.get("/api/tracks").json()["tracks"][0]["id"]

    second_response = run_rescan(client)
    assert second_response.status_code == 200
    assert scan_counts(second_response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [],
        "tracks_purged": 0,
    }

    conn = app.state.db_conn
    rows = conn.execute("SELECT id, media_root, relative_path FROM tracks").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == first_track_id
    assert rows[0]["media_root"] == forward_slash_root


def test_rescan_heals_legacy_backslash_spelled_rows(tmp_path):
    """Legacy-heal test: a database populated by a prior version of the code
    (before this fix) may already contain rows keyed by the scanner's
    str(Path(root)) spelling. When the configured root is forward-slash
    spelled, replace_tracks() must still find and delete those legacy rows
    (not just rows keyed by the raw string), so a rescan does not leave stale
    orphaned rows behind or crash inserting a duplicate."""
    media_root_path = tmp_path / "Media"
    media_root_path.mkdir()
    (media_root_path / "Song One.flac").write_bytes(b"")
    forward_slash_root = str(media_root_path).replace("\\", "/")
    legacy_backslash_root = str(Path(forward_slash_root))

    db_path = tmp_path / "library.db"
    app = create_app(db_path=db_path)
    client = TestClient(app)

    conn = app.state.db_conn
    conn.execute(
        """
        INSERT INTO tracks (
            media_root, relative_path, absolute_path, artist, title,
            has_instrumental, has_vocals, has_lead_vocals, has_backing_vocals,
            has_drums, has_bass, has_guitar, has_piano, has_other, has_lrc,
            lrc_state, stem_count, last_scanned_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL, 0, '2024-01-01T00:00:00+00:00')
        """,
        (
            legacy_backslash_root,
            "Song One.flac",
            str(media_root_path / "Song One.flac"),
            None,
            "Song One",
        ),
    )
    conn.commit()

    client.put(
        "/api/settings",
        json={"media_roots": [forward_slash_root], "mirror_roots": [], "device_preference": "auto"},
    )

    response = run_rescan(client)
    assert response.status_code == 200
    assert scan_counts(response) == {
        "tracks_found": 1,
        "media_roots_scanned": 1,
        "unavailable_roots": [],
        "tracks_purged": 0,
    }

    rows = conn.execute("SELECT media_root, relative_path FROM tracks").fetchall()
    assert len(rows) == 1
    assert rows[0]["media_root"] == forward_slash_root
    assert rows[0]["relative_path"] == "Song One.flac"
