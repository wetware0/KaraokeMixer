import sqlite3

from app.db import get_connection, get_settings, init_db, update_settings


def test_get_settings_returns_defaults(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    assert get_settings(conn) == {
        "media_roots": [],
        "mirror_roots": [],
        "device_preference": "auto",
        "downloads_root": None,
        "youtube_cookies": {"mode": "none"},
    }


def test_update_settings_persists_and_returns_new_values(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    updated = update_settings(
        conn,
        {
            "media_roots": ["D:/Media/ABBA"],
            "mirror_roots": ["D:/Stems"],
            "device_preference": "cpu",
            "downloads_root": "D:/Downloads",
            "youtube_cookies": {"mode": "browser", "browser": "chrome"},
        },
    )
    assert updated == {
        "media_roots": ["D:/Media/ABBA"],
        "mirror_roots": ["D:/Stems"],
        "device_preference": "cpu",
        "downloads_root": "D:/Downloads",
        "youtube_cookies": {"mode": "browser", "browser": "chrome"},
    }
    assert get_settings(conn) == updated


def test_init_db_migrates_a_pre_existing_settings_table_missing_the_new_columns(tmp_path):
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            media_roots TEXT NOT NULL DEFAULT '[]',
            mirror_roots TEXT NOT NULL DEFAULT '[]',
            device_preference TEXT NOT NULL DEFAULT 'auto'
        );
        """
    )
    conn.execute("INSERT INTO settings (id) VALUES (1)")
    conn.commit()

    init_db(conn)  # simulates opening a database created before Milestone 2c

    settings = get_settings(conn)
    assert settings["downloads_root"] is None
    assert settings["youtube_cookies"] == {"mode": "none"}

    # Binding constraint: every app startup re-runs init_db() against a
    # database that may already have been migrated (by this same process on
    # a prior run, or by a prior process entirely). A second init_db() call
    # on the same connection must not raise (e.g. "duplicate column name")
    # and must not duplicate the columns.
    init_db(conn)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(settings)")]
    assert columns.count("downloads_root") == 1
    assert columns.count("youtube_cookies") == 1
    settings = get_settings(conn)
    assert settings["downloads_root"] is None
    assert settings["youtube_cookies"] == {"mode": "none"}

    # Re-opening the same database file (a fresh process attaching to a
    # database this test already migrated) must also be safe, and settings
    # must still round-trip correctly afterward.
    conn.close()
    reopened = get_connection(db_path)
    reopened_columns = [row["name"] for row in reopened.execute("PRAGMA table_info(settings)")]
    assert reopened_columns.count("downloads_root") == 1
    assert reopened_columns.count("youtube_cookies") == 1
    updated = update_settings(
        reopened,
        {
            "media_roots": ["D:/Media/ABBA"],
            "mirror_roots": [],
            "device_preference": "auto",
            "downloads_root": "D:/Downloads",
            "youtube_cookies": {"mode": "file", "cookies_file": "C:/cookies.txt"},
        },
    )
    assert updated == {
        "media_roots": ["D:/Media/ABBA"],
        "mirror_roots": [],
        "device_preference": "auto",
        "downloads_root": "D:/Downloads",
        "youtube_cookies": {"mode": "file", "cookies_file": "C:/cookies.txt"},
    }
    assert get_settings(reopened) == updated


from fastapi.testclient import TestClient

from app.main import create_app


def test_get_settings_route_returns_defaults(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json() == {
        "media_roots": [],
        "mirror_roots": [],
        "device_preference": "auto",
        "downloads_root": None,
        "youtube_cookies": {"mode": "none"},
    }


def test_put_settings_route_updates_and_returns_values(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": ["D:/Media/ABBA"],
            "mirror_roots": [],
            "device_preference": "cuda",
        },
    )
    assert response.status_code == 200
    assert response.json()["device_preference"] == "cuda"


def test_put_settings_route_rejects_invalid_device_preference(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={"media_roots": [], "mirror_roots": [], "device_preference": "quantum"},
    )
    assert response.status_code == 422


def test_put_settings_route_rejects_an_invalid_cookie_mode(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "youtube_cookies": {"mode": "smoke_signal"},
        },
    )
    assert response.status_code == 422


def test_put_settings_route_rejects_browser_mode_with_an_empty_browser(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "youtube_cookies": {"mode": "browser", "browser": ""},
        },
    )
    assert response.status_code == 422


def test_put_settings_route_rejects_browser_mode_with_no_browser_field_at_all(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "youtube_cookies": {"mode": "browser"},
        },
    )
    assert response.status_code == 422


def test_put_settings_route_rejects_file_mode_with_an_empty_cookies_file(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "youtube_cookies": {"mode": "file", "cookies_file": "   "},
        },
    )
    assert response.status_code == 422


def test_put_settings_route_accepts_downloads_root_and_browser_cookies(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    response = client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "downloads_root": "D:/Downloads",
            "youtube_cookies": {"mode": "browser", "browser": "chrome"},
        },
    )
    assert response.status_code == 200
    assert response.json()["downloads_root"] == "D:/Downloads"
    assert response.json()["youtube_cookies"] == {"mode": "browser", "browser": "chrome"}


def test_browse_folder_route_returns_native_picker_selection(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "library.db")
    monkeypatch.setattr("app.routes.settings._pick_folder", lambda initial: "D:\\Chosen")
    response = TestClient(app).post("/api/settings/browse-folder", json={"initial_path": "D:/Media"})
    assert response.status_code == 200
    assert response.json() == {"path": "D:\\Chosen"}


def test_browse_folder_route_returns_null_when_cancelled(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "library.db")
    monkeypatch.setattr("app.routes.settings._pick_folder", lambda initial: None)
    response = TestClient(app).post("/api/settings/browse-folder", json={"initial_path": None})
    assert response.status_code == 200
    assert response.json() == {"path": None}
