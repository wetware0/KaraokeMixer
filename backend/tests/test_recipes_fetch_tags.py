from app.recipes import REGISTRY


def test_fetch_tags_recipe_is_registered_on_cpu_lane_and_visible():
    recipe = REGISTRY["fetch_tags"]
    assert recipe.lane == "cpu"
    assert recipe.hidden is False
    assert recipe.options_schema is None


def test_fetch_tags_recipe_stage_factory_builds_a_fetch_tags_stage():
    stage = REGISTRY["fetch_tags"].stage_factories[0]({})
    assert stage.name == "fetch_tags"
