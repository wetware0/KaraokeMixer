from app.pipeline import StageContext, StageStatus
from app.recipes import REGISTRY


def test_karaoke_recipe_is_registered_on_gpu_lane_and_visible():
    recipe = REGISTRY["karaoke"]
    assert recipe.lane == "gpu"
    assert recipe.hidden is False
    assert recipe.options_schema["backing_vocal_mode"]["choices"] == ["stripped", "faint", "stereo_mix", "best"]
    assert recipe.options_schema["backing_vocal_mode"]["default"] == "stripped"
    assert recipe.options_schema["model"]["default"] == "htdemucs"
    assert recipe.options_schema["model"]["choices"] == [
        "htdemucs", "htdemucs_ft", "mdx", "mdx_extra_q", "htdemucs_6s",
    ]
    assert recipe.options_schema["processing_profile"]["default"] == "balanced"


def test_karaoke_recipe_stage_factory_builds_a_karaoke_instrumental_stage():
    stage = REGISTRY["karaoke"].stage_factories[0]({"model": "mdx", "device": "cpu", "backing_vocal_mode": "faint"})

    assert stage.name == "karaoke_instrumental"


def test_karaoke_profiles_choose_fast_and_high_quality_engines():
    build = REGISTRY["karaoke"].stage_factories[0]

    fast = build({"processing_profile": "fast", "device": "cpu"})
    quality = build({"processing_profile": "high_quality", "device": "cpu"})

    assert (fast._model, fast._backing_vocal_mode) == ("mdx", "stripped")
    assert (quality._model, quality._backing_vocal_mode) == ("htdemucs_ft", "best")


def test_karaoke_recipe_options_schema_gains_lyrics_fields():
    recipe = REGISTRY["karaoke"]
    assert recipe.options_schema["fetch_lyrics"]["default"] is True
    assert recipe.options_schema["align_lyrics"]["default"] is True
    assert recipe.options_schema["asr_model"]["default"] == "small.en"


def test_karaoke_recipe_stage_factories_include_fetch_and_align_lyrics():
    stages = [
        factory({"model": "htdemucs", "device": "cpu", "backing_vocal_mode": "stripped"})
        for factory in REGISTRY["karaoke"].stage_factories
    ]

    assert [stage.name for stage in stages] == ["karaoke_instrumental", "fetch_lyrics", "align_lyrics"]
    assert stages[2]._require_worker is True


def test_karaoke_recipe_fetch_lyrics_stage_respects_its_own_option_key(tmp_path):
    stage = REGISTRY["karaoke"].stage_factories[1]({})
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"fetch_lyrics": False})

    assert stage.run(ctx).status == StageStatus.SKIPPED


def test_karaoke_recipe_align_lyrics_stage_respects_its_own_option_key(tmp_path):
    stage = REGISTRY["karaoke"].stage_factories[2]({"device": "cpu"})
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"align_lyrics": False})

    assert stage.run(ctx).status == StageStatus.SKIPPED
