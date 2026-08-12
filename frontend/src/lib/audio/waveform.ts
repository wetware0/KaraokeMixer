export interface PeakBucket {
  min: number;
  max: number;
}

export interface WaveformBuffer {
  getChannelData(channel: number): Float32Array;
  length: number;
  numberOfChannels: number;
}

export function extractPeaks(buffer: WaveformBuffer, bucketCount: number, channel = 0): PeakBucket[] {
  if (bucketCount <= 0) return [];
  const data = buffer.getChannelData(Math.min(channel, buffer.numberOfChannels - 1));
  const samplesPerBucket = data.length / bucketCount;
  const peaks: PeakBucket[] = [];
  for (let bucket = 0; bucket < bucketCount; bucket++) {
    const start = Math.floor(bucket * samplesPerBucket);
    const end = Math.max(start + 1, Math.floor((bucket + 1) * samplesPerBucket));
    let min = Infinity;
    let max = -Infinity;
    for (let i = start; i < end && i < data.length; i++) {
      const value = data[i];
      if (value < min) min = value;
      if (value > max) max = value;
    }
    if (min === Infinity) {
      min = 0;
      max = 0;
    }
    peaks.push({ min, max });
  }
  return peaks;
}

export interface WaveformBufferWithRate extends WaveformBuffer {
  sampleRate: number;
}

/** Windowed variant of `extractPeaks`: instead of summarizing the entire
 * buffer into `bucketCount` peaks, summarizes only the sample range that
 * falls within [startSec, endSec). This is what lets a zoomed-in inspector
 * view show finer per-bucket time resolution as the window narrows, rather
 * than showing a coarse slice of a single whole-song extraction. */
export function extractPeaksRange(
  buffer: WaveformBufferWithRate, bucketCount: number, startSec: number, endSec: number, channel = 0,
): PeakBucket[] {
  if (bucketCount <= 0) return [];
  const data = buffer.getChannelData(Math.min(channel, buffer.numberOfChannels - 1));
  const startSample = Math.max(0, Math.min(data.length, Math.floor(startSec * buffer.sampleRate)));
  const endSample = Math.max(startSample, Math.min(data.length, Math.ceil(endSec * buffer.sampleRate)));
  const slice = data.subarray(startSample, endSample);
  return extractPeaks({ getChannelData: () => slice, length: slice.length, numberOfChannels: 1 }, bucketCount);
}

/** Rescales a window of peak data so the loudest peak in view reaches full
 * amplitude (±1) - per-window normalization. This is what makes a quiet vocal
 * passage visually readable in a zoomed-in inspector view, where the
 * whole-song peak might be far louder than anything in the current window.
 * Guards the all-zero window (silence, or an empty array): returns the
 * peaks unchanged rather than dividing by zero, which would otherwise
 * produce NaN/Infinity for every bar. */
export function normalizePeaks(peaks: PeakBucket[]): PeakBucket[] {
  let maxAbs = 0;
  for (const peak of peaks) {
    maxAbs = Math.max(maxAbs, Math.abs(peak.min), Math.abs(peak.max));
  }
  if (maxAbs === 0) return peaks;
  const scale = 1 / maxAbs;
  return peaks.map((peak) => ({ min: peak.min * scale, max: peak.max * scale }));
}

/** Resolves a CSS custom property (e.g. `--accent`) to its computed value for
 * use as a canvas `fillStyle` - the canvas 2D API cannot parse `var(...)`
 * expressions directly, unlike DOM/CSS properties, so callers that want a
 * canvas fill color to track the page's theme tokens must resolve it via
 * `getComputedStyle` themselves and pass the literal color to `drawWaveform`.
 * Falls back to `fallback` when the property is unset/empty (e.g. in test
 * environments with no real stylesheet cascade). */
export function resolveCanvasColor(el: Element, varName: string, fallback: string): string {
  const value = getComputedStyle(el).getPropertyValue(varName).trim();
  return value || fallback;
}

export interface WaveformBarGeometry {
  x: number;
  yTop: number;
  height: number;
}

export function computeBarGeometry(peaks: PeakBucket[], widthPx: number, heightPx: number): WaveformBarGeometry[] {
  const barWidth = widthPx / Math.max(1, peaks.length);
  const centerY = heightPx / 2;
  return peaks.map((peak, index) => {
    const top = centerY - peak.max * centerY;
    const bottom = centerY - peak.min * centerY;
    return { x: index * barWidth, yTop: top, height: Math.max(1, bottom - top) };
  });
}

export interface CanvasLike {
  clearRect(x: number, y: number, w: number, h: number): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  fillStyle: string;
}

export function drawWaveform(
  ctx: CanvasLike, peaks: PeakBucket[], widthPx: number, heightPx: number, color: string,
): void {
  ctx.clearRect(0, 0, widthPx, heightPx);
  ctx.fillStyle = color;
  const barWidth = widthPx / Math.max(1, peaks.length);
  for (const bar of computeBarGeometry(peaks, widthPx, heightPx)) {
    ctx.fillRect(bar.x, bar.yTop, Math.max(1, barWidth - 1), bar.height);
  }
}

/** Draws a thin vertical playhead line at `x` spanning the full canvas
 * height. Callers are responsible for only invoking this when the playhead
 * time actually falls within the visible [viewStart, viewEnd) window. */
export function drawPlayhead(ctx: CanvasLike, x: number, heightPx: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(x - 1, 0, 2, heightPx);
}

/** Draws a translucent loop-region overlay spanning [xStart, xEnd) - the
 * caller is responsible for clamping xStart/xEnd to the canvas bounds (e.g.
 * via timeToX against the currently-visible window) and for resolving
 * `color` through `resolveCanvasColor` first, same as `drawWaveform`. */
export function drawLoopRegion(ctx: CanvasLike, xStart: number, xEnd: number, heightPx: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(xStart, 0, Math.max(0, xEnd - xStart), heightPx);
}

/** Draws the selected lyric section beneath timing markers. Keep this
 * separate from drawLoopRegion even though both are rectangles: selection
 * and looping are independent editor states and use different theme colors. */
export function drawSelectionRegion(ctx: CanvasLike, xStart: number, xEnd: number, heightPx: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(xStart, 0, Math.max(2, xEnd - xStart), heightPx);
}
