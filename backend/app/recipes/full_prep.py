from __future__ import annotations

from ..stages.align_lyrics import AlignLyricsStage
from ..stages.demucs import DemucsSeparateStage, stem_index_map
from ..stages.fetch_lyrics import FetchLyricsStage
from ..stages.fetch_tags import FetchTagsStage
from ..stages.karaoke_instrumental import KaraokeInstrumentalStage, PreparedInstrumentalStage
from .karaoke import OPTIONS_SCHEMA as KARAOKE_OPTIONS_SCHEMA
from .registry import RecipeDefinition, register
from .profiles import profile_option

# full_prep reuses every stage class verbatim (no new stage code) - it is
# purely a composition of the existing karaoke/full_stems/lyrics building
# blocks plus the new fetch_tags stage, so it reuses karaoke's options
# schema wholesale rather than redeclaring the same model/backing_vocal_mode/
# fetch_lyrics/align_lyrics/asr_model fields a second time.
OPTIONS_SCHEMA = dict(KARAOKE_OPTIONS_SCHEMA)


def _build_demucs_stage(options: dict) -> DemucsSeparateStage:
    model = str(profile_option(options, "model", "htdemucs"))
    device = options.get("device", "cpu")
    backing_mode = str(profile_option(options, "backing_vocal_mode", "stripped"))
    return DemucsSeparateStage(
        model=model,
        device=device,
        stems=list(stem_index_map(model)),
        instrumental_mode=backing_mode if backing_mode != "best" else None,
    )


def _build_karaoke_stage(options: dict) -> KaraokeInstrumentalStage | PreparedInstrumentalStage:
    backing_mode = str(profile_option(options, "backing_vocal_mode", "stripped"))
    if backing_mode != "best":
        return PreparedInstrumentalStage()
    return KaraokeInstrumentalStage(
        model=str(profile_option(options, "model", "htdemucs")),
        device=options.get("device", "cpu"),
        backing_vocal_mode=backing_mode,
    )


def _build_fetch_lyrics_stage(options: dict) -> FetchLyricsStage:
    return FetchLyricsStage(enabled_option_key="fetch_lyrics")


def _build_align_stage(options: dict) -> AlignLyricsStage:
    return AlignLyricsStage(
        asr_model=str(profile_option(options, "asr_model", "small.en")),
        device=options.get("device", "cpu"),
        enabled_option_key="align_lyrics",
        require_worker=True,
    )


def _build_fetch_tags_stage(options: dict) -> FetchTagsStage:
    return FetchTagsStage()


RECIPE = RecipeDefinition(
    name="full_prep",
    lane="gpu",
    stage_factories=[
        _build_demucs_stage,
        _build_karaoke_stage,
        _build_fetch_lyrics_stage,
        _build_align_stage,
        _build_fetch_tags_stage,
    ],
    options_schema=OPTIONS_SCHEMA,
    batch_by_stage=True,
)
register(RECIPE)
