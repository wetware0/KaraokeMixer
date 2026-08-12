from fastapi.testclient import TestClient

from app.db import create_job, get_job, set_item_error, set_item_stages, set_item_status, set_job_status
from app.main import create_app
from tests.scan_test_helpers import run_rescan

from .queue_test_helpers import wait_for_event


def _seed_track(tmp_path) -> tuple[TestClient, int]:
    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"")

    app = create_app(db_path=tmp_path / "library.db")
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={"media_roots": [str(media_root)], "mirror_roots": [], "device_preference": "auto"},
    )
    run_rescan(client)
    track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
    return client, track_id


def test_post_jobs_with_track_ids_creates_a_job_that_runs_to_completion(tmp_path):
    client, track_id = _seed_track(tmp_path)
    subscriber = client.app.state.event_bus.subscribe()

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"fake_delay_seconds": 0}},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["status"] == "completed"
    assert detail["items"][0]["track_id"] == track_id


def test_post_jobs_rejects_unknown_recipe(tmp_path):
    client, track_id = _seed_track(tmp_path)
    response = client.post("/api/jobs", json={"recipe": "no_such_recipe", "track_ids": [track_id], "options": {}})
    assert response.status_code == 422


def test_post_jobs_rejects_when_no_tracks_match(tmp_path):
    client, _ = _seed_track(tmp_path)
    response = client.post("/api/jobs", json={"recipe": "fake", "track_ids": [999], "options": {}})
    assert response.status_code == 422


def test_get_jobs_lists_newest_first_with_item_counts(tmp_path):
    client, track_id = _seed_track(tmp_path)
    first = client.post(
        "/api/jobs", json={"recipe": "fake", "track_ids": [track_id], "options": {"fake_delay_seconds": 0}}
    ).json()["job_id"]
    second = client.post(
        "/api/jobs", json={"recipe": "fake", "track_ids": [track_id], "options": {"fake_delay_seconds": 0}}
    ).json()["job_id"]

    jobs = client.get("/api/jobs").json()["jobs"]

    assert [job["id"] for job in jobs] == [second, first]
    assert set(jobs[0]["item_counts"].keys()) == {
        "queued", "running", "completed", "failed", "skipped", "cancelled"
    }


def test_get_job_returns_404_for_unknown_id(tmp_path):
    client, _ = _seed_track(tmp_path)
    response = client.get("/api/jobs/999")
    assert response.status_code == 404


def test_get_job_history_supports_status_search_and_paging(tmp_path):
    client, track_id = _seed_track(tmp_path)
    completed_id = create_job(
        client.app.state.db_conn,
        "lyrics_only",
        {},
        [{"track_id": track_id, "source_path": "ABBA/Dancing Queen.flac"}],
    )
    failed_id = create_job(
        client.app.state.db_conn,
        "karaoke",
        {},
        [{"track_id": track_id, "source_path": "Beatles/Eleanor Rigby.flac"}],
    )
    set_job_status(client.app.state.db_conn, completed_id, "completed", finished_at="2026-08-10T01:00:00+00:00")
    set_job_status(client.app.state.db_conn, failed_id, "failed", finished_at="2026-08-10T02:00:00+00:00")

    response = client.get("/api/jobs/history?status=failed&query=Eleanor&limit=1&offset=0")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["jobs"][0]["id"] == failed_id
    assert client.get("/api/jobs/history?status=unknown").status_code == 422
    assert client.get("/api/jobs/history?limit=1000").status_code == 422


def test_get_job_items_returns_a_filtered_page(tmp_path):
    client, track_id = _seed_track(tmp_path)
    job_id = create_job(
        client.app.state.db_conn,
        "karaoke",
        {},
        [
            {"track_id": track_id, "source_path": "Beatles/Eleanor Rigby.flac"},
            {"track_id": track_id, "source_path": "Beatles/Yellow Submarine.flac"},
        ],
    )
    items = get_job(client.app.state.db_conn, job_id)["items"]
    for item in items:
        set_item_status(client.app.state.db_conn, item["id"], "failed")

    response = client.get(f"/api/jobs/{job_id}/items?status=failed&query=Yellow&limit=1")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["source_path"].endswith("Yellow Submarine.flac")
    assert client.get("/api/jobs/999/items").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/items?status=unknown").status_code == 422


def test_get_track_failures_identifies_the_track_and_failed_stage(tmp_path):
    client, track_id = _seed_track(tmp_path)
    job_id = create_job(
        client.app.state.db_conn,
        "karaoke",
        {},
        [{"track_id": track_id, "source_path": "Song.flac"}],
    )
    item_id = get_job(client.app.state.db_conn, job_id)["items"][0]["id"]
    set_item_status(client.app.state.db_conn, item_id, "failed")
    set_item_stages(
        client.app.state.db_conn,
        item_id,
        [{"name": "karaoke_instrumental", "status": "failed", "error": "disk full"}],
    )
    set_item_error(client.app.state.db_conn, item_id, "disk full")

    response = client.get("/api/jobs/track-failures")

    assert response.status_code == 200
    assert response.json() == {
        "failures": [
            {"track_id": track_id, "job_id": job_id, "stage": "karaoke_instrumental", "message": "disk full"}
        ]
    }


def test_post_jobs_cancel_marks_a_running_job_cancelled(tmp_path):
    client, track_id = _seed_track(tmp_path)
    subscriber = client.app.state.event_bus.subscribe()

    job_id = client.post(
        "/api/jobs", json={"recipe": "fake", "track_ids": [track_id], "options": {"fake_delay_seconds": 0.3}}
    ).json()["job_id"]

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "running"})
    cancel_response = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json() == {"job_id": job_id, "status": "cancelling"}

    wait_for_event(subscriber, lambda e: e.get("job_id") == job_id and e.get("type") == "job" and e["status"] in ("cancelled", "completed"))


def test_post_jobs_cancel_returns_404_for_unknown_id(tmp_path):
    client, _ = _seed_track(tmp_path)
    response = client.post("/api/jobs/999/cancel")
    assert response.status_code == 404


def test_post_jobs_rejects_invalid_option_choice_for_a_schema_recipe(tmp_path):
    # Uses the hidden "fake" recipe (inert stages, no subprocess) rather than
    # "karaoke"/"full_stems" - this test is only about the route's generic
    # options-schema validation logic, not about any particular recipe, and
    # submitting a real GPU recipe here would let the lane thread attempt a
    # real subprocess spawn against a worker venv that doesn't exist on this
    # machine.
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"volume_mode": "extremely_loud"}},
    )

    assert response.status_code == 422
    assert "volume_mode" in response.json()["detail"]


def test_post_jobs_accepts_a_valid_schema_option(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"volume_mode": "loud", "fake_delay_seconds": 0}},
    )

    assert response.status_code == 200


def test_post_jobs_merges_current_media_and_mirror_roots_into_job_options(tmp_path):
    client, track_id = _seed_track(tmp_path)
    media_root = client.get("/api/settings").json()["media_roots"][0]
    client.put(
        "/api/settings",
        json={"media_roots": [media_root], "mirror_roots": ["D:/Stems"], "device_preference": "auto"},
    )

    job_id = client.post(
        "/api/jobs", json={"recipe": "fake", "track_ids": [track_id], "options": {"fake_delay_seconds": 0}}
    ).json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["options"]["media_roots"] == [media_root]
    assert detail["options"]["mirror_roots"] == ["D:/Stems"]


def test_post_jobs_rejects_unknown_option_key(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"modle": "x", "fake_delay_seconds": 0}},
    )

    assert response.status_code == 422
    assert "modle" in response.json()["detail"]


def test_post_jobs_rejects_client_supplied_media_roots(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "recipe": "fake",
            "track_ids": [track_id],
            "options": {"fake_delay_seconds": 0, "media_roots": ["C:/Sneaky"]},
        },
    )

    assert response.status_code == 422
    assert "media_roots" in response.json()["detail"]


def test_post_jobs_accepts_a_valid_checkbox_option(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "recipe": "fake",
            "track_ids": [track_id],
            "options": {"dry_run": True, "fake_delay_seconds": 0},
        },
    )

    assert response.status_code == 200


def test_post_jobs_rejects_an_invalid_checkbox_option(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "recipe": "fake",
            "track_ids": [track_id],
            "options": {"dry_run": "yes", "fake_delay_seconds": 0},
        },
    )

    assert response.status_code == 422
    assert "dry_run" in response.json()["detail"]


def test_post_jobs_accepts_a_valid_number_option(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "recipe": "fake",
            "track_ids": [track_id],
            "options": {"passes": 2, "fake_delay_seconds": 0},
        },
    )

    assert response.status_code == 200


def test_post_jobs_resolves_auto_device_to_the_probed_device(tmp_path):
    client, track_id = _seed_track(tmp_path)
    client.app.state.device = "cuda"  # simulate probe_device() having found a GPU

    job_id = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"device": "auto", "fake_delay_seconds": 0}},
    ).json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["options"]["device"] == "cuda"


def test_post_jobs_leaves_an_explicit_device_choice_untouched(tmp_path):
    client, track_id = _seed_track(tmp_path)
    client.app.state.device = "cuda"  # even with a GPU available, an explicit cpu choice must win

    job_id = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"device": "cpu", "fake_delay_seconds": 0}},
    ).json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["options"]["device"] == "cpu"


def test_post_jobs_rejects_an_invalid_device_value(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"device": "tpu", "fake_delay_seconds": 0}},
    )

    assert response.status_code == 422
    assert "device" in response.json()["detail"]


def test_post_jobs_rejects_an_invalid_output_mode_value(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"output_mode": "nowhere", "fake_delay_seconds": 0}},
    )

    assert response.status_code == 422
    assert "output_mode" in response.json()["detail"]


def test_post_jobs_rejects_a_non_boolean_overwrite_value(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={"recipe": "fake", "track_ids": [track_id], "options": {"overwrite": "yes", "fake_delay_seconds": 0}},
    )

    assert response.status_code == 422
    assert "overwrite" in response.json()["detail"]


def test_post_jobs_rejects_an_invalid_number_option(tmp_path):
    client, track_id = _seed_track(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "recipe": "fake",
            "track_ids": [track_id],
            "options": {"passes": "two", "fake_delay_seconds": 0},
        },
    )

    assert response.status_code == 422
    assert "passes" in response.json()["detail"]
