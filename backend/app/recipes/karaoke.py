from __future__ import annotations

from ..stages.align_lyrics import AlignLyricsStage
from ..stages.demucs import DEMUCS_MODELS
from ..stages.fetch_lyrics import FetchLyricsStage
from ..stages.karaoke_instrumental import BACKING_VOCAL_MODES, KaraokeInstrumentalStage
from .registry import RecipeDefinition, register
from .profiles import PROFILE_OPTION, profile_option

ASR_MODEL_CHOICES = ["small.en", "base.en", "medium"]

OPTIONS_SCHEMA = {
    "processing_profile": PROFILE_OPTION,
    "model": {
        "type": "select", "choices": list(DEMUCS_MODELS), "default": "htdemucs", "advanced": True,
    },
    "backing_vocal_mode": {
        "type": "select", "choices": list(BACKING_VOCAL_MODES), "default": "stripped", "advanced": True,
    },
    "fetch_lyrics": {"type": "checkbox", "default": True},
    "align_lyrics": {"type": "checkbox", "default": True},
    "asr_model": {
        "type": "select", "choices": ASR_MODEL_CHOICES, "default": "small.en", "advanced": True,
    },
}


def _build_stage(options: dict) -> KaraokeInstrumentalStage:
    return KaraokeInstrumentalStage(
        model=str(profile_option(options, "model", "htdemucs")),
        device=options.get("device", "cpu"),
        backing_vocal_mode=str(profile_option(options, "backing_vocal_mode", "stripped")),
    )


def _build_fetch_stage(options: dict) -> FetchLyricsStage:
    return FetchLyricsStage(enabled_option_key="fetch_lyrics")


def _build_align_stage(options: dict) -> AlignLyricsStage:
    return AlignLyricsStage(
        asr_model=str(profile_option(options, "asr_model", "small.en")),
        device=options.get("device", "cpu"),
        enabled_option_key="align_lyrics",
        require_worker=True,
    )


RECIPE = RecipeDefinition(
    name="karaoke", lane="gpu",
    stage_factories=[_build_stage, _build_fetch_stage, _build_align_stage],
    options_schema=OPTIONS_SCHEMA,
    batch_by_stage=True,
)
register(RECIPE)
