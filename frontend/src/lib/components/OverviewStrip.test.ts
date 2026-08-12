import { fireEvent, render } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import OverviewStrip from "./OverviewStrip.svelte";
import { makeFakeAudioBuffer } from "../audio/testFakes";
import * as waveform from "../audio/waveform";

const fakeCtx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// jsdom has no real ResizeObserver; this fake stands in for it so the
// component's "redraw on container resize" wiring can be exercised.
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

// A width deliberately different from any internal fallback constant (800)
// - proves click/drag math tracks the container's ACTUAL rendered width.
function stubRects(width = 1200): void {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: width, bottom: 48, width, height: 48, x: 0, y: 0, toJSON: () => ({}),
  });
}

function makeBuffer() {
  return makeFakeAudioBuffer({ duration: 200, sampleRate: 1 });
}

describe("OverviewStrip", () => {
  it("positions the lens rectangle as a PERCENTAGE of the track duration, not a pixel offset against any fixed width", () => {
    stubRects();
    const { container } = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 20, viewEnd: 40 },
    });

    const lens = container.querySelector(".overview-strip-lens") as HTMLElement;
    expect(lens.style.left).toBe("10%"); // 20/200 * 100
    expect(lens.style.width).toBe("10%"); // (40-20)/200 * 100
  });

  it("lens position is identical regardless of the container's actual rendered width (percentage-based, no measurement needed)", () => {
    stubRects(800);
    const narrow = render(OverviewStrip, { props: { buffer: makeBuffer(), duration: 200, viewStart: 20, viewEnd: 40 } });
    stubRects(1200);
    const wide = render(OverviewStrip, { props: { buffer: makeBuffer(), duration: 200, viewStart: 20, viewEnd: 40 } });

    const narrowLens = narrow.container.querySelector(".overview-strip-lens") as HTMLElement;
    const wideLens = wide.container.querySelector(".overview-strip-lens") as HTMLElement;
    expect(narrowLens.style.left).toBe(wideLens.style.left);
    expect(narrowLens.style.width).toBe(wideLens.style.width);
  });

  it("clicking the background centers the window there (preserving the span), using the ACTUAL measured (non-800) width", async () => {
    stubRects(1200);
    const onWindowChange = vi.fn();
    const { container } = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 0, viewEnd: 20, onWindowChange },
    });

    const strip = container.querySelector(".overview-strip") as HTMLElement;
    await fireEvent.click(strip, { clientX: 400 }); // 400/1200*200 = 66.667

    const [window] = onWindowChange.mock.calls[0];
    expect(window.viewStart).toBeCloseTo(400 / 1200 * 200 - 10, 5);
    expect(window.viewEnd).toBeCloseTo(400 / 1200 * 200 + 10, 5);
  });

  it("the same click x produces a DIFFERENT centered time at a different measured width, proving the math tracks the live width", async () => {
    const onWindowChangeNarrow = vi.fn();
    stubRects(800);
    const narrow = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 0, viewEnd: 20, onWindowChange: onWindowChangeNarrow },
    });
    await fireEvent.click(narrow.container.querySelector(".overview-strip") as HTMLElement, { clientX: 400 });

    const onWindowChangeWide = vi.fn();
    stubRects(1200);
    const wide = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 0, viewEnd: 20, onWindowChange: onWindowChangeWide },
    });
    await fireEvent.click(wide.container.querySelector(".overview-strip") as HTMLElement, { clientX: 400 });

    const [narrowWindow] = onWindowChangeNarrow.mock.calls[0];
    const [wideWindow] = onWindowChangeWide.mock.calls[0];
    expect(narrowWindow).not.toEqual(wideWindow);
    expect(narrowWindow.viewStart).toBeCloseTo(400 / 800 * 200 - 10, 5);
    expect(wideWindow.viewStart).toBeCloseTo(400 / 1200 * 200 - 10, 5);
  });

  it("dragging the lens pans continuously", async () => {
    stubRects(1200);
    const onWindowChange = vi.fn();
    const { container } = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 0, viewEnd: 20, onWindowChange },
    });

    const lens = container.querySelector(".overview-strip-lens") as HTMLElement;
    const strip = container.querySelector(".overview-strip") as HTMLElement;

    await fireEvent(lens, new MouseEvent("pointerdown", { clientX: 40, bubbles: true }));
    await fireEvent(strip, new MouseEvent("pointermove", { clientX: 300, bubbles: true }));

    // 300/1200*200 = 50 -> centered window [40, 60]
    const [window] = onWindowChange.mock.calls[0];
    expect(window.viewStart).toBeCloseTo(40, 5);
    expect(window.viewEnd).toBeCloseTo(60, 5);
  });

  it("clamps the centered window to [0, duration]", async () => {
    stubRects();
    const onWindowChange = vi.fn();
    const { container } = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 0, viewEnd: 20, onWindowChange },
    });

    const strip = container.querySelector(".overview-strip") as HTMLElement;
    await fireEvent.click(strip, { clientX: 0 }); // time 0

    expect(onWindowChange).toHaveBeenCalledWith({ viewStart: 0, viewEnd: 20 });
  });

  it("observes the container via a ResizeObserver, and redraws with the new width when it fires (no window resize needed)", async () => {
    FakeResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    stubRects(800);

    const { container } = render(OverviewStrip, {
      props: { buffer: makeBuffer(), duration: 200, viewStart: 20, viewEnd: 40 },
    });

    expect(FakeResizeObserver.instances).toHaveLength(1);
    const observer = FakeResizeObserver.instances[0];
    expect(observer.observed).toEqual([container.querySelector(".overview-strip")]);

    const drawWaveformSpy = vi.spyOn(waveform, "drawWaveform");
    stubRects(1500);
    observer.callback([], observer as unknown as ResizeObserver);
    await Promise.resolve(); // let the $state write's $effect re-run flush

    const canvas = container.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas.width).toBe(1500);
    expect(drawWaveformSpy).toHaveBeenCalledWith(expect.anything(), expect.anything(), 1500, expect.any(Number), expect.any(String));
  });
});
