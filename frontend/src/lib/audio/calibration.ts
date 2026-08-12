import { clampTapOffsetSeconds } from "../tapOffsetStore";

export const DEFAULT_BEEP_COUNT = 8;
export const DEFAULT_BEEP_INTERVAL_SECONDS = 1.5;

export function defaultNow(): number {
  return performance.now() / 1000;
}

function defaultPlayBeep(): void {
  const Ctor = (globalThis as unknown as { AudioContext?: new () => AudioContext }).AudioContext;
  if (!Ctor) return; // no real AudioContext available (e.g. jsdom without an injected playBeep) - silently no-op
  const ctx = new Ctor();
  const oscillator = ctx.createOscillator();
  oscillator.type = "sine";
  oscillator.frequency.value = 880;
  oscillator.connect(ctx.destination);
  oscillator.start();
  oscillator.stop(ctx.currentTime + 0.12);
  oscillator.onended = () => void ctx.close();
}

export interface BeepSchedulerOptions {
  beepCount?: number;
  intervalSeconds?: number;
  /** How long AFTER the last beep to wait before firing onComplete - gives
   * the user time to tap along with that final beep before the calibration
   * result is computed. Defaults to `intervalSeconds` (one full beep's
   * worth of reaction room). */
  graceSeconds?: number;
  now?: () => number;
  setTimer?: (callback: () => void, delayMs: number) => number;
  clearTimer?: (handle: number) => void;
  playBeep?: () => void;
}

export interface BeepScheduler {
  start(): void;
  cancel(): void;
}

/** Schedules `beepCount` beeps at a fixed `intervalSeconds` apart, starting
 * from `now()` at the moment `start()` is called. `onBeep(index, time)`
 * fires (via the injectable `playBeep`) at each beep; `onComplete()` fires
 * `graceSeconds` after the last one. Every timing primitive (`now`,
 * `setTimer`/`clearTimer`, `playBeep`) is injectable - the same pattern as
 * `createEngine`'s `audioContextFactory`/`scheduleFrame` - so jsdom tests
 * fake the entire clock and assert on scheduling without ever waiting on a
 * real timer or touching a real AudioContext. */
export function createBeepScheduler(
  onBeep: (beepIndex: number, beepTime: number) => void,
  onComplete: () => void,
  options: BeepSchedulerOptions = {},
): BeepScheduler {
  const beepCount = options.beepCount ?? DEFAULT_BEEP_COUNT;
  const intervalSeconds = options.intervalSeconds ?? DEFAULT_BEEP_INTERVAL_SECONDS;
  const graceSeconds = options.graceSeconds ?? intervalSeconds;
  const now = options.now ?? defaultNow;
  const setTimer = options.setTimer ?? ((cb, delayMs) => setTimeout(cb, delayMs) as unknown as number);
  const clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle));
  const playBeep = options.playBeep ?? defaultPlayBeep;

  let handles: number[] = [];

  function start(): void {
    cancel();
    const startTime = now();
    for (let index = 0; index < beepCount; index++) {
      const beepTime = startTime + index * intervalSeconds;
      handles.push(
        setTimer(() => {
          playBeep();
          onBeep(index, beepTime);
        }, (beepTime - startTime) * 1000),
      );
    }
    const completeTime = startTime + (beepCount - 1) * intervalSeconds + graceSeconds;
    handles.push(setTimer(onComplete, (completeTime - startTime) * 1000));
  }

  function cancel(): void {
    for (const handle of handles) clearTimer(handle);
    handles = [];
  }

  return { start, cancel };
}

/** Pairs each tap to its NEAREST beep time (not by strict index), computes
 * each pair's `tap - beep` reaction delay, and returns the clamped median.
 * Discards any pair where |diff| > half the beep interval (outlier/missed
 * beep); requires at least 3 surviving pairs. A single outlier tap (missed
 * beep, early double-tap) or mid-run missed beeps (which would corrupt
 * index-based pairing) are handled robustly: only plausible pairs survive
 * filtering, and fewer than 3 valid pairs returns null (failed calibration).
 * Returns null when there are no pairs to compute from (the user tapped zero
 * times) or when fewer than 3 pairs pass the distance filter. */
export function computeTapOffset(beepTimes: number[], tapTimes: number[]): number | null {
  if (beepTimes.length === 0 || tapTimes.length === 0) return null;

  // Compute the beep interval from the first two beeps (assumes even spacing).
  const beepInterval = beepTimes.length > 1 ? beepTimes[1] - beepTimes[0] : 1;
  const maxDelta = beepInterval / 2;

  // For each tap, find the nearest beep and compute the delta.
  const diffs: number[] = [];
  for (const tapTime of tapTimes) {
    let nearestBeep = beepTimes[0];
    let minDistance = Math.abs(tapTime - beepTimes[0]);
    for (const beepTime of beepTimes) {
      const distance = Math.abs(tapTime - beepTime);
      if (distance < minDistance) {
        minDistance = distance;
        nearestBeep = beepTime;
      }
    }
    // Only keep the pair if the delta is within the plausible range.
    const delta = tapTime - nearestBeep;
    if (Math.abs(delta) <= maxDelta) {
      diffs.push(delta);
    }
  }

  // Require at least 3 valid pairs for a reliable offset.
  if (diffs.length < 3) return null;

  diffs.sort((a, b) => a - b);
  const mid = Math.floor(diffs.length / 2);
  const median = diffs.length % 2 === 0 ? (diffs[mid - 1] + diffs[mid]) / 2 : diffs[mid];
  return clampTapOffsetSeconds(median);
}
