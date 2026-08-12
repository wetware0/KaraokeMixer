from app.pipeline import StageContext, StageStatus
from app.recipes import REGISTRY


def test_full_prep_recipe_is_registered_on_gpu_lane_and_visible():
    recipe = REGISTRY["full_prep"]
    assert recipe.lane == "gpu"
    assert recipe.hidden is False


def test_full_prep_options_schema_matches_karaokes_options_schema():
    assert REGISTRY["full_prep"].options_schema == REGISTRY["karaoke"].options_schema


def test_full_prep_stage_factories_build_the_expected_stages_in_order():
    options = {"model": "htdemucs", "device": "cpu", "backing_vocal_mode": "stripped", "asr_model": "small.en"}
    stages = [factory(options) for factory in REGISTRY["full_prep"].stage_factories]

    assert [stage.name for stage in stages] == [
        "demucs_separate", "karaoke_instrumental", "fetch_lyrics", "align_lyrics", "fetch_tags",
    ]
    assert stages[0]._instrumental_mode == "stripped"
    assert stages[1].__class__.__name__ == "PreparedInstrumentalStage"


def test_high_quality_full_prep_uses_uvr_instrumental_after_demucs_stems():
    options = {"processing_profile": "high_quality", "device": "cpu"}
    stages = [factory(options) for factory in REGISTRY["full_prep"].stage_factories]

    assert stages[0]._model == "htdemucs_ft"
    assert stages[0]._instrumental_mode is None
    assert stages[1].__class__.__name__ == "KaraokeInstrumentalStage"
    assert stages[1]._backing_vocal_mode == "best"


def test_full_prep_fetch_lyrics_and_align_stages_respect_the_shared_karaoke_option_keys(tmp_path):
    stages = {
        factory({}).name: factory
        for factory in REGISTRY["full_prep"].stage_factories
    }
    fetch_stage = stages["fetch_lyrics"]({})
    align_stage = stages["align_lyrics"]({"device": "cpu"})
    assert align_stage._require_worker is True

    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"fetch_lyrics": False, "align_lyrics": False})

    assert fetch_stage.run(ctx).status == StageStatus.SKIPPED
    assert align_stage.run(ctx).status == StageStatus.SKIPPED
