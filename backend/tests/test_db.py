import threading
from pathlib import Path
from types import SimpleNamespace

from app.db import get_connection, list_tracks, purge_tracks_not_in_roots, replace_tracks, update_settings, update_track_tags


def _outputs(**overrides):
    values = dict(
        instrumental=False,
        vocals=False,
        lead_vocals=False,
        backing_vocals=False,
        drums=False,
        bass=False,
        guitar=False,
        piano=False,
        other=False,
        lrc=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_track(media_root, relative_path, artist, title):
    return SimpleNamespace(
        media_root=media_root,
        relative_path=relative_path,
        absolute_path=f"{media_root}/{relative_path}",
        artist=artist,
        title=title,
        outputs=_outputs(),
        lrc_state=None,
        stem_count=0,
        album=None,
        year=None,
        duration_seconds=None,
    )


class _PoisonTrack:
    """Track-like object that pauses mid-insert (attribute access, before the
    row is written) so a concurrent writer gets a window to interleave with
    this thread's still-open transaction, then raises to simulate a rescan
    that fails partway through."""

    def __init__(self, media_root, ready_event, proceed_event):
        self.media_root = media_root
        self.relative_path = "poison.flac"
        self.absolute_path = f"{media_root}/poison.flac"
        self.title = "Poison"
        self.outputs = _outputs()
        self.lrc_state = None
        self.stem_count = 0
        self.album = None
        self.year = None
        self.duration_seconds = None
        self._ready_event = ready_event
        self._proceed_event = proceed_event

    @property
    def artist(self):
        self._ready_event.set()
        # Bounded wait: gives the concurrent writer a window to run inside
        # this thread's still-open transaction. With the write lock held for
        # the whole `with conn:` block, the other writer cannot even start
        # until this thread is done, so this is a deterministic upper bound
        # on test runtime rather than a race.
        self._proceed_event.wait(timeout=0.3)
        raise RuntimeError("simulated failure mid-insert")


def test_concurrent_write_does_not_lose_rows_when_a_writer_fails_mid_insert(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    media_root = "D:/Media"

    # Seed a pre-existing row for the media_root that a failed rescan must not lose.
    replace_tracks(
        conn, media_root, [_make_track(media_root, "Song One.flac", "Artist", "Song One")]
    )
    assert [t["title"] for t in list_tracks(conn)] == ["Song One"]

    ready_event = threading.Event()
    proceed_event = threading.Event()
    errors: list[BaseException] = []

    def run_failing_rescan():
        try:
            replace_tracks(
                conn, media_root, [_PoisonTrack(media_root, ready_event, proceed_event)]
            )
        except RuntimeError as exc:
            errors.append(exc)

    def run_concurrent_settings_update():
        assert ready_event.wait(timeout=5), "rescan thread never reached the poison track"
        update_settings(
            conn,
            {"media_roots": [media_root], "mirror_roots": [], "device_preference": "cpu"},
        )
        proceed_event.set()

    thread_a = threading.Thread(target=run_failing_rescan)
    thread_b = threading.Thread(target=run_concurrent_settings_update)

    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert len(errors) == 1, "the simulated rescan failure should have propagated"

    # The concurrent settings write must still have gone through.
    assert conn.execute(
        "SELECT device_preference FROM settings WHERE id = 1"
    ).fetchone()["device_preference"] == "cpu"

    remaining = list_tracks(conn)
    assert [t["title"] for t in remaining] == ["Song One"], (
        "a failed rescan must not wipe pre-existing rows for the media root "
        "(a concurrent writer's commit must not land this writer's half-done work)"
    )


def test_purge_tracks_not_in_roots_removes_rows_for_a_removed_root(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    root_a = "D:/RootA"
    root_b = "D:/RootB"
    replace_tracks(conn, root_a, [_make_track(root_a, "Song A.flac", "Artist", "Song A")])
    replace_tracks(conn, root_b, [_make_track(root_b, "Song B.flac", "Artist", "Song B")])

    purged = purge_tracks_not_in_roots(conn, [root_a])

    assert purged == 1
    assert [t["title"] for t in list_tracks(conn)] == ["Song A"]


def test_purge_tracks_not_in_roots_keeps_spelling_variant_of_a_configured_root(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    configured_root = "D:/RootA"
    legacy_spelled_root = str(Path(configured_root))  # e.g. "D:\\RootA" on Windows
    replace_tracks(
        conn,
        legacy_spelled_root,
        [_make_track(legacy_spelled_root, "Song A.flac", "Artist", "Song A")],
    )

    purged = purge_tracks_not_in_roots(conn, [configured_root])

    assert purged == 0
    assert [t["title"] for t in list_tracks(conn)] == ["Song A"]


def test_purge_tracks_not_in_roots_with_no_configured_roots_removes_everything(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    root_a = "D:/RootA"
    replace_tracks(conn, root_a, [_make_track(root_a, "Song A.flac", "Artist", "Song A")])

    purged = purge_tracks_not_in_roots(conn, [])

    assert purged == 1
    assert list_tracks(conn) == []


def test_get_connection_creates_tracks_and_settings_tables(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"tracks", "settings"}.issubset(tables)


def test_get_connection_seeds_default_settings_row(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    row = conn.execute(
        "SELECT media_roots, mirror_roots, device_preference FROM settings WHERE id = 1"
    ).fetchone()
    assert row["media_roots"] == "[]"
    assert row["mirror_roots"] == "[]"
    assert row["device_preference"] == "auto"


def test_get_connection_is_idempotent_across_calls(tmp_path):
    db_path = tmp_path / "library.db"
    get_connection(db_path)
    second_conn = get_connection(db_path)
    row = second_conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()
    assert row["n"] == 1


def test_get_connection_migrates_album_year_duration_columns_onto_an_existing_db(tmp_path):
    db_path = tmp_path / "library.db"
    conn = get_connection(db_path)
    conn.close()

    # Simulate a pre-migration DB: drop the three new columns by rebuilding
    # the table without them, exactly like a real upgrade scenario would
    # start from a DB created before this migration existed.
    import sqlite3
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute("ALTER TABLE tracks RENAME TO tracks_new_with_columns")
    legacy_conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, media_root TEXT NOT NULL, relative_path TEXT NOT NULL,
            absolute_path TEXT NOT NULL, artist TEXT, title TEXT NOT NULL,
            has_instrumental INTEGER NOT NULL DEFAULT 0, has_vocals INTEGER NOT NULL DEFAULT 0,
            has_lead_vocals INTEGER NOT NULL DEFAULT 0, has_backing_vocals INTEGER NOT NULL DEFAULT 0,
            has_drums INTEGER NOT NULL DEFAULT 0, has_bass INTEGER NOT NULL DEFAULT 0,
            has_guitar INTEGER NOT NULL DEFAULT 0, has_piano INTEGER NOT NULL DEFAULT 0,
            has_other INTEGER NOT NULL DEFAULT 0, has_lrc INTEGER NOT NULL DEFAULT 0,
            lrc_state TEXT, stem_count INTEGER NOT NULL DEFAULT 0, last_scanned_at TEXT NOT NULL,
            UNIQUE(media_root, relative_path)
        )
        """
    )
    legacy_conn.execute("DROP TABLE tracks_new_with_columns")
    legacy_conn.commit()
    legacy_conn.close()

    migrated_conn = get_connection(db_path)
    columns = {row["name"] for row in migrated_conn.execute("PRAGMA table_info(tracks)")}
    assert {"album", "year", "duration_seconds"}.issubset(columns)


def test_replace_tracks_stores_album_year_duration(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    track = _make_track("D:/Media", "Song.flac", "ABBA", "Song")
    track.album = "Arrival"
    track.year = 1976
    track.duration_seconds = 213.5

    replace_tracks(conn, "D:/Media", [track])

    row = list_tracks(conn)[0]
    assert row["album"] == "Arrival"
    assert row["year"] == 1976
    assert row["duration_seconds"] == 213.5


def test_replace_tracks_preserves_id_and_updates_metadata_for_an_existing_file(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    original = _make_track("D:/Media", "Song.flac", "Old Artist", "Old Title")
    replace_tracks(conn, "D:/Media", [original])
    original_id = list_tracks(conn)[0]["id"]

    rescanned = _make_track("D:/Media", "Song.flac", "New Artist", "New Title")
    rescanned.outputs = _outputs(lrc=True)
    rescanned.lrc_state = "enhanced"
    rescanned.year = 2026
    replace_tracks(conn, "D:/Media", [rescanned])

    row = list_tracks(conn)[0]
    assert row["id"] == original_id
    assert row["artist"] == "New Artist"
    assert row["title"] == "New Title"
    assert row["outputs"]["lrc"] is True
    assert row["lrc_state"] == "enhanced"
    assert row["year"] == 2026


def test_replace_tracks_preserves_remaining_ids_and_removes_missing_files(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    first = _make_track("D:/Media", "First.flac", "Artist", "First")
    second = _make_track("D:/Media", "Second.flac", "Artist", "Second")
    replace_tracks(conn, "D:/Media", [first, second])
    ids_before = {row["relative_path"]: row["id"] for row in list_tracks(conn)}

    replace_tracks(conn, "D:/Media", [second])

    rows = list_tracks(conn)
    assert len(rows) == 1
    assert rows[0]["relative_path"] == "Second.flac"
    assert rows[0]["id"] == ids_before["Second.flac"]


def test_update_track_tags_updates_and_returns_the_fresh_row(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    replace_tracks(conn, "D:/Media", [_make_track("D:/Media", "Song.flac", "ABBA", "Song")])
    track_id = list_tracks(conn)[0]["id"]

    updated = update_track_tags(
        conn, track_id, artist="New Artist", title="New Title", album="New Album", year=2001
    )

    assert updated["artist"] == "New Artist"
    assert updated["title"] == "New Title"
    assert updated["album"] == "New Album"
    assert updated["year"] == 2001


def test_update_track_tags_returns_none_for_an_unknown_track_id(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    assert update_track_tags(conn, 999, artist="A", title="T", album=None, year=None) is None
