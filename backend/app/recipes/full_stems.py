from __future__ import annotations

from ..stages.demucs import DEMUCS_MODELS, DemucsSeparateStage, stem_index_map
from ..stages.uvr import UvrVocalSplitStage
from .registry import RecipeDefinition, register
from .profiles import PROFILE_OPTION, profile_option

OPTIONS_SCHEMA = {
    "processing_profile": PROFILE_OPTION,
    "model": {
        "type": "select", "choices": list(DEMUCS_MODELS), "default": "htdemucs", "advanced": True,
    },
    "split": {"type": "checkbox", "default": False, "advanced": True},
}


def _build_demucs_stage(options: dict) -> DemucsSeparateStage:
    model = str(profile_option(options, "model", "htdemucs"))
    device = options.get("device", "cpu")
    return DemucsSeparateStage(model=model, device=device, stems=list(stem_index_map(model)))


def _build_split_stage(options: dict) -> UvrVocalSplitStage:
    return UvrVocalSplitStage(enabled=bool(profile_option(options, "split", False)))


RECIPE = RecipeDefinition(
    name="full_stems",
    lane="gpu",
    stage_factories=[_build_demucs_stage, _build_split_stage],
    options_schema=OPTIONS_SCHEMA,
    batch_by_stage=True,
)
register(RECIPE)
