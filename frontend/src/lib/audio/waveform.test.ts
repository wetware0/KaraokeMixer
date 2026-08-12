import { afterEach, describe, expect, it, vi } from "vitest";
import { computeBarGeometry, drawPlayhead, drawWaveform, extractPeaks, extractPeaksRange, normalizePeaks, resolveCanvasColor } from "./waveform";

function fakeBuffer(samples: number[]): { getChannelData(channel: number): Float32Array; length: number; numberOfChannels: number } {
  const data = Float32Array.from(samples);
  return { getChannelData: () => data, length: data.length, numberOfChannels: 1 };
}

function fakeBufferWithRate(
  samples: number[], sampleRate: number,
): { getChannelData(channel: number): Float32Array; length: number; numberOfChannels: number; sampleRate: number } {
  return { ...fakeBuffer(samples), sampleRate };
}

describe("extractPeaks", () => {
  it("computes min/max per bucket from a synthetic buffer", () => {
    const peaks = extractPeaks(fakeBuffer([-1, 1, -0.5, 0.5]), 2);
    expect(peaks).toEqual([{ min: -1, max: 1 }, { min: -0.5, max: 0.5 }]);
  });

  it("returns an empty array for a zero or negative bucket count", () => {
    expect(extractPeaks(fakeBuffer([1, 2, 3]), 0)).toEqual([]);
  });

  it("handles a bucket count larger than the sample count without throwing", () => {
    const peaks = extractPeaks(fakeBuffer([1, -1]), 8);
    expect(peaks).toHaveLength(8);
    expect(peaks.every((p) => typeof p.min === "number" && typeof p.max === "number")).toBe(true);
  });
});

describe("extractPeaksRange", () => {
  it("summarizes only the requested time window, at 1 sample/sec, with distinct amplitude in a known window", () => {
    // 10 seconds at 1 sample/sec: samples 0-4 quiet (0.1), samples 5-9 loud (0.9).
    const samples = [0.1, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9];
    const buffer = fakeBufferWithRate(samples, 1);

    const quietWindow = extractPeaksRange(buffer, 1, 0, 5);
    expect(quietWindow[0].max).toBeCloseTo(0.1, 5);

    const loudWindow = extractPeaksRange(buffer, 1, 5, 10);
    expect(loudWindow[0].max).toBeCloseTo(0.9, 5);
  });

  it("returns different peak data for a narrower window than for the whole buffer (a ramp, so no window is self-similar to another)", () => {
    const samples = Array.from({ length: 100 }, (_, i) => i / 100); // monotonic ramp, 0 -> 0.99
    const buffer = fakeBufferWithRate(samples, 10); // 10 seconds total

    const wholeSong = extractPeaksRange(buffer, 4, 0, 10);
    const zoomedIn = extractPeaksRange(buffer, 4, 4, 6); // samples 40-59 only

    expect(wholeSong).not.toEqual(zoomedIn);
    // The zoomed window's first bucket covers samples 40-44 (~0.40-0.44),
    // nowhere near the whole song's first bucket (samples 0-24, ~0-0.24).
    expect(zoomedIn[0].max).toBeGreaterThan(wholeSong[0].max);
  });

  it("returns an empty array for a zero bucket count", () => {
    const buffer = fakeBufferWithRate([1, -1], 1);
    expect(extractPeaksRange(buffer, 0, 0, 1)).toEqual([]);
  });
});

describe("drawPlayhead", () => {
  it("sets the fill color and draws a thin vertical bar centered on x", () => {
    const ctx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };
    drawPlayhead(ctx, 42, 260, "#7c8cff");
    expect(ctx.fillStyle).toBe("#7c8cff");
    expect(ctx.fillRect).toHaveBeenCalledWith(41, 0, 2, 260);
  });
});

describe("normalizePeaks", () => {
  it("rescales a window so the loudest peak reaches full amplitude", () => {
    const peaks = [{ min: -0.2, max: 0.5 }, { min: -0.1, max: 0.25 }];
    const result = normalizePeaks(peaks);
    expect(result[0].max).toBeCloseTo(1, 5);
    expect(result[0].min).toBeCloseTo(-0.4, 5);
    expect(result[1].max).toBeCloseTo(0.5, 5);
    expect(result[1].min).toBeCloseTo(-0.2, 5);
  });

  it("guards the all-zero window: returns peaks unchanged, no division by zero", () => {
    const peaks = [{ min: 0, max: 0 }, { min: 0, max: 0 }];
    const result = normalizePeaks(peaks);
    expect(result).toEqual([{ min: 0, max: 0 }, { min: 0, max: 0 }]);
    expect(result.every((p) => Number.isFinite(p.min) && Number.isFinite(p.max))).toBe(true);
  });

  it("guards an empty peaks array", () => {
    expect(normalizePeaks([])).toEqual([]);
  });

  it("handles a single-sample window", () => {
    const result = normalizePeaks([{ min: -0.3, max: 0.3 }]);
    expect(result).toEqual([{ min: -1, max: 1 }]);
  });

  it("scales correctly when the largest-magnitude peak is negative (mixed sign)", () => {
    // max magnitude here is |min| = 0.4, not max = 0.1
    const result = normalizePeaks([{ min: -0.4, max: 0.1 }]);
    expect(result[0].min).toBeCloseTo(-1, 5);
    expect(result[0].max).toBeCloseTo(0.25, 5);
  });

  it("handles an all-negative window (max is negative too)", () => {
    const result = normalizePeaks([{ min: -0.4, max: -0.1 }]);
    expect(result[0].min).toBeCloseTo(-1, 5);
    expect(result[0].max).toBeCloseTo(-0.25, 5);
  });

  it("leaves an already fully-normalized window unchanged", () => {
    const result = normalizePeaks([{ min: -1, max: 1 }]);
    expect(result).toEqual([{ min: -1, max: 1 }]);
  });
});

describe("resolveCanvasColor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("resolves a CSS custom property via getComputedStyle, trimmed", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (name: string) => (name === "--accent" ? "  #123456  " : ""),
    } as unknown as CSSStyleDeclaration);

    const el = document.createElement("canvas");
    expect(resolveCanvasColor(el, "--accent", "#7c8cff")).toBe("#123456");
  });

  it("falls back to the literal default when the property is unset/empty", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: () => "",
    } as unknown as CSSStyleDeclaration);

    const el = document.createElement("canvas");
    expect(resolveCanvasColor(el, "--accent", "#7c8cff")).toBe("#7c8cff");
  });
});

describe("computeBarGeometry", () => {
  it("maps each peak to an x position and a height proportional to its amplitude", () => {
    const peaks = [{ min: -1, max: 1 }, { min: -0.5, max: 0.5 }];
    const bars = computeBarGeometry(peaks, 100, 50);
    expect(bars[0]).toEqual({ x: 0, yTop: 0, height: 50 });
    expect(bars[1].x).toBe(50);
    expect(bars[1].height).toBeCloseTo(25, 5);
  });
});

describe("drawWaveform", () => {
  it("clears the canvas, sets the fill color, and draws one bar per peak", () => {
    const ctx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };
    const peaks = [{ min: -1, max: 1 }, { min: -0.2, max: 0.2 }, { min: 0, max: 0 }];

    drawWaveform(ctx, peaks, 90, 40, "#7c8cff");

    expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 90, 40);
    expect(ctx.fillStyle).toBe("#7c8cff");
    expect(ctx.fillRect).toHaveBeenCalledTimes(3);
  });
});
