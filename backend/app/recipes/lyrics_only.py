from __future__ import annotations

from ..stages.align_lyrics import AlignLyricsStage
from ..stages.fetch_lyrics import FetchLyricsStage
from .registry import RecipeDefinition, register
from .profiles import PROFILE_OPTION, profile_option

ASR_MODEL_CHOICES = ["small.en", "base.en", "medium"]

OPTIONS_SCHEMA = {
    "processing_profile": PROFILE_OPTION,
    "fetch": {"type": "checkbox", "default": True},
    "align": {"type": "checkbox", "default": True},
    "asr_model": {
        "type": "select", "choices": ASR_MODEL_CHOICES, "default": "small.en", "advanced": True,
    },
}


def _build_fetch_stage(options: dict) -> FetchLyricsStage:
    return FetchLyricsStage(enabled_option_key="fetch")


def _build_align_stage(options: dict) -> AlignLyricsStage:
    return AlignLyricsStage(
        asr_model=str(profile_option(options, "asr_model", "small.en")),
        device=options.get("device", "cpu"),
        enabled_option_key="align",
        require_worker=True,
    )


RECIPE = RecipeDefinition(
    name="lyrics_only", lane="gpu",
    stage_factories=[_build_fetch_stage, _build_align_stage],
    options_schema=OPTIONS_SCHEMA,
    batch_by_stage=True,
)
register(RECIPE)
