from __future__ import annotations

from pathlib import Path

from ..metadata.providers import DEFAULT_TAGS_PROVIDERS, TagsProvider, download_artwork, search_tags_providers
from ..pipeline import StageContext, StageResult, StageStatus
from ..scanner import read_extended_tags
from ..tags import read_embedded_artwork, write_embedded_artwork, write_text_tags

SUPPORTED_SUFFIXES = {".flac", ".mp3", ".m4a"}


class FetchTagsStage:
    """Fills missing artist/title/album/year metadata and embeds cover art
    from iTunes (MusicBrainz+CoverArtArchive fallback) directly into the
    ORIGINAL audio file's tag/metadata blocks - the one spec-approved
    exception to "never modify source audio" (the audio STREAM itself is
    untouched; only metadata containers are rewritten, exactly like
    AlignLyricsStage rewrites a sidecar .lrc in place). Fills only missing
    fields/artwork unless ctx.overwrite is set.

    declared_outputs is always [] - like AlignLyricsStage, this stage's
    resumability is a content check made inside run(), not a file-existence
    check, since its "output" is metadata embedded in a file that already
    exists before the stage runs.
    """

    name = "fetch_tags"

    def __init__(self, providers: list[TagsProvider] | None = None) -> None:
        self._providers = providers if providers is not None else DEFAULT_TAGS_PROVIDERS

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        path = ctx.source_path
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return StageResult(
                status=StageStatus.SKIPPED, detail=f"unsupported format for tag writing: {path.suffix}"
            )

        current = read_extended_tags(path)
        has_artwork = read_embedded_artwork(path) is not None
        missing_fields = current.album is None or current.year is None
        overwrite = ctx.overwrite

        if not missing_fields and has_artwork and not overwrite:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail="tags and artwork already present",
                refresh_track_metadata=True,
            )

        result = search_tags_providers(current.artist or "", current.title, self._providers)
        if result is None:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail="no metadata match found",
                refresh_track_metadata=True,
            )
        match, provider_name = result

        new_artist = (match.artist if overwrite and match.artist else current.artist) or match.artist
        new_album = (match.album if overwrite and match.album else current.album) or match.album
        new_year = (match.year if overwrite and match.year else current.year) or match.year

        wrote_text = new_artist != current.artist or new_album != current.album or new_year != current.year
        wrote_artwork = False

        try:
            if wrote_text:
                write_text_tags(path, artist=new_artist, title=current.title, album=new_album, year=new_year)

            if (not has_artwork or overwrite) and match.artwork_url:
                downloaded = download_artwork(match.artwork_url)
                if downloaded is not None:
                    # download_artwork already sniffs/validates the image
                    # bytes and only ever returns "image/jpeg" or
                    # "image/png" - trust it rather than re-checking here.
                    data, mime = downloaded
                    write_embedded_artwork(path, data, mime)
                    wrote_artwork = True
        except ValueError as exc:
            # A text-tag write may have succeeded before an artwork write
            # failed. Ask the queue to re-read the file in that case so the
            # Library still reflects the partial, durable change honestly.
            return StageResult(
                status=StageStatus.FAILED,
                detail=str(exc),
                refresh_track_metadata=True,
            )

        if not wrote_text and not wrote_artwork:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail=f"no new data from {provider_name}",
                refresh_track_metadata=True,
            )
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=f"tags updated from {provider_name}",
            refresh_track_metadata=True,
        )
