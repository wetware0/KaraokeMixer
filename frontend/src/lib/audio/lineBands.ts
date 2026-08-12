import { deriveLineTimestamp, isInstrumentalLine, type LrcModel } from "../lrcModel";
import { xToTime } from "./markerHitTest";

export interface LineBand {
  lineIndex: number;
  start: number;
  /** null when no word in the line has an individual time yet (line-timed
   * only) - the caller decides how to visually terminate an open-ended band
   * (e.g. at the next band's start, or at the view/track end). */
  end: number | null;
  text: string;
  instrumental: boolean;
}

/** Derives one visual band per timed lyric line - plus one per instrumental
 * break line (see isInstrumentalLine), even though a break's canonical
 * on-disk shape (a bare `[mm:ss.xx]` line with no words) makes it
 * non-lyric - from its first timed word (falling back to the line's own
 * `lineStart` for a line-timed-only line, which is also how a break's
 * single timestamp becomes its band's start) to `tailSeconds` past its
 * last timed word. A line with no timing information at all (no lineStart,
 * no timed words) produces no band - there is nothing to place it at on
 * the timeline. */
export function computeLineBands(model: LrcModel, tailSeconds = 0.4): LineBand[] {
  const bands: LineBand[] = [];
  model.lines.forEach((line, lineIndex) => {
    const instrumental = isInstrumentalLine(model, lineIndex);
    if (!line.isLyric && !instrumental) return;
    const start = deriveLineTimestamp(line);
    if (start === null) return;

    let lastTimedWordTime: number | null = null;
    for (const word of line.words) {
      if (word.time !== null) lastTimedWordTime = word.time;
    }
    const end = lastTimedWordTime !== null ? lastTimedWordTime + tailSeconds : null;

    bands.push({ lineIndex, start, end, text: line.text, instrumental });
  });
  return bands;
}

/** Picks the `afterLineIndex` argument for `insertInstrumentalLine`: the
 * line whose band starts latest while still starting at or before `time` -
 * i.e. "insert the break right after whichever line is currently playing
 * (or most recently played)". Returns -1 (insert before the first line)
 * when no band starts at or before `time`. */
export function chooseInstrumentalInsertIndex(bands: LineBand[], time: number): number {
  let bestLineIndex = -1;
  let bestStart = -Infinity;
  for (const band of bands) {
    if (band.start <= time && band.start > bestStart) {
      bestStart = band.start;
      bestLineIndex = band.lineIndex;
    }
  }
  return bestLineIndex;
}

export interface LoopSpan {
  start: number;
  end: number;
}

export interface TimingSelection {
  lineIndex: number;
  wordIndex: number | null;
}

export interface TimingSelectionSpan extends LoopSpan {
  kind: "line" | "word" | "break";
}

/** Returns the timeline section represented by the current lyric selection.
 *
 * Lines and instrumental breaks extend to the next timestamped line. Words
 * extend to the next timed word, or to the next line/break timestamp. This
 * makes a bare timestamp break a hard visual boundary, matching alignment
 * semantics instead of allowing the preceding word highlight to cross it.
 */
export function computeTimingSelectionSpan(
  model: LrcModel,
  selection: TimingSelection | null,
  duration: number,
): TimingSelectionSpan | null {
  if (!selection) return null;
  const line = model.lines[selection.lineIndex];
  if (!line) return null;

  const lineStart = deriveLineTimestamp(line);
  const selectedWord = selection.wordIndex === null ? null : line.words[selection.wordIndex];
  const start = selectedWord?.time ?? (selection.wordIndex === null ? lineStart : null);
  if (start === null || start === undefined) return null;

  let end: number | null = null;
  if (selectedWord && selection.wordIndex !== null) {
    for (let wordIndex = selection.wordIndex + 1; wordIndex < line.words.length; wordIndex++) {
      const nextWordTime = line.words[wordIndex].time;
      if (nextWordTime !== null && nextWordTime > start) {
        end = nextWordTime;
        break;
      }
    }
  }

  if (end === null) {
    for (let lineIndex = selection.lineIndex + 1; lineIndex < model.lines.length; lineIndex++) {
      const nextLineTime = deriveLineTimestamp(model.lines[lineIndex]);
      if (nextLineTime !== null && nextLineTime > start) {
        end = nextLineTime;
        break;
      }
    }
  }

  if (end === null) end = duration > start ? duration : start + 0.4;
  return {
    start,
    end,
    kind: selectedWord ? "word" : isInstrumentalLine(model, selection.lineIndex) ? "break" : "line",
  };
}

/** The minimum pointer movement (in pixels) a drag on the line-band strip's
 * background must cross before it counts as "dragging to set a loop"
 * rather than "a click" - below this threshold, `null` is returned so the
 * caller can fall through to click/select handling instead. Below-threshold
 * movement is common jitter on an intended click, not an intentional drag. */
export const LOOP_DRAG_THRESHOLD_PX = 6;

/** Converts a drag gesture on the strip (from `startX` to `currentX`, both
 * in canvas-local pixels) into a loop span in seconds, or `null` if the
 * gesture hasn't moved past the click/drag threshold yet. The returned span
 * is always ordered (start <= end) regardless of drag direction. */
export function computeDragLoop(
  startX: number, currentX: number, viewStart: number, viewEnd: number, widthPx: number,
  thresholdPx: number = LOOP_DRAG_THRESHOLD_PX,
): LoopSpan | null {
  if (Math.abs(currentX - startX) <= thresholdPx) return null;
  const t1 = xToTime(startX, viewStart, viewEnd, widthPx);
  const t2 = xToTime(currentX, viewStart, viewEnd, widthPx);
  return { start: Math.min(t1, t2), end: Math.max(t1, t2) };
}
