import { describe, expect, it } from "vitest";
import {
  advanceFollowWindow, advanceFollowWindowWithLoop, centerWindow, findNearestMarker, LOOP_FOLLOW_MARGIN_FRACTION,
  MAX_ZOOM_SPAN_SECONDS, MIN_ZOOM_SPAN_SECONDS, overviewXToTime, panWindow, timeToX, xToTime, zoomWindow,
} from "./markerHitTest";

describe("timeToX / xToTime", () => {
  it("are inverses of each other across a view window", () => {
    const x = timeToX(12.5, 10, 20, 800);
    expect(x).toBeCloseTo(200, 5);
    expect(xToTime(x, 10, 20, 800)).toBeCloseTo(12.5, 5);
  });

  it("returns the view start when widthPx is zero or negative", () => {
    expect(xToTime(50, 10, 20, 0)).toBe(10);
  });

  it("returns 0 when the view span is zero or negative", () => {
    expect(timeToX(5, 10, 10, 800)).toBe(0);
  });
});

describe("advanceFollowWindow", () => {
  it("leaves the window unchanged while the playhead is still inside it", () => {
    expect(advanceFollowWindow(10, 20, 15, 100)).toEqual({ viewStart: 10, viewEnd: 20 });
  });

  it("leaves the window unchanged while the playhead is exactly at viewStart", () => {
    expect(advanceFollowWindow(10, 20, 10, 100)).toEqual({ viewStart: 10, viewEnd: 20 });
  });

  it("advances the window forward by exactly one span once the playhead reaches viewEnd, preserving the span (nowhere near duration)", () => {
    expect(advanceFollowWindow(10, 20, 20, 100)).toEqual({ viewStart: 20, viewEnd: 30 });
  });

  it("advances the window when the playhead has moved past viewEnd (nowhere near duration)", () => {
    expect(advanceFollowWindow(10, 20, 25, 100)).toEqual({ viewStart: 20, viewEnd: 30 });
  });

  it("preserves a non-default span when advancing mid-track (unaffected by the duration clamp)", () => {
    expect(advanceFollowWindow(0, 6, 6, 100)).toEqual({ viewStart: 6, viewEnd: 12 });
  });

  it("clamps the final window to end exactly at duration instead of overshooting past the track end", () => {
    // 3s track, 2s span: a naive advance from [1,3] would go to [3,5],
    // overshooting the 3s duration by 2s of trailing empty space. Clamped:
    // newEnd = min(3+2, 3) = 3, so newStart is pulled back to
    // max(0, 3-2) = 1, giving a full-width window [1,3] ending exactly at
    // duration instead.
    expect(advanceFollowWindow(1, 3, 3, 3)).toEqual({ viewStart: 1, viewEnd: 3 });
  });

  it("does not clamp when the naive advance does not reach duration", () => {
    expect(advanceFollowWindow(0, 2, 2, 10)).toEqual({ viewStart: 2, viewEnd: 4 });
  });

  it("clamps to a window starting at 0 when duration is shorter than the span", () => {
    // span 5, duration 3: newEnd = min(5, 3) = 3, newStart pulled back to
    // max(0, 3-5) = 0, giving [0, 3].
    expect(advanceFollowWindow(0, 5, 5, 3)).toEqual({ viewStart: 0, viewEnd: 3 });
  });
});

describe("advanceFollowWindowWithLoop", () => {
  it("behaves exactly like advanceFollowWindow when no loop is active", () => {
    expect(advanceFollowWindowWithLoop(10, 20, 25, 100, null)).toEqual(advanceFollowWindow(10, 20, 25, 100));
    expect(advanceFollowWindowWithLoop(10, 20, 15, 100, null)).toEqual(advanceFollowWindow(10, 20, 15, 100));
  });

  it("leaves the window unchanged while the playhead is still inside it, loop or not", () => {
    expect(advanceFollowWindowWithLoop(0, 10, 5, 100, { start: 0, end: 15 })).toEqual({ viewStart: 0, viewEnd: 10 });
  });

  it("leaves the window unchanged once the loop (plus margin) already fits entirely within the view - nothing further to show", () => {
    // loop [2, 2.9], span 10, margin 0.5 -> maxEnd 3.4, comfortably <= viewEnd (10).
    expect(advanceFollowWindowWithLoop(0, 10, 12, 100, { start: 2, end: 2.9 })).toEqual({ viewStart: 0, viewEnd: 10 });
  });

  it("clamps a wide loop's forward advance so viewEnd never exceeds loop.end + margin, instead of a full-span overshoot into dead time", () => {
    // loop [0, 15) wider than the 10s window; margin = 10*0.05 = 0.5 -> maxEnd = 15.5.
    // A naive advanceFollowWindow(0,10,10,100) would jump to [10,20], well
    // past the loop's end - the playhead will never actually get there
    // before the engine wraps back to loop.start.
    const result = advanceFollowWindowWithLoop(0, 10, 10, 100, { start: 0, end: 15 });
    expect(result.viewEnd).toBeCloseTo(15.5, 5);
    expect(result.viewEnd - result.viewStart).toBeCloseTo(10, 5); // span preserved
    expect(result.viewStart).toBeCloseTo(5.5, 5);
  });

  it("does not advance further once already clamped at the loop's margin-extended end", () => {
    const first = advanceFollowWindowWithLoop(0, 10, 10, 100, { start: 0, end: 15 });
    // Playhead continues forward but is still within the clamped window -
    // no further change (same as advanceFollowWindow's own in-window guard).
    const second = advanceFollowWindowWithLoop(first.viewStart, first.viewEnd, 15.4, 100, { start: 0, end: 15 });
    expect(second).toEqual(first);
  });

  it("does not clamp when the naive single-span advance already stays within loop.end + margin", () => {
    // loop [5, 20): naive advance from [0,10] at playhead 10 goes to
    // [10,20] (span preserved) - 20 is already <= loop.end(20)+margin(0.5)
    // = 20.5, so the plain advanceFollowWindow result is returned as-is,
    // proving the clamp is only a ceiling, not an unconditional override.
    const result = advanceFollowWindowWithLoop(0, 10, 10, 1000, { start: 5, end: 20 });
    expect(result).toEqual({ viewStart: 10, viewEnd: 20 });
  });

  it("uses LOOP_FOLLOW_MARGIN_FRACTION (5%) of the current span as the margin", () => {
    // span 20 -> margin 1; naive advance from [0,20] at playhead 20 goes to
    // [20,40], which overshoots loop.end(25) so the clamp activates,
    // landing exactly at loop.end + margin = 26.
    const span = 20;
    const result = advanceFollowWindowWithLoop(0, span, span, 1000, { start: 0, end: 25 });
    expect(result.viewEnd).toBeCloseTo(25 + span * LOOP_FOLLOW_MARGIN_FRACTION, 5);
  });

  it("still clamps the overall window to duration even with a loop active", () => {
    // loop end + margin would exceed duration - duration wins.
    const result = advanceFollowWindowWithLoop(0, 10, 10, 12, { start: 0, end: 50 });
    expect(result.viewEnd).toBeLessThanOrEqual(12);
  });
});

describe("findNearestMarker", () => {
  const markers = [
    { lineIndex: 0, wordIndex: 0, time: 1.0 },
    { lineIndex: 0, wordIndex: 1, time: 1.5 },
    { lineIndex: 1, wordIndex: 0, time: 5.0 },
  ];

  it("returns the closest marker within the distance threshold", () => {
    expect(findNearestMarker(markers, 1.4, 0.5)).toEqual(markers[1]);
  });

  it("returns null when nothing is within the threshold", () => {
    expect(findNearestMarker(markers, 3.0, 0.5)).toBeNull();
  });

  it("returns null for an empty marker list", () => {
    expect(findNearestMarker([], 1.0, 1.0)).toBeNull();
  });
});

describe("overviewXToTime", () => {
  it("maps proportionally against the full track duration", () => {
    expect(overviewXToTime(300, 600, 200)).toBeCloseTo(100, 5);
  });

  it("clamps to [0, duration]", () => {
    expect(overviewXToTime(-50, 600, 200)).toBe(0);
    expect(overviewXToTime(9999, 600, 200)).toBe(200);
  });

  it("returns 0 when widthPx or duration is non-positive", () => {
    expect(overviewXToTime(100, 0, 200)).toBe(0);
    expect(overviewXToTime(100, 600, 0)).toBe(0);
  });
});

describe("centerWindow", () => {
  it("centers the span on the given time", () => {
    expect(centerWindow(50, 10, 200)).toEqual({ viewStart: 45, viewEnd: 55 });
  });

  it("clamps so the window never starts before 0", () => {
    expect(centerWindow(2, 10, 200)).toEqual({ viewStart: 0, viewEnd: 10 });
  });

  it("clamps so the window never runs past duration", () => {
    expect(centerWindow(198, 10, 200)).toEqual({ viewStart: 190, viewEnd: 200 });
  });

  it("returns the full track when span covers (or exceeds) the whole duration", () => {
    expect(centerWindow(50, 300, 200)).toEqual({ viewStart: 0, viewEnd: 200 });
  });
});

describe("panWindow", () => {
  it("shifts the window forward by a fraction of its own span", () => {
    expect(panWindow(10, 20, 0.2, 100)).toEqual({ viewStart: 12, viewEnd: 22 });
  });

  it("shifts the window backward for a negative fraction", () => {
    expect(panWindow(10, 20, -0.2, 100)).toEqual({ viewStart: 8, viewEnd: 18 });
  });

  it("clamps at the start of the track", () => {
    expect(panWindow(2, 12, -1, 100)).toEqual({ viewStart: 0, viewEnd: 10 });
  });

  it("clamps at the end of the track", () => {
    expect(panWindow(90, 100, 1, 100)).toEqual({ viewStart: 90, viewEnd: 100 });
  });

  it("is a no-op (full track) when the span already covers the whole duration", () => {
    expect(panWindow(0, 100, 0.5, 100)).toEqual({ viewStart: 0, viewEnd: 100 });
  });
});

describe("zoomWindow", () => {
  it("zooms out by the given factor, centered on centerTime", () => {
    // span 10 * 1.25 = 12.5, centered on 15 (ratio 0.5 within [10,20])
    expect(zoomWindow(10, 20, 1.25, 15, 200)).toEqual({ viewStart: 8.75, viewEnd: 21.25 });
  });

  it("zooms in by the given factor, centered on centerTime", () => {
    // span 10 / 1.25 = 8, centered on 15
    expect(zoomWindow(10, 20, 1 / 1.25, 15, 200)).toEqual({ viewStart: 11, viewEnd: 19 });
  });

  it("keeps the cursor's relative position fixed when zooming off-center", () => {
    // span 10, centerTime 12 -> ratio 0.2 within [10,20]; zoom out to span 20
    const result = zoomWindow(10, 20, 2, 12, 200);
    expect(result.viewEnd - result.viewStart).toBeCloseTo(20, 5);
    expect(12 - result.viewStart).toBeCloseTo(0.2 * 20, 5);
  });

  it("clamps the span to MIN_ZOOM_SPAN_SECONDS", () => {
    const result = zoomWindow(10, 11, 1 / 100, 10.5, 200);
    expect(result.viewEnd - result.viewStart).toBeCloseTo(MIN_ZOOM_SPAN_SECONDS, 5);
  });

  it("clamps the span to MAX_ZOOM_SPAN_SECONDS", () => {
    const result = zoomWindow(0, 10, 100, 5, 1000);
    expect(result.viewEnd - result.viewStart).toBeCloseTo(MAX_ZOOM_SPAN_SECONDS, 5);
  });

  it("clamps the span to duration when duration is shorter than MAX_ZOOM_SPAN_SECONDS", () => {
    const result = zoomWindow(0, 10, 100, 5, 30);
    expect(result).toEqual({ viewStart: 0, viewEnd: 30 });
  });

  it("clamps the window within [0, duration]", () => {
    const result = zoomWindow(0, 10, 2, 0, 100);
    expect(result.viewStart).toBeGreaterThanOrEqual(0);
    expect(result.viewEnd).toBeLessThanOrEqual(100);
  });
});
