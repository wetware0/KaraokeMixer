from __future__ import annotations

import time
from pathlib import Path

from ..pipeline import Stage, StageContext, StageResult, StageStatus, atomic_publish
from .registry import RecipeDefinition, register


class FakePrepareStage:
    """No declared outputs - stands in for a discovery/analysis step that
    always re-runs, since there is nothing on disk yet to resume from."""

    name = "fake_prepare"

    def __init__(self, delay_seconds: float = 0.02) -> None:
        self._delay_seconds = delay_seconds

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        time.sleep(self._delay_seconds)
        return StageResult(status=StageStatus.COMPLETED, detail="prepared")


class FakePublishStage(Stage):
    """Writes a small marker file beside the source, atomically."""

    name = "fake_publish"

    def __init__(self, delay_seconds: float = 0.02) -> None:
        self._delay_seconds = delay_seconds

    def _output_path(self, ctx: StageContext) -> Path:
        return ctx.source_path.with_name(f"{ctx.source_path.stem}.fake.txt")

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return [self._output_path(ctx)]

    def run(self, ctx: StageContext) -> StageResult:
        time.sleep(self._delay_seconds)
        output_path = self._output_path(ctx)
        atomic_publish(output_path, lambda part: part.write_text("fake output\n", encoding="utf-8"))
        return StageResult(status=StageStatus.COMPLETED, detail=f"wrote {output_path.name}")


def _delay(options: dict) -> float:
    return float(options.get("fake_delay_seconds", 0.02))


RECIPE = RecipeDefinition(
    name="fake",
    lane="cpu",
    stage_factories=[
        lambda options: FakePrepareStage(delay_seconds=_delay(options)),
        lambda options: FakePublishStage(delay_seconds=_delay(options)),
    ],
    hidden=True,
    options_schema={
        "volume_mode": {"type": "select", "choices": ["quiet", "loud"], "default": "quiet"},
        "dry_run": {"type": "checkbox", "default": False},
        "passes": {"type": "number", "default": 1},
        "fake_delay_seconds": {"type": "number", "default": 0.02},
    },
)
register(RECIPE)
