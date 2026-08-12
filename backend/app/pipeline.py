from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol


class StageStatus(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class StageResult:
    status: StageStatus
    detail: str
    # True when the stage inspected or changed metadata embedded in the source
    # track. The queue re-reads and publishes that one library row immediately,
    # including skipped items whose on-disk tags may be newer than the DB.
    refresh_track_metadata: bool = False
    # Descriptions of generated artifacts. The queue adds job identity and a
    # final file signature only after the stage has atomically published them.
    output_provenance: list[dict] = field(default_factory=list)


@dataclass
class StageContext:
    source_path: Path
    overwrite: bool
    options: dict
    cancel_event: threading.Event = field(default_factory=threading.Event)
    on_progress: Optional[Callable[[dict], None]] = None
    worker_runner: Optional[Callable[..., object]] = None


class Stage(Protocol):
    name: str

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        """Files this stage writes when it runs. An empty list means "always run" -
        there is nothing on disk to check for resumability."""
        ...

    def run(self, ctx: StageContext) -> StageResult:
        ...


def should_skip(stage: Stage, ctx: StageContext) -> bool:
    """True when every one of `stage`'s declared outputs already exists and
    the job did not request an overwrite.

    Exposed as its own function (not just an internal branch of `run_stage`)
    so a caller - `queue.execute_item` in particular - can make this decision
    *before* announcing that a stage has started running, instead of
    announcing "running" and then immediately correcting to "skipped"."""
    outputs = stage.declared_outputs(ctx)
    return bool(outputs) and not ctx.overwrite and all(path.exists() for path in outputs)


def run_stage(stage: Stage, ctx: StageContext) -> StageResult:
    """Run `stage`, skipping it if all of its declared outputs already exist
    and the job did not request an overwrite."""
    if should_skip(stage, ctx):
        return StageResult(status=StageStatus.SKIPPED, detail="outputs already exist")
    return stage.run(ctx)


def atomic_publish(destination: Path, write_fn: Callable[[Path], None]) -> None:
    """Write `destination` atomically: write to a `.part` sibling, then rename.

    A crash or exception mid-write leaves at most a stray `.part` file next to
    `destination` and never a truncated/corrupt `destination` itself; on
    failure the `.part` file is removed and the exception re-raised.

    Creates `destination.parent` (and any missing ancestors) first - the
    mirror-mode output-mode case (Task 3's `resolve_output_path`) can name a
    destination under a mirror root that has never been written to before,
    and every stage in this plan reaches its final file through this one
    function, so this is the single chokepoint where that directory needs
    to exist.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    part_path = destination.with_name(destination.name + ".part")
    try:
        write_fn(part_path)
        os.replace(part_path, destination)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
