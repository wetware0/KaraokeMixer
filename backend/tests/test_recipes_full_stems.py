from pathlib import Path

from app.pipeline import StageContext
from app.recipes import REGISTRY


def test_full_stems_recipe_is_registered_on_gpu_lane_and_visible():
    recipe = REGISTRY["full_stems"]
    assert recipe.lane == "gpu"
    assert recipe.hidden is False
    assert recipe.options_schema["model"]["choices"] == [
        "htdemucs", "htdemucs_ft", "mdx", "mdx_extra_q", "htdemucs_6s",
    ]
    assert recipe.options_schema["split"]["default"] is False
    assert recipe.options_schema["processing_profile"]["choices"] == ["fast", "balanced", "high_quality"]


def test_full_stems_recipe_has_two_stages_demucs_then_optional_split():
    stages = [factory({"model": "htdemucs"}) for factory in REGISTRY["full_stems"].stage_factories]

    assert [stage.name for stage in stages] == ["demucs_separate", "uvr_vocal_split"]


def test_full_stems_demucs_stage_requests_all_stems_for_a_six_stem_model():
    stage = REGISTRY["full_stems"].stage_factories[0]({"model": "htdemucs_6s", "device": "cpu"})
    ctx = StageContext(source_path=Path("/media/song.flac"), overwrite=False, options={})

    outputs = stage.declared_outputs(ctx)

    names = {path.name for path in outputs}
    assert names == {
        "song.vocals.mp3", "song.drums.mp3", "song.bass.mp3",
        "song.other.mp3", "song.guitar.mp3", "song.piano.mp3",
    }


def test_high_quality_profile_selects_fine_tuned_demucs_and_vocal_split():
    stages = [
        factory({"processing_profile": "high_quality", "device": "cpu"})
        for factory in REGISTRY["full_stems"].stage_factories
    ]

    assert stages[0]._model == "htdemucs_ft"
    assert stages[1]._enabled is True
