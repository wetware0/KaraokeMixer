from __future__ import annotations

from ..stages.fetch_tags import FetchTagsStage
from .registry import RecipeDefinition, register

RECIPE = RecipeDefinition(
    name="fetch_tags",
    lane="cpu",
    stage_factories=[lambda options: FetchTagsStage()],
    options_schema=None,
)
register(RECIPE)
