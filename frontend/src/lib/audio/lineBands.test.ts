import { describe, expect, it } from "vitest";
import { parseLrc } from "../lrcModel";
import {
  chooseInstrumentalInsertIndex, computeDragLoop, computeLineBands, computeTimingSelectionSpan, LOOP_DRAG_THRESHOLD_PX,
} from "./lineBands";

describe("computeLineBands", () => {
  it("produces no band for an untimed lyric line", () => {
    const model = parseLrc("Hello there\n");
    expect(computeLineBands(model)).toEqual([]);
  });

  it("produces no band for a non-lyric line (blank/metadata) nor for an untimed lyric line", () => {
    const model = parseLrc("[ar:Someone]\n\nHello\n");
    expect(computeLineBands(model)).toEqual([]);
  });

  it("bands a fully-enhanced (per-word-timed) line from its first word to its last word's time + tailSeconds", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hello<00:01.50> world\n");
    const bands = computeLineBands(model);
    expect(bands).toEqual([
      { lineIndex: 0, start: 1.0, end: 1.5 + 0.4, text: "Hello world", instrumental: false },
    ]);
  });

  it("bands a line-timed-only line (no per-word times) using lineStart for start, and null for end", () => {
    const model = parseLrc("[00:05.00]Some lyrics here\n");
    const bands = computeLineBands(model);
    expect(bands).toEqual([
      { lineIndex: 0, start: 5.0, end: null, text: "Some lyrics here", instrumental: false },
    ]);
  });

  it("supports a custom tailSeconds", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n");
    const bands = computeLineBands(model, 1.0);
    expect(bands[0].end).toBeCloseTo(2.0, 5);
  });

  it("flags the legacy single-♪-word marker line as instrumental (back-compat)", () => {
    const model = parseLrc("[00:10.00]<00:10.00>♪\n");
    const bands = computeLineBands(model);
    expect(bands).toEqual([{ lineIndex: 0, start: 10.0, end: 10.4, text: "♪", instrumental: true }]);
  });

  it("bands a bare-timestamp break line (no words at all) despite it being non-lyric", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:10.00]\n");
    const bands = computeLineBands(model);
    expect(bands).toEqual([
      { lineIndex: 0, start: 1.0, end: 1.4, text: "Hi", instrumental: false },
      { lineIndex: 1, start: 10.0, end: null, text: "", instrumental: true },
    ]);
  });

  it("preserves original line indices when interleaved with untimed lines", () => {
    const model = parseLrc("Untimed intro\n[00:02.00]<00:02.00>Timed\n");
    const bands = computeLineBands(model);
    expect(bands).toEqual([{ lineIndex: 1, start: 2.0, end: 2.4, text: "Timed", instrumental: false }]);
  });
});

describe("chooseInstrumentalInsertIndex", () => {
  const bands = [
    { lineIndex: 0, start: 1.0, end: 2.0, text: "a", instrumental: false },
    { lineIndex: 2, start: 5.0, end: 6.0, text: "b", instrumental: false },
    { lineIndex: 3, start: 10.0, end: 11.0, text: "c", instrumental: false },
  ];

  it("returns the lineIndex of the latest-starting band at or before the given time", () => {
    expect(chooseInstrumentalInsertIndex(bands, 7)).toBe(2);
  });

  it("returns -1 when the time is before every band", () => {
    expect(chooseInstrumentalInsertIndex(bands, 0.5)).toBe(-1);
  });

  it("returns the last band's lineIndex when the time is after every band", () => {
    expect(chooseInstrumentalInsertIndex(bands, 100)).toBe(3);
  });

  it("returns -1 for an empty band list", () => {
    expect(chooseInstrumentalInsertIndex([], 5)).toBe(-1);
  });

  it("matches exactly on a band's own start time", () => {
    expect(chooseInstrumentalInsertIndex(bands, 5.0)).toBe(2);
  });
});

describe("computeTimingSelectionSpan", () => {
  const timedModel = parseLrc(
    "[00:01.00]<00:01.00>Hello<00:01.50> world\n[00:03.00]\n[00:05.00]<00:05.00>Next<00:05.50> line\n",
  );

  it("spans a selected word up to the next timed word", () => {
    expect(computeTimingSelectionSpan(timedModel, { lineIndex: 0, wordIndex: 0 }, 10)).toEqual({
      start: 1, end: 1.5, kind: "word",
    });
  });

  it("ends the last word at a following bare-timestamp break", () => {
    expect(computeTimingSelectionSpan(timedModel, { lineIndex: 0, wordIndex: 1 }, 10)).toEqual({
      start: 1.5, end: 3, kind: "word",
    });
  });

  it("spans a selected line to the next timestamped line", () => {
    expect(computeTimingSelectionSpan(timedModel, { lineIndex: 0, wordIndex: null }, 10)).toEqual({
      start: 1, end: 3, kind: "line",
    });
  });

  it("spans an instrumental break to the following line", () => {
    expect(computeTimingSelectionSpan(timedModel, { lineIndex: 1, wordIndex: null }, 10)).toEqual({
      start: 3, end: 5, kind: "break",
    });
  });

  it("uses track duration for the final timed selection", () => {
    expect(computeTimingSelectionSpan(timedModel, { lineIndex: 2, wordIndex: 1 }, 10)).toEqual({
      start: 5.5, end: 10, kind: "word",
    });
  });

  it("returns no span for an untimed selected word", () => {
    const untimed = parseLrc("[00:01.00]Hello world\n");
    expect(computeTimingSelectionSpan(untimed, { lineIndex: 0, wordIndex: 0 }, 10)).toBeNull();
  });
});

describe("computeDragLoop", () => {
  it("returns null when movement is at or below the threshold (treated as a click, not a drag)", () => {
    expect(computeDragLoop(100, 100 + LOOP_DRAG_THRESHOLD_PX, 0, 10, 800)).toBeNull();
    expect(computeDragLoop(100, 100, 0, 10, 800)).toBeNull();
  });

  it("returns an ordered loop span once movement exceeds the threshold, dragging left-to-right", () => {
    // 100px and 300px over an 800px-wide [0,10) window -> 1.25s and 3.75s
    expect(computeDragLoop(100, 300, 0, 10, 800)).toEqual({ start: 1.25, end: 3.75 });
  });

  it("orders the span correctly when dragging right-to-left", () => {
    expect(computeDragLoop(300, 100, 0, 10, 800)).toEqual({ start: 1.25, end: 3.75 });
  });

  it("respects a custom threshold", () => {
    expect(computeDragLoop(100, 110, 0, 10, 800, 20)).toBeNull();
    expect(computeDragLoop(100, 130, 0, 10, 800, 20)).not.toBeNull();
  });
});
