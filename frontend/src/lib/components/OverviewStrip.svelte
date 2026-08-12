<script lang="ts">
  import { onMount } from "svelte";
  import { drawWaveform, extractPeaks, normalizePeaks, resolveCanvasColor, type WaveformBufferWithRate } from "../audio/waveform";
  import { centerWindow, overviewXToTime, type ViewWindow } from "../audio/markerHitTest";

  const BUCKET_COUNT = 600;
  const HEIGHT = 48;
  // Only used before the container has ever been measured (see WaveformInspector's identical note).
  const FALLBACK_WIDTH = 800;

  let {
    buffer, duration, viewStart, viewEnd, onWindowChange = () => {},
  }: {
    buffer: WaveformBufferWithRate | null; duration: number; viewStart: number; viewEnd: number;
    onWindowChange?: (window: ViewWindow) => void;
  } = $props();

  let canvas: HTMLCanvasElement | undefined = $state();
  let container: HTMLDivElement | undefined = $state();
  let dragging = false;
  // Bumped by the ResizeObserver below whenever the container's own size
  // changes with no other prop change - read inside the redraw $effect
  // purely to make it re-run then (a container resize alone isn't
  // otherwise part of Svelte's reactive graph). The measurement itself is
  // still taken fresh from getBoundingClientRect() inside redraw().
  let resizeGeneration = $state(0);
  let resizeObserver: ResizeObserver | undefined;

  // Whole-song peaks, re-extracted only when the buffer itself changes (a
  // fixed BUCKET_COUNT summary of the entire track) - unlike the zoomable
  // main inspector, the overview never re-derives peaks for a narrower
  // window; only its lens rectangle moves.
  const peaks = $derived(buffer ? normalizePeaks(extractPeaks(buffer, BUCKET_COUNT)) : []);

  function measuredWidth(): number {
    const rect = container?.getBoundingClientRect();
    return rect && rect.width > 0 ? rect.width : FALLBACK_WIDTH;
  }

  function redraw(): void {
    if (!canvas) return;
    const width = measuredWidth();
    if (canvas.width !== width) canvas.width = width;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const color = resolveCanvasColor(canvas, "--text-dim", "#5c6373");
    drawWaveform(ctx, peaks, width, HEIGHT, color);
  }

  $effect(() => {
    peaks;
    resizeGeneration;
    redraw();
  });

  onMount(() => {
    redraw();
    // Observe the container itself (not just `window`) so a container-width
    // change with no window resize still triggers a redraw. ResizeObserver
    // isn't available in jsdom by default; tests stub a minimal fake on
    // globalThis before mounting.
    if (container && typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(() => {
        resizeGeneration += 1;
      });
      resizeObserver.observe(container);
    }
    return () => resizeObserver?.disconnect();
  });

  // The lens rectangle is positioned as a PERCENTAGE of the track duration
  // (not a pixel offset against some assumed canvas width) so it scales
  // correctly with the container's actual CSS width automatically, with no
  // need to re-measure anything on render.
  function lensLeftPercent(): number {
    return duration > 0 ? (viewStart / duration) * 100 : 0;
  }

  function lensWidthPercent(): number {
    return duration > 0 ? Math.max(0, ((viewEnd - viewStart) / duration) * 100) : 0;
  }

  function localX(clientX: number): number {
    if (!container) return 0;
    const rect = container.getBoundingClientRect();
    return clientX - rect.left;
  }

  function panTo(clientX: number): void {
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const span = viewEnd - viewStart;
    const time = overviewXToTime(localX(clientX), rect.width || FALLBACK_WIDTH, duration);
    onWindowChange(centerWindow(time, span, duration));
  }

  function onBackgroundClick(event: MouseEvent): void {
    // A plain click on the lens itself (as opposed to a drag) is handled by
    // its own pointerdown/pointermove above; without this guard the click
    // event still bubbles up to this background handler and would
    // needlessly re-center the window a second time.
    if (dragging || (event.target as HTMLElement).closest(".overview-strip-lens")) return;
    panTo(event.clientX);
  }

  function onLensPointerDown(event: PointerEvent): void {
    dragging = true;
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function onContainerPointerMove(event: PointerEvent): void {
    if (!dragging) return;
    panTo(event.clientX);
  }

  function onContainerPointerUp(): void {
    dragging = false;
  }
</script>

<div
  class="overview-strip"
  role="presentation"
  bind:this={container}
  onclick={onBackgroundClick}
  onpointermove={onContainerPointerMove}
  onpointerup={onContainerPointerUp}
>
  <canvas bind:this={canvas} width={FALLBACK_WIDTH} height={HEIGHT}></canvas>
  <div
    class="overview-strip-lens"
    role="slider"
    tabindex="0"
    aria-label="Visible window"
    aria-valuemin="0"
    aria-valuemax={duration}
    aria-valuenow={viewStart}
    style={`left: ${lensLeftPercent()}%; width: ${lensWidthPercent()}%`}
    onpointerdown={onLensPointerDown}
  ></div>
</div>
