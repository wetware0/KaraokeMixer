from __future__ import annotations

from ..stages.align_lyrics import AlignLyricsStage
from .lyrics_only import ASR_MODEL_CHOICES
from .registry import RecipeDefinition, register


OPTIONS_SCHEMA = {
    "asr_model": {"type": "select", "choices": ASR_MODEL_CHOICES, "default": "small.en"},
}


def _build_align_stage(options: dict) -> AlignLyricsStage:
    return AlignLyricsStage(
        asr_model=options.get("asr_model", "small.en"),
        device=options.get("device", "cpu"),
        realign_enhanced=True,
        reset_existing_timing=True,
        require_worker=True,
    )


# An editor-only command. It remains hidden from the generic Process dialog,
# where a creator should choose task-level recipes rather than an internal
# one-stage implementation detail.
RECIPE = RecipeDefinition(
    name="align_only",
    lane="gpu",
    stage_factories=[_build_align_stage],
    hidden=True,
    options_schema=OPTIONS_SCHEMA,
)
register(RECIPE)
