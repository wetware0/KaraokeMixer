<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { fetchLrc, fetchTrackParts, partAudioUrl } from "../api";
  import { createEngine, type AudioEngine, type CreateEngineOptions } from "../audio/engine";
  import { extractPeaks, type PeakBucket } from "../audio/waveform";
  import { defaultOfflineContextFactory, downloadMp3, downloadWav, encodeMp3, encodeWav, renderMix, type OfflineContextFactory } from "../audio/exportMix";
  import { xToTime } from "../audio/markerHitTest";
  import { parseLrc, type LrcModel } from "../lrcModel";
  import type { Track } from "../types";
  import KaraokeDisplay from "./KaraokeDisplay.svelte";
  import StemLane from "./StemLane.svelte";

  let {
    track, onBack = () => {}, onPlaybackChange = () => {},
    engineFactory = createEngine,
    offlineContextFactory = defaultOfflineContextFactory,
  }: {
    track: Track;
    onBack?: () => void;
    onPlaybackChange?: (playing: boolean) => void;
    engineFactory?: (options?: CreateEngineOptions) => AudioEngine;
    offlineContextFactory?: OfflineContextFactory;
  } = $props();

  interface LaneState {
    id: string; label: string; peaks: PeakBucket[]; gain: number; muted: boolean; solo: boolean;
  }

  const PART_LABELS: Record<string, string> = {
    original: "Original", instrumental: "Instrumental", vocals: "Vocals",
    lead_vocals: "Lead vocals", backing_vocals: "Backing vocals", drums: "Drums",
    bass: "Bass", guitar: "Guitar", piano: "Piano", other: "Other",
  };

  const MP3_EXPORT_KBPS = 192;

  let engine: AudioEngine | null = null;
  let lanes = $state<LaneState[]>([]);
  let currentTime = $state(0);
  let duration = $state(0);
  let playing = $state(false);
  let lrcModel = $state<LrcModel | null>(null);
  let loading = $state(true);
  let loadedLanes = $state(0);
  let totalLanes = $state(0);
  let error = $state<string | null>(null);
  let dragStart: number | null = null;
  let timelineEl: HTMLDivElement | undefined = $state();
  let unsubscribeTick: (() => void) | undefined;
  let destroyed = false;
  let exportFormat = $state<"wav" | "mp3">("wav");
  let exporting = $state(false);
  let exportError = $state<string | null>(null);

  function updatePlaying(value: boolean): void {
    if (playing === value) return;
    playing = value;
    onPlaybackChange(value);
  }

  async function setup(): Promise<void> {
    try {
      const parts = await fetchTrackParts(track.id);
      if (destroyed) return;
      const playable = parts.filter((part) => part.exists);
      engine = engineFactory();
      totalLanes = playable.length;
      await engine.load(
        playable.map((part) => ({ id: part.part, url: partAudioUrl(track.id, part.part) })),
        (loaded, total) => {
          loadedLanes = loaded;
          totalLanes = total;
        },
      );
      if (destroyed) {
        engine?.dispose();
        engine = null;
        return;
      }
      // A separated ("stem") lane duplicates content already present in the
      // original mixdown - if both play unmuted at gain 1, the shared
      // content (e.g. vocals) sounds twice as loud. Default the original
      // lane to muted whenever any separated part exists; when original is
      // the only lane (no separation happened), it stays unmuted so
      // playback isn't silent by default.
      const hasSeparatedParts = playable.some((part) => part.part !== "original");
      lanes = playable.map((part) => {
        const muted = part.part === "original" && hasSeparatedParts;
        if (muted) engine!.setMuted(part.part, true);
        return {
          id: part.part,
          label: PART_LABELS[part.part] ?? part.part,
          peaks: extractPeaks(engine!.getBuffer(part.part)!, 200),
          gain: 1, muted, solo: false,
        };
      });
      duration = engine.getDuration();
      unsubscribeTick = engine.onTick((time) => {
        currentTime = time;
        updatePlaying(engine!.isPlaying());
      });

      if (track.lrc_state) {
        const lrc = await fetchLrc(track.id);
        if (destroyed) {
          unsubscribeTick?.();
          engine?.dispose();
          engine = null;
          return;
        }
        if (lrc.exists) lrcModel = parseLrc(lrc.content);
      }
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void setup();
    window.addEventListener("keydown", onKeydown);
  });

  onDestroy(() => {
    destroyed = true;
    updatePlaying(false);
    unsubscribeTick?.();
    engine?.dispose();
    engine = null;
    window.removeEventListener("keydown", onKeydown);
  });

  function onKeydown(event: KeyboardEvent): void {
    const target = event.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable) return;
    if (event.code === "Space") {
      event.preventDefault();
      void togglePlay();
    }
  }

  async function togglePlay(): Promise<void> {
    if (!engine) return;
    if (engine.isPlaying()) {
      engine.pause();
    } else {
      await engine.play();
    }
    updatePlaying(engine.isPlaying());
  }

  function setGain(laneId: string, value: number): void {
    engine?.setGain(laneId, value);
    lanes = lanes.map((lane) => (lane.id === laneId ? { ...lane, gain: value } : lane));
  }

  function toggleMute(laneId: string): void {
    const lane = lanes.find((l) => l.id === laneId);
    if (!lane) return;
    const muted = !lane.muted;
    engine?.setMuted(laneId, muted);
    lanes = lanes.map((l) => (l.id === laneId ? { ...l, muted } : l));
  }

  function toggleSolo(laneId: string): void {
    const lane = lanes.find((l) => l.id === laneId);
    if (!lane) return;
    const solo = !lane.solo;
    engine?.setSolo(laneId, solo);
    lanes = lanes.map((l) => (l.id === laneId ? { ...l, solo } : l));
  }

  function seekFraction(fraction: number): void {
    if (duration > 0) engine?.seek(fraction * duration);
  }

  // Keyboard-accessible seeking: Arrow Left/Right nudge the playhead by 1%
  // of the track duration, matching a common media player convention for
  // fine-grained seeking via keyboard. This allows keyboard users to seek
  // through the mix without relying on clicking/dragging.
  function onLanesWrapKeydown(event: KeyboardEvent): void {
    const target = event.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (!engine) return;
      engine.seek(Math.max(0, engine.getCurrentTime() - duration * 0.01));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      if (!engine) return;
      engine.seek(Math.min(duration, engine.getCurrentTime() + duration * 0.01));
    }
  }

  const playheadLeftPercent = $derived(duration > 0 ? (currentTime / duration) * 100 : 0);

  const karaokeLaneId = $derived(
    lanes.some((l) => l.id === "lead_vocals") ? "lead_vocals" : lanes.some((l) => l.id === "vocals") ? "vocals" : null,
  );

  // A true preset: applying it always produces the same karaoke
  // configuration regardless of whatever mute/solo state the lanes were
  // scrambled into beforehand - it is not a toggle relative to prior state.
  // Vocals (lead_vocals if present, else vocals) go OFF; every other
  // separated lane is forced audible (their own gain sliders still apply);
  // the original lane stays muted whenever separated parts exist (it would
  // otherwise still carry the vocal content the preset is meant to remove).
  // Solo is cleared on every lane too: the engine's effective-gain rule is
  // "if anything is soloed, only soloed lanes are audible" - a residual solo
  // left over from before the preset was applied would silence every lane
  // the preset just unmuted, since none of them would be the soloed one.
  // The preset must fully own the resulting mix state, not just the mutes.
  function applyKaraokePreset(): void {
    if (!karaokeLaneId) return;
    const hasSeparatedParts = lanes.some((lane) => lane.id !== "original");
    lanes = lanes.map((lane) => {
      const muted = lane.id === karaokeLaneId || (lane.id === "original" && hasSeparatedParts);
      engine?.setMuted(lane.id, muted);
      engine?.setSolo(lane.id, false);
      return { ...lane, muted, solo: false };
    });
  }

  async function exportMix(): Promise<void> {
    if (!engine) return;
    exportError = null;
    try {
      exporting = true;
      const mixLanes = lanes.map((lane) => ({
        id: lane.id, buffer: engine!.getBuffer(lane.id)!, gain: lane.gain, muted: lane.muted, solo: lane.solo,
      }));
      const rendered = await renderMix(mixLanes, offlineContextFactory, exportFormat);
      if (exportFormat === "mp3") {
        downloadMp3(`${track.title}.mix.mp3`, encodeMp3(rendered, MP3_EXPORT_KBPS));
      } else {
        downloadWav(`${track.title}.mix.wav`, encodeWav(rendered));
      }
    } catch (err) {
      exportError = err instanceof Error ? err.message : String(err);
    } finally {
      exporting = false;
    }
  }

  function onTimelinePointerDown(event: PointerEvent): void {
    if (!timelineEl) return;
    const rect = timelineEl.getBoundingClientRect();
    dragStart = xToTime(event.clientX - rect.left, 0, duration, rect.width);
  }

  function onTimelinePointerUp(event: PointerEvent): void {
    if (dragStart === null || !timelineEl) return;
    const rect = timelineEl.getBoundingClientRect();
    const end = xToTime(event.clientX - rect.left, 0, duration, rect.width);
    const region = [dragStart, end].sort((a, b) => a - b);
    if (region[1] - region[0] < 0.1) {
      engine?.setLoopRegion(null);
      engine?.seek(region[0]);
    } else {
      engine?.setLoopRegion({ start: region[0], end: region[1] });
    }
    dragStart = null;
  }
</script>

<div class="mixer">
  <button class="back-button" onclick={onBack}>← Back</button>
  <div class="workspace-screen-header">
    <div>
      <p class="dialog-eyebrow">MIX & EXPORT</p>
      <h2>{track.title}</h2>
      <p>Balance the separated parts, preview the result, then export a performance-ready mix.</p>
    </div>
  </div>
  {#if loading}
    <p>{loadedLanes > 0 ? `Decoding ${loadedLanes} of ${totalLanes}…` : "Fetching audio…"}</p>
  {:else if error}
    <p class="mixer-error">{error}</p>
  {:else}
    <div class="mixer-lanes-wrap">
      <div class="mixer-lanes">
        {#each lanes as lane (lane.id)}
          <StemLane
            id={lane.id} label={lane.label} peaks={lane.peaks}
            gain={lane.gain} muted={lane.muted} solo={lane.solo}
            onGainChange={(value) => setGain(lane.id, value)}
            onMuteToggle={() => toggleMute(lane.id)}
            onSoloToggle={() => toggleSolo(lane.id)}
            onSeek={seekFraction}
          />
        {/each}
      </div>
      {#if playing && duration > 0}
        <div class="mixer-playhead" style={`left: ${playheadLeftPercent}%`}></div>
      {/if}
    </div>
    <div
      class="mixer-timeline-strip"
      role="slider"
      tabindex="0"
      aria-label="Playback position"
      aria-valuemin="0"
      aria-valuemax={duration}
      aria-valuenow={currentTime}
      bind:this={timelineEl}
      onpointerdown={onTimelinePointerDown}
      onpointerup={onTimelinePointerUp}
      onkeydown={onLanesWrapKeydown}
    ></div>
    <div class="mixer-transport">
      <button class="mixer-play-button" onclick={togglePlay}>{playing ? "Pause" : "Play"}</button>
      <span class="mixer-time">{currentTime.toFixed(2)}s / {duration.toFixed(2)}s</span>
      <button disabled={!karaokeLaneId} onclick={applyKaraokePreset} title="Mute the lead vocal and build a balanced backing track">Karaoke preset</button>
      <span class="mixer-export-group">
        <label for="mixer-export-format">Export as</label>
        <select id="mixer-export-format" bind:value={exportFormat} aria-label="Export format" class="mixer-export-format" disabled={exporting}>
          <option value="wav">WAV</option>
          <option value="mp3">MP3</option>
        </select>
        <button onclick={exportMix} disabled={exporting}>{exporting ? "Exporting…" : "Export mix…"}</button>
      </span>
      {#if exportError}
        <span class="mixer-export-error">{exportError}</span>
      {/if}
    </div>
    {#if lrcModel}
      <KaraokeDisplay model={lrcModel} currentTime={currentTime} />
    {/if}
  {/if}
</div>
