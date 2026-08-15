from __future__ import annotations

from ..stages.improve_lyrics import ImproveLyricsStage
from .registry import RecipeDefinition, register


def _build_stage(options: dict) -> ImproveLyricsStage:
    return ImproveLyricsStage(device=options.get("device", "cpu"))


RECIPE = RecipeDefinition(
    name="improve_lyrics",
    lane="gpu",
    stage_factories=[_build_stage],
    options_schema={},
    batch_by_stage=True,
)
register(RECIPE)
