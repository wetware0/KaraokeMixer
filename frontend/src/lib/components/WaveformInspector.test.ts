import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import WaveformInspector from "./WaveformInspector.svelte";
import { makeFakeAudioBuffer } from "../audio/testFakes";
import * as waveform from "../audio/waveform";

const fakeCtx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// jsdom has no real ResizeObserver; this fake stands in for it so
// components that construct one (to redraw on a container-width change
// with no window resize) can be exercised and driven by hand.
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  callback: ResizeObserverCallback;
  observed: Element[] = [];
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }
  observe(el: Element): void {
    this.observed.push(el);
  }
  unobserve(): void {}
  disconnect(): void {}
}

// A width deliberately different from the component's internal 800px
// fallback constant - every test in this file uses this as the mocked
// rendered width, so a passing test actually proves the pixel<->time math
// tracks the container's live measured width rather than happening to
// match a hardcoded constant.
function stubRects(width = 1200): void {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: width, bottom: 120, width, height: 120, x: 0, y: 0, toJSON: () => ({}),
  });
}

// A 20-second buffer at 1 sample/sec whose first half is quiet (0.1) and
// second half is loud (0.9) - distinct enough that windowed peaks for
// [0,10) and [10,20) are trivially distinguishable.
function makeWindowedBuffer() {
  return makeFakeAudioBuffer({
    duration: 20, sampleRate: 1,
    fill: (_channel, index) => (index < 10 ? 0.1 : 0.9),
  });
}

describe("WaveformInspector", () => {
  it("renders line starts as distinct, accessible markers", () => {
    stubRects();
    const { container } = render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10,
        markers: [{ lineIndex: 0, wordIndex: -1, time: 2.5, kind: "line" }],
      },
    });

    expect(screen.getByLabelText("Line start at 2.50s")).toBeTruthy();
    expect(container.querySelector(".waveform-marker-line")).toBeTruthy();
  });

  it("renders one marker button per marker, positioned by time against the ACTUAL measured (non-800) width", () => {
    stubRects();
    render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10,
        markers: [
          { lineIndex: 0, wordIndex: 0, time: 2.5 },
          { lineIndex: 0, wordIndex: 1, time: 7.5 },
        ],
      },
    });

    const markers = screen.getAllByRole("button");
    expect(markers).toHaveLength(2);
    expect(markers[0].style.left).toBe("300px"); // 2.5 / 10 * 1200
    expect(markers[1].style.left).toBe("900px"); // 7.5 / 10 * 1200
  });

  it("marker positions scale with a DIFFERENT rendered width, proving they track live measurement rather than a fixed constant", () => {
    stubRects(800);
    const narrow = render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [{ lineIndex: 0, wordIndex: 0, time: 5 }] },
    });
    const narrowLeft = (screen.getAllByRole("button")[0] as HTMLElement).style.left;

    stubRects(1200);
    const wide = render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [{ lineIndex: 0, wordIndex: 0, time: 5 }] },
    });
    const wideMarkers = wide.container.querySelectorAll('[aria-label="Word timing at 5.00s"]');

    expect(narrowLeft).toBe("400px"); // 5/10*800
    expect((wideMarkers[0] as HTMLElement).style.left).toBe("600px"); // 5/10*1200
    narrow.unmount();
  });

  it("sets the canvas bitmap width to the measured rendered width (not a stretched fixed-800px bitmap)", () => {
    stubRects(1200);
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [] },
    });
    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas.width).toBe(1200);
  });

  it("observes the container via a ResizeObserver, and redraws with the new width when it fires (no window resize needed)", async () => {
    FakeResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    stubRects(800);

    const { container } = render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [{ lineIndex: 0, wordIndex: 0, time: 5 }] },
    });

    expect(FakeResizeObserver.instances).toHaveLength(1);
    const observer = FakeResizeObserver.instances[0];
    expect(observer.observed).toEqual([container.querySelector(".waveform-inspector")]);

    // Simulate the container growing with no window "resize" event at all -
    // just the observer firing, as a real ResizeObserver would on a fluid
    // layout reflow.
    stubRects(1500);
    observer.callback([], observer as unknown as ResizeObserver);
    await Promise.resolve(); // let the $state write's $effect re-run flush

    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas.width).toBe(1500);
    const marker = document.querySelector(".waveform-marker") as HTMLElement;
    expect(marker.style.left).toBe("750px"); // 5/10*1500
  });

  it("drags a marker and reports the new time (against the measured width) via onMarkerDrag, then onMarkerDragEnd on release", async () => {
    stubRects();
    const onMarkerDrag = vi.fn();
    const onMarkerDragEnd = vi.fn();
    const { container } = render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10,
        markers: [{ lineIndex: 0, wordIndex: 0, time: 2.5 }],
        onMarkerDrag, onMarkerDragEnd,
      },
    });

    const marker = screen.getByRole("button");
    const wrapper = container.querySelector(".waveform-inspector") as HTMLElement;

    // jsdom has no PointerEvent constructor, so @testing-library/dom's
    // fireEvent.pointerDown/Move/Up() helpers fall back to a plain Event
    // that silently drops clientX (Event's constructor init dict has no
    // such field). MouseEvent DOES carry clientX and IS implemented in
    // jsdom; the component's addEventListener("pointerdown"/"pointermove"/
    // "pointerup", ...) matches on the event's `type` string regardless of
    // which class built it, so dispatching a same-named MouseEvent still
    // triggers the real handlers, with working clientX.
    await fireEvent(marker, new MouseEvent("pointerdown", { clientX: 300, bubbles: true }));
    await fireEvent(wrapper, new MouseEvent("pointermove", { clientX: 600, bubbles: true }));

    expect(onMarkerDrag).toHaveBeenCalledWith({ lineIndex: 0, wordIndex: 0, time: 2.5 }, 5); // 600/1200*10

    await fireEvent(wrapper, new MouseEvent("pointerup", { clientX: 600, bubbles: true }));
    expect(onMarkerDragEnd).toHaveBeenCalledWith({ lineIndex: 0, wordIndex: 0, time: 2.5 });
  });

  it("calls onSeek when the canvas itself is clicked", async () => {
    stubRects();
    const onSeek = vi.fn();
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], onSeek },
    });

    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    await fireEvent.click(canvas, { clientX: 600 });

    expect(onSeek).toHaveBeenCalledWith(5); // 600/1200*10
  });

  it("calls the timing handler instead of seeking on Shift+click", async () => {
    stubRects();
    const onSeek = vi.fn();
    const onShiftClick = vi.fn();
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], onSeek, onShiftClick },
    });
    const canvases = document.querySelectorAll("canvas");
    expect(canvases).toHaveLength(1);

    await fireEvent.click(canvases[0], { clientX: 600, shiftKey: true });

    expect(onShiftClick).toHaveBeenCalledWith(5);
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("re-derives and redraws with different peak data when viewStart/viewEnd change (zoom window), instead of a static whole-song extraction", async () => {
    stubRects();
    const drawWaveformSpy = vi.spyOn(waveform, "drawWaveform");
    const buffer = makeWindowedBuffer();

    const { rerender } = render(WaveformInspector, {
      props: { buffer, viewStart: 0, viewEnd: 10, markers: [] }, // quiet half
    });
    const quietPeaks = drawWaveformSpy.mock.calls.at(-1)?.[1];

    drawWaveformSpy.mockClear();
    await rerender({ buffer, viewStart: 10, viewEnd: 20, markers: [] }); // loud half
    const loudPeaks = drawWaveformSpy.mock.calls.at(-1)?.[1];

    expect(drawWaveformSpy).toHaveBeenCalled();
    expect(loudPeaks).not.toEqual(quietPeaks);
  });

  it("draws a playhead line when playheadTime is within the visible window", async () => {
    stubRects();
    const drawPlayheadSpy = vi.spyOn(waveform, "drawPlayhead");
    const buffer = makeWindowedBuffer();

    render(WaveformInspector, {
      props: { buffer, viewStart: 0, viewEnd: 10, markers: [], playheadTime: 5 },
    });

    expect(drawPlayheadSpy).toHaveBeenCalledWith(expect.anything(), 600, expect.any(Number), expect.any(String)); // 5/10*1200
  });

  it("does not draw a playhead line when playheadTime is outside the visible window", async () => {
    stubRects();
    const drawPlayheadSpy = vi.spyOn(waveform, "drawPlayhead");
    const buffer = makeWindowedBuffer();

    render(WaveformInspector, {
      props: { buffer, viewStart: 0, viewEnd: 10, markers: [], playheadTime: 15 },
    });

    expect(drawPlayheadSpy).not.toHaveBeenCalled();
  });

  it("draws a loop-region overlay when a loop overlapping the view is set", () => {
    stubRects();
    const drawLoopRegionSpy = vi.spyOn(waveform, "drawLoopRegion");
    const buffer = makeWindowedBuffer();

    render(WaveformInspector, {
      props: { buffer, viewStart: 0, viewEnd: 10, markers: [], loop: { start: 2, end: 4 } },
    });

    expect(drawLoopRegionSpy).toHaveBeenCalledWith(expect.anything(), 240, 480, expect.any(Number), expect.any(String)); // 2/10*1200, 4/10*1200
  });

  it("draws the selected lyric section across its visible time range", () => {
    stubRects();
    const drawSelectionSpy = vi.spyOn(waveform, "drawSelectionRegion");

    render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [],
        selection: { start: 2, end: 4, kind: "line" },
      },
    });

    expect(drawSelectionSpy).toHaveBeenCalledWith(expect.anything(), 240, 480, expect.any(Number), expect.any(String));
  });

  it("clips a selected section to the visible waveform window", () => {
    stubRects();
    const drawSelectionSpy = vi.spyOn(waveform, "drawSelectionRegion");

    render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 2, viewEnd: 6, markers: [],
        selection: { start: 1, end: 4, kind: "break" },
      },
    });

    expect(drawSelectionSpy).toHaveBeenCalledWith(expect.anything(), 0, 600, expect.any(Number), expect.any(String));
  });

  it("does not draw a selected section that is outside the visible window", () => {
    stubRects();
    const drawSelectionSpy = vi.spyOn(waveform, "drawSelectionRegion");

    render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [],
        selection: { start: 12, end: 14, kind: "word" },
      },
    });

    expect(drawSelectionSpy).not.toHaveBeenCalled();
  });

  it("does not draw a loop-region overlay when the loop is entirely outside the view", () => {
    stubRects();
    const drawLoopRegionSpy = vi.spyOn(waveform, "drawLoopRegion");
    const buffer = makeWindowedBuffer();

    render(WaveformInspector, {
      props: { buffer, viewStart: 0, viewEnd: 10, markers: [], loop: { start: 15, end: 18 } },
    });

    expect(drawLoopRegionSpy).not.toHaveBeenCalled();
  });

  it("calls onSeekAndPlay (not onSeek) when the canvas is double-clicked", async () => {
    stubRects();
    const onSeek = vi.fn();
    const onSeekAndPlay = vi.fn();
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], onSeek, onSeekAndPlay },
    });

    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    await fireEvent.dblClick(canvas, { clientX: 600 });

    expect(onSeekAndPlay).toHaveBeenCalledWith(5); // 600/1200*10
  });

  it("awaits an async onSeekAndPlay without throwing (fire-and-forget via void, matching togglePlayback's async contract)", async () => {
    stubRects();
    let resolvePlay: () => void = () => {};
    const played = new Promise<void>((resolve) => {
      resolvePlay = resolve;
    });
    const onSeekAndPlay = vi.fn(() => played);
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], onSeekAndPlay },
    });

    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    await fireEvent.dblClick(canvas, { clientX: 600 });

    expect(onSeekAndPlay).toHaveBeenCalledWith(5);
    resolvePlay();
    await played;
  });

  it("plain wheel pans the window by a fraction of the span, clamped to duration", async () => {
    stubRects();
    const onWindowChange = vi.fn();
    const { container } = render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], duration: 20, onWindowChange,
      },
    });

    const wrapper = container.querySelector(".waveform-inspector") as HTMLElement;
    await fireEvent.wheel(wrapper, { deltaY: 100 });

    expect(onWindowChange).toHaveBeenCalledWith({ viewStart: 2, viewEnd: 12 });
  });

  it("ctrl+wheel zooms the window centered on the cursor's time", async () => {
    stubRects();
    const onWindowChange = vi.fn();
    const { container } = render(WaveformInspector, {
      props: {
        buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers: [], duration: 200, onWindowChange,
      },
    });

    const wrapper = container.querySelector(".waveform-inspector") as HTMLElement;
    await fireEvent.wheel(wrapper, { deltaY: 100, ctrlKey: true, clientX: 400 });

    const result = onWindowChange.mock.calls[0][0];
    expect(result.viewEnd - result.viewStart).toBeCloseTo(12.5, 5);
  });

  it("clicking a marker calls onMarkerSelect with that marker (selecting via the waveform, not just dragging)", async () => {
    stubRects();
    const onMarkerSelect = vi.fn();
    const markers = [{ lineIndex: 0, wordIndex: 0, time: 2 }];
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers, onMarkerSelect },
    });

    await fireEvent.click(screen.getByLabelText("Word timing at 2.00s"));

    expect(onMarkerSelect).toHaveBeenCalledWith(markers[0]);
  });

  it("pressing Enter on a focused marker also calls onMarkerSelect", async () => {
    stubRects();
    const onMarkerSelect = vi.fn();
    const markers = [{ lineIndex: 0, wordIndex: 0, time: 2 }];
    render(WaveformInspector, {
      props: { buffer: makeWindowedBuffer(), viewStart: 0, viewEnd: 10, markers, onMarkerSelect },
    });

    await fireEvent.keyDown(screen.getByLabelText("Word timing at 2.00s"), { key: "Enter" });

    expect(onMarkerSelect).toHaveBeenCalledWith(markers[0]);
  });
});
