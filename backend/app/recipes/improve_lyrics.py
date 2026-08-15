from __future__ import annotations

from ..stages.high_accuracy_lyrics import HighAccuracyLyricsStage, PrepareVocalReferenceStage
from ..stages.improve_lyrics import ImproveLyricsStage
from .registry import RecipeDefinition, register


def _is_high_accuracy(options: dict) -> bool:
    return options.get("timing_review_profile", "high_accuracy") == "high_accuracy"


def _build_vocal_stage(options: dict) -> PrepareVocalReferenceStage:
    return PrepareVocalReferenceStage(
        enabled=_is_high_accuracy(options),
        device=options.get("device", "cpu"),
    )


def _build_review_stage(options: dict) -> ImproveLyricsStage | HighAccuracyLyricsStage:
    if _is_high_accuracy(options):
        return HighAccuracyLyricsStage(
            device=options.get("device", "cpu"),
            asr_model=options.get("asr_model", "medium"),
        )
    return ImproveLyricsStage(
        device=options.get("device", "cpu"),
        deep_review=options.get("timing_review_profile", "deep") == "deep",
        asr_model=options.get("asr_model", "medium"),
    )


RECIPE = RecipeDefinition(
    name="improve_lyrics",
    lane="gpu",
    stage_factories=[_build_vocal_stage, _build_review_stage],
    options_schema={
        "timing_review_profile": {
            "type": "select",
            "choices": ["high_accuracy", "deep", "quick"],
            "default": "high_accuracy",
        },
        "asr_model": {
            "type": "select", "choices": ["small.en", "medium"],
            "default": "medium", "advanced": True,
        },
    },
    batch_by_stage=True,
)
register(RECIPE)
