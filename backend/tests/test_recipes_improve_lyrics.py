from app.recipes import REGISTRY


def test_improve_lyrics_defaults_to_deep_medium_review():
    recipe = REGISTRY["improve_lyrics"]

    assert recipe.options_schema["timing_review_profile"]["default"] == "deep"
    assert recipe.options_schema["asr_model"]["default"] == "medium"
    stage = recipe.stage_factories[0]({"device": "cuda"})
    assert stage._deep_review is True
    assert stage._asr_model == "medium"


def test_improve_lyrics_can_still_run_the_quick_dual_audio_review():
    stage = REGISTRY["improve_lyrics"].stage_factories[0]({
        "device": "cpu", "timing_review_profile": "quick", "asr_model": "small.en",
    })

    assert stage._deep_review is False
    assert stage._asr_model == "small.en"
