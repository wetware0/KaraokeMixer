from __future__ import annotations

from .registry import REGISTRY, RecipeDefinition, register
from . import fake  # noqa: F401  imported for its registration side effect
from . import karaoke  # noqa: F401  imported for its registration side effect
from . import full_stems  # noqa: F401  imported for its registration side effect
from . import lyrics_only  # noqa: F401  imported for its registration side effect
from . import youtube_import  # noqa: F401  imported for its registration side effect
from . import fetch_tags  # noqa: F401  imported for its registration side effect
from . import full_prep  # noqa: F401  imported for its registration side effect
from . import align_only  # noqa: F401  imported for its registration side effect

__all__ = ["REGISTRY", "RecipeDefinition", "register"]
