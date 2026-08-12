from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/recipes")
def read_recipes(request: Request) -> dict:
    registry = request.app.state.job_queue.registry
    recipes = [
        {"name": recipe.name, "lane": recipe.lane, "options_schema": recipe.options_schema}
        for recipe in registry.values()
        if not recipe.hidden
    ]
    recipes.sort(key=lambda recipe: recipe["name"])
    return {"recipes": recipes}
