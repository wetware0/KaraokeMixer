from __future__ import annotations

import logging
import queue as queue_module
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .db import (
    create_job,
    get_job,
    get_track,
    record_instrumental_provenance,
    reset_stuck_jobs,
    set_item_status,
    set_item_stages,
    set_item_error,
    set_job_status,
    update_track_outputs,
    update_track_tags,
)
from .events import EventBus
from .pipeline import Stage, StageContext, StageResult, StageStatus, should_skip
from .recipes import REGISTRY, RecipeDefinition
from .scanner import find_outputs, read_extended_tags
from .workers.runner import PersistentWorkerPool

log = logging.getLogger(__name__)


def execute_item(
    stages: list[Stage],
    source_path: Path,
    options: dict,
    overwrite: bool,
    cancel_event: threading.Event,
    on_stage_change: Callable[[Stage, StageResult | None], None],
    on_progress: Callable[[Stage, dict], None] | None = None,
    worker_runner: Callable[..., object] | None = None,
) -> str:
    """Run `stages` in order for one item, honoring cancellation between AND
    during stages.

    Returns "completed" if at least one stage actually ran (not skipped) and
    none failed; "skipped" if every stage's declared outputs already existed;
    "failed" if any stage failed (remaining stages are not attempted);
    "cancelled" if `cancel_event` was set before a stage got its turn, or
    while a stage was running AND that stage's own result was not
    `COMPLETED` (a stage that forwards `ctx.cancel_event` into a subprocess
    runner call typically returns FAILED when actually cancelled mid-run,
    since there is no StageStatus.CANCELLED - that non-completed result is
    what this override catches). A stage whose own result IS `COMPLETED` is
    always trusted and never overridden, even if cancellation happened to be
    requested while it ran - it did its job.

    The should_skip() check happens before the "now running" announcement, so
    a stage that gets skipped because its outputs already exist is reported
    once, directly as "skipped" - never as a transient "running" followed by
    a correction to "skipped".
    """
    any_failed = False
    any_ran = False
    for stage in stages:
        if cancel_event.is_set():
            return "cancelled"

        ctx = StageContext(
            source_path=source_path,
            overwrite=overwrite,
            options=options,
            cancel_event=cancel_event,
            on_progress=(lambda event, _stage=stage: on_progress(_stage, event)) if on_progress else None,
            worker_runner=worker_runner,
        )

        if should_skip(stage, ctx):
            result = StageResult(status=StageStatus.SKIPPED, detail="outputs already exist")
            on_stage_change(stage, result)
            continue

        on_stage_change(stage, None)
        result = stage.run(ctx)
        on_stage_change(stage, result)

        if cancel_event.is_set() and result.status != StageStatus.COMPLETED:
            # A stage's own run() may have observed cancellation mid-flight
            # (by forwarding ctx.cancel_event into a subprocess runner call)
            # and returned something other than COMPLETED - typically FAILED,
            # since there is no StageStatus.CANCELLED. This is what makes
            # cancelling the *last* stage in the list (no further loop
            # iteration to catch it at the top) still terminate the item
            # promptly as "cancelled". A stage that reports COMPLETED is
            # never overridden here, regardless of cancel_event's state - see
            # the docstring above and test_a_stage_that_completes_despite_a_
            # mid_run_cancellation_still_reports_completed.
            return "cancelled"

        if result.status == StageStatus.FAILED:
            any_failed = True
            break
        if result.status == StageStatus.COMPLETED:
            any_ran = True

    if any_failed:
        return "failed"
    if any_ran:
        return "completed"
    return "skipped"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobQueueManager:
    """Two permanent lane threads ("gpu", "cpu"), each serially draining its
    own queue.Queue of job ids. Exactly one job runs per lane at a time."""

    LANES = ("gpu", "cpu")

    def __init__(
        self,
        conn: sqlite3.Connection,
        event_bus: EventBus,
        registry: dict[str, RecipeDefinition] | None = None,
    ) -> None:
        self._conn = conn
        self._event_bus = event_bus
        self._registry = registry if registry is not None else REGISTRY
        self._lane_queues: dict[str, "queue_module.Queue[int]"] = {
            lane: queue_module.Queue() for lane in self.LANES
        }
        self._cancel_events: dict[int, threading.Event] = {}
        self._cancel_events_lock = threading.Lock()
        for lane in self.LANES:
            thread = threading.Thread(
                target=self._lane_loop, args=(lane,), name=f"job-lane-{lane}", daemon=True
            )
            thread.start()
        self._recover_crashed_jobs()

    @property
    def registry(self) -> dict[str, RecipeDefinition]:
        return self._registry

    def submit(self, recipe: str, options: dict, items: list[dict]) -> int:
        if recipe not in self._registry:
            raise ValueError(f"Unknown recipe: {recipe}")
        job_id = create_job(self._conn, recipe, options, items)
        with self._cancel_events_lock:
            self._cancel_events[job_id] = threading.Event()
        self._event_bus.publish({"type": "job", "job_id": job_id, "status": "queued"})
        lane = self._registry[recipe].lane
        self._lane_queues[lane].put(job_id)
        return job_id

    def cancel(self, job_id: int) -> None:
        with self._cancel_events_lock:
            event = self._cancel_events.get(job_id)
            if event is None:
                event = threading.Event()
                self._cancel_events[job_id] = event
        event.set()

    def _lane_loop(self, lane: str) -> None:
        while True:
            job_id = self._lane_queues[lane].get()
            self._run_job(job_id)

    def _run_job(self, job_id: int) -> None:
        with self._cancel_events_lock:
            cancel_event = self._cancel_events.setdefault(job_id, threading.Event())

        job = get_job(self._conn, job_id)
        if job is None:
            return

        try:
            if cancel_event.is_set():
                for item in job["items"]:
                    set_item_status(self._conn, item["id"], "cancelled")
                    self._event_bus.publish(
                        {
                            "type": "item",
                            "job_id": job_id,
                            "item_id": item["id"],
                            "status": "cancelled",
                            "current_stage": None,
                        }
                    )
                set_job_status(self._conn, job_id, "cancelled", finished_at=_utc_now())
                self._event_bus.publish({"type": "job", "job_id": job_id, "status": "cancelled"})
                return

            set_job_status(self._conn, job_id, "running", started_at=_utc_now())
            self._event_bus.publish({"type": "job", "job_id": job_id, "status": "running"})

            recipe_def = self._registry[job["recipe"]]
            job_failed = False

            with PersistentWorkerPool() as worker_pool:
                if recipe_def.batch_by_stage and len(job["items"]) > 1:
                    job_failed = self._run_items_by_stage(
                        job_id, job["items"], recipe_def, job["options"], cancel_event, worker_pool
                    )
                else:
                    for item in job["items"]:
                        if cancel_event.is_set():
                            set_item_status(self._conn, item["id"], "cancelled")
                            self._event_bus.publish(
                                {
                                    "type": "item",
                                    "job_id": job_id,
                                    "item_id": item["id"],
                                    "status": "cancelled",
                                    "current_stage": None,
                                }
                            )
                            continue

                        try:
                            terminal = self._run_item(
                                job_id, item, recipe_def, job["options"], cancel_event, worker_pool.run
                            )
                        except Exception as exc:
                            # A stage raising (subprocess crash, missing binary, OSError,
                            # etc.) must fail only this item, never escape to the lane
                            # loop - an uncaught exception here would kill the lane's
                            # daemon thread, wedging every job queued behind it forever.
                            terminal = "failed"
                            set_item_status(self._conn, item["id"], "failed", current_stage=None)
                            set_item_error(self._conn, item["id"], str(exc))
                            self._event_bus.publish(
                                {
                                    "type": "item",
                                    "job_id": job_id,
                                    "item_id": item["id"],
                                    "status": "failed",
                                    "current_stage": None,
                                }
                            )
                        if terminal == "failed":
                            job_failed = True

            # Product ruling: cancelled wins over failed at the job level,
            # even if one or more items already failed before the cancel
            # landed. A user who clicked Cancel doesn't want to be told their
            # job "failed" - "cancelled" reflects what THEY did, not what the
            # pipeline happened to observe first. No information is lost:
            # each item's own error detail is preserved unconditionally via
            # set_item_error() above regardless of the job's overall
            # final_status, so a failed item still shows its real error in
            # the job detail view even though the job itself reads
            # "cancelled".
            if cancel_event.is_set():
                final_status = "cancelled"
            elif job_failed:
                final_status = "failed"
            else:
                final_status = "completed"
            set_job_status(self._conn, job_id, final_status, finished_at=_utc_now())
            self._event_bus.publish({"type": "job", "job_id": job_id, "status": final_status})
        except Exception:
            # Final safety net: nothing above this point may propagate, or
            # `_lane_loop`'s `while True` dies silently - the job row is stuck
            # at "running" forever and every later job on this lane queues
            # forever without ever running. Also covers an unknown-recipe
            # KeyError on `self._registry[job["recipe"]]` (submit() already
            # validates known recipes, but a registry edited or swapped out
            # between submit and execution could still hit this).
            log.exception("Job %s (recipe=%r) crashed in the queue's outer safety net", job_id, job["recipe"])
            try:
                set_job_status(self._conn, job_id, "failed", finished_at=_utc_now())
            except Exception:
                pass
            self._event_bus.publish({"type": "job", "job_id": job_id, "status": "failed"})

    def _run_items_by_stage(
        self,
        job_id: int,
        items: list[dict],
        recipe_def: RecipeDefinition,
        options: dict,
        cancel_event: threading.Event,
        worker_pool: PersistentWorkerPool,
    ) -> bool:
        """Run a bulk job stage-major so each heavy model is loaded once.

        A phase processes every eligible item through one stage, then closes
        that phase's worker before the next model is loaded. This both avoids
        cold starts and prevents Demucs plus WhisperX from accumulating in GPU
        memory during combined karaoke recipes.
        """
        records: dict[int, dict] = {}
        for item in items:
            stages = [factory(options) for factory in recipe_def.stage_factories]
            stages_state = [
                {"name": stage.name, "status": "pending", "started_at": None, "finished_at": None, "error": None}
                for stage in stages
            ]
            records[item["id"]] = {
                "stages": stages,
                "state": stages_state,
                "any_ran": False,
                "terminal": None,
            }
            set_item_stages(self._conn, item["id"], stages_state)

        stage_count = len(next(iter(records.values()))["stages"]) if records else 0
        if stage_count == 0:
            for item in items:
                self._finish_batch_item(job_id, item["id"], "skipped")
            return False

        for stage_index in range(stage_count):
            for item in items:
                item_id = item["id"]
                record = records[item_id]
                if record["terminal"] is not None:
                    continue
                if cancel_event.is_set():
                    record["terminal"] = "cancelled"
                    self._finish_batch_item(job_id, item_id, "cancelled")
                    continue

                stage = record["stages"][stage_index]
                state = record["state"]
                set_item_status(self._conn, item_id, "running", current_stage=stage.name)
                self._event_bus.publish(
                    {
                        "type": "item", "job_id": job_id, "item_id": item_id,
                        "status": "running", "current_stage": stage.name,
                    }
                )
                ctx = StageContext(
                    source_path=Path(item["source_path"]),
                    overwrite=bool(options.get("overwrite", False)),
                    options=options,
                    cancel_event=cancel_event,
                    worker_runner=worker_pool.run,
                )

                if should_skip(stage, ctx):
                    result = StageResult(status=StageStatus.SKIPPED, detail="outputs already exist")
                else:
                    state[stage_index]["status"] = "running"
                    state[stage_index]["started_at"] = _utc_now()
                    set_item_stages(self._conn, item_id, state)
                    self._event_bus.publish(
                        {
                            "type": "stage", "job_id": job_id, "item_id": item_id,
                            "stage": stage.name, "status": "running",
                        }
                    )
                    last_progress_emit_at = 0.0

                    def on_progress(event: dict, *, _stage=stage, _item_id=item_id) -> None:
                        nonlocal last_progress_emit_at
                        now = time.monotonic()
                        if last_progress_emit_at and now - last_progress_emit_at < 0.25:
                            return
                        last_progress_emit_at = now
                        self._event_bus.publish(
                            {
                                "type": "stage_progress", "job_id": job_id, "item_id": _item_id,
                                "stage": _stage.name, "detail": event.get("message", ""),
                            }
                        )

                    ctx.on_progress = on_progress
                    try:
                        result = stage.run(ctx)
                    except Exception as exc:
                        result = StageResult(status=StageStatus.FAILED, detail=str(exc))

                state[stage_index]["status"] = result.status.value
                state[stage_index]["finished_at"] = _utc_now()
                if result.status == StageStatus.FAILED:
                    state[stage_index]["error"] = result.detail
                set_item_stages(self._conn, item_id, state)
                self._event_bus.publish(
                    {
                        "type": "stage", "job_id": job_id, "item_id": item_id,
                        "stage": stage.name, "status": result.status.value,
                    }
                )
                self._publish_output_provenance(job_id, item, stage, result)
                self._publish_track_metadata_change(job_id, item, result)
                self._publish_track_outputs_change(job_id, item, stage, result, options)

                if result.status == StageStatus.COMPLETED:
                    record["any_ran"] = True
                if cancel_event.is_set() and result.status != StageStatus.COMPLETED:
                    terminal = "cancelled"
                elif result.status == StageStatus.FAILED:
                    terminal = "failed"
                elif cancel_event.is_set() and stage_index < stage_count - 1:
                    terminal = "cancelled"
                elif stage_index == stage_count - 1:
                    terminal = "completed" if record["any_ran"] else "skipped"
                else:
                    terminal = None

                if terminal is None:
                    set_item_status(self._conn, item_id, "queued", current_stage=None)
                    self._event_bus.publish(
                        {
                            "type": "item", "job_id": job_id, "item_id": item_id,
                            "status": "queued", "current_stage": None,
                        }
                    )
                else:
                    record["terminal"] = terminal
                    if terminal == "failed":
                        set_item_error(self._conn, item_id, result.detail)
                    self._finish_batch_item(job_id, item_id, terminal)

            # Release one model family before the next phase starts. The same
            # pool object can lazily start a clean worker for the next stage.
            worker_pool.close()
            if all(record["terminal"] is not None for record in records.values()):
                break

        return any(record["terminal"] == "failed" for record in records.values())

    def _finish_batch_item(self, job_id: int, item_id: int, terminal: str) -> None:
        set_item_status(self._conn, item_id, terminal, current_stage=None)
        self._event_bus.publish(
            {
                "type": "item", "job_id": job_id, "item_id": item_id,
                "status": terminal, "current_stage": None,
            }
        )

    def _publish_track_metadata_change(self, job_id: int, item: dict, result: StageResult) -> None:
        """Reconcile one source-file metadata write and broadcast its fresh row.

        Processing stages normally create sidecar files whose output flags are
        discovered by the completion rescan. FetchTagsStage is different: it
        inspects or mutates the original file's metadata and explicitly marks
        that fact in its result, allowing a single-row update after each item.
        """
        track_id = item.get("track_id")
        if not result.refresh_track_metadata or track_id is None:
            return
        refreshed = read_extended_tags(Path(item["source_path"]))
        updated = update_track_tags(
            self._conn,
            track_id,
            artist=refreshed.artist,
            title=refreshed.title,
            album=refreshed.album,
            year=refreshed.year,
        )
        if updated is None:
            return
        updated.pop("absolute_path", None)
        self._event_bus.publish(
            {
                "type": "track_updated",
                "job_id": job_id,
                "item_id": item["id"],
                "track_id": track_id,
                "track": updated,
            }
        )

    def _publish_track_outputs_change(
        self,
        job_id: int,
        item: dict,
        stage: Stage,
        result: StageResult,
        options: dict,
    ) -> None:
        """Reconcile only one processed track's sidecar-derived columns.

        This is deliberately performed by the queue at the stage boundary:
        the database is correct even if no browser is connected, while the
        event lets a connected Library and lyric editor update immediately.
        A whole-library filesystem rescan is neither necessary nor acceptable
        for a catalogue containing tens of thousands of tracks.
        """
        track_id = item.get("track_id")
        if (
            track_id is None
            or result.refresh_track_metadata
            or stage.name == "fetch_tags"
            or result.status not in {StageStatus.COMPLETED, StageStatus.SKIPPED}
        ):
            return
        track = get_track(self._conn, track_id)
        if track is None:
            return
        source_path = Path(item["source_path"])
        mirror_roots = [Path(root) for root in options.get("mirror_roots", [])]
        outputs, lrc_state = find_outputs(source_path, Path(track["media_root"]), mirror_roots)
        updated = update_track_outputs(self._conn, track_id, outputs, lrc_state)
        if updated is None:
            return
        self._event_bus.publish(
            {
                "type": "track_updated",
                "job_id": job_id,
                "item_id": item["id"],
                "track_id": track_id,
                "track": updated,
            }
        )

    def _publish_output_provenance(
        self, job_id: int, item: dict, stage: Stage, result: StageResult
    ) -> None:
        """Record and publish an artifact only after atomic stage success."""
        track_id = item.get("track_id")
        if result.status != StageStatus.COMPLETED or track_id is None:
            return
        for provenance in result.output_provenance:
            updated = record_instrumental_provenance(
                self._conn,
                track_id,
                job_id,
                item["id"],
                stage.name,
                provenance,
                recorded_at=_utc_now(),
            )
            if updated is None:
                continue
            self._event_bus.publish(
                {
                    "type": "track_updated",
                    "job_id": job_id,
                    "item_id": item["id"],
                    "track_id": track_id,
                    "track": updated,
                }
            )

    def _run_item(
        self,
        job_id: int,
        item: dict,
        recipe_def: RecipeDefinition,
        options: dict,
        cancel_event: threading.Event,
        worker_runner: Callable[..., object] | None = None,
    ) -> str:
        stages = [factory(options) for factory in recipe_def.stage_factories]
        stages_state = [
            {"name": stage.name, "status": "pending", "started_at": None, "finished_at": None, "error": None}
            for stage in stages
        ]
        set_item_stages(self._conn, item["id"], stages_state)
        set_item_status(
            self._conn, item["id"], "running", current_stage=stages[0].name if stages else None
        )
        self._event_bus.publish(
            {
                "type": "item",
                "job_id": job_id,
                "item_id": item["id"],
                "status": "running",
                "current_stage": stages[0].name if stages else None,
            }
        )

        def on_stage_change(stage, result, _state=stages_state, _item_id=item["id"]):
            index = next(i for i, entry in enumerate(_state) if entry["name"] == stage.name)
            now = _utc_now()
            if result is None:
                _state[index]["status"] = "running"
                _state[index]["started_at"] = now
                set_item_status(self._conn, _item_id, "running", current_stage=stage.name)
            else:
                _state[index]["status"] = result.status.value
                _state[index]["finished_at"] = now
                if result.status == StageStatus.FAILED:
                    _state[index]["error"] = result.detail
            set_item_stages(self._conn, _item_id, _state)
            self._event_bus.publish(
                {
                    "type": "stage",
                    "job_id": job_id,
                    "item_id": _item_id,
                    "stage": stage.name,
                    "status": _state[index]["status"],
                }
            )
            if result is not None:
                self._publish_output_provenance(job_id, item, stage, result)
                self._publish_track_metadata_change(job_id, item, result)
                self._publish_track_outputs_change(job_id, item, stage, result, options)

        # Rate-limit stage_progress publishing per stage: a chatty stage
        # (e.g. WhisperX emitting a progress line per word/segment) can fire
        # dozens of on_progress() calls a second, and each one used to become
        # its own event_bus.publish() - a firehose the frontend (and any
        # other subscriber) had to eat in full. Leading-edge throttling only
        # (always emit the first event for a stage, then at most one more
        # per 250ms) - no trailing flush, so a burst's last line may be
        # dropped, but that's an acceptable trade: the stage-status event
        # that follows (on_stage_change, above) always fires and tells
        # subscribers the stage finished either way.
        last_progress_emit_at: dict[str, float] = {}
        progress_interval_seconds = 0.25

        def on_progress(stage, event, _item_id=item["id"]):
            now = time.monotonic()
            last_emit = last_progress_emit_at.get(stage.name)
            if last_emit is not None and (now - last_emit) < progress_interval_seconds:
                return
            last_progress_emit_at[stage.name] = now
            self._event_bus.publish(
                {
                    "type": "stage_progress",
                    "job_id": job_id,
                    "item_id": _item_id,
                    "stage": stage.name,
                    "detail": event.get("message", ""),
                }
            )

        terminal = execute_item(
            stages,
            Path(item["source_path"]),
            options,
            bool(options.get("overwrite", False)),
            cancel_event,
            on_stage_change,
            on_progress=on_progress,
            worker_runner=worker_runner,
        )

        set_item_status(self._conn, item["id"], terminal, current_stage=None)
        if terminal == "failed":
            error = next((entry["error"] for entry in stages_state if entry["error"]), "stage failed")
            set_item_error(self._conn, item["id"], error)
        self._event_bus.publish(
            {
                "type": "item",
                "job_id": job_id,
                "item_id": item["id"],
                "status": terminal,
                "current_stage": None,
            }
        )
        return terminal

    def _recover_crashed_jobs(self) -> None:
        for job_id in reset_stuck_jobs(self._conn):
            with self._cancel_events_lock:
                self._cancel_events[job_id] = threading.Event()
            job = None
            try:
                job = get_job(self._conn, job_id)
                if job is None:
                    continue
                self._event_bus.publish({"type": "job", "job_id": job_id, "status": "queued"})
                lane = self._registry[job["recipe"]].lane
                self._lane_queues[lane].put(job_id)
            except Exception:
                # A stale row referencing a recipe that no longer exists in the
                # registry (e.g. removed since the crash) must not abort app
                # startup - mark just this job "failed" and keep recovering
                # the rest.
                log.exception(
                    "Job %s crashed while being recovered at startup (recipe=%r)",
                    job_id, job["recipe"] if job else None,
                )
                set_job_status(self._conn, job_id, "failed", finished_at=_utc_now())
                self._event_bus.publish({"type": "job", "job_id": job_id, "status": "failed"})
