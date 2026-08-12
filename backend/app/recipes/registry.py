from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..pipeline import Stage


LANES = ("gpu", "cpu")


@dataclass(frozen=True)
class RecipeDefinition:
    name: str
    lane: str  # one of LANES
    stage_factories: list[Callable[[dict], Stage]] = field(default_factory=list)
    hidden: bool = False  # test/dev-only recipes that must never be offered to real users
    options_schema: dict | None = None  # {option_name: {"type": "select"|"checkbox"|"number", "choices"?: [...], "default": value}}
    batch_by_stage: bool = False  # run every item through stage N before loading stage N+1


REGISTRY: dict[str, RecipeDefinition] = {}


def register(recipe: RecipeDefinition) -> None:
    """Register a recipe definition. Raises ValueError at registration time
    (import time, in practice) if the recipe names a lane the queue manager
    does not run - an unrunnable lane would otherwise only surface later, as
    a job that sits in the database forever without any worker to drain it."""
    if recipe.lane not in LANES:
        raise ValueError(
            f"Recipe {recipe.name!r} has invalid lane {recipe.lane!r}; must be one of {LANES}"
        )
    REGISTRY[recipe.name] = recipe
