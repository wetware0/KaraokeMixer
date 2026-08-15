from app.recipes import REGISTRY
from app.stages.high_accuracy_lyrics import HighAccuracyLyricsStage, PrepareVocalReferenceStage
from app.stages.improve_lyrics import ImproveLyricsStage


def test_improve_lyrics_defaults_to_high_accuracy_medium_review():
    recipe = REGISTRY["improve_lyrics"]

    assert recipe.options_schema["timing_review_profile"]["default"] == "high_accuracy"
    assert recipe.options_schema["asr_model"]["default"] == "medium"
    prepare = recipe.stage_factories[0]({"device": "cuda"})
    review = recipe.stage_factories[1]({"device": "cuda"})
    assert isinstance(prepare, PrepareVocalReferenceStage)
    assert prepare._enabled is True
    assert isinstance(review, HighAccuracyLyricsStage)
    assert review._asr_model == "medium"


def test_improve_lyrics_can_still_run_the_quick_dual_audio_review():
    prepare = REGISTRY["improve_lyrics"].stage_factories[0]({
        "device": "cpu", "timing_review_profile": "quick", "asr_model": "small.en",
    })
    stage = REGISTRY["improve_lyrics"].stage_factories[1]({
        "device": "cpu", "timing_review_profile": "quick", "asr_model": "small.en",
    })

    assert prepare._enabled is False
    assert isinstance(stage, ImproveLyricsStage)
    assert stage._deep_review is False
    assert stage._asr_model == "small.en"


def test_improve_lyrics_keeps_the_legacy_deep_review_available():
    stage = REGISTRY["improve_lyrics"].stage_factories[1]({
        "device": "cuda", "timing_review_profile": "deep", "asr_model": "medium",
    })

    assert isinstance(stage, ImproveLyricsStage)
    assert stage._deep_review is True
