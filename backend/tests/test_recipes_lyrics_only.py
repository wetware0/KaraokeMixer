from app.pipeline import StageContext, StageStatus
from app.recipes import REGISTRY


def test_lyrics_only_recipe_is_registered_on_gpu_lane_and_visible():
    recipe = REGISTRY["lyrics_only"]
    assert recipe.lane == "gpu"
    assert recipe.hidden is False
    assert recipe.options_schema["fetch"]["default"] is True
    assert recipe.options_schema["align"]["default"] is True
    assert recipe.options_schema["asr_model"]["choices"] == ["small.en", "base.en", "medium"]
    assert recipe.options_schema["asr_model"]["default"] == "small.en"
    assert recipe.options_schema["processing_profile"]["default"] == "balanced"


def test_stage_factories_build_fetch_then_align():
    stages = [factory({"asr_model": "base.en", "device": "cpu"}) for factory in REGISTRY["lyrics_only"].stage_factories]

    assert [stage.name for stage in stages] == ["fetch_lyrics", "align_lyrics"]
    assert stages[1]._require_worker is True


def test_fast_and_high_quality_profiles_choose_expected_asr_models():
    build = REGISTRY["lyrics_only"].stage_factories[1]

    assert build({"processing_profile": "fast", "device": "cpu"})._asr_model == "base.en"
    assert build({"processing_profile": "high_quality", "device": "cpu"})._asr_model == "medium"


def test_fetch_stage_respects_the_fetch_checkbox(tmp_path):
    stage = REGISTRY["lyrics_only"].stage_factories[0]({})
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"fetch": False})

    assert stage.run(ctx).status == StageStatus.SKIPPED


def test_align_stage_respects_the_align_checkbox(tmp_path):
    stage = REGISTRY["lyrics_only"].stage_factories[1]({"asr_model": "base.en", "device": "cpu"})
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"align": False})

    assert stage.run(ctx).status == StageStatus.SKIPPED
