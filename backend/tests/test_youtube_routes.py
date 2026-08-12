import queue as queue_module

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.youtube.downloader import DownloadResult, YoutubeDownloadError

from .queue_test_helpers import wait_for_event


def _client_with_downloads_root(tmp_path, monkeypatch, downloader_side_effect=None):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "media_roots": [], "mirror_roots": [], "device_preference": "auto",
            "downloads_root": str(tmp_path / "Downloads"), "youtube_cookies": {"mode": "none"},
        },
    )

    def fake_prober(url, cookies=None):
        return {"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"}

    def fake_downloader(url, destination, cookies=None):
        if downloader_side_effect is not None:
            raise downloader_side_effect
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-m4a")
        return DownloadResult(path=destination, title="Chiquitita", duration=200.0, uploader="ABBA")

    monkeypatch.setattr("app.routes.youtube.probe_youtube_url", fake_prober)
    monkeypatch.setattr("app.youtube.downloader.probe_youtube_url", fake_prober)
    monkeypatch.setattr("app.youtube.downloader.download_youtube_audio", fake_downloader)
    return app, client


def test_probe_route_returns_video_metadata(tmp_path, monkeypatch):
    _, client = _client_with_downloads_root(tmp_path, monkeypatch)

    response = client.post("/api/youtube/probe", json={"url": "https://youtube.com/watch?v=abc"})

    assert response.status_code == 200
    assert response.json()["title"] == "Chiquitita"


def test_import_route_downloads_and_completes_the_job(tmp_path, monkeypatch):
    app, client = _client_with_downloads_root(tmp_path, monkeypatch)
    subscriber = app.state.event_bus.subscribe()

    response = client.post("/api/youtube/import", json={"url": "https://youtube.com/watch?v=abc"})

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})


def test_import_route_rejects_when_no_downloads_root_or_media_root_configured(tmp_path):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)

    response = client.post("/api/youtube/import", json={"url": "https://youtube.com/watch?v=abc"})

    assert response.status_code == 422


def test_import_route_falls_back_to_the_first_media_root_when_no_downloads_root_is_set(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    media_root = tmp_path / "Media"
    media_root.mkdir()
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )

    def fake_prober(url, cookies=None):
        return {"title": "Chiquitita", "duration": 200.0, "uploader": "ABBA"}

    def fake_downloader(url, destination, cookies=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-m4a")
        return DownloadResult(path=destination, title="Chiquitita", duration=200.0, uploader="ABBA")

    monkeypatch.setattr("app.youtube.downloader.probe_youtube_url", fake_prober)
    monkeypatch.setattr("app.youtube.downloader.download_youtube_audio", fake_downloader)
    subscriber = app.state.event_bus.subscribe()

    job_id = client.post("/api/youtube/import", json={"url": "https://youtube.com/watch?v=abc"}).json()["job_id"]

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})
    assert (media_root / "ABBA - Chiquitita.m4a").exists()


def test_import_route_pre_resolves_the_chained_jobs_auto_device_to_the_probed_device(tmp_path, monkeypatch):
    # The chained "process after" job is submitted from inside the
    # youtube_import stage (no app.state there), so the route must resolve
    # "auto" -> the probed device upfront, exactly like POST /api/jobs does
    # (routes/jobs.py's own auto-device resolution) - otherwise a chained
    # karaoke job would silently run on whatever device the stage's default
    # happens to be, ignoring the machine's actual capability.
    app, client = _client_with_downloads_root(tmp_path, monkeypatch)
    app.state.device = "cuda"  # simulate a GPU-equipped machine

    response = client.post(
        "/api/youtube/import",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "process_after": {"recipe": "karaoke", "options": {"model": "htdemucs"}},
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["options"]["process_after"]["recipe"] == "karaoke"
    assert detail["options"]["process_after"]["options"]["device"] == "cuda"


def test_import_route_keeps_an_explicit_chained_device_choice(tmp_path, monkeypatch):
    app, client = _client_with_downloads_root(tmp_path, monkeypatch)
    app.state.device = "cuda"

    response = client.post(
        "/api/youtube/import",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "process_after": {"recipe": "karaoke", "options": {"model": "htdemucs", "device": "cpu"}},
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["options"]["process_after"]["options"]["device"] == "cpu"


def test_import_route_rejects_an_invalid_chained_option_before_submitting_the_import_job(tmp_path, monkeypatch):
    app, client = _client_with_downloads_root(tmp_path, monkeypatch)
    subscriber = app.state.event_bus.subscribe()

    response = client.post(
        "/api/youtube/import",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "process_after": {"recipe": "karaoke", "options": {"model": "not-a-real-model"}},
        },
    )

    assert response.status_code == 422
    # And the import job itself must never have been submitted at all - a
    # bad chained option 422s upfront, before any job (let alone a youtube
    # download) is started.
    with pytest.raises(queue_module.Empty):
        subscriber.get(timeout=0.2)


def test_import_route_rejects_an_unknown_chained_recipe(tmp_path, monkeypatch):
    _, client = _client_with_downloads_root(tmp_path, monkeypatch)

    response = client.post(
        "/api/youtube/import",
        json={
            "url": "https://youtube.com/watch?v=abc",
            "process_after": {"recipe": "no-such-recipe", "options": {}},
        },
    )

    assert response.status_code == 422


def test_import_route_fails_the_job_with_a_clear_message_on_age_restriction(tmp_path, monkeypatch):
    app, client = _client_with_downloads_root(
        tmp_path, monkeypatch, downloader_side_effect=YoutubeDownloadError("age gate", age_restricted=True)
    )
    subscriber = app.state.event_bus.subscribe()

    job_id = client.post("/api/youtube/import", json={"url": "https://youtube.com/watch?v=abc"}).json()["job_id"]

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "failed"})
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert "cookies" in detail["items"][0]["error_text"].lower()
