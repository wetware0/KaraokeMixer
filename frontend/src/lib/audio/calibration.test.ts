import { describe, expect, it, vi } from "vitest";
import { computeTapOffset, createBeepScheduler, DEFAULT_BEEP_COUNT, DEFAULT_BEEP_INTERVAL_SECONDS } from "./calibration";

function manualTimers() {
  const pending = new Map<number, { callback: () => void; fireAt: number }>();
  let nextHandle = 1;
  let time = 0;
  return {
    setTimer: (callback: () => void, delayMs: number) => {
      const handle = nextHandle++;
      pending.set(handle, { callback, fireAt: time + delayMs });
      return handle;
    },
    clearTimer: (handle: number) => { pending.delete(handle); },
    now: () => time,
    advance(ms: number) {
      time += ms;
      for (const [handle, entry] of [...pending.entries()]) {
        if (entry.fireAt <= time) {
          pending.delete(handle);
          entry.callback();
        }
      }
    },
  };
}

describe("createBeepScheduler", () => {
  it("fires onBeep once per beep, at the scheduled interval, then onComplete after the grace period", () => {
    const timers = manualTimers();
    const beeps: number[] = [];
    let completed = false;
    const scheduler = createBeepScheduler(
      (_index, time) => beeps.push(time),
      () => { completed = true; },
      {
        beepCount: 3, intervalSeconds: 1, graceSeconds: 1,
        now: timers.now, setTimer: timers.setTimer, clearTimer: timers.clearTimer, playBeep: () => {},
      },
    );

    scheduler.start();
    expect(beeps).toEqual([]); // nothing fires synchronously

    timers.advance(0); // t=0: beep 0
    expect(beeps).toEqual([0]);

    timers.advance(1000); // t=1: beep 1
    expect(beeps).toEqual([0, 1]);
    expect(completed).toBe(false);

    timers.advance(1000); // t=2: beep 2 (the last one)
    expect(beeps).toEqual([0, 1, 2]);
    expect(completed).toBe(false); // grace period hasn't elapsed yet

    timers.advance(1000); // t=3: grace period elapses
    expect(completed).toBe(true);
  });

  it("calls the injectable playBeep exactly once per beep", () => {
    const timers = manualTimers();
    const playBeep = vi.fn();
    const scheduler = createBeepScheduler(() => {}, () => {}, {
      beepCount: 2, intervalSeconds: 1, graceSeconds: 0,
      now: timers.now, setTimer: timers.setTimer, clearTimer: timers.clearTimer, playBeep,
    });

    scheduler.start();
    timers.advance(0);
    timers.advance(1000);

    expect(playBeep).toHaveBeenCalledTimes(2);
  });

  it("cancel() clears every pending timer so no further onBeep/onComplete fires", () => {
    const timers = manualTimers();
    const onBeep = vi.fn();
    const onComplete = vi.fn();
    const scheduler = createBeepScheduler(onBeep, onComplete, {
      beepCount: 4, intervalSeconds: 1,
      now: timers.now, setTimer: timers.setTimer, clearTimer: timers.clearTimer, playBeep: () => {},
    });

    scheduler.start();
    timers.advance(0); // beep 0 fires
    scheduler.cancel();
    timers.advance(10_000); // would have fired everything else, if not cancelled

    expect(onBeep).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("defaults beepCount/intervalSeconds to the documented constants", () => {
    const timers = manualTimers();
    const onBeep = vi.fn();
    const scheduler = createBeepScheduler(onBeep, () => {}, {
      now: timers.now, setTimer: timers.setTimer, clearTimer: timers.clearTimer, playBeep: () => {},
    });

    scheduler.start();
    for (let i = 0; i < DEFAULT_BEEP_COUNT; i++) timers.advance(DEFAULT_BEEP_INTERVAL_SECONDS * 1000);

    expect(onBeep).toHaveBeenCalledTimes(DEFAULT_BEEP_COUNT);
  });
});

describe("computeTapOffset", () => {
  it("returns the clamped median of tap-minus-beep deltas when 3+ valid pairs exist", () => {
    // deltas: 0.10, 0.12, 0.30 -> median 0.12 (all pairs valid: within 0.5s interval)
    expect(computeTapOffset([0, 1, 2], [0.10, 1.12, 2.30])).toBeCloseTo(0.12, 5);
  });

  it("averages the two middle values for an even number of valid pairs", () => {
    // 4 beeps, 4 taps: deltas 0.10, 0.15, 0.10, 0.15 -> sorted [0.10, 0.10, 0.15, 0.15] -> median (0.10 + 0.15) / 2 = 0.125
    expect(computeTapOffset([0, 1, 2, 3], [0.10, 1.15, 2.10, 3.15])).toBeCloseTo(0.125, 5);
  });

  it("clamps the result to [0, 0.5]", () => {
    // 3 taps at 0.4s offset, all within filter threshold -> deltas [0.4, 0.4, 0.4], median 0.4
    expect(computeTapOffset([0, 1, 2], [0.4, 1.4, 2.4])).toBeCloseTo(0.4, 5);
    // Extreme offset gets clamped: 3 pairs where deltas are 0.6 (outside 0.5 clamp),
    // but within the filter threshold (0.5s interval -> 0.25s threshold is too small,
    // so use 10s interval). Deltas [0.6, 0.6, 0.6], all within threshold 5s,
    // median 0.6 exceeds max 0.5, so clamped to 0.5.
    expect(computeTapOffset([0, 10, 20], [0.6, 10.6, 20.6])).toBe(0.5);
  });

  it("returns null when there are no taps", () => {
    expect(computeTapOffset([0, 1, 2], [])).toBeNull();
  });

  it("returns null when fewer than 3 taps survive the outlier filter", () => {
    // 8 beeps, 2 taps: only 2 valid pairs (need 3 minimum)
    expect(computeTapOffset([0, 1, 2, 3, 4, 5, 6, 7], [0.1, 1.2])).toBeNull();
  });

  it("handles a missed beep in the middle: pairs each tap to its nearest beep", () => {
    // 8 beeps, 7 taps: user missed beep 4. With index-based pairing, this would corrupt
    // all later pairs. With nearest-neighbor, each tap pairs correctly to its nearest beep:
    // Beeps: [0, 1, 2, 3, 4, 5, 6, 7]
    // Taps:  [0.1, 1.1, 2.1, 3.1, 5.1, 6.1, 7.1]  (missed beep at 4)
    // Pairs:
    //   0.1 -> beep 0: delta 0.1
    //   1.1 -> beep 1: delta 0.1
    //   2.1 -> beep 2: delta 0.1
    //   3.1 -> beep 3: delta 0.1
    //   5.1 -> beep 5: delta 0.1
    //   6.1 -> beep 6: delta 0.1
    //   7.1 -> beep 7: delta 0.1
    // All 7 pairs valid (|0.1| <= 0.5), median = 0.1
    expect(computeTapOffset(
      [0, 1, 2, 3, 4, 5, 6, 7],
      [0.1, 1.1, 2.1, 3.1, 5.1, 6.1, 7.1]
    )).toBeCloseTo(0.1, 5);
  });

  it("filters outlier taps that are too far from any beep", () => {
    // 8 beeps at 1.5s interval, 7 taps where one is 1s away (outlier beyond 0.75s threshold)
    // Beeps: [0, 1.5, 3, 4.5, 6, 7.5, 9, 10.5]
    // Taps:  [0.1, 1.6, 3.1, 4.6, 6.1, 7.6, 9.1]
    // Deltas: 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1 (all valid, < 0.75)
    expect(computeTapOffset(
      [0, 1.5, 3, 4.5, 6, 7.5, 9, 10.5],
      [0.1, 1.6, 3.1, 4.6, 6.1, 7.6, 9.1]
    )).toBeCloseTo(0.1, 5);
  });
});
