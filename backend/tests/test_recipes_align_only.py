from app.recipes import REGISTRY


def test_align_only_is_a_hidden_gpu_recipe_with_one_forced_alignment_stage():
    recipe = REGISTRY["align_only"]

    assert recipe.hidden is True
    assert recipe.lane == "gpu"
    assert recipe.options_schema["asr_model"]["default"] == "small.en"

    stages = [factory({"device": "cpu", "asr_model": "base.en"}) for factory in recipe.stage_factories]
    assert [stage.name for stage in stages] == ["align_lyrics"]
    assert stages[0]._device == "cpu"
    assert stages[0]._asr_model == "base.en"
    assert stages[0]._realign_enhanced is True
    assert stages[0]._reset_existing_timing is True
    assert stages[0]._require_worker is True
