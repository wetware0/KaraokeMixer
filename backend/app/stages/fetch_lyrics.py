from __future__ import annotations

from pathlib import Path

from ..lrc import LrcDocument, TimingState, classify_lrc_file
from ..lyrics.paths import resolve_lrc_path
from ..lyrics.providers import DEFAULT_PROVIDERS, LyricsProvider, search_providers
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..scanner import read_tags


class FetchLyricsStage:
    """Writes {name}.lrc via the configured lyrics providers, tried in
    order. Resumable via normal existence-based skip (declared_outputs),
    matching spec section 4's overwrite toggle. "No lyrics found" is
    SKIPPED, not FAILED, per spec section 7 - separation outputs still
    publish. `enabled_option_key` lets karaoke ("fetch_lyrics") and
    lyrics_only ("fetch") each use their own option name for the same
    stage class.

    An existing ENHANCED (word-timed) .lrc is never downgraded, even under
    overwrite=True: word timing is expensive GPU output (Task 9's
    AlignLyricsStage), and silently replacing it with plain/line-timed text
    fetched here would be silent data loss. Delete the .lrc to force a
    re-fetch."""

    name = "fetch_lyrics"

    def __init__(
        self, providers: list[LyricsProvider] | None = None, enabled_option_key: str = "fetch_lyrics"
    ) -> None:
        self._providers = providers if providers is not None else DEFAULT_PROVIDERS
        self._enabled_option_key = enabled_option_key

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return [resolve_lrc_path(ctx.source_path, ctx.options)]

    def run(self, ctx: StageContext) -> StageResult:
        if not ctx.options.get(self._enabled_option_key, True):
            return StageResult(status=StageStatus.SKIPPED, detail="lyrics fetch not requested")

        destination = resolve_lrc_path(ctx.source_path, ctx.options)
        if destination.exists() and classify_lrc_file(destination) == TimingState.ENHANCED:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail="existing word-timed lyrics preserved; delete the .lrc to re-fetch",
            )

        artist, title = read_tags(ctx.source_path)
        result = search_providers(artist or "", title or ctx.source_path.stem, self._providers)
        if result is None:
            return StageResult(status=StageStatus.SKIPPED, detail="no lyrics found")

        text, synced, provider_name = result
        atomic_publish(destination, lambda part: part.write_text(text, encoding="utf-8"))
        state = LrcDocument.parse(text).state
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=f"wrote {destination.name} from {provider_name} ({state.value})",
        )
