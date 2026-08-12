<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    computeTapOffset, createBeepScheduler, defaultNow, DEFAULT_BEEP_COUNT,
    type BeepScheduler, type BeepSchedulerOptions,
  } from "../audio/calibration";

  let {
    onApply, onCancel,
    createScheduler = createBeepScheduler,
    schedulerOptions = {},
  }: {
    onApply: (offsetSeconds: number) => void;
    onCancel: () => void;
    createScheduler?: (
      onBeep: (beepIndex: number, beepTime: number) => void,
      onComplete: () => void,
      options?: BeepSchedulerOptions,
    ) => BeepScheduler;
    schedulerOptions?: BeepSchedulerOptions;
  } = $props();

  const now = $derived(schedulerOptions.now ?? defaultNow);
  const beepCount = $derived(schedulerOptions.beepCount ?? DEFAULT_BEEP_COUNT);

  let phase = $state<"idle" | "running" | "done">("idle");
  let beepTimes = $state<number[]>([]);
  let tapTimes = $state<number[]>([]);
  let resultOffset = $state<number | null>(null);
  let scheduler: BeepScheduler | null = null;

  function start(): void {
    phase = "running";
    beepTimes = [];
    tapTimes = [];
    resultOffset = null;
    scheduler = createScheduler(
      (_index, time) => {
        beepTimes = [...beepTimes, time];
      },
      () => {
        resultOffset = computeTapOffset(beepTimes, tapTimes);
        phase = "done";
      },
      schedulerOptions,
    );
    scheduler.start();
  }

  function tap(): void {
    if (phase !== "running") return;
    tapTimes = [...tapTimes, now()];
  }

  function redo(): void {
    scheduler?.cancel();
    start();
  }

  function cancel(): void {
    scheduler?.cancel();
    onCancel();
  }

  function apply(): void {
    if (resultOffset === null) return;
    onApply(resultOffset);
  }

  // Attached to `window` (not this panel's own root element) to match the
  // same global-keydown convention Mixer/LyricEditor already use for their
  // own Space shortcut - this is what lets a bare Space keypress register
  // as a tap while this panel is open, with no need to first click into the
  // panel to move DOM focus there.
  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.code === "Space" && phase === "running") {
      event.preventDefault();
      tap();
    }
  }

  onMount(() => window.addEventListener("keydown", onWindowKeydown));
  onDestroy(() => {
    window.removeEventListener("keydown", onWindowKeydown);
    scheduler?.cancel();
  });
</script>

<div class="tap-calibration-panel" role="region" aria-label="Tap offset calibration">
  {#if phase === "idle"}
    <p>Calibrate tap timing: {beepCount} beeps will play at a fixed interval. Tap along (Space or the button below).</p>
    <button type="button" onclick={start}>Start calibration</button>
  {:else}
    {#if phase === "running"}
      <p>Beep {beepTimes.length} of {beepCount} — tap along now</p>
      <button type="button" onclick={tap}>Tap</button>
    {:else}
      <p>
        {#if resultOffset !== null}
          Computed offset: {resultOffset.toFixed(3)}s
        {:else}
          No taps recorded — try again.
        {/if}
      </p>
    {/if}
    <button type="button" onclick={redo}>Redo</button>
    {#if phase === "done"}
      <button type="button" disabled={resultOffset === null} onclick={apply}>Apply</button>
    {/if}
  {/if}
  <button type="button" onclick={cancel}>Cancel</button>
</div>
