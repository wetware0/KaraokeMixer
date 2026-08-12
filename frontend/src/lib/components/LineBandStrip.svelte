<script lang="ts">
  import { computeDragLoop, computeLineBands, type LineBand, type LoopSpan } from "../audio/lineBands";
  import type { LrcModel } from "../lrcModel";

  // Only used before the container has ever been measured (see
  // WaveformInspector's identical note) - drag/loop math re-measures the
  // container's actual rendered width at event time instead.
  const FALLBACK_WIDTH = 800;

  let {
    model, viewStart, viewEnd, duration, selectedLineIndex = null, loop = null,
    onSelectLine = () => {}, onLoopChange = () => {}, onRemoveInstrumental = () => {},
  }: {
    model: LrcModel; viewStart: number; viewEnd: number; duration: number;
    selectedLineIndex?: number | null;
    /** Active loop region (seconds), or null - drawn as a translucent
     * overlay across whatever part of it falls within the visible window,
     * same data WaveformInspector draws on the canvas above. */
    loop?: LoopSpan | null;
    onSelectLine?: (lineIndex: number) => void;
    onLoopChange?: (loop: LoopSpan | null) => void;
    onRemoveInstrumental?: (lineIndex: number) => void;
  } = $props();

  const bands = $derived(computeLineBands(model));

  // A band with no explicit end (line-timed only, no per-word times yet)
  // visually extends up to the next band's start, or to the track's end if
  // it's the last band - there's nothing else to bound it by.
  const visualBands = $derived(
    bands.map((band, index) => ({
      ...band,
      visualEnd: band.end ?? bands[index + 1]?.start ?? duration,
    })),
  );

  let container: HTMLDivElement | undefined = $state();
  let dragStartX: number | null = null;
  let dragActive = false;

  // Percentage-of-the-view-window positioning (rather than a pixel offset
  // against some assumed strip width) so bands scale correctly with the
  // container's actual fluid CSS width automatically, with no need to
  // measure anything at render time.
  function leftPercent(time: number): number {
    const span = viewEnd - viewStart;
    if (span <= 0) return 0;
    return ((time - viewStart) / span) * 100;
  }

  function widthPercent(start: number, end: number): number {
    const span = viewEnd - viewStart;
    if (span <= 0) return 0;
    return Math.max(0, ((end - start) / span) * 100);
  }

  const loopOverlayVisible = $derived(!!loop && loop.end > viewStart && loop.start < viewEnd);

  function localX(clientX: number): number {
    if (!container) return 0;
    const rect = container.getBoundingClientRect();
    return clientX - rect.left;
  }

  function containerWidth(): number {
    return container?.getBoundingClientRect().width || FALLBACK_WIDTH;
  }

  function onBandClick(band: LineBand): void {
    onSelectLine(band.lineIndex);
  }

  function onBandDblClick(band: LineBand & { visualEnd: number }): void {
    onLoopChange({ start: band.start, end: band.visualEnd });
  }

  function onBandRemoveClick(event: MouseEvent, band: LineBand): void {
    event.stopPropagation();
    onRemoveInstrumental(band.lineIndex);
  }

  // Drag-to-loop only starts when the pointer goes down on the strip's
  // background (not on a band - bands have their own click/dblclick
  // behavior above), matching the "drag anywhere on the strip's background"
  // design (bands, being on top, naturally absorb the pointerdown first).
  function onContainerPointerDown(event: PointerEvent): void {
    if ((event.target as HTMLElement).closest(".line-band")) return;
    dragStartX = localX(event.clientX);
    dragActive = true;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function onContainerPointerMove(event: PointerEvent): void {
    if (!dragActive || dragStartX === null) return;
    const loopSpan = computeDragLoop(dragStartX, localX(event.clientX), viewStart, viewEnd, containerWidth());
    if (loopSpan) onLoopChange(loopSpan);
  }

  function onContainerPointerUp(): void {
    dragActive = false;
    dragStartX = null;
  }
</script>

<div
  class="line-band-strip"
  role="presentation"
  bind:this={container}
  onpointerdown={onContainerPointerDown}
  onpointermove={onContainerPointerMove}
  onpointerup={onContainerPointerUp}
>
  {#if loopOverlayVisible && loop}
    <div
      class="line-band-strip-loop"
      style={`left: ${leftPercent(Math.max(loop.start, viewStart))}%; width: ${widthPercent(Math.max(loop.start, viewStart), Math.min(loop.end, viewEnd))}%`}
    ></div>
  {/if}
  {#each visualBands as band, index (band.lineIndex)}
    <button
      type="button"
      class="line-band"
      class:line-band-odd={index % 2 === 1}
      class:line-band-instrumental={band.instrumental}
      class:line-band-selected={selectedLineIndex === band.lineIndex}
      style={`left: ${leftPercent(band.start)}%; width: ${widthPercent(band.start, band.visualEnd)}%`}
      onclick={() => onBandClick(band)}
      ondblclick={() => onBandDblClick(band)}
      onkeydown={(e) => { if (e.key === "Enter") { e.preventDefault(); onBandClick(band); } }}
      title={band.instrumental ? "Instrumental section" : band.text}
      aria-label={band.instrumental ? "Instrumental section" : `Line: ${band.text}`}
    >
      <span class="line-band-label">{band.instrumental ? "[break]" : band.text}</span>
      {#if band.instrumental}
        <span
          role="button"
          tabindex="0"
          class="line-band-remove"
          onclick={(e) => onBandRemoveClick(e, band)}
          onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") onBandRemoveClick(e as unknown as MouseEvent, band); }}
          aria-label={`Remove instrumental section at line ${band.lineIndex}`}
        >×</span>
      {/if}
    </button>
  {/each}
</div>
