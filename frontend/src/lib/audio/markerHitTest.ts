export function timeToX(time: number, viewStart: number, viewEnd: number, widthPx: number): number {
  const span = viewEnd - viewStart;
  if (span <= 0) return 0;
  return ((time - viewStart) / span) * widthPx;
}

export function xToTime(x: number, viewStart: number, viewEnd: number, widthPx: number): number {
  if (widthPx <= 0) return viewStart;
  const span = viewEnd - viewStart;
  return viewStart + (x / widthPx) * span;
}

export interface ViewWindow {
  viewStart: number;
  viewEnd: number;
}

/** "Follow mode" for a playing inspector: once the playhead reaches (or
 * passes) viewEnd, slides the visible window forward so its new start is
 * the old end - keeping the span (viewEnd - viewStart) unchanged - rather
 * than leaving the playhead to run off the right edge of the view. Returns
 * the window unchanged while the playhead is still inside it.
 *
 * The advance is clamped to `duration`: a naive full-span advance can run
 * past the end of the track, showing trailing empty space. Instead, once
 * the window would reach the track's end, it's pulled back so the final
 * window is still full-width but ends exactly at `duration`. */
export function advanceFollowWindow(
  viewStart: number, viewEnd: number, playheadTime: number, duration: number,
): ViewWindow {
  if (playheadTime < viewEnd) return { viewStart, viewEnd };
  const span = viewEnd - viewStart;
  const newStart = viewEnd;
  const newEnd = Math.min(newStart + span, duration);
  if (newEnd === duration) {
    return { viewStart: Math.max(0, duration - span), viewEnd: newEnd };
  }
  return { viewStart: newStart, viewEnd: newEnd };
}

export interface LoopRegionLike {
  start: number;
  end: number;
}

/** The fraction of the current span reserved as a margin past a loop's end
 * when clamping follow-mode advancement - see `advanceFollowWindowWithLoop`. */
export const LOOP_FOLLOW_MARGIN_FRACTION = 0.05;

/** Like `advanceFollowWindow`, but loop-aware: when a loop is active and
 * wider than the view span, a plain full-span advance would run the window
 * past `loop.end` into "dead time" the playhead will never actually reach
 * before the engine wraps back to `loop.start` - producing a once-per-
 * repeat overshoot-then-snap-back. Instead, this clamps the advanced
 * window so `viewEnd` never exceeds `loop.end` plus a small margin (5% of
 * the span): the window advances TOWARD the loop's end, not past it.
 *
 * When the loop already fits entirely within the current view (its end,
 * plus margin, is already <= viewEnd), there's nothing further to show by
 * advancing, so the window is left unchanged - this is what makes a loop
 * narrower than the view span stay put instead of drifting.
 *
 * Wraparound (the playhead time jumping backward, once the engine actually
 * wraps to `loop.start`) is NOT this function's concern - the caller
 * detects that separately (`time < viewStart`) and re-centers via
 * `centerWindow` instead. */
export function advanceFollowWindowWithLoop(
  viewStart: number, viewEnd: number, playheadTime: number, duration: number,
  loop: LoopRegionLike | null,
): ViewWindow {
  if (!loop) return advanceFollowWindow(viewStart, viewEnd, playheadTime, duration);

  const span = viewEnd - viewStart;
  const margin = span * LOOP_FOLLOW_MARGIN_FRACTION;
  const maxEnd = Math.min(duration, loop.end + margin);

  if (playheadTime < viewEnd) return { viewStart, viewEnd }; // nothing to advance yet
  if (maxEnd <= viewEnd) return { viewStart, viewEnd }; // loop (plus margin) already fully in view

  const advanced = advanceFollowWindow(viewStart, viewEnd, playheadTime, duration);
  if (advanced.viewEnd <= maxEnd) return advanced; // the naive advance already respects the clamp

  const clampedEnd = maxEnd;
  const clampedStart = Math.max(0, clampedEnd - span);
  return { viewStart: clampedStart, viewEnd: clampedEnd };
}

/** x-coordinate to time mapping for the overview strip, which always shows
 * the whole track (unlike the zoomable main inspector's `xToTime`, which
 * maps within an arbitrary [viewStart, viewEnd) window) - so it maps
 * directly against `duration` instead of a view span. Clamped to
 * [0, duration] since a pointer can be dragged past either edge. */
export function overviewXToTime(x: number, widthPx: number, duration: number): number {
  if (widthPx <= 0 || duration <= 0) return 0;
  return Math.max(0, Math.min(duration, (x / widthPx) * duration));
}

/** Centers a [viewStart, viewEnd) window of the given `span` on `centerTime`,
 * clamping so the window never runs outside [0, duration]. Used both for
 * "click the overview to jump there" (one-shot) and "drag the lens" (called
 * continuously as the pointer moves) - both are just "put the window's
 * center at this time," so they share one clamped implementation. */
export function centerWindow(centerTime: number, span: number, duration: number): ViewWindow {
  if (span >= duration) return { viewStart: 0, viewEnd: duration };
  const viewStart = Math.max(0, Math.min(duration - span, centerTime - span / 2));
  return { viewStart, viewEnd: viewStart + span };
}

/** Pans the view window by `deltaFraction` of its own span (e.g. 0.2 = 20%
 * of the current span), clamped to [0, duration]. A no-op (returns the full
 * track) when the span already covers the whole track. */
export function panWindow(viewStart: number, viewEnd: number, deltaFraction: number, duration: number): ViewWindow {
  const span = viewEnd - viewStart;
  if (span >= duration) return { viewStart: 0, viewEnd: duration };
  const newStart = Math.max(0, Math.min(duration - span, viewStart + deltaFraction * span));
  return { viewStart: newStart, viewEnd: newStart + span };
}

export const MIN_ZOOM_SPAN_SECONDS = 1;
export const MAX_ZOOM_SPAN_SECONDS = 60;

/** Zooms the view span by `factor` (e.g. 1.25 to zoom out, 1/1.25 to zoom
 * in), clamped to [MIN_ZOOM_SPAN_SECONDS, MAX_ZOOM_SPAN_SECONDS] and to
 * `duration`, keeping `centerTime` at the same fractional position within
 * the window (so zooming under the mouse cursor keeps the cursor's time
 * roughly fixed on screen, rather than always re-centering the window). */
export function zoomWindow(
  viewStart: number, viewEnd: number, factor: number, centerTime: number, duration: number,
): ViewWindow {
  const span = viewEnd - viewStart;
  const clampedSpan = Math.min(
    Math.max(MIN_ZOOM_SPAN_SECONDS, span * factor), Math.min(MAX_ZOOM_SPAN_SECONDS, duration || MAX_ZOOM_SPAN_SECONDS),
  );
  if (clampedSpan >= duration) return { viewStart: 0, viewEnd: duration };
  const ratio = span > 0 ? (centerTime - viewStart) / span : 0.5;
  const newStart = Math.max(0, Math.min(duration - clampedSpan, centerTime - ratio * clampedSpan));
  return { viewStart: newStart, viewEnd: newStart + clampedSpan };
}

export interface MarkerRef {
  lineIndex: number;
  wordIndex: number;
  time: number;
  kind?: "word" | "line";
}

export function findNearestMarker(
  markers: MarkerRef[], targetTime: number, maxDistanceSeconds: number,
): MarkerRef | null {
  let nearest: MarkerRef | null = null;
  let nearestDistance = Infinity;
  for (const marker of markers) {
    const distance = Math.abs(marker.time - targetTime);
    if (distance < nearestDistance) {
      nearest = marker;
      nearestDistance = distance;
    }
  }
  return nearest && nearestDistance <= maxDistanceSeconds ? nearest : null;
}
