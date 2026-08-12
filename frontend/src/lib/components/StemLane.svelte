<script lang="ts">
  import { onMount } from "svelte";
  import { drawWaveform, resolveCanvasColor, type PeakBucket } from "../audio/waveform";

  let {
    id, label, peaks, gain, muted, solo,
    onGainChange = () => {}, onMuteToggle = () => {}, onSoloToggle = () => {}, onSeek = () => {},
  }: {
    id: string; label: string; peaks: PeakBucket[]; gain: number; muted: boolean; solo: boolean;
    onGainChange?: (value: number) => void;
    onMuteToggle?: () => void;
    onSoloToggle?: () => void;
    onSeek?: (fraction: number) => void;
  } = $props();

  let canvas: HTMLCanvasElement | undefined = $state();

  function redraw(): void {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const color = muted
      ? resolveCanvasColor(canvas, "--text-dim", "#5c6373")
      : resolveCanvasColor(canvas, "--accent", "#7c8cff");
    drawWaveform(ctx, peaks, canvas.width, canvas.height, color);
  }

  $effect(() => {
    peaks;
    muted;
    redraw();
  });

  onMount(redraw);

  function onCanvasClick(event: MouseEvent): void {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    onSeek((event.clientX - rect.left) / rect.width);
  }
</script>

<div class="stem-lane" data-lane-id={id}>
  <span class="stem-lane-label">{label}</span>
  <canvas bind:this={canvas} class="stem-lane-waveform" width="600" height="48" onclick={onCanvasClick}></canvas>
  <input
    type="range" min="0" max="1" step="0.01" value={gain}
    class="stem-lane-gain"
    oninput={(e) => onGainChange(Number((e.target as HTMLInputElement).value))}
    aria-label={`${label} volume`}
  />
  <button
    type="button"
    class="stem-lane-mute"
    class:active={muted}
    onclick={onMuteToggle}
    aria-label={muted ? `Unmute ${label}` : `Mute ${label}`}
    aria-pressed={muted}
    title={muted ? `Unmute ${label} — restore this track to the mix` : `Mute ${label} — silence this track`}
  >M</button>
  <button
    type="button"
    class="stem-lane-solo"
    class:active={solo}
    onclick={onSoloToggle}
    aria-label={solo ? `Unsolo ${label}` : `Solo ${label}`}
    aria-pressed={solo}
    title={solo ? `Unsolo ${label} — return to the full mix` : `Solo ${label} — hear this track by itself`}
  >S</button>
</div>
