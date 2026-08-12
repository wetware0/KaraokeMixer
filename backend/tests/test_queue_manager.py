import threading
from types import SimpleNamespace

import pytest

from app.db import get_connection, get_job, list_tracks, replace_tracks
from app.events import EventBus
from app.pipeline import StageResult, StageStatus
from app.instrumental_provenance import build_instrumental_provenance
from app.queue import JobQueueManager
from app.recipes import REGISTRY
from app.recipes.registry import RecipeDefinition
from app.scanner import ExtendedTags

from .queue_test_helpers import make_blocking_recipe, make_raising_recipe, wait_for_event


def _track_record(media_root, relative_path, title):
    outputs = SimpleNamespace(
        instrumental=False, vocals=False, lead_vocals=False, backing_vocals=False,
        drums=False, bass=False, guitar=False, piano=False, other=False, lrc=False,
    )
    return SimpleNamespace(
        media_root=str(media_root),
        relative_path=relative_path,
        absolute_path=str(media_root / relative_path),
        artist=None,
        title=title,
        outputs=outputs,
        lrc_state=None,
        stem_count=0,
        album=None,
        year=None,
        duration_seconds=None,
    )


class _MetadataRefreshStage:
    name = "metadata_refresh"

    def declared_outputs(self, _ctx):
        return []

    def run(self, _ctx):
        return StageResult(
            status=StageStatus.COMPLETED,
            detail="metadata updated",
            refresh_track_metadata=True,
        )


class _InstrumentalStage:
    name = "karaoke_instrumental"

    def declared_outputs(self, ctx):
        return [ctx.source_path.with_name(f"{ctx.source_path.stem}.instrumental.mp3")]

    def run(self, ctx):
        output = self.declared_outputs(ctx)[0]
        output.write_bytes(b"instrumental")
        return StageResult(
            status=StageStatus.COMPLETED,
            detail="instrumental written",
            output_provenance=[build_instrumental_provenance(
                ctx.options,
                output,
                engine="demucs",
                model="htdemucs",
                backing_vocal_mode="stripped",
            )],
        )


class _LyricsStage:
    name = "align_lyrics"

    def declared_outputs(self, ctx):
        return [ctx.source_path.with_suffix(".lrc")]

    def run(self, ctx):
        self.declared_outputs(ctx)[0].write_text(
            "[00:01.00]<00:01.00>Hello<00:01.50> world\n",
            encoding="utf-8",
        )
        return StageResult(status=StageStatus.COMPLETED, detail="enhanced lyrics written")


def test_submitted_job_runs_to_completion_on_its_lane(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus)

    job_id = manager.submit(
        "fake",
        {"fake_delay_seconds": 0},
        [{"track_id": None, "source_path": str(tmp_path / "a.flac")}],
    )

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})

    job = get_job(conn, job_id)
    assert job["status"] == "completed"
    assert job["started_at"] is not None
    assert job["finished_at"] is not None
    assert job["items"][0]["status"] == "completed"
    assert job["items"][0]["current_stage"] is None
    stage_names = [stage["name"] for stage in job["items"][0]["stages"]]
    assert stage_names == ["fake_prepare", "fake_publish"]
    assert all(stage["status"] == "completed" for stage in job["items"][0]["stages"])


@pytest.mark.parametrize("batch_by_stage", [False, True])
def test_metadata_stage_updates_db_and_publishes_fresh_track_before_job_completion(
    tmp_path, monkeypatch, batch_by_stage
):
    conn = get_connection(tmp_path / "library.db")
    sources = [tmp_path / "First.flac", tmp_path / "Second.flac"]
    for source in sources:
        source.write_bytes(b"test")
    replace_tracks(
        conn,
        str(tmp_path),
        [_track_record(tmp_path, source.name, source.stem) for source in sources],
    )
    track_ids = [track["id"] for track in list_tracks(conn)]
    monkeypatch.setattr(
        "app.queue.read_extended_tags",
        lambda _path: ExtendedTags(artist="ABBA", title="Dancing Queen", album="Arrival", year=1976),
    )
    recipe = RecipeDefinition(
        name="metadata_refresh",
        lane="cpu",
        stage_factories=[lambda _options: _MetadataRefreshStage()],
        batch_by_stage=batch_by_stage,
    )
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus, registry={recipe.name: recipe})

    job_id = manager.submit(
        recipe.name,
        {},
        [
            {"track_id": track_id, "source_path": str(source)}
            for track_id, source in zip(track_ids, sources)
        ],
    )

    events = [
        wait_for_event(subscriber, lambda item: item.get("type") == "track_updated")
        for _source in sources
    ]
    assert all(event["job_id"] == job_id for event in events)
    assert {event["track_id"] for event in events} == set(track_ids)
    assert all(event["track"]["artist"] == "ABBA" for event in events)
    assert all(event["track"]["album"] == "Arrival" for event in events)
    assert all("absolute_path" not in event["track"] for event in events)
    assert {track["title"] for track in list_tracks(conn)} == {"Dancing Queen"}


@pytest.mark.parametrize("batch_by_stage", [False, True])
def test_instrumental_stage_records_quality_and_publishes_it_immediately(
    tmp_path, batch_by_stage
):
    conn = get_connection(tmp_path / "library.db")
    sources = [tmp_path / "First.flac", tmp_path / "Second.flac"]
    for source in sources:
        source.write_bytes(b"source")
    replace_tracks(
        conn,
        str(tmp_path),
        [_track_record(tmp_path, source.name, source.stem) for source in sources],
    )
    track_ids = [track["id"] for track in list_tracks(conn)]
    recipe = RecipeDefinition(
        name="instrumental_test",
        lane="cpu",
        stage_factories=[lambda _options: _InstrumentalStage()],
        batch_by_stage=batch_by_stage,
    )
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus, registry={recipe.name: recipe})

    job_id = manager.submit(
        recipe.name,
        {"processing_profile": "balanced", "device": "cpu"},
        [
            {"track_id": track_id, "source_path": str(source)}
            for track_id, source in zip(track_ids, sources)
        ],
    )
    events = [
        wait_for_event(subscriber, lambda event: event.get("type") == "track_updated")
        for _source in sources
    ]

    assert all(event["job_id"] == job_id for event in events)
    assert all(event["track"]["outputs"]["instrumental"] is True for event in events)
    assert all(event["track"]["instrumental_provenance"]["quality"] == "balanced" for event in events)
    assert all(event["track"]["instrumental_provenance"]["attribution"] == "confirmed" for event in events)


@pytest.mark.parametrize("batch_by_stage", [False, True])
def test_lyrics_stage_updates_only_its_catalogue_rows_and_publishes_them_immediately(
    tmp_path, batch_by_stage
):
    conn = get_connection(tmp_path / "library.db")
    sources = [tmp_path / "First.flac", tmp_path / "Second.flac"]
    for source in sources:
        source.write_bytes(b"source")
    replace_tracks(
        conn,
        str(tmp_path),
        [_track_record(tmp_path, source.name, source.stem) for source in sources],
    )
    track_ids = [track["id"] for track in list_tracks(conn)]
    recipe = RecipeDefinition(
        name="lyrics_refresh_test",
        lane="cpu",
        stage_factories=[lambda _options: _LyricsStage()],
        batch_by_stage=batch_by_stage,
    )
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus, registry={recipe.name: recipe})

    job_id = manager.submit(
        recipe.name,
        {"mirror_roots": []},
        [
            {"track_id": track_id, "source_path": str(source)}
            for track_id, source in zip(track_ids, sources)
        ],
    )
    events = [
        wait_for_event(
            subscriber,
            lambda event: event.get("type") == "track_updated"
            and event.get("track", {}).get("lrc_state") == "enhanced",
        )
        for _source in sources
    ]

    assert {event["track_id"] for event in events} == set(track_ids)
    assert all(event["track"]["outputs"]["lrc"] is True for event in events)
    rows = list_tracks(conn)
    assert all(track["outputs"]["lrc"] is True for track in rows)
    assert all(track["lrc_state"] == "enhanced" for track in rows)


def test_submitting_an_unknown_recipe_raises_value_error(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    manager = JobQueueManager(conn, EventBus())

    with pytest.raises(ValueError):
        manager.submit("no_such_recipe", {}, [{"track_id": None, "source_path": "a.flac"}])


def test_job_events_include_queued_running_and_completed_in_order(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus)

    job_id = manager.submit(
        "fake",
        {"fake_delay_seconds": 0},
        [{"track_id": None, "source_path": str(tmp_path / "a.flac")}],
    )

    job_statuses = []
    while True:
        event = subscriber.get(timeout=5)
        if event["type"] == "job" and event["job_id"] == job_id:
            job_statuses.append(event["status"])
            if event["status"] == "completed":
                break
    assert job_statuses == ["queued", "running", "completed"]


def test_stage_exception_fails_the_item_and_job_without_killing_the_lane(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    registry = {**REGISTRY, "raising": make_raising_recipe(lane="cpu", message="boom")}
    manager = JobQueueManager(conn, bus, registry=registry)

    job_id = manager.submit(
        "raising", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}]
    )

    wait_for_event(
        subscriber,
        lambda e: e["type"] == "item" and e["job_id"] == job_id and e["status"] == "failed",
    )
    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "failed"})

    job = get_job(conn, job_id)
    assert job["status"] == "failed"
    assert job["items"][0]["status"] == "failed"
    assert job["items"][0]["current_stage"] is None
    assert "boom" in job["items"][0]["error_text"]

    # Lane survival: the same manager's cpu lane must still process a normal
    # job submitted after the crashing one - proof the lane thread is alive.
    job_id_2 = manager.submit(
        "fake",
        {"fake_delay_seconds": 0},
        [{"track_id": None, "source_path": str(tmp_path / "b.flac")}],
    )
    wait_for_event(
        subscriber, lambda e: e == {"type": "job", "job_id": job_id_2, "status": "completed"}
    )
    job_2 = get_job(conn, job_id_2)
    assert job_2["status"] == "completed"


def test_cancelling_a_job_before_it_starts_publishes_item_cancelled_events(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    release_event = threading.Event()
    registry = {**REGISTRY, "blocking": make_blocking_recipe(release_event, lane="cpu")}
    manager = JobQueueManager(conn, bus, registry=registry)

    # Occupy the cpu lane with a job that will not finish until we release it.
    blocking_job_id = manager.submit(
        "blocking", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}]
    )
    wait_for_event(
        subscriber,
        lambda e: e == {"type": "job", "job_id": blocking_job_id, "status": "running"},
    )

    # This job sits behind it in the same lane's queue, untouched, so
    # cancelling it here guarantees _run_job sees cancel_event already set
    # before the job ever starts (the pre-start branch under test).
    job_id = manager.submit(
        "fake",
        {"fake_delay_seconds": 0},
        [
            {"track_id": None, "source_path": str(tmp_path / "b.flac")},
            {"track_id": None, "source_path": str(tmp_path / "c.flac")},
        ],
    )
    manager.cancel(job_id)

    release_event.set()  # let the blocking job finish so the lane advances

    events_for_job: list[dict] = []
    while True:
        event = subscriber.get(timeout=5)
        if event.get("job_id") == job_id:
            events_for_job.append(event)
            if event["type"] == "job" and event["status"] == "cancelled":
                break

    job = get_job(conn, job_id)
    assert job["status"] == "cancelled"
    assert all(item["status"] == "cancelled" for item in job["items"])

    item_cancelled_events = [
        e
        for e in events_for_job
        if e["type"] == "item" and e["status"] == "cancelled" and e["current_stage"] is None
    ]
    assert {e["item_id"] for e in item_cancelled_events} == {item["id"] for item in job["items"]}
    assert len(item_cancelled_events) == len(job["items"])
