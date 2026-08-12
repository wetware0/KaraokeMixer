from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .instrumental_provenance import UVR_KARAOKE_MODELS, build_instrumental_provenance
from .output_paths import resolve_output_path
from .processing_profiles import PROCESSING_PROFILES, profile_option
from .scanner import PART_NAMES, TrackOutputs, locate_output

# FastAPI serves requests from a threadpool but all routes share a single
# sqlite3 connection (check_same_thread=False). `with conn:` only commits or
# rolls back the connection's current transaction; if two threads interleave
# statements on that shared transaction, one thread's commit can flush the
# other thread's half-finished writes, and a failed writer's rollback then has
# nothing left to undo. Serializing the write blocks with this lock ensures
# each `with conn:` block is atomic from the app's point of view.
_write_lock = threading.Lock()

DEFAULT_DB_PATH = Path(
    os.environ.get("KARAOKE_MM_DATA_DIR", str(Path.home() / ".karaoke-media-manager"))
) / "library.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_root TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    artist TEXT,
    title TEXT NOT NULL,
    has_instrumental INTEGER NOT NULL DEFAULT 0,
    has_vocals INTEGER NOT NULL DEFAULT 0,
    has_lead_vocals INTEGER NOT NULL DEFAULT 0,
    has_backing_vocals INTEGER NOT NULL DEFAULT 0,
    has_drums INTEGER NOT NULL DEFAULT 0,
    has_bass INTEGER NOT NULL DEFAULT 0,
    has_guitar INTEGER NOT NULL DEFAULT 0,
    has_piano INTEGER NOT NULL DEFAULT 0,
    has_other INTEGER NOT NULL DEFAULT 0,
    has_lrc INTEGER NOT NULL DEFAULT 0,
    lrc_state TEXT,
    stem_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT NOT NULL,
    album TEXT,
    year INTEGER,
    duration_seconds REAL,
    instrumental_provenance_json TEXT,
    UNIQUE(media_root, relative_path)
);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    media_roots TEXT NOT NULL DEFAULT '[]',
    mirror_roots TEXT NOT NULL DEFAULT '[]',
    device_preference TEXT NOT NULL DEFAULT 'auto',
    downloads_root TEXT,
    youtube_cookies TEXT NOT NULL DEFAULT '{"mode":"none"}'
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    track_id INTEGER,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT,
    stages_json TEXT NOT NULL DEFAULT '[]',
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS app_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS library_folders (
    path TEXT PRIMARY KEY COLLATE NOCASE,
    media_root TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_items_job_id ON job_items(job_id);
CREATE INDEX IF NOT EXISTS idx_job_items_track_id_id ON job_items(track_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_job_items_job_status_id ON job_items(job_id, status, id);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with _write_lock, conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        _add_column_if_missing(conn, "settings", "downloads_root", "TEXT")
        _add_column_if_missing(conn, "settings", "youtube_cookies", 'TEXT NOT NULL DEFAULT \'{"mode":"none"}\'')
        _add_column_if_missing(conn, "tracks", "album", "TEXT")
        _add_column_if_missing(conn, "tracks", "year", "INTEGER")
        _add_column_if_missing(conn, "tracks", "duration_seconds", "REAL")
        _add_column_if_missing(conn, "tracks", "instrumental_provenance_json", "TEXT")
    _run_instrumental_provenance_backfill_migration(conn)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    """CREATE TABLE IF NOT EXISTS is a no-op on a table that already exists,
    so it never retrofits a new column onto a database created before this
    migration was added - this PRAGMA-based check does, unconditionally, on
    every startup."""
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _run_instrumental_provenance_backfill_migration(conn: sqlite3.Connection) -> None:
    migration_name = "2026-08-10-instrumental-provenance-v1"
    existing = conn.execute("SELECT 1 FROM app_migrations WHERE name = ?", (migration_name,)).fetchone()
    if existing is not None:
        return
    attributed = backfill_known_instrumental_provenance(conn)
    with _write_lock, conn:
        conn.execute(
            "INSERT OR IGNORE INTO app_migrations (name, applied_at, detail_json) VALUES (?, ?, ?)",
            (
                migration_name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps({"instrumentals_attributed": attributed}),
            ),
        )


def _instrumental_provenance_for_scan(existing_json: str | None, track) -> str | None:
    """Preserve provenance only while it still describes the scanned file."""
    if not track.outputs.instrumental or not existing_json:
        return None
    output_path = getattr(track, "instrumental_output_path", None)
    output_mtime_ns = getattr(track, "instrumental_mtime_ns", None)
    output_size = getattr(track, "instrumental_size", None)
    # Older test/import TrackRecord shapes do not carry a signature. They
    # cannot disprove an existing record, whereas the real scanner always can.
    if output_path is None or output_mtime_ns is None or output_size is None:
        return existing_json
    try:
        provenance = json.loads(existing_json)
        same_path = os.path.normcase(os.path.abspath(provenance["output_path"])) == os.path.normcase(
            os.path.abspath(str(output_path))
        )
        if (
            same_path
            and provenance.get("output_mtime_ns") == output_mtime_ns
            and provenance.get("output_size") == output_size
        ):
            return existing_json
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def get_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT media_roots, mirror_roots, device_preference, downloads_root, youtube_cookies FROM settings WHERE id = 1"
    ).fetchone()
    return {
        "media_roots": json.loads(row["media_roots"]),
        "mirror_roots": json.loads(row["mirror_roots"]),
        "device_preference": row["device_preference"],
        "downloads_root": row["downloads_root"],
        "youtube_cookies": json.loads(row["youtube_cookies"]),
    }


def update_settings(conn: sqlite3.Connection, settings: dict) -> dict:
    with _write_lock, conn:
        conn.execute(
            """
            UPDATE settings
            SET media_roots = ?, mirror_roots = ?, device_preference = ?, downloads_root = ?, youtube_cookies = ?
            WHERE id = 1
            """,
            (
                json.dumps(settings["media_roots"]),
                json.dumps(settings["mirror_roots"]),
                settings["device_preference"],
                settings.get("downloads_root"),
                json.dumps(settings.get("youtube_cookies", {"mode": "none"})),
            ),
        )
    return get_settings(conn)


def replace_tracks(conn: sqlite3.Connection, media_root: str, tracks: list) -> None:
    """Upsert a root's fresh scan while preserving ids for unchanged files.

    Rows are always keyed by `media_root` - the exact, raw configured-root
    string (e.g. straight from settings) - never by `track.media_root`, which
    the scanner stamps as str(Path(configured_root)) and so may be spelled
    differently (backslashes vs forward slashes on Windows). Keying by the
    scanner's spelling would let a rescan's DELETE (below) miss rows inserted
    under the raw spelling from a previous scan, and the following INSERT
    would then collide with those leftover rows on the UNIQUE(media_root,
    relative_path) constraint.

    Existing rows are matched by relative path across both the configured and
    legacy-normalized root spellings. Keeping their ids stable is essential:
    open editors, running jobs, and job history all refer to tracks by id. A
    delete/reinsert rescan used to invalidate those references mid-refresh.

    Duplicate legacy spelling variants and files no longer present in the scan
    are removed inside the same transaction.
    """
    normalized_media_root = str(Path(media_root))
    scanned_at = datetime.now(timezone.utc).isoformat()
    with _write_lock, conn:
        existing_rows = conn.execute(
            """
            SELECT id, media_root, relative_path, instrumental_provenance_json
            FROM tracks
            WHERE media_root IN (?, ?)
            ORDER BY CASE WHEN media_root = ? THEN 0 ELSE 1 END, id
            """,
            (media_root, normalized_media_root, media_root),
        ).fetchall()
        existing_by_relative_path: dict[str, int] = {}
        existing_provenance_by_relative_path: dict[str, str | None] = {}
        duplicate_ids: list[int] = []
        for row in existing_rows:
            if row["relative_path"] in existing_by_relative_path:
                duplicate_ids.append(row["id"])
            else:
                existing_by_relative_path[row["relative_path"]] = row["id"]
                existing_provenance_by_relative_path[row["relative_path"]] = row["instrumental_provenance_json"]
        if duplicate_ids:
            conn.executemany("DELETE FROM tracks WHERE id = ?", ((track_id,) for track_id in duplicate_ids))

        retained_ids: set[int] = set()
        for track in tracks:
            values = (
                media_root,
                track.relative_path,
                track.absolute_path,
                track.artist,
                track.title,
                int(track.outputs.instrumental),
                int(track.outputs.vocals),
                int(track.outputs.lead_vocals),
                int(track.outputs.backing_vocals),
                int(track.outputs.drums),
                int(track.outputs.bass),
                int(track.outputs.guitar),
                int(track.outputs.piano),
                int(track.outputs.other),
                int(track.outputs.lrc),
                track.lrc_state,
                track.stem_count,
                scanned_at,
                track.album,
                track.year,
                track.duration_seconds,
                _instrumental_provenance_for_scan(
                    existing_provenance_by_relative_path.get(track.relative_path), track
                ),
            )
            existing_id = existing_by_relative_path.get(track.relative_path)
            if existing_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO tracks (
                        media_root, relative_path, absolute_path, artist, title,
                        has_instrumental, has_vocals, has_lead_vocals, has_backing_vocals,
                        has_drums, has_bass, has_guitar, has_piano, has_other, has_lrc,
                        lrc_state, stem_count, last_scanned_at, album, year, duration_seconds,
                        instrumental_provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                retained_ids.add(cursor.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE tracks SET
                        media_root = ?, relative_path = ?, absolute_path = ?, artist = ?, title = ?,
                        has_instrumental = ?, has_vocals = ?, has_lead_vocals = ?, has_backing_vocals = ?,
                        has_drums = ?, has_bass = ?, has_guitar = ?, has_piano = ?, has_other = ?, has_lrc = ?,
                        lrc_state = ?, stem_count = ?, last_scanned_at = ?, album = ?, year = ?, duration_seconds = ?,
                        instrumental_provenance_json = ?
                    WHERE id = ?
                    """,
                    (*values, existing_id),
                )
                retained_ids.add(existing_id)

        stale_ids = [row["id"] for row in existing_rows if row["id"] not in retained_ids]
        if stale_ids:
            conn.executemany("DELETE FROM tracks WHERE id = ?", ((track_id,) for track_id in stale_ids))


def upsert_track_scan_batch(
    conn: sqlite3.Connection,
    media_root: str,
    tracks: list,
    scan_token: str,
) -> None:
    """Publish one completed batch from an in-progress library scan.

    Unlike :func:`replace_tracks`, this deliberately does not delete rows
    absent from *this* batch. The background scanner calls
    :func:`finish_track_root_scan` only after the complete root has been
    traversed, so existing tracks never disappear merely because their batch
    has not been reached yet. Existing ids are preserved for open editors and
    queued processing jobs.
    """
    if not tracks:
        return

    normalized_media_root = str(Path(media_root))
    relative_paths = [track.relative_path for track in tracks]
    placeholders = ", ".join("?" for _ in relative_paths)
    with _write_lock, conn:
        existing_rows = conn.execute(
            f"""
            SELECT id, media_root, relative_path, instrumental_provenance_json
            FROM tracks
            WHERE media_root IN (?, ?)
              AND relative_path IN ({placeholders})
            ORDER BY CASE WHEN media_root = ? THEN 0 ELSE 1 END, id
            """,
            (media_root, normalized_media_root, *relative_paths, media_root),
        ).fetchall()
        existing_by_relative_path: dict[str, int] = {}
        existing_provenance_by_relative_path: dict[str, str | None] = {}
        duplicate_ids: list[int] = []
        for row in existing_rows:
            if row["relative_path"] in existing_by_relative_path:
                duplicate_ids.append(row["id"])
            else:
                existing_by_relative_path[row["relative_path"]] = row["id"]
                existing_provenance_by_relative_path[row["relative_path"]] = row["instrumental_provenance_json"]
        if duplicate_ids:
            conn.executemany("DELETE FROM tracks WHERE id = ?", ((track_id,) for track_id in duplicate_ids))

        for track in tracks:
            values = (
                media_root,
                track.relative_path,
                track.absolute_path,
                track.artist,
                track.title,
                int(track.outputs.instrumental),
                int(track.outputs.vocals),
                int(track.outputs.lead_vocals),
                int(track.outputs.backing_vocals),
                int(track.outputs.drums),
                int(track.outputs.bass),
                int(track.outputs.guitar),
                int(track.outputs.piano),
                int(track.outputs.other),
                int(track.outputs.lrc),
                track.lrc_state,
                track.stem_count,
                scan_token,
                track.album,
                track.year,
                track.duration_seconds,
                _instrumental_provenance_for_scan(
                    existing_provenance_by_relative_path.get(track.relative_path), track
                ),
            )
            existing_id = existing_by_relative_path.get(track.relative_path)
            if existing_id is None:
                conn.execute(
                    """
                    INSERT INTO tracks (
                        media_root, relative_path, absolute_path, artist, title,
                        has_instrumental, has_vocals, has_lead_vocals, has_backing_vocals,
                        has_drums, has_bass, has_guitar, has_piano, has_other, has_lrc,
                        lrc_state, stem_count, last_scanned_at, album, year, duration_seconds,
                        instrumental_provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            else:
                conn.execute(
                    """
                    UPDATE tracks SET
                        media_root = ?, relative_path = ?, absolute_path = ?, artist = ?, title = ?,
                        has_instrumental = ?, has_vocals = ?, has_lead_vocals = ?, has_backing_vocals = ?,
                        has_drums = ?, has_bass = ?, has_guitar = ?, has_piano = ?, has_other = ?, has_lrc = ?,
                        lrc_state = ?, stem_count = ?, last_scanned_at = ?, album = ?, year = ?, duration_seconds = ?,
                        instrumental_provenance_json = ?
                    WHERE id = ?
                    """,
                    (*values, existing_id),
                )


def finish_track_root_scan(conn: sqlite3.Connection, media_root: str, scan_token: str) -> int:
    """Remove stale rows only after a root completed successfully."""
    normalized_media_root = str(Path(media_root))
    with _write_lock, conn:
        cursor = conn.execute(
            """
            DELETE FROM tracks
            WHERE media_root IN (?, ?)
              AND last_scanned_at <> ?
            """,
            (media_root, normalized_media_root, scan_token),
        )
        return cursor.rowcount


def purge_tracks_not_in_roots(conn: sqlite3.Connection, media_roots: list[str]) -> int:
    """Delete tracks whose media_root is no longer among the configured roots.

    Called after a rescan to clean up rows left behind when a media root is
    removed from settings entirely (as opposed to a still-configured root
    that is merely unavailable on disk right now, e.g. an unmounted drive -
    those rows must survive, so the caller passes the full configured
    media_roots list regardless of which roots were actually scannable).

    Mirrors replace_tracks' spelling tolerance (see its docstring): settings
    store the raw configured string (e.g. "D:/x"), but a row may carry the
    str(Path(...))-normalized spelling (e.g. "D:\\x") from the scanner or a
    legacy database. A configured root keeps rows spelled either way, so both
    spellings of each configured root are treated as "still configured".

    Returns the number of rows deleted.
    """
    acceptable: set[str] = set()
    for root in media_roots:
        acceptable.add(root)
        acceptable.add(str(Path(root)))

    with _write_lock, conn:
        if not acceptable:
            cursor = conn.execute("DELETE FROM tracks")
        else:
            placeholders = ", ".join("?" for _ in acceptable)
            cursor = conn.execute(
                f"DELETE FROM tracks WHERE media_root NOT IN ({placeholders})",
                tuple(acceptable),
            )
        return cursor.rowcount


def _row_to_track(row: sqlite3.Row) -> dict:
    provenance = None
    if row["instrumental_provenance_json"]:
        try:
            stored_provenance = json.loads(row["instrumental_provenance_json"])
            if isinstance(stored_provenance, dict):
                # The catalogue endpoint is intentionally compact for 80,000+
                # rows. File signatures and absolute paths remain in SQLite
                # for rescan validation; the Library receives only what it
                # needs to display and explain quality.
                provenance = {
                    key: stored_provenance.get(key)
                    for key in (
                        "schema_version", "part", "quality", "engine", "engine_version",
                        "model", "models", "backing_vocal_mode", "device", "job_id",
                        "stage", "attribution", "confirmed_by", "recorded_at",
                    )
                }
        except (TypeError, json.JSONDecodeError):
            provenance = None
    return {
        "id": row["id"],
        "media_root": row["media_root"],
        "relative_path": row["relative_path"],
        "artist": row["artist"],
        "title": row["title"],
        "outputs": {
            "instrumental": bool(row["has_instrumental"]),
            "vocals": bool(row["has_vocals"]),
            "lead_vocals": bool(row["has_lead_vocals"]),
            "backing_vocals": bool(row["has_backing_vocals"]),
            "drums": bool(row["has_drums"]),
            "bass": bool(row["has_bass"]),
            "guitar": bool(row["has_guitar"]),
            "piano": bool(row["has_piano"]),
            "other": bool(row["has_other"]),
            "lrc": bool(row["has_lrc"]),
        },
        "lrc_state": row["lrc_state"],
        "stem_count": row["stem_count"],
        "album": row["album"],
        "year": row["year"],
        "duration_seconds": row["duration_seconds"],
        "instrumental_provenance": provenance,
    }


def list_tracks(conn: sqlite3.Connection, query: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tracks"
    params: tuple = ()
    if query:
        sql += " WHERE artist LIKE ? OR title LIKE ? OR relative_path LIKE ?"
        wildcard = f"%{query}%"
        params = (wildcard, wildcard, wildcard)
    sql += " ORDER BY artist, title"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_track(row) for row in rows]


def get_track(conn: sqlite3.Connection, track_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        return None
    track = _row_to_track(row)
    track["absolute_path"] = row["absolute_path"]  # internal use only; never in API responses
    return track


def record_instrumental_provenance(
    conn: sqlite3.Connection,
    track_id: int,
    job_id: int,
    job_item_id: int,
    stage_name: str,
    provenance: dict,
    *,
    attribution: str = "confirmed",
    recorded_at: str | None = None,
) -> dict | None:
    """Attach the writer and exact file signature to the current instrumental."""
    if provenance.get("part") != "instrumental":
        return None
    track_row = conn.execute("SELECT absolute_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if track_row is None:
        return None
    enriched = _enrich_instrumental_provenance(
        Path(track_row["absolute_path"]),
        job_id,
        job_item_id,
        stage_name,
        provenance,
        attribution=attribution,
        recorded_at=recorded_at,
    )
    if enriched is None:
        return None
    with _write_lock, conn:
        conn.execute(
            "UPDATE tracks SET has_instrumental = 1, instrumental_provenance_json = ? WHERE id = ?",
            (json.dumps(enriched, sort_keys=True), track_id),
        )
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return _row_to_track(row) if row is not None else None


def confirm_instrumental_quality_for_artists(
    conn: sqlite3.Connection,
    artists: list[str],
    quality: str,
    *,
    confirmed_by: str,
    audit_name: str | None = None,
) -> dict:
    """Apply a user-confirmed quality without inventing processing history.

    Exact displayed-artist matching avoids substring surprises such as ABBA
    matching Black Sabbath. The current output signature is retained so a
    later rescan clears the confirmation if that instrumental is replaced.
    """
    exact_artists = sorted({artist.strip() for artist in artists if artist.strip()})
    if not exact_artists:
        raise ValueError("at least one artist is required")
    if quality not in PROCESSING_PROFILES:
        raise ValueError(f"unsupported instrumental quality: {quality}")
    confirmer = confirmed_by.strip()
    if not confirmer:
        raise ValueError("confirmed_by is required")

    placeholders = ", ".join("?" for _ in exact_artists)
    rows = conn.execute(
        f"""
        SELECT id, media_root, absolute_path, artist
        FROM tracks
        WHERE artist IN ({placeholders}) AND has_instrumental = 1
        ORDER BY id
        """,
        tuple(exact_artists),
    ).fetchall()
    mirror_roots = [Path(root) for root in get_settings(conn)["mirror_roots"]]
    recorded_at = datetime.now(timezone.utc).isoformat()
    updates: list[tuple[str, int]] = []
    skipped: list[int] = []
    counts_by_artist = {artist: 0 for artist in exact_artists}
    for row in rows:
        source_path = Path(row["absolute_path"])
        output_path = locate_output(
            source_path,
            Path(row["media_root"]),
            mirror_roots,
            ".instrumental.mp3",
        )
        if output_path is None:
            skipped.append(row["id"])
            continue
        base = {
            "schema_version": 1,
            "part": "instrumental",
            "quality": quality,
            "engine": "manual_confirmation",
            "engine_version": None,
            "model": "user_confirmed",
            "models": [],
            "backing_vocal_mode": "unknown",
            "device": None,
            "output_mode": "existing",
            "output_path": str(output_path),
            "confirmed_by": confirmer,
        }
        enriched = _enrich_instrumental_provenance(
            source_path,
            None,
            None,
            "manual_confirmation",
            base,
            attribution="manual",
            recorded_at=recorded_at,
        )
        if enriched is None:
            skipped.append(row["id"])
            continue
        updates.append((json.dumps(enriched, sort_keys=True), row["id"]))
        counts_by_artist[row["artist"]] += 1

    detail = {
        "artists": exact_artists,
        "quality": quality,
        "confirmed_by": confirmer,
        "tracks_updated": len(updates),
        "tracks_skipped": len(skipped),
        "counts_by_artist": counts_by_artist,
    }
    with _write_lock, conn:
        if updates:
            conn.executemany(
                "UPDATE tracks SET instrumental_provenance_json = ? WHERE id = ?",
                updates,
            )
        if audit_name:
            conn.execute(
                """
                INSERT INTO app_migrations (name, applied_at, detail_json)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    applied_at = excluded.applied_at,
                    detail_json = excluded.detail_json
                """,
                (audit_name, recorded_at, json.dumps(detail, sort_keys=True)),
            )
    return detail


def _enrich_instrumental_provenance(
    source_path: Path,
    job_id: int | None,
    job_item_id: int | None,
    stage_name: str,
    provenance: dict,
    *,
    attribution: str,
    recorded_at: str | None,
) -> dict | None:
    output_path = Path(str(provenance.get("output_path", "")))
    try:
        output_stat = output_path.stat()
    except OSError:
        return None
    try:
        source_stat = source_path.stat()
    except OSError:
        source_stat = None
    enriched = {
        **provenance,
        "job_id": job_id,
        "job_item_id": job_item_id,
        "stage": stage_name,
        "attribution": attribution,
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
        "output_path": str(output_path),
        "output_mtime_ns": output_stat.st_mtime_ns,
        "output_size": output_stat.st_size,
        "source_path": str(source_path),
        "source_mtime_ns": source_stat.st_mtime_ns if source_stat else None,
        "source_size": source_stat.st_size if source_stat else None,
    }
    return enriched


def backfill_known_instrumental_provenance(conn: sqlite3.Connection) -> int:
    """Infer current provenance only when history and file time agree.

    A completed producer stage proves what that job wrote, while the final
    instrumental's mtime proves it is still that artifact. A ten-second
    tolerance covers filesystem timestamp precision without claiming copied,
    externally replaced, or otherwise ambiguous outputs.
    """
    rows = conn.execute(
        """
        SELECT
            track.id AS track_id,
            item.id AS item_id,
            item.source_path,
            item.stages_json,
            job.id AS job_id,
            job.recipe,
            job.settings_json
        FROM tracks AS track
        JOIN job_items AS item ON item.track_id = track.id
        JOIN jobs AS job ON job.id = item.job_id
        WHERE track.has_instrumental = 1
          AND track.instrumental_provenance_json IS NULL
          AND job.recipe IN ('karaoke', 'full_prep')
        ORDER BY item.id DESC
        """
    ).fetchall()
    best_by_track: dict[int, tuple[datetime, sqlite3.Row, dict, str, dict]] = {}
    for row in rows:
        try:
            options = json.loads(row["settings_json"])
            stages = json.loads(row["stages_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        backing_mode = str(profile_option(options, "backing_vocal_mode", "stripped"))
        producer_stage = (
            "demucs_separate"
            if row["recipe"] == "full_prep" and backing_mode != "best"
            else "karaoke_instrumental"
        )
        stage = next(
            (entry for entry in stages if entry.get("name") == producer_stage and entry.get("status") == "completed"),
            None,
        )
        if not stage or not stage.get("finished_at"):
            continue
        try:
            finished_at = datetime.fromisoformat(stage["finished_at"])
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=timezone.utc)
            output_path = resolve_output_path(Path(row["source_path"]), "instrumental", options)
            output_stat = output_path.stat()
        except (OSError, TypeError, ValueError):
            continue
        if abs(output_stat.st_mtime - finished_at.timestamp()) > 10:
            continue
        if backing_mode == "best":
            base = build_instrumental_provenance(
                options,
                output_path,
                engine="uvr_karaoke_ensemble",
                engine_version="audio-separator==0.44.5",
                model="karaoke",
                models=UVR_KARAOKE_MODELS,
                backing_vocal_mode=backing_mode,
            )
        else:
            base = build_instrumental_provenance(
                options,
                output_path,
                engine="demucs",
                model=str(profile_option(options, "model", "htdemucs")),
                backing_vocal_mode=backing_mode,
            )
        existing = best_by_track.get(row["track_id"])
        if existing is None or finished_at > existing[0]:
            best_by_track[row["track_id"]] = (finished_at, row, base, producer_stage, stage)

    updates: list[tuple[str, int]] = []
    for track_id, (finished_at, row, base, producer_stage, _stage) in best_by_track.items():
        enriched = _enrich_instrumental_provenance(
            Path(row["source_path"]),
            row["job_id"],
            row["item_id"],
            producer_stage,
            base,
            attribution="inferred",
            recorded_at=finished_at.isoformat(),
        )
        if enriched is not None:
            updates.append((json.dumps(enriched, sort_keys=True), track_id))
    if updates:
        with _write_lock, conn:
            conn.executemany(
                "UPDATE tracks SET has_instrumental = 1, instrumental_provenance_json = ? WHERE id = ?",
                updates,
            )
    return len(updates)


def track_has_active_job(conn: sqlite3.Connection, track_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM job_items AS item
        JOIN jobs AS job ON job.id = item.job_id
        WHERE item.track_id = ? AND job.status IN ('queued', 'running')
        LIMIT 1
        """,
        (track_id,),
    ).fetchone()
    return row is not None


def delete_track_record(conn: sqlite3.Connection, track_id: int) -> bool:
    """Remove a catalogue row while retaining historical job records."""
    with _write_lock, conn:
        conn.execute("UPDATE job_items SET track_id = NULL WHERE track_id = ?", (track_id,))
        cursor = conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    return cursor.rowcount > 0


def remember_library_folder(conn: sqlite3.Connection, path: Path, media_root: Path) -> None:
    """Keep user-created empty folders visible in the Library tree.

    Non-empty folders are already derived from track rows. Persisting only the
    folders the user explicitly creates avoids a second 80,000-file directory
    walk merely to rediscover empty directories on every Library visit.
    """
    with _write_lock, conn:
        conn.execute(
            "INSERT OR REPLACE INTO library_folders (path, media_root) VALUES (?, ?)",
            (str(path), str(media_root)),
        )


def list_remembered_library_folders(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute("SELECT path, media_root FROM library_folders ORDER BY path COLLATE NOCASE").fetchall()
    return [{"path": row["path"], "media_root": row["media_root"]} for row in rows]


def relocate_remembered_library_folders(conn: sqlite3.Connection, source: Path, destination: Path) -> None:
    """Rewrite persisted empty-folder paths below a renamed folder."""
    updates: list[tuple[str, str]] = []
    removals: list[str] = []
    for row in list_remembered_library_folders(conn):
        path = Path(row["path"])
        try:
            suffix = path.resolve().relative_to(source.resolve())
        except (OSError, ValueError):
            continue
        removals.append(row["path"])
        updates.append((str(destination / suffix), row["media_root"]))
    if not removals:
        return
    with _write_lock, conn:
        conn.executemany("DELETE FROM library_folders WHERE path = ?", [(path,) for path in removals])
        conn.executemany(
            "INSERT OR REPLACE INTO library_folders (path, media_root) VALUES (?, ?)",
            updates,
        )


def forget_remembered_library_folders(conn: sqlite3.Connection, folder: Path) -> None:
    removals: list[str] = []
    for row in list_remembered_library_folders(conn):
        try:
            Path(row["path"]).resolve().relative_to(folder.resolve())
        except (OSError, ValueError):
            continue
        removals.append(row["path"])
    if removals:
        with _write_lock, conn:
            conn.executemany("DELETE FROM library_folders WHERE path = ?", [(path,) for path in removals])


def relocate_track_records(conn: sqlite3.Connection, relocations: list[dict]) -> list[dict]:
    """Update catalogue locations without changing stable track ids/history."""
    if not relocations:
        return []
    with _write_lock, conn:
        for relocation in relocations:
            row = conn.execute(
                "SELECT instrumental_provenance_json FROM tracks WHERE id = ?",
                (relocation["track_id"],),
            ).fetchone()
            provenance_json = row["instrumental_provenance_json"] if row else None
            if provenance_json and relocation.get("path_map"):
                try:
                    provenance = json.loads(provenance_json)
                    old_output = os.path.normcase(os.path.abspath(str(provenance.get("output_path", ""))))
                    for old_path, new_path in relocation["path_map"].items():
                        if old_output == os.path.normcase(os.path.abspath(old_path)):
                            provenance["output_path"] = new_path
                            provenance_json = json.dumps(provenance, sort_keys=True)
                            break
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            conn.execute(
                """
                UPDATE tracks
                SET media_root = ?, relative_path = ?, absolute_path = ?,
                    instrumental_provenance_json = ?
                WHERE id = ?
                """,
                (
                    relocation["media_root"],
                    relocation["relative_path"],
                    relocation["absolute_path"],
                    provenance_json,
                    relocation["track_id"],
                ),
            )
    return [track for track_id in [item["track_id"] for item in relocations] if (track := get_track(conn, track_id))]


def delete_track_records(conn: sqlite3.Connection, track_ids: list[int]) -> int:
    """Remove several catalogue rows while retaining their historical jobs."""
    unique_ids = list(dict.fromkeys(track_ids))
    if not unique_ids:
        return 0
    placeholders = ", ".join("?" for _ in unique_ids)
    with _write_lock, conn:
        conn.execute(f"UPDATE job_items SET track_id = NULL WHERE track_id IN ({placeholders})", unique_ids)
        cursor = conn.execute(f"DELETE FROM tracks WHERE id IN ({placeholders})", unique_ids)
    return cursor.rowcount


def update_track_tags(
    conn: sqlite3.Connection, track_id: int, *, artist: str | None, title: str, album: str | None, year: int | None
) -> dict | None:
    """Targeted single-row update after a tag-editor Save (Task 4's
    PUT .../tags) - deliberately NOT a full filesystem rescan: the file's
    stems/lrc/duration are unaffected by a text-tag edit, so recomputing
    just the four edited fields and re-reading the row is both correct and
    far cheaper than re-walking the whole media root."""
    with _write_lock, conn:
        conn.execute(
            "UPDATE tracks SET artist = ?, title = ?, album = ?, year = ? WHERE id = ?",
            (artist, title, album, year, track_id),
        )
    return get_track(conn, track_id)


def update_track_lrc_state(conn: sqlite3.Connection, track_id: int, lrc_state: str | None) -> dict | None:
    """Publish a canonical lyric save to the library row immediately.

    A lyric edit changes only the two lyric-derived columns, so making the
    editor wait for a full media-root rescan is both slow and unnecessary.
    Return the public track shape (without ``absolute_path``) so the client
    can replace the visible row as part of the successful save response.
    """
    with _write_lock, conn:
        conn.execute(
            "UPDATE tracks SET has_lrc = ?, lrc_state = ? WHERE id = ?",
            (int(lrc_state is not None), lrc_state, track_id),
        )
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return _row_to_track(row) if row is not None else None


def update_track_outputs(
    conn: sqlite3.Connection,
    track_id: int,
    outputs: TrackOutputs,
    lrc_state: str | None,
) -> dict | None:
    """Publish one processing result to its existing catalogue row.

    Processing stages create sidecars beside the source or in a mirror root;
    they do not require tags, duration, or any unrelated source file to be
    read again.  Updating the ten output-derived columns here avoids turning
    every completed job into a full media-root rescan.
    """
    stem_count = sum(
        1 for part in PART_NAMES if part != "instrumental" and getattr(outputs, part)
    )
    with _write_lock, conn:
        conn.execute(
            """
            UPDATE tracks
            SET has_instrumental = ?, has_vocals = ?, has_lead_vocals = ?,
                has_backing_vocals = ?, has_drums = ?, has_bass = ?,
                has_guitar = ?, has_piano = ?, has_other = ?, has_lrc = ?,
                lrc_state = ?, stem_count = ?,
                instrumental_provenance_json = CASE
                    WHEN ? THEN instrumental_provenance_json ELSE NULL
                END
            WHERE id = ?
            """,
            (
                int(outputs.instrumental), int(outputs.vocals),
                int(outputs.lead_vocals), int(outputs.backing_vocals),
                int(outputs.drums), int(outputs.bass), int(outputs.guitar),
                int(outputs.piano), int(outputs.other), int(outputs.lrc),
                lrc_state, stem_count, int(outputs.instrumental), track_id,
            ),
        )
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return _row_to_track(row) if row is not None else None


JOB_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}
JOB_ITEM_STATUSES = {"queued", "running", "completed", "failed", "skipped", "cancelled"}


def create_job(conn: sqlite3.Connection, recipe: str, options: dict, items: list[dict]) -> int:
    """Insert a job row and its job_items rows in one transaction. Returns job_id."""
    created_at = datetime.now(timezone.utc).isoformat()
    with _write_lock, conn:
        cursor = conn.execute(
            "INSERT INTO jobs (recipe, settings_json, status, created_at) VALUES (?, ?, 'queued', ?)",
            (recipe, json.dumps(options), created_at),
        )
        job_id = cursor.lastrowid
        for item in items:
            conn.execute(
                """
                INSERT INTO job_items (job_id, track_id, source_path, status, stages_json)
                VALUES (?, ?, ?, 'queued', '[]')
                """,
                (job_id, item.get("track_id"), item["source_path"]),
            )
    return job_id


def _row_to_job_item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "track_id": row["track_id"],
        "source_path": row["source_path"],
        "status": row["status"],
        "current_stage": row["current_stage"],
        "stages": json.loads(row["stages_json"]),
        "error_text": row["error_text"],
    }


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job_row is None:
        return None
    item_rows = conn.execute(
        "SELECT * FROM job_items WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    return {
        "id": job_row["id"],
        "recipe": job_row["recipe"],
        "options": json.loads(job_row["settings_json"]),
        "status": job_row["status"],
        "created_at": job_row["created_at"],
        "started_at": job_row["started_at"],
        "finished_at": job_row["finished_at"],
        "items": [_row_to_job_item(row) for row in item_rows],
    }


def list_jobs(conn: sqlite3.Connection) -> list[dict]:
    """List all jobs newest-first, each annotated with its per-status item
    counts. Item counts are computed with a single grouped aggregate query
    across all jobs (not one query per job) to avoid N+1 query behavior as
    the jobs table grows."""
    job_rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    count_rows = conn.execute(
        "SELECT job_id, status, COUNT(*) AS n FROM job_items GROUP BY job_id, status"
    ).fetchall()
    counts_by_job: dict[int, dict[str, int]] = {}
    for row in count_rows:
        job_counts = counts_by_job.setdefault(row["job_id"], {status: 0 for status in JOB_ITEM_STATUSES})
        job_counts[row["status"]] = row["n"]

    jobs = []
    for job_row in job_rows:
        counts = counts_by_job.get(job_row["id"], {status: 0 for status in JOB_ITEM_STATUSES})
        jobs.append(
            {
                "id": job_row["id"],
                "recipe": job_row["recipe"],
                "options": json.loads(job_row["settings_json"]),
                "status": job_row["status"],
                "created_at": job_row["created_at"],
                "started_at": job_row["started_at"],
                "finished_at": job_row["finished_at"],
                "item_counts": counts,
            }
        )
    return jobs


def list_job_history(
    conn: sqlite3.Connection,
    *,
    statuses: set[str] | None = None,
    query: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Return one bounded page of job summaries for the History screen."""
    conditions: list[str] = []
    params: list[object] = []
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        conditions.append(f"jobs.status IN ({placeholders})")
        params.extend(sorted(statuses))
    cleaned_query = query.strip()
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        conditions.append(
            "(CAST(jobs.id AS TEXT) LIKE ? OR jobs.recipe LIKE ? OR EXISTS ("
            "SELECT 1 FROM job_items AS searched_item "
            "WHERE searched_item.job_id = jobs.id AND searched_item.source_path LIKE ?))"
        )
        params.extend([pattern, pattern, pattern])
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()[0]
    job_rows = conn.execute(
        f"SELECT * FROM jobs{where} ORDER BY jobs.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    job_ids = [row["id"] for row in job_rows]
    counts_by_job: dict[int, dict[str, int]] = {}
    if job_ids:
        placeholders = ", ".join("?" for _ in job_ids)
        count_rows = conn.execute(
            f"SELECT job_id, status, COUNT(*) AS n FROM job_items "
            f"WHERE job_id IN ({placeholders}) GROUP BY job_id, status",
            job_ids,
        ).fetchall()
        for row in count_rows:
            counts = counts_by_job.setdefault(
                row["job_id"], {status: 0 for status in JOB_ITEM_STATUSES}
            )
            counts[row["status"]] = row["n"]

    jobs = [
        {
            "id": row["id"],
            "recipe": row["recipe"],
            "options": json.loads(row["settings_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "item_counts": counts_by_job.get(row["id"], {status: 0 for status in JOB_ITEM_STATUSES}),
        }
        for row in job_rows
    ]
    return {"jobs": jobs, "total": total, "limit": limit, "offset": offset}


def list_job_items_page(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str | None = None,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict | None:
    """Return a bounded, searchable page of track results for one job."""
    if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        return None
    conditions = ["job_id = ?"]
    params: list[object] = [job_id]
    if status:
        conditions.append("status = ?")
        params.append(status)
    cleaned_query = query.strip()
    if cleaned_query:
        conditions.append("source_path LIKE ?")
        params.append(f"%{cleaned_query}%")
    where = " AND ".join(conditions)
    total = conn.execute(f"SELECT COUNT(*) FROM job_items WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM job_items WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {
        "items": [_row_to_job_item(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _processing_error_summary(error_text: str | None) -> str:
    if not error_text:
        return "Processing failed"
    lowered = error_text.lower()
    if "stereo needs to be set to true" in lowered:
        return "Surround audio could not be processed by the stereo separation model"
    if "pytorchstreamreader" in lowered and "central directory" in lowered:
        return "A UVR model download was incomplete or corrupt"
    first_line = next((line.strip() for line in error_text.splitlines() if line.strip()), "")
    return first_line or "Processing failed"


def list_track_processing_failures(conn: sqlite3.Connection) -> list[dict]:
    """Return only unresolved failures from each track's latest job item.

    A later queued/running attempt suppresses the old failure while it is in
    progress; a later completed/skipped item clears it permanently. This keeps
    the Library truthful without attaching stale error state to the track row.
    The covering index above keeps the latest-item lookup bounded for large
    catalogues and long job histories.
    """
    rows = conn.execute(
        """
        SELECT item.track_id, item.job_id, item.stages_json, item.error_text
        FROM job_items AS item
        JOIN (
            SELECT track_id, MAX(id) AS latest_item_id
            FROM job_items
            WHERE track_id IS NOT NULL
            GROUP BY track_id
        ) AS latest ON latest.latest_item_id = item.id
        WHERE item.status = 'failed'
        ORDER BY item.id DESC
        """
    ).fetchall()
    failures: list[dict] = []
    for row in rows:
        stages = json.loads(row["stages_json"])
        failed_stage = next((stage.get("name") for stage in stages if stage.get("status") == "failed"), None)
        failures.append(
            {
                "track_id": row["track_id"],
                "job_id": row["job_id"],
                "stage": failed_stage,
                "message": _processing_error_summary(row["error_text"]),
            }
        )
    return failures


def set_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    with _write_lock, conn:
        if started_at is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?", (status, started_at, job_id)
            )
        elif finished_at is not None:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?", (status, finished_at, job_id)
            )
        else:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def set_item_status(
    conn: sqlite3.Connection, item_id: int, status: str, *, current_stage: str | None = None
) -> None:
    with _write_lock, conn:
        conn.execute(
            "UPDATE job_items SET status = ?, current_stage = ? WHERE id = ?",
            (status, current_stage, item_id),
        )


def set_item_stages(conn: sqlite3.Connection, item_id: int, stages: list[dict]) -> None:
    with _write_lock, conn:
        conn.execute("UPDATE job_items SET stages_json = ? WHERE id = ?", (json.dumps(stages), item_id))


def set_item_error(conn: sqlite3.Connection, item_id: int, error_text: str) -> None:
    with _write_lock, conn:
        conn.execute("UPDATE job_items SET error_text = ? WHERE id = ?", (error_text, item_id))


def reset_stuck_jobs(conn: sqlite3.Connection) -> list[int]:
    """Crash recovery: any job left 'running' or 'queued' from a previous
    process is swept back to a clean 'queued' state so it can be re-enqueued.

    'running' jobs were mid-execution when the process died; their 'running'
    items are reset to 'queued' too (stages that already completed are
    untouched in stages_json, so the resumed run's skip-if-exists logic picks
    up where it left off). 'queued' jobs never got past the in-memory lane
    queue, which does not survive a restart - without this sweep they would
    stay 'queued' in the database forever with no worker aware of them.

    Returns the affected job ids ordered by id (submission order) so the
    caller can re-enqueue them in the order they were originally submitted."""
    with _write_lock, conn:
        stuck_rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('running', 'queued') ORDER BY id"
        ).fetchall()
        job_ids = [row["id"] for row in stuck_rows]
        for job_id in job_ids:
            conn.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL WHERE id = ?", (job_id,)
            )
            conn.execute(
                "UPDATE job_items SET status = 'queued', current_stage = NULL "
                "WHERE job_id = ? AND status = 'running'",
                (job_id,),
            )
    return job_ids
