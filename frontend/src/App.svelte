<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { fetchSystem } from "./lib/api";
  import AppHeader from "./lib/components/AppHeader.svelte";
  import JobTray from "./lib/components/JobTray.svelte";
  import Library from "./lib/components/Library.svelte";
  import LyricEditor from "./lib/components/LyricEditor.svelte";
  import Mixer from "./lib/components/Mixer.svelte";
  import ProcessingHistory from "./lib/components/ProcessingHistory.svelte";
  import { protectWindowClose } from "./lib/closeProtection";
  import { jobsStore } from "./lib/jobsStore.svelte";
  import { tracksStore } from "./lib/tracksStore.svelte";
  import type { Track } from "./lib/types";

  let device = $state<"cuda" | "cpu" | null>(null);
  let whisperxAvailable = $state<boolean | null>(null);
  let view = $state<"library" | "mixer" | "editor" | "history">("library");
  let selectedTrackSnapshot = $state<Track | null>(null);
  let trackPlaying = $state(false);
  // Keep an open Mixer/Lyric Editor attached to the refreshed library record.
  // The snapshot is retained as a fallback when the current search filter does
  // not include the selected track.
  let selectedTrack = $derived(
    selectedTrackSnapshot === null
      ? null
      : tracksStore.tracks.find((track) => track.id === selectedTrackSnapshot!.id) ?? selectedTrackSnapshot
  );

  onMount(() => {
    // jobsStore.start() must not wait on the device probe: if fetchSystem()
    // rejects (e.g. backend briefly down during startup), the socket/polling
    // loop should still start so the job tray recovers on its own. Decoupling
    // these also avoids an unhandled promise rejection from the previous
    // single awaited chain.
    jobsStore.start();
    tracksStore.resumeRescan().catch(() => {});
    fetchSystem()
      .then((system) => {
        device = system.device;
        whisperxAvailable = system.workers.whisperx;
      })
      .catch(() => {});
    window.addEventListener("beforeunload", handleBeforeUnload);
  });

  onDestroy(() => {
    jobsStore.stop();
    tracksStore.stopRescanMonitoring();
    window.removeEventListener("beforeunload", handleBeforeUnload);
  });

  function handleBeforeUnload(event: BeforeUnloadEvent): void {
    protectWindowClose(event, {
      jobs: jobsStore.jobs,
      scanStatus: tracksStore.scanStatus,
      trackPlaying,
    });
  }

  function openMixer(track: Track): void {
    selectedTrackSnapshot = track;
    view = "mixer";
  }

  function openEditor(track: Track): void {
    selectedTrackSnapshot = track;
    view = "editor";
  }

  function backToLibrary(): void {
    view = "library";
    selectedTrackSnapshot = null;
  }

  function openHistory(): void {
    selectedTrackSnapshot = null;
    view = "history";
  }
</script>

<div class="app-shell">
  <AppHeader
    {device}
    showHistoryAction={view === "library" || view === "history"}
    historyActive={view === "history"}
    onOpenHistory={openHistory}
  />
  <main class="app-body">
  <!-- Keep the large Library mounted while a focused tool is open. Recreating
       thousands of rows on every Back navigation was both slow and discarded
       the creator's search, scroll position, folder expansion and selection. -->
  <div class="app-library-view" hidden={view !== "library"}>
    <Library
      {device}
      {whisperxAvailable}
      active={view === "library"}
      onOpenMixer={openMixer}
      onOpenEditor={openEditor}
      onPlaybackChange={(playing) => (trackPlaying = playing)}
    />
  </div>
  {#if view === "mixer" && selectedTrack}
    <Mixer track={selectedTrack} onBack={backToLibrary} onPlaybackChange={(playing) => (trackPlaying = playing)} />
  {:else if view === "editor" && selectedTrack}
    <LyricEditor
      track={selectedTrack}
      {whisperxAvailable}
      onBack={backToLibrary}
      onPlaybackChange={(playing) => (trackPlaying = playing)}
    />
  {:else if view === "history"}
    <ProcessingHistory onBack={backToLibrary} />
  {/if}
  </main>
  <JobTray showFailed={view !== "history"} />
</div>
