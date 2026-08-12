from __future__ import annotations

from ..stages.youtube_import import YoutubeImportStage
from .registry import RecipeDefinition, register

RECIPE = RecipeDefinition(
    name="youtube_import",
    lane="cpu",
    stage_factories=[lambda options: YoutubeImportStage()],
    hidden=True,  # driven by its own dialog (Task 17), never the generic ProcessDialog/recipe select
    options_schema=None,
)
register(RECIPE)
