<script lang="ts">
  import { onMount } from "svelte";
  import {
    drawLoopRegion, drawPlayhead, drawSelectionRegion, drawWaveform, extractPeaksRange, normalizePeaks, resolveCanvasColor,
    type WaveformBufferWithRate,
  } from "../audio/waveform";
  import type { TimingSelectionSpan } from "../audio/lineBands";
  import { panWindow, timeToX, xToTime, zoomWindow, type MarkerRef, type ViewWindow } from "../audio/markerHitTest";

  const BUCKET_COUNT = 400;
  const PAN_FRACTION = 0.2;
  const ZOOM_FACTOR = 1.25;
  // Used only before the container has ever been measured (e.g. the very
  // first synchronous render, or a test that doesn't stub
  // getBoundingClientRect) - every real draw/interaction re-measures the
  // container's actual rendered width instead of assuming this value.
  const FALLBACK_WIDTH = 800;

  let {
    buffer, viewStart, viewEnd, markers, duration = 0, selectedMarker = null, selection = null, playheadTime = null, loop = null,
    onMarkerDrag = () => {}, onMarkerDragEnd = () => {}, onSeek = () => {}, onSeekAndPlay = () => {},
    onWindowChange = () => {}, onMarkerSelect = () => {}, onShiftClick = () => {},
  }: {
    buffer: WaveformBufferWithRate | null; viewStart: number; viewEnd: number; markers: MarkerRef[];
    /** Track duration, used to clamp wheel-driven pan/zoom; defaults to 0
     * (no clamping benefit, but harmless) for callers that don't have it yet. */
    duration?: number;
    selectedMarker?: { lineIndex: number; wordIndex: number; kind?: "word" | "line" } | null;
    /** Selected lyric line, word, or break section in seconds. */
    selection?: TimingSelectionSpan | null;
    /** Current playhead time during playback; null (or outside the visible
     * window) means no playhead line is drawn. */
    playheadTime?: number | null;
    /** Active loop region (seconds), or null when no loop is set - drawn as
     * a translucent overlay across whatever part of it is in view. */
    loop?: { start: number; end: number } | null;
    onMarkerDrag?: (ref: MarkerRef, newTime: number) => void;
    onMarkerDragEnd?: (ref: MarkerRef) => void;
    onSeek?: (time: number) => void;
    /** Double-click: seek here AND start playback (distinct from the
     * single-click seek-only `onSeek`). onSeekAndPlay may be async (matching
     * togglePlayback's await engine.play()); this component doesn't need to
     * await it itself. */
    onSeekAndPlay?: (time: number) => void | Promise<void>;
    onWindowChange?: (window: ViewWindow) => void;
    /** Fires when a marker is clicked or activated via Enter - lets a
     * caller select the corresponding word (e.g. LyricEditor's selectWord)
     * without needing to drag it. */
    onMarkerSelect?: (ref: MarkerRef) => void;
    /** Shift+click is reserved for a caller's direct timing gesture. It is
     * deliberately separate from ordinary seek so one click cannot do both. */
    onShiftClick?: (time: number) => void;
  } = $props();

  const HEIGHT = 260;

  let container: HTMLDivElement | undefined = $state();
  let canvas: HTMLCanvasElement | undefined = $state();
  let draggingRef: MarkerRef | null = null;
  // The container's last-measured rendered width in CSS pixels - drives both
  // the canvas bitmap's resolution and every marker's `left` position, so
  // the visual layout always matches the coordinate space used for click/
  // drag math (measured fresh, at event time, from the same element).
  let renderWidth = $state(FALLBACK_WIDTH);
  // Bumped by the ResizeObserver below whenever the container's own size
  // changes (a fluid-layout resize, a panel being dragged wider/narrower,
  // etc.) with no other prop change - read inside the redraw $effect purely
  // to make it re-run then, since a container resize alone isn't otherwise
  // part of Svelte's reactive graph. The measurement itself is still taken
  // fresh from getBoundingClientRect() inside redraw()/measuredWidth().
  let resizeGeneration = $state(0);
  let resizeObserver: ResizeObserver | undefined;

  // Re-extracted whenever the buffer or the visible [viewStart, viewEnd)
  // window changes, so zooming/panning the inspector always shows peaks for
  // the currently-visible window rather than a static whole-song extraction.
  // normalizePeaks rescales this window so its loudest peak reaches full
  // amplitude - otherwise a quiet vocal passage viewed in isolation would
  // still be drawn at whatever fraction of full-scale it was originally
  // recorded at, and stay visually flat.
  const peaks = $derived(buffer ? normalizePeaks(extractPeaksRange(buffer, BUCKET_COUNT, viewStart, viewEnd)) : []);

  function measuredWidth(): number {
    const rect = container?.getBoundingClientRect();
    return rect && rect.width > 0 ? rect.width : FALLBACK_WIDTH;
  }

  function redraw(): void {
    if (!canvas) return;
    const width = measuredWidth();
    renderWidth = width;
    // Keep the canvas bitmap's pixel resolution matched to its actual
    // rendered CSS width (which is 100% of a fluid container, not a fixed
    // 800px) - otherwise the bitmap is stretched/squashed by the browser
    // and drawing coordinates (in bitmap-pixel space) no longer line up
    // with click coordinates (in CSS-pixel space).
    if (canvas.width !== width) canvas.width = width;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const accentColor = resolveCanvasColor(canvas, "--accent", "#7c8cff");
    drawWaveform(ctx, peaks, width, HEIGHT, accentColor);
    if (selection && selection.end > viewStart && selection.start < viewEnd) {
      const selectionColor = resolveCanvasColor(
        canvas,
        selection.kind === "word" ? "--waveform-selection-word" : "--waveform-selection",
        selection.kind === "word" ? "#64748b55" : "#64748b38",
      );
      const xStart = Math.max(0, timeToX(Math.max(selection.start, viewStart), viewStart, viewEnd, width));
      const xEnd = Math.min(width, timeToX(Math.min(selection.end, viewEnd), viewStart, viewEnd, width));
      drawSelectionRegion(ctx, xStart, xEnd, HEIGHT, selectionColor);
    }
    if (loop) {
      const loopColor = resolveCanvasColor(canvas, "--accent-dim", "#7c8cff33");
      const xStart = Math.max(0, timeToX(Math.max(loop.start, viewStart), viewStart, viewEnd, width));
      const xEnd = Math.min(width, timeToX(Math.min(loop.end, viewEnd), viewStart, viewEnd, width));
      if (loop.end > viewStart && loop.start < viewEnd) drawLoopRegion(ctx, xStart, xEnd, HEIGHT, loopColor);
    }
    if (playheadTime !== null && playheadTime !== undefined && playheadTime >= viewStart && playheadTime < viewEnd) {
      drawPlayhead(ctx, timeToX(playheadTime, viewStart, viewEnd, width), HEIGHT, accentColor);
    }
  }

  $effect(() => {
    peaks;
    selection;
    playheadTime;
    loop;
    resizeGeneration;
    redraw();
  });

  onMount(() => {
    redraw();
    // Observe the container itself (not just `window`) so a container-width
    // change with no window resize - e.g. a fluid layout reflow, a sibling
    // panel being resized, or a flex/grid track changing - still triggers a
    // redraw. ResizeObserver isn't available in jsdom by default; tests stub
    // a minimal fake on globalThis before mounting.
    if (container && typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(() => {
        resizeGeneration += 1;
      });
      resizeObserver.observe(container);
    }
    return () => resizeObserver?.disconnect();
  });

  function markerX(marker: MarkerRef): number {
    return timeToX(marker.time, viewStart, viewEnd, renderWidth);
  }

  function onMarkerClick(marker: MarkerRef): void {
    onMarkerSelect(marker);
  }

  function onMarkerKeydown(marker: MarkerRef, event: KeyboardEvent): void {
    if (event.key === "Enter") {
      event.preventDefault();
      onMarkerSelect(marker);
    }
  }

  function onMarkerPointerDown(marker: MarkerRef, event: PointerEvent): void {
    draggingRef = marker;
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function onContainerPointerMove(event: PointerEvent): void {
    if (!draggingRef || !container) return;
    const rect = container.getBoundingClientRect();
    const time = xToTime(event.clientX - rect.left, viewStart, viewEnd, rect.width || FALLBACK_WIDTH);
    onMarkerDrag(draggingRef, time);
  }

  function onContainerPointerUp(): void {
    if (!draggingRef) return;
    onMarkerDragEnd(draggingRef);
    draggingRef = null;
  }

  function onCanvasClick(event: MouseEvent): void {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const time = xToTime(event.clientX - rect.left, viewStart, viewEnd, rect.width || FALLBACK_WIDTH);
    if (event.shiftKey) onShiftClick(time);
    else onSeek(time);
  }

  function onCanvasDblClick(event: MouseEvent): void {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    void onSeekAndPlay(xToTime(event.clientX - rect.left, viewStart, viewEnd, rect.width || FALLBACK_WIDTH));
  }

  // Plain wheel pans the window by a fraction of its own span per notch;
  // Ctrl/Cmd+wheel zooms instead, centered on the cursor's time so the
  // point under the pointer stays roughly fixed on screen while zooming.
  function onWheel(event: WheelEvent): void {
    event.preventDefault();
    const notches = Math.sign(event.deltaY) || 0;
    if (notches === 0) return;
    if (event.ctrlKey || event.metaKey) {
      const rect = canvas?.getBoundingClientRect();
      const width = rect?.width || FALLBACK_WIDTH;
      const cursorTime = rect ? xToTime(event.clientX - rect.left, viewStart, viewEnd, width) : (viewStart + viewEnd) / 2;
      const factor = notches > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
      onWindowChange(zoomWindow(viewStart, viewEnd, factor, cursorTime, duration));
    } else {
      onWindowChange(panWindow(viewStart, viewEnd, notches * PAN_FRACTION, duration));
    }
  }
</script>

<div
  class="waveform-inspector"
  role="presentation"
  bind:this={container}
  onpointermove={onContainerPointerMove}
  onpointerup={onContainerPointerUp}
  onwheel={onWheel}
>
  <canvas bind:this={canvas} width={FALLBACK_WIDTH} height={HEIGHT} onclick={onCanvasClick} ondblclick={onCanvasDblClick}></canvas>
  {#each markers as marker ((marker.kind ?? "word") + "-" + marker.lineIndex + "-" + marker.wordIndex)}
    <button
      type="button"
      class="waveform-marker"
      class:waveform-marker-line={marker.kind === "line"}
      class:waveform-marker-selected={selectedMarker?.lineIndex === marker.lineIndex && selectedMarker?.wordIndex === marker.wordIndex && (selectedMarker?.kind ?? "word") === (marker.kind ?? "word")}
      style={`left: ${markerX(marker)}px`}
      onpointerdown={(e) => onMarkerPointerDown(marker, e)}
      onclick={() => onMarkerClick(marker)}
      onkeydown={(e) => onMarkerKeydown(marker, e)}
      aria-label={`${marker.kind === "line" ? "Line start" : "Word timing"} at ${marker.time.toFixed(2)}s`}
    ></button>
  {/each}
</div>
