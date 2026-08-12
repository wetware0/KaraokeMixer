from app.recipes import REGISTRY


def test_youtube_import_recipe_is_registered_hidden_on_cpu_lane():
    recipe = REGISTRY["youtube_import"]
    assert recipe.lane == "cpu"
    assert recipe.hidden is True


def test_stage_factory_builds_a_youtube_import_stage():
    stage = REGISTRY["youtube_import"].stage_factories[0]({})

    assert stage.name == "youtube_import"
