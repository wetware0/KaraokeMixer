from app.db import (
    create_job,
    get_connection,
    get_job,
    list_job_history,
    list_job_items_page,
    list_jobs,
    list_track_processing_failures,
    reset_stuck_jobs,
    set_item_error,
    set_item_stages,
    set_item_status,
    set_job_status,
)


def test_create_job_inserts_job_and_items_returns_job_id(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(
        conn,
        "fake",
        {"device": "cpu", "overwrite": False},
        [
            {"track_id": 1, "source_path": "a.flac"},
            {"track_id": None, "source_path": "b.flac"},
        ],
    )
    assert isinstance(job_id, int)

    job = get_job(conn, job_id)
    assert job["recipe"] == "fake"
    assert job["options"] == {"device": "cpu", "overwrite": False}
    assert job["status"] == "queued"
    assert job["started_at"] is None
    assert job["finished_at"] is None
    assert len(job["items"]) == 2
    assert job["items"][0]["track_id"] == 1
    assert job["items"][0]["source_path"] == "a.flac"
    assert job["items"][0]["status"] == "queued"
    assert job["items"][0]["current_stage"] is None
    assert job["items"][0]["stages"] == []
    assert job["items"][0]["error_text"] is None
    assert job["items"][1]["track_id"] is None


def test_job_history_is_paged_newest_first_and_includes_counts(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    first = create_job(conn, "lyrics_only", {}, [{"track_id": 1, "source_path": "ABBA/Song.flac"}])
    second = create_job(
        conn,
        "karaoke",
        {},
        [
            {"track_id": 2, "source_path": "Beatles/One.flac"},
            {"track_id": 3, "source_path": "Beatles/Two.flac"},
        ],
    )
    second_items = get_job(conn, second)["items"]
    set_item_status(conn, second_items[0]["id"], "completed")
    set_item_status(conn, second_items[1]["id"], "failed")
    set_job_status(conn, second, "failed", finished_at="2026-08-10T01:00:00+00:00")

    page = list_job_history(conn, limit=1, offset=0)

    assert page["total"] == 2
    assert [job["id"] for job in page["jobs"]] == [second]
    assert page["jobs"][0]["item_counts"]["completed"] == 1
    assert page["jobs"][0]["item_counts"]["failed"] == 1
    assert list_job_history(conn, limit=1, offset=1)["jobs"][0]["id"] == first


def test_job_history_filters_status_and_searches_track_paths(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    completed = create_job(conn, "lyrics_only", {}, [{"track_id": 1, "source_path": "ABBA/Dancing Queen.flac"}])
    failed = create_job(conn, "karaoke", {}, [{"track_id": 2, "source_path": "Beatles/Eleanor Rigby.flac"}])
    set_job_status(conn, completed, "completed", finished_at="2026-08-10T01:00:00+00:00")
    set_job_status(conn, failed, "failed", finished_at="2026-08-10T02:00:00+00:00")

    assert [job["id"] for job in list_job_history(conn, statuses={"failed"})["jobs"]] == [failed]
    assert [job["id"] for job in list_job_history(conn, query="Dancing Queen")["jobs"]] == [completed]
    assert [job["id"] for job in list_job_history(conn, query=str(failed))["jobs"]] == [failed]


def test_job_items_page_filters_and_searches_without_loading_the_whole_job(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(
        conn,
        "karaoke",
        {},
        [
            {"track_id": 1, "source_path": "ABBA/Dancing Queen.flac"},
            {"track_id": 2, "source_path": "Beatles/Eleanor Rigby.flac"},
            {"track_id": 3, "source_path": "Beatles/Yellow Submarine.flac"},
        ],
    )
    items = get_job(conn, job_id)["items"]
    set_item_status(conn, items[0]["id"], "completed")
    set_item_status(conn, items[1]["id"], "failed")
    set_item_status(conn, items[2]["id"], "failed")

    failed_page = list_job_items_page(conn, job_id, status="failed", limit=1)
    assert failed_page["total"] == 2
    assert failed_page["items"][0]["source_path"].endswith("Eleanor Rigby.flac")
    assert list_job_items_page(conn, job_id, query="Yellow Submarine")["total"] == 1
    assert list_job_items_page(conn, 999) is None


def test_get_job_returns_none_for_unknown_id(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    assert get_job(conn, 999) is None


def test_list_jobs_orders_newest_first_with_item_counts(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    first_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    second_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "b.flac"}])

    jobs = list_jobs(conn)

    assert [job["id"] for job in jobs] == [second_id, first_id]
    assert jobs[0]["item_counts"] == {
        "queued": 1,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": 0,
    }


def test_set_job_status_updates_status_and_started_at(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])

    set_job_status(conn, job_id, "running", started_at="2026-01-01T00:00:00+00:00")

    job = get_job(conn, job_id)
    assert job["status"] == "running"
    assert job["started_at"] == "2026-01-01T00:00:00+00:00"
    assert job["finished_at"] is None


def test_set_item_status_updates_status_and_current_stage(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    item_id = get_job(conn, job_id)["items"][0]["id"]

    set_item_status(conn, item_id, "running", current_stage="fake_prepare")

    item = get_job(conn, job_id)["items"][0]
    assert item["status"] == "running"
    assert item["current_stage"] == "fake_prepare"


def test_set_item_stages_persists_parsed_json(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    item_id = get_job(conn, job_id)["items"][0]["id"]
    stages = [
        {"name": "fake_prepare", "status": "completed", "started_at": "t1", "finished_at": "t2", "error": None}
    ]

    set_item_stages(conn, item_id, stages)

    assert get_job(conn, job_id)["items"][0]["stages"] == stages


def test_set_item_error_persists_error_text(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    item_id = get_job(conn, job_id)["items"][0]["id"]

    set_item_error(conn, item_id, "disk full")

    assert get_job(conn, job_id)["items"][0]["error_text"] == "disk full"


def test_track_processing_failures_reports_only_each_tracks_latest_failed_attempt(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    failed_job_id = create_job(
        conn,
        "karaoke",
        {},
        [
            {"track_id": 10, "source_path": "failed.flac"},
            {"track_id": 20, "source_path": "retried.flac"},
        ],
    )
    failed_items = get_job(conn, failed_job_id)["items"]
    for item in failed_items:
        set_item_status(conn, item["id"], "failed")
        set_item_stages(
            conn,
            item["id"],
            [{"name": "karaoke_instrumental", "status": "failed", "error": "boom"}],
        )
        set_item_error(conn, item["id"], "audio-separator exited with code 1")

    # A newer attempt owns track 20's state immediately; its older failure is
    # no longer presented while the retry is queued or after it succeeds.
    create_job(conn, "karaoke", {}, [{"track_id": 20, "source_path": "retried.flac"}])

    assert list_track_processing_failures(conn) == [
        {
            "track_id": 10,
            "job_id": failed_job_id,
            "stage": "karaoke_instrumental",
            "message": "audio-separator exited with code 1",
        }
    ]


def test_track_processing_failures_explains_surround_audio_failure(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "karaoke", {}, [{"track_id": 10, "source_path": "surround.flac"}])
    item_id = get_job(conn, job_id)["items"][0]["id"]
    set_item_status(conn, item_id, "failed")
    set_item_stages(
        conn,
        item_id,
        [{"name": "karaoke_instrumental", "status": "failed", "error": "stereo mismatch"}],
    )
    set_item_error(conn, item_id, "AssertionError: stereo needs to be set to True if passing in audio signal")

    assert list_track_processing_failures(conn)[0]["message"] == (
        "Surround audio could not be processed by the stereo separation model"
    )


def test_reset_stuck_jobs_resets_running_rows_to_queued_and_returns_job_ids(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    stuck_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    stuck_item_id = get_job(conn, stuck_id)["items"][0]["id"]
    set_job_status(conn, stuck_id, "running", started_at="2026-01-01T00:00:00+00:00")
    set_item_status(conn, stuck_item_id, "running", current_stage="fake_prepare")

    # A job that reached a terminal state must not be swept up alongside the
    # stuck 'running' job.
    completed_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "b.flac"}])
    set_job_status(conn, completed_id, "completed", finished_at="2026-01-01T00:00:01+00:00")

    reset_ids = reset_stuck_jobs(conn)

    assert reset_ids == [stuck_id]
    stuck_job = get_job(conn, stuck_id)
    assert stuck_job["status"] == "queued"
    assert stuck_job["started_at"] is None
    assert stuck_job["items"][0]["status"] == "queued"
    assert stuck_job["items"][0]["current_stage"] is None
    assert get_job(conn, completed_id)["status"] == "completed"


def test_reset_stuck_jobs_leaves_terminal_jobs_untouched(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    set_job_status(conn, job_id, "failed", finished_at="2026-01-01T00:00:00+00:00")

    assert reset_stuck_jobs(conn) == []
    assert get_job(conn, job_id)["status"] == "failed"


def test_reset_stuck_jobs_sweeps_never_started_queued_jobs_in_submission_order(tmp_path):
    # A job that never advanced past 'queued' (e.g. the process died before
    # its lane thread pulled it off the in-memory queue) must also be swept,
    # since that in-memory queue does not survive a restart. Ordering must
    # match submission order (ascending id) so callers re-enqueue jobs in the
    # order they were originally submitted.
    conn = get_connection(tmp_path / "library.db")
    first_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "a.flac"}])
    second_id = create_job(conn, "fake", {}, [{"track_id": None, "source_path": "b.flac"}])

    reset_ids = reset_stuck_jobs(conn)

    assert reset_ids == [first_id, second_id]
    assert get_job(conn, first_id)["status"] == "queued"
    assert get_job(conn, second_id)["status"] == "queued"
