from app.recipes.registry import REGISTRY, RecipeDefinition, register


def test_options_schema_defaults_to_none():
    recipe = RecipeDefinition(name="schema-default-test", lane="cpu", stage_factories=[])
    assert recipe.options_schema is None


def test_options_schema_round_trips_through_register():
    schema = {"model": {"type": "select", "choices": ["a", "b"], "default": "a"}}
    recipe = RecipeDefinition(name="schema-round-trip-test", lane="cpu", stage_factories=[], options_schema=schema)

    register(recipe)
    try:
        assert REGISTRY["schema-round-trip-test"].options_schema == schema
    finally:
        del REGISTRY["schema-round-trip-test"]
