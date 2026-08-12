<script lang="ts">
  import { findActiveLine, findActiveWord, isInstrumentalLine, type LrcModel } from "../lrcModel";

  let {
    model,
    currentTime,
    selectedWord = null,
    selectedLineIndex = null,
    onWordClick = () => {},
    onLineClick = () => {},
    breakLabel = undefined,
    onRemoveBreak = undefined,
  }: {
    model: LrcModel;
    currentTime: number;
    selectedWord?: { lineIndex: number; wordIndex: number } | null;
    selectedLineIndex?: number | null;
    onWordClick?: (ref: { lineIndex: number; wordIndex: number }) => void;
    onLineClick?: (lineIndex: number) => void;
    /** When set, an instrumental-break line (see isInstrumentalLine) renders
     * as this label text instead of its literal ♪ word - purely a display
     * substitution, the underlying model/renderLrc output is untouched.
     * Left undefined (the default) for the Mixer's karaoke pane, which must
     * keep rendering the ♪ word exactly as before. */
    breakLabel?: string;
    /** When provided alongside breakLabel, renders a small × control next to
     * the break label that calls this with the line's index. Omitted (the
     * default) hides the control - e.g. the Mixer never passes it. */
    onRemoveBreak?: (lineIndex: number) => void;
  } = $props();

  const active = $derived(findActiveWord(model, currentTime));
  const activeLineIndex = $derived(active?.lineIndex ?? findActiveLine(model, currentTime));
  // A bare-timestamp break line (see isInstrumentalLine) is non-lyric (no
  // words), so it's only included here when breakLabel is set - i.e. only
  // when this pane actually renders a "[break]" label for it (LyricEditor).
  // Without breakLabel (the Mixer), such a line is skipped entirely, same
  // as any other non-lyric line - no empty <p> gap is introduced. The
  // legacy single-♪-word form is unaffected either way: it's isLyric=true
  // (a real word, "♪"), so it was already included via the isLyric check.
  const lyricLines = $derived(
    model.lines
      .map((line, lineIndex) => ({ line, lineIndex }))
      .filter((entry) => entry.line.isLyric || (breakLabel !== undefined && isInstrumentalLine(model, entry.lineIndex))),
  );

  let lineEls: Record<number, HTMLParagraphElement> = $state({});
  let lastScrolledLine: number | null = null;

  // Auto-scroll the active line into view - but only when the active
  // word's *line* changes, not on every tick while it's still the same
  // line (that would fight any scrolling the user is doing themselves).
  // Shared by both LyricEditor (during play-along) and Mixer's karaoke pane.
  $effect(() => {
    const current = activeLineIndex;
    if (current !== null && current !== lastScrolledLine) {
      lastScrolledLine = current;
      lineEls[current]?.scrollIntoView?.({ block: "nearest" });
    }
  });
</script>

<div class="karaoke-display">
  {#each lyricLines as { line, lineIndex } (lineIndex)}
    <p
      class="karaoke-line"
      class:karaoke-line-active={activeLineIndex === lineIndex}
      class:karaoke-line-selected={selectedLineIndex === lineIndex}
      bind:this={lineEls[lineIndex]}
    >
      {#if breakLabel !== undefined && isInstrumentalLine(model, lineIndex)}
        <button type="button" class="karaoke-break-label" onclick={() => onLineClick(lineIndex)}>{breakLabel}</button>
        {#if onRemoveBreak}
          <button
            type="button"
            class="karaoke-break-remove"
            aria-label="Remove break"
            onclick={() => onRemoveBreak?.(lineIndex)}
          >×</button>
        {/if}
      {:else}
        {#each line.words as word, wordIndex (wordIndex)}
          <button
            type="button"
            class="karaoke-word"
            class:karaoke-word-active={active?.lineIndex === lineIndex && active?.wordIndex === wordIndex}
            class:karaoke-word-selected={selectedWord?.lineIndex === lineIndex && selectedWord?.wordIndex === wordIndex}
            onclick={() => onWordClick({ lineIndex, wordIndex })}
          >{word.text}</button>
        {/each}
      {/if}
    </p>
  {/each}
</div>
