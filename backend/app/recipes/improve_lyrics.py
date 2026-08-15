from __future__ import annotations

from ..stages.improve_lyrics import ImproveLyricsStage
from .registry import RecipeDefinition, register


def _build_stage(options: dict) -> ImproveLyricsStage:
    return ImproveLyricsStage(
        device=options.get("device", "cpu"),
        deep_review=options.get("timing_review_profile", "deep") == "deep",
        asr_model=options.get("asr_model", "medium"),
    )


RECIPE = RecipeDefinition(
    name="improve_lyrics",
    lane="gpu",
    stage_factories=[_build_stage],
    options_schema={
        "timing_review_profile": {
            "type": "select", "choices": ["deep", "quick"], "default": "deep",
        },
        "asr_model": {
            "type": "select", "choices": ["small.en", "medium"],
            "default": "medium", "advanced": True,
        },
    },
    batch_by_stage=True,
)
register(RECIPE)
