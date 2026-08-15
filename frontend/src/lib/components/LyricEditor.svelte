<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { confirmLyricTimingQuality, fetchJob, fetchLrc, fetchTrackParts, partAudioUrl, saveLrc, submitJob } from "../api";
  import { createEngine, type AudioEngine, type CreateEngineOptions, type EngineAudioBuffer, type LoopRegion } from "../audio/engine";
  import { advanceFollowWindowWithLoop, centerWindow, type MarkerRef, type ViewWindow } from "../audio/markerHitTest";
  import { chooseInstrumentalInsertIndex, computeLineBands, computeTimingSelectionSpan } from "../audio/lineBands";
  import {
    findActiveLine, LrcEditController, NUDGE_STEP_SECONDS,
    insertInstrumentalLine, nudgeWordTime, parseLrc, removeInstrumentalLine, renderLrc, setLineStart, setWordTime, tapStamp,
    type LrcModel,
  } from "../lrcModel";
  import type { LrcReadResponse, LrcState, LyricTimingReport, Track } from "../types";
  import { jobsStore } from "../jobsStore.svelte";
  import { tracksStore } from "../tracksStore.svelte";
  import { loadTapOffsetSeconds, saveTapOffsetSeconds } from "../tapOffsetStore";
  import KaraokeDisplay from "./KaraokeDisplay.svelte";
  import LineBandStrip from "./LineBandStrip.svelte";
  import OverviewStrip from "./OverviewStrip.svelte";
  import TapCalibrationPanel from "./TapCalibrationPanel.svelte";
  import WaveformInspector from "./WaveformInspector.svelte";

  let {
    track, whisperxAvailable = null, onBack = () => {}, onPlaybackChange = () => {}, engineFactory = createEngine,
  }: {
    track: Track;
    whisperxAvailable?: boolean | null;
    onBack?: () => void;
    onPlaybackChange?: (playing: boolean) => void;
    engineFactory?: (options?: CreateEngineOptions) => AudioEngine;
  } = $props();

  const SOURCE_PRIORITY = ["lead_vocals", "vocals", "original"];
  const WORD_SELECT_ZOOM_SPAN_SECONDS = 3;
  const COARSE_NUDGE_MULTIPLIER = 10;

  let engine: AudioEngine | null = null;
  let controller: LrcEditController | null = null;
  let sourceBuffer = $state<EngineAudioBuffer | null>(null);
  let duration = $state(0);
  let currentTime = $state(0);
  let selected = $state<{ lineIndex: number; wordIndex: number } | null>(null);
  let selectedLineIndex = $state<number | null>(null);
  let tapMode = $state(false);
  let tapOffsetSeconds = $state(loadTapOffsetSeconds());
  let showCalibration = $state(false);
  let playing = $state(false);
  // The last-loaded (or last-saved) rendered LRC text. `dirty` below is a
  // pure comparison against this baseline rather than an "an edit happened"
  // boolean flag - that's what lets undo-ing back to the original content,
  // or saving, make the dirty indicator disappear again.
  let baseline = $state("");
  let loading = $state(true);
  let loadedLanes = $state(0);
  let totalLanes = $state(0);
  let error = $state<string | null>(null);
  let errorMessage = $state<string | null>(null);
  let viewStart = $state(0);
  let viewEnd = $state(10);
  // Active loop region, or null when no loop is set. Persists across
  // play/pause (it's independent state, untouched by togglePlayback); reset
  // to null naturally on track change because a new track means a fresh
  // LyricEditor instance (App.svelte routes back through the library view
  // in between, so this component's state never survives a track switch).
  let loop = $state<LoopRegion | null>(null);
  let modelVersion = $state(0); // bumped on every apply/undo/redo so reads below recompute
  let unsubscribeTick: (() => void) | undefined;
  let unsubscribeRetime: (() => void) | undefined;
  let unsubscribeBackgroundJobs: (() => void) | undefined;
  let retimeJobId = $state<number | null>(null);
  let retimeMessage = $state<string | null>(null);
  let backgroundRefreshPending = $state(false);
  let backgroundRefreshMessage = $state<string | null>(null);
  let timingQualityOverride = $state<"review" | "high_quality" | null | undefined>(undefined);
  const timingQuality = $derived(timingQualityOverride ?? track.lyric_timing_provenance?.quality ?? null);
  let qualityMessage = $state<string | null>(null);
  let timingReport = $state<LyricTimingReport | null>(null);
  const wordConfidence = $derived.by(() => Object.fromEntries(
    (timingReport?.words ?? []).map((word) => [`${word.line_index}:${word.word_index}`, word.confidence]),
  ));
  const reviewWords = $derived((timingReport?.words ?? []).filter((word) => word.status === "review"));
  let lastFollowedLine = -1;
  let destroyed = false;

  function updatePlaying(value: boolean): void {
    if (playing === value) return;
    playing = value;
    onPlaybackChange(value);
  }

  function currentModel(): LrcModel | null {
    modelVersion;
    return controller?.model ?? null;
  }

  function canUndo(): boolean {
    modelVersion;
    return controller?.canUndo() ?? false;
  }

  function canRedo(): boolean {
    modelVersion;
    return controller?.canRedo() ?? false;
  }

  const dirty = $derived.by(() => {
    const model = currentModel();
    return model !== null && renderLrc(model) !== baseline;
  });

  const timingSelection = $derived.by(() => {
    const model = currentModel();
    if (!model || selectedLineIndex === null) return null;
    return computeTimingSelectionSpan(
      model,
      { lineIndex: selectedLineIndex, wordIndex: selected?.wordIndex ?? null },
      duration,
    );
  });

  async function loadLrcFromDisk(preloaded?: LrcReadResponse): Promise<{ content: string; state: LrcState | null }> {
    const lrc = preloaded ?? await fetchLrc(track.id);
    if (destroyed) return { content: "", state: lrc.state };
    controller = new LrcEditController(parseLrc(lrc.content));
    timingReport = lrc.timing_report ?? null;
    baseline = renderLrc(controller.model);
    selected = null;
    selectedLineIndex = null;
    lastFollowedLine = -1;
    modelVersion += 1;
    return { content: baseline, state: lrc.state };
  }

  async function setup(): Promise<void> {
    try {
      const parts = await fetchTrackParts(track.id);
      if (destroyed) return;
      const existing = new Set(parts.filter((p) => p.exists).map((p) => p.part));
      const sourcePart = SOURCE_PRIORITY.find((part) => existing.has(part)) ?? "original";
      engine = engineFactory();
      totalLanes = 1;
      await engine.load([{ id: "source", url: partAudioUrl(track.id, sourcePart) }], (loaded, total) => {
        loadedLanes = loaded;
        totalLanes = total;
      });
      if (destroyed) {
        engine?.dispose();
        engine = null;
        return;
      }
      sourceBuffer = engine.getBuffer("source");
      duration = engine.getDuration();
      viewEnd = Math.min(10, duration || 10);
      unsubscribeTick = engine.onTick((time) => {
        currentTime = time;
        updatePlaying(engine!.isPlaying());
        // Follow mode: only slides the visible window forward while actually
        // playing - never while paused/scrubbing. When a loop is active, the
        // engine itself seeks back to loop.start once playback reaches
        // loop.end (see engine.ts's tick()), which shows up here as `time`
        // suddenly jumping BACKWARD below viewStart - re-center the view on
        // the loop instead of leaving the playhead invisible off-screen to
        // the left. Forward advancement (including the loop-aware clamping
        // that keeps a wide loop's far edge from scrolling past view - see
        // advanceFollowWindowWithLoop's own doc comment) is otherwise fully
        // delegated to the pure helper.
        if (playing) {
          const model = currentModel();
          const hasWordTiming = model?.lines.some((line) => line.words.some((word) => word.time !== null)) ?? false;
          if (model && !hasWordTiming) {
            const activeLine = findActiveLine(model, time);
            if (activeLine !== -1 && activeLine !== lastFollowedLine) {
              lastFollowedLine = activeLine;
              const lineStart = model.lines[activeLine]?.lineStart;
              if (lineStart !== null && lineStart !== undefined) {
                const centered = centerWindow(lineStart, viewEnd - viewStart, duration);
                viewStart = centered.viewStart;
                viewEnd = centered.viewEnd;
              }
            }
          }
          if (loop && time < viewStart) {
            const centered = centerWindow(loop.start, viewEnd - viewStart, duration);
            viewStart = centered.viewStart;
            viewEnd = centered.viewEnd;
          } else {
            const advanced = advanceFollowWindowWithLoop(viewStart, viewEnd, time, duration, loop);
            viewStart = advanced.viewStart;
            viewEnd = advanced.viewEnd;
          }
        }
      });

      await loadLrcFromDisk();
      if (destroyed) {
        unsubscribeTick?.();
        engine?.dispose();
        engine = null;
        return;
      }
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    unsubscribeBackgroundJobs = jobsStore.onJobCompleted((jobIds) => void refreshAfterBackgroundJobs(jobIds));
    void setup();
    window.addEventListener("keydown", onKeydown);
  });

  onDestroy(() => {
    destroyed = true;
    updatePlaying(false);
    unsubscribeTick?.();
    unsubscribeRetime?.();
    unsubscribeBackgroundJobs?.();
    engine?.dispose();
    engine = null;
    window.removeEventListener("keydown", onKeydown);
  });

  function markers(): MarkerRef[] {
    const model = currentModel();
    if (!model) return [];
    const refs: MarkerRef[] = [];
    model.lines.forEach((line, lineIndex) => {
      if (line.lineStart !== null) refs.push({ lineIndex, wordIndex: -1, time: line.lineStart, kind: "line" });
      line.words.forEach((word, wordIndex) => {
        if (word.time !== null) refs.push({ lineIndex, wordIndex, time: word.time, kind: "word" });
      });
    });
    return refs;
  }

  function applyEdit(next: LrcModel): void {
    controller?.apply(next);
    modelVersion += 1;
  }

  function undo(): void {
    controller?.undo();
    modelVersion += 1;
  }

  function redo(): void {
    controller?.redo();
    modelVersion += 1;
  }

  function selectWord(ref: { lineIndex: number; wordIndex: number }): void {
    selected = ref;
    selectedLineIndex = ref.lineIndex;
    const model = currentModel();
    const word = model?.lines[ref.lineIndex]?.words[ref.wordIndex];
    if (word && word.time !== null) {
      viewStart = Math.max(0, word.time - WORD_SELECT_ZOOM_SPAN_SECONDS);
      viewEnd = Math.min(duration || word.time + WORD_SELECT_ZOOM_SPAN_SECONDS, word.time + WORD_SELECT_ZOOM_SPAN_SECONDS);
    }
  }

  function selectMarker(ref: MarkerRef): void {
    if (ref.kind === "line") {
      selected = null;
      selectedLineIndex = ref.lineIndex;
      viewStart = Math.max(0, ref.time - WORD_SELECT_ZOOM_SPAN_SECONDS);
      viewEnd = Math.min(duration || ref.time + WORD_SELECT_ZOOM_SPAN_SECONDS, ref.time + WORD_SELECT_ZOOM_SPAN_SECONDS);
      return;
    }
    selectWord(ref);
  }

  function setLoop(next: LoopRegion | null): void {
    loop = next;
    engine?.setLoopRegion(next);
  }

  function clearLoop(): void {
    setLoop(null);
  }

  // A line-band click is a true line/break selection. Keeping it distinct
  // from a word selection makes the waveform shading and timing controls
  // describe the section the user actually clicked.
  function onSelectLine(lineIndex: number): void {
    const model = currentModel();
    if (!model?.lines[lineIndex]) return;
    selected = null;
    selectedLineIndex = lineIndex;
    const span = computeTimingSelectionSpan(model, { lineIndex, wordIndex: null }, duration);
    if (span) {
      viewStart = Math.max(0, span.start - WORD_SELECT_ZOOM_SPAN_SECONDS);
      viewEnd = Math.min(duration || span.end + WORD_SELECT_ZOOM_SPAN_SECONDS, span.end + WORD_SELECT_ZOOM_SPAN_SECONDS);
    }
  }

  function onWindowChange(window: ViewWindow): void {
    viewStart = window.viewStart;
    viewEnd = window.viewEnd;
  }

  // Matches togglePlayback's contract: engine.play() may await ctx.resume()
  // (autoplay policy), so this awaits it too rather than firing-and-forgetting,
  // and updates `playing` from the engine's actual state once settled.
  async function onSeekAndPlay(time: number): Promise<void> {
    if (!engine) return;
    engine.seek(time);
    await engine.play();
    updatePlaying(engine.isPlaying());
  }

  // Inserts a bare-timestamp break line (see insertInstrumentalLine) at the
  // playhead (while playing) or at the current view window's center (while
  // paused), auto-choosing which existing line to insert it after via
  // chooseInstrumentalInsertIndex (the last line whose band starts at or
  // before that time).
  function addInstrumentalBreak(): void {
    const model = currentModel();
    if (!model) return;
    const time = playing ? currentTime : (viewStart + viewEnd) / 2;
    const afterLineIndex = chooseInstrumentalInsertIndex(computeLineBands(model), time);
    applyEdit(insertInstrumentalLine(model, afterLineIndex, time));
  }

  function removeInstrumentalBreak(lineIndex: number): void {
    const model = currentModel();
    if (!model) return;
    applyEdit(removeInstrumentalLine(model, lineIndex));
    if (selectedLineIndex === lineIndex) {
      selected = null;
      selectedLineIndex = null;
    }
  }

  function onMarkerDrag(ref: MarkerRef, newTime: number): void {
    const model = currentModel();
    if (!model) return;
    applyEdit(ref.kind === "line"
      ? setLineStart(model, ref.lineIndex, newTime)
      : setWordTime(model, ref.lineIndex, ref.wordIndex, newTime));
  }

  function onShiftClick(time: number): void {
    const model = currentModel();
    if (!model || !selected) {
      errorMessage = "Select a lyric word, then Shift+click the waveform to set its time.";
      return;
    }
    applyEdit(setWordTime(model, selected.lineIndex, selected.wordIndex, time));
    errorMessage = null;
  }

  // The seal-amendment note (engine.play() is now async) applies here: this
  // handler is itself synchronous (matches WaveformInspector's
  // onMarkerDragEnd?: (ref: MarkerRef) => void prop), so the fire-and-forget
  // is made explicit with `void` rather than left as an unmarked floating
  // promise.
  function onMarkerDragEnd(ref: MarkerRef): void {
    const model = currentModel();
    const markerTime = ref.kind === "line"
      ? model?.lines[ref.lineIndex]?.lineStart
      : model?.lines[ref.lineIndex]?.words[ref.wordIndex]?.time;
    if (!engine || markerTime === null || markerTime === undefined) return;
    engine.seek(Math.max(0, markerTime - 0.5));
    void engine.play().then(() => updatePlaying(engine?.isPlaying() ?? false));
    setTimeout(() => {
      engine?.pause();
      updatePlaying(false);
    }, 1000);
  }

  // Play starts from the selected word's time minus 0.5s (a little runway
  // before the word so its lead-in is audible), or - when nothing is
  // selected - from the current view window's start.
  function playStartPosition(): number {
    const model = currentModel();
    const word = selected ? model?.lines[selected.lineIndex]?.words[selected.wordIndex] : null;
    if (word && word.time !== null) return Math.max(0, word.time - 0.5);
    return viewStart;
  }

  async function togglePlayback(): Promise<void> {
    if (!engine) return;
    if (engine.isPlaying()) {
      engine.pause();
      updatePlaying(false);
    } else {
      engine.seek(playStartPosition());
      await engine.play();
      updatePlaying(engine.isPlaying());
    }
  }

  function flattenWordRefs(model: LrcModel): { lineIndex: number; wordIndex: number }[] {
    const refs: { lineIndex: number; wordIndex: number }[] = [];
    model.lines.forEach((line, lineIndex) => line.words.forEach((_word, wordIndex) => refs.push({ lineIndex, wordIndex })));
    return refs;
  }

  function nudgeSelected(direction: 1 | -1, coarse: boolean): void {
    const model = currentModel();
    if (!model || !selected) return;
    const multiplier = coarse ? COARSE_NUDGE_MULTIPLIER : 1;
    applyEdit(nudgeWordTime(model, selected.lineIndex, selected.wordIndex, direction * NUDGE_STEP_SECONDS * multiplier));
  }

  function selectAdjacentWord(direction: 1 | -1): void {
    const model = currentModel();
    if (!model) return;
    const flat = flattenWordRefs(model);
    if (flat.length === 0) return;
    const currentIndex = selected
      ? flat.findIndex((r) => r.lineIndex === selected!.lineIndex && r.wordIndex === selected!.wordIndex)
      : -1;
    const nextIndex = currentIndex === -1 ? (direction === 1 ? 0 : flat.length - 1) : currentIndex + direction;
    const target = flat[Math.max(0, Math.min(flat.length - 1, nextIndex))];
    if (target) selectWord(target);
  }

  function selectNextReviewWord(): void {
    if (reviewWords.length === 0) return;
    const current = selected
      ? reviewWords.findIndex((word) => word.line_index === selected!.lineIndex && word.word_index === selected!.wordIndex)
      : -1;
    const next = reviewWords[(current + 1) % reviewWords.length];
    selectWord({ lineIndex: next.line_index, wordIndex: next.word_index });
  }

  function tapNext(): void {
    const model = currentModel();
    if (!model || !engine) return;
    const flat = flattenWordRefs(model);
    const startFrom = selected
      ? flat.findIndex((r) => r.lineIndex === selected!.lineIndex && r.wordIndex === selected!.wordIndex) + 1
      : 0;
    const target = flat[startFrom];
    if (!target) return;
    applyEdit(tapStamp(model, target.lineIndex, target.wordIndex, engine.getCurrentTime(), tapOffsetSeconds));
    selected = target;
  }

  function onKeydown(event: KeyboardEvent): void {
    if (showCalibration) return; // TapCalibrationPanel owns Space (tap) while open
    const target = event.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable) return;

    if (event.key === "t" || event.key === "T") {
      tapMode = !tapMode;
      return;
    }
    if (event.code === "Space") {
      event.preventDefault();
      if (tapMode) tapNext();
      else void togglePlayback();
      return;
    }
    if (event.ctrlKey && (event.key === "z" || event.key === "Z")) {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
      return;
    }
    if (event.ctrlKey && (event.key === "y" || event.key === "Y")) {
      event.preventDefault();
      redo();
      return;
    }
    if (event.key === "Escape") {
      if (loop) {
        event.preventDefault();
        clearLoop();
      }
      return;
    }
    if (event.key === "[" || event.key === "]") {
      event.preventDefault();
      selectAdjacentWord(event.key === "]" ? 1 : -1);
      return;
    }
    if (selected && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
      event.preventDefault();
      nudgeSelected(event.key === "ArrowRight" ? 1 : -1, event.shiftKey);
    }
  }

  async function save(): Promise<void> {
    const model = currentModel();
    if (!model) return;
    errorMessage = null;
    try {
      const rendered = renderLrc(model);
      const result = await saveLrc(track.id, rendered);
      if (result.track) tracksStore.replaceTrack(result.track);
      baseline = rendered;
      timingReport = null;
      timingQualityOverride = null;
      qualityMessage = "Timing quality needs review again because the lyrics changed.";
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    }
  }

  async function saveAs(): Promise<void> {
    const model = currentModel();
    if (!model) return;
    const suffix = window.prompt("Save as suffix (writes {name}.{suffix}.lrc):", "alt");
    if (!suffix) return;
    errorMessage = null;
    try {
      await saveLrc(track.id, renderLrc(model), { suffix });
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    }
  }

  async function confirmHighQualityTiming(): Promise<void> {
    if (dirty || timingQuality === "high_quality") return;
    if (!window.confirm("Confirm High Quality only after listening through the track and checking the word highlighting. Continue?")) return;
    errorMessage = null;
    try {
      const updated = await confirmLyricTimingQuality(track.id);
      tracksStore.replaceTrack(updated);
      timingQualityOverride = updated.lyric_timing_provenance?.quality ?? null;
      qualityMessage = "High Quality timing recorded for this exact LRC file. Any later lyric or timing edit will require review again.";
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    }
  }

  async function finishRetime(jobId: number): Promise<void> {
    if (retimeJobId !== jobId || destroyed) return;
    unsubscribeRetime?.();
    unsubscribeRetime = undefined;
    try {
      const job = await fetchJob(jobId);
      if (job.status !== "completed") {
        const reason = job.items.find((item) => item.error_text)?.error_text;
        retimeMessage = reason ? `AI timing did not finish: ${reason}` : `AI timing ${job.status}. Open Jobs for details.`;
        return;
      }
      const before = baseline;
      const after = await loadLrcFromDisk();
      if (after.state !== "enhanced") {
        retimeMessage = `AI did not produce enhanced per-word timing (result: ${after.state ?? "no LRC"}). Open Jobs for details.`;
      } else {
        retimeMessage = after.content === before
          ? "Enhanced per-word timing finished without changing the file. Review the job details."
          : "Enhanced per-word timing loaded. Every lyric word was retimed; review it against the vocal.";
      }
    } catch (err) {
      retimeMessage = err instanceof Error ? err.message : String(err);
    } finally {
      retimeJobId = null;
    }
  }

  async function refreshAfterBackgroundJobs(jobIds: readonly number[]): Promise<void> {
    const otherJobIds = jobIds.filter((jobId) => jobId !== retimeJobId);
    if (destroyed || otherJobIds.length === 0) return;

    // Job summaries do not carry their track ids, so inspect the completed
    // details before touching an open editor. If a detail lookup fails, fall
    // back to a cheap LRC comparison: missing a legitimate refresh is worse
    // than one harmless read after an unrelated job.
    let affectsCurrentTrack = false;
    let lookupFailed = false;
    const details = await Promise.all(otherJobIds.map(async (jobId) => {
      try {
        return await fetchJob(jobId);
      } catch {
        lookupFailed = true;
        return null;
      }
    }));
    if (destroyed) return;
    affectsCurrentTrack = details.some((job) => job?.items.some((item) => item.track_id === track.id));
    if (!affectsCurrentTrack && !lookupFailed) return;

    try {
      const latest = await fetchLrc(track.id);
      if (destroyed) return;
      const contentChanged = latest.content !== baseline;
      const reportChanged = JSON.stringify(latest.timing_report ?? null) !== JSON.stringify(timingReport);
      if (!contentChanged && !reportChanged) return;
      if (!contentChanged) {
        timingReport = latest.timing_report ?? null;
        backgroundRefreshMessage = "Lyric timing confidence refreshed after background processing completed.";
        return;
      }
      if (dirty) {
        backgroundRefreshPending = true;
        backgroundRefreshMessage = "Background processing produced newer lyrics. Load them when ready; your unsaved edits will be preserved until you choose.";
        return;
      }
      await loadLrcFromDisk(latest);
      backgroundRefreshPending = false;
      backgroundRefreshMessage = "Lyrics refreshed after background processing completed.";
    } catch (err) {
      if (!destroyed) {
        backgroundRefreshMessage = `Background processing completed, but the lyric refresh failed: ${err instanceof Error ? err.message : String(err)}`;
      }
    }
  }

  async function loadPendingBackgroundLyrics(): Promise<void> {
    if (!backgroundRefreshPending) return;
    if (dirty && !window.confirm("Discard unsaved lyric changes and load the background result?")) return;
    try {
      await loadLrcFromDisk();
      backgroundRefreshPending = false;
      backgroundRefreshMessage = "Latest background lyrics loaded.";
    } catch (err) {
      backgroundRefreshMessage = `Could not load the latest lyrics: ${err instanceof Error ? err.message : String(err)}`;
    }
  }

  async function retimeWithAi(): Promise<void> {
    if (dirty || retimeJobId !== null) return;
    retimeMessage = null;
    errorMessage = null;
    try {
      const { job_id: jobId } = await submitJob({
        recipe: "align_only",
        track_ids: [track.id],
        options: { device: "auto" },
      });
      retimeJobId = jobId;
      unsubscribeRetime?.();
      unsubscribeRetime = jobsStore.onJobCompletedFor(jobId, () => void finishRetime(jobId));

      // A very small job can finish before the socket listener is installed.
      const job = await fetchJob(jobId);
      if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
        await finishRetime(jobId);
      } else {
        retimeMessage = "AI is rebuilding enhanced timing for every lyric word. Existing line, break, and word markers will be replaced.";
      }
    } catch (err) {
      unsubscribeRetime?.();
      unsubscribeRetime = undefined;
      retimeJobId = null;
      retimeMessage = err instanceof Error ? err.message : String(err);
    }
  }

  function openCalibration(): void {
    showCalibration = true;
  }

  function applyCalibration(offsetSeconds: number): void {
    tapOffsetSeconds = offsetSeconds;
    saveTapOffsetSeconds(offsetSeconds);
    showCalibration = false;
  }

  function cancelCalibration(): void {
    showCalibration = false;
  }

  function back(): void {
    if (dirty && !window.confirm("Discard unsaved lyric changes?")) return;
    onBack();
  }
</script>

<div class="lyric-editor">
  <button class="back-button" onclick={back}>← Back</button>
  <div class="workspace-screen-header">
    <div>
      <p class="dialog-eyebrow">LYRICS & TIMING</p>
      <h2>{track.title}{dirty ? " *" : ""}</h2>
      <p>Review each lyric against the vocal, correct timing, and mark instrumental breaks.</p>
    </div>
    {#if dirty}<span class="workspace-unsaved">Unsaved changes</span>{/if}
  </div>
  {#if loading}
    <p>{loadedLanes > 0 ? `Decoding ${loadedLanes} of ${totalLanes}…` : "Fetching audio…"}</p>
  {:else if error}
    <p class="lyric-editor-error">{error}</p>
  {:else if currentModel()}
    <OverviewStrip buffer={sourceBuffer} duration={duration} viewStart={viewStart} viewEnd={viewEnd} onWindowChange={onWindowChange} />
    <WaveformInspector
      buffer={sourceBuffer} viewStart={viewStart} viewEnd={viewEnd} duration={duration} markers={markers()}
      selectedMarker={selected
        ? { ...selected, kind: "word" }
        : selectedLineIndex !== null
          ? { lineIndex: selectedLineIndex, wordIndex: -1, kind: "line" }
          : null}
      playheadTime={playing ? currentTime : null}
      loop={loop}
      selection={timingSelection}
      onMarkerDrag={onMarkerDrag} onMarkerDragEnd={onMarkerDragEnd}
      onMarkerSelect={selectMarker}
      onShiftClick={onShiftClick}
      onSeek={(time) => engine?.seek(time)}
      onSeekAndPlay={onSeekAndPlay}
      onWindowChange={onWindowChange}
    />
    <LineBandStrip
      model={currentModel()!} viewStart={viewStart} viewEnd={viewEnd} duration={duration}
      {selectedLineIndex}
      loop={loop}
      onSelectLine={onSelectLine}
      onLoopChange={setLoop}
      onRemoveInstrumental={removeInstrumentalBreak}
    />
    <div class="lyric-editor-toolbar">
      <button onclick={() => void togglePlayback()}>{playing ? "Pause" : "Play"}</button>
      <button class:active={tapMode} onclick={() => (tapMode = !tapMode)}>Tap mode (T)</button>
      <button onclick={openCalibration}>Calibrate tap timing</button>
      <button onclick={undo} disabled={!canUndo()}>Undo</button>
      <button onclick={redo} disabled={!canRedo()}>Redo</button>
      <button onclick={addInstrumentalBreak}>Add break</button>
      <button
        onclick={() => void retimeWithAi()}
        disabled={dirty || retimeJobId !== null || whisperxAvailable === false}
        title={dirty
          ? "Save or undo manual changes before AI re-timing"
          : whisperxAvailable === false
            ? "Enhanced timing is unavailable until the WhisperX worker is installed"
            : "Replace all existing timing with enhanced per-word timing from the complete vocal audio"}
      >{retimeJobId !== null ? "Re-timing every word…" : "Re-time every word with AI"}</button>
      {#if loop}
        <button onclick={clearLoop}>Clear loop</button>
      {/if}
      <button onclick={save}>Save</button>
      <button onclick={saveAs}>Save As…</button>
      {#if reviewWords.length > 0}
        <button onclick={selectNextReviewWord}>Next review word ({reviewWords.length})</button>
      {/if}
      <button
        onclick={() => void confirmHighQualityTiming()}
        disabled={dirty || timingQuality === "high_quality" || track.lrc_state !== "enhanced"}
        title={dirty
          ? "Save your changes before confirming timing quality"
          : timingQuality === "high_quality"
            ? "This exact LRC has been listening-reviewed"
            : "Confirm only after listening through the word highlighting"}
      >{timingQuality === "high_quality" ? "High Quality timing ✓" : "Confirm High Quality timing"}</button>
      {#if errorMessage}
        <span class="lyric-editor-save-error">{errorMessage}</span>
      {/if}
    </div>
    <p class="lyric-editor-hint">Select a word, then Shift+click the waveform to set its time. Use [ and ] to move between words.</p>
    {#if whisperxAvailable === false}
      <p class="lyric-editor-status">Enhanced per-word timing is unavailable until the WhisperX worker is installed.</p>
    {/if}
    {#if retimeMessage}<p class="lyric-editor-status" aria-live="polite">{retimeMessage}</p>{/if}
    {#if qualityMessage}<p class="lyric-editor-status" aria-live="polite">{qualityMessage}</p>{/if}
    {#if timingReport}
      <p class="lyric-editor-status" aria-live="polite">
        Confidence {timingReport.summary.confidence_score ?? 0}/100 ·
        {timingReport.summary.verified_words ?? 0} verified ·
        {timingReport.summary.review_words ?? 0} words in {timingReport.summary.review_lines ?? 0} lines need review ·
        {timingReport.summary.corrected_words ?? 0} corrected
      </p>
    {/if}
    {#if backgroundRefreshMessage}
      <p class="lyric-editor-status" aria-live="polite">
        {backgroundRefreshMessage}
        {#if backgroundRefreshPending}
          <button class="lyric-editor-inline-action" onclick={() => void loadPendingBackgroundLyrics()}>Load latest lyrics</button>
        {/if}
      </p>
    {/if}
    {#if showCalibration}
      <TapCalibrationPanel onApply={applyCalibration} onCancel={cancelCalibration} />
    {/if}
    <div class="lyric-scroll-box">
      <KaraokeDisplay
        model={currentModel()!} currentTime={currentTime} onWordClick={selectWord}
        selectedWord={selected} {selectedLineIndex} onLineClick={onSelectLine}
        {wordConfidence}
        breakLabel="[break]" onRemoveBreak={removeInstrumentalBreak}
      />
    </div>
  {/if}
</div>
