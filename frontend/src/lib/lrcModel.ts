// Mirrors backend/app/lyrics/alignment.py's regexes and tag-placement
// convention exactly - see this file's Task 11 "Format contract" note.
const LINE_TIMESTAMP_RE = /^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/;
const WORD_TIMESTAMP_RE = /<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>/g;
const METADATA_RE = /^\[[A-Za-z][A-Za-z0-9_-]*:.*\]$/;
const TOKEN_RE = /\S+/g;

function parseTimestamp(minutes: string, seconds: string, fraction: string | undefined): number {
  const fractionText = fraction ?? "0";
  const fractionValue = Number(fractionText) / Math.pow(10, fractionText.length);
  return Number(minutes) * 60 + Number(seconds) + fractionValue;
}

export function formatTimestamp(seconds: number): string {
  const centiseconds = Math.max(0, Math.round(seconds * 100));
  const minutes = Math.floor(centiseconds / 6000);
  const remainder = centiseconds % 6000;
  const wholeSeconds = Math.floor(remainder / 100);
  const fraction = remainder % 100;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(minutes)}:${pad(wholeSeconds)}.${pad(fraction)}`;
}

export interface LrcWord {
  text: string;
  startIndex: number; // offset into the line's tag-stripped `text`
  endIndex: number;
  time: number | null; // seconds; null when not yet timed
}

export interface LrcLine {
  raw: string; // verbatim original line - used unchanged for non-lyric lines on render
  text: string; // plain lyric text with all timestamp tags stripped
  isLyric: boolean;
  lineStart: number | null;
  words: LrcWord[];
}

export interface LrcModel {
  lines: LrcLine[];
  newline: string; // "\n" or "\r\n" - whichever the source content used
  endsWithNewline: boolean; // reproduced on render, mirroring alignment.py's ends_with_newline
}

function tokenize(text: string): { text: string; startIndex: number; endIndex: number }[] {
  const tokens: { text: string; startIndex: number; endIndex: number }[] = [];
  TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TOKEN_RE.exec(text)) !== null) {
    tokens.push({ text: match[0], startIndex: match.index, endIndex: match.index + match[0].length });
  }
  return tokens;
}

/** Associates each word tag in `remainder` (the line's content, still
 * carrying its `<mm:ss.xx>` tags) with the token it directly precedes in the
 * already tag-stripped `text`, by comparing character offsets rather than
 * assuming "the Nth tag times the Nth word" - that assumption breaks for a
 * partially-timed line, where a tag can be present for some words and
 * omitted for others (see renderLrc's enhanced-partial form), so tag count
 * and token count no longer match up 1:1 by position. */
function buildWords(remainder: string, text: string): LrcWord[] {
  const strippedFull = remainder.replace(WORD_TIMESTAMP_RE, "");
  const leadingTrim = strippedFull.length - strippedFull.trimStart().length;

  const tags: { offset: number; time: number }[] = [];
  WORD_TIMESTAMP_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  let strippedOffset = 0;
  let remainderPos = 0;
  while ((match = WORD_TIMESTAMP_RE.exec(remainder)) !== null) {
    strippedOffset += match.index - remainderPos;
    tags.push({ offset: strippedOffset - leadingTrim, time: parseTimestamp(match[1], match[2], match[3]) });
    remainderPos = match.index + match[0].length;
  }

  let tagIndex = 0;
  return tokenize(text).map((token) => {
    let time: number | null = null;
    while (tagIndex < tags.length && tags[tagIndex].offset <= token.startIndex) {
      time = tags[tagIndex].time;
      tagIndex++;
    }
    return { ...token, time };
  });
}

function parseLine(raw: string): LrcLine {
  const lineMatch = LINE_TIMESTAMP_RE.exec(raw);
  if (lineMatch) {
    const lineStart = parseTimestamp(lineMatch[1], lineMatch[2], lineMatch[3]);
    const remainder = raw.slice(lineMatch[0].length);
    const text = remainder.replace(WORD_TIMESTAMP_RE, "").trim();
    const isLyric = text.length > 0;
    return {
      raw, text, isLyric, lineStart,
      words: isLyric ? buildWords(remainder, text) : [],
    };
  }

  const trimmed = raw.trim();
  if (!trimmed || METADATA_RE.test(trimmed)) {
    return { raw, text: "", isLyric: false, lineStart: null, words: [] };
  }

  // No line timestamp: any word tags here are orphaned (the backend
  // classifier treats a word tag with no line stamp as UNKNOWN), so - same
  // as before this fix - they are not parsed into word times. Passing `text`
  // as both arguments means buildWords finds no tags in it (they only exist
  // in the untrimmed `raw`), so every word comes back untimed, unchanged
  // from the prior behavior.
  const text = raw.replace(WORD_TIMESTAMP_RE, "").trim();
  const isLyric = text.length > 0;
  return { raw, text, isLyric, lineStart: null, words: isLyric ? buildWords(text, text) : [] };
}

export function parseLrc(content: string): LrcModel {
  const newline = content.includes("\r\n") ? "\r\n" : "\n";
  // Split on \r\n, lone \r, or lone \n as a line break - matching Python's
  // str.splitlines() (which alignment.py relies on and which treats a bare
  // \r as a terminator too). A prior version split on /\r\n|\n/ only, so a
  // lone \r never matched as a separator, yet endsWithNewline was still
  // computed via a separate endsWith("\r") check - for content like "Hi\r"
  // that combination produced endsWithNewline=true while .split() had not
  // produced a trailing sentinel to drop, so the slice(0, -1) below deleted
  // the only real line. Deriving endsWithNewline from the split's own
  // trailing "" sentinel (instead of a separate endsWith check) keeps the
  // two facts in sync by construction.
  const rawLines = content.split(/\r\n|\r|\n/);
  // .split() leaves a trailing "" element when content ends with a line
  // break; drop it here so "A\n" parses as exactly one line. endsWithNewline
  // reattaches a trailing newline on render instead, mirroring
  // alignment.AlignmentDocument's newline/ends_with_newline fields. Note: a
  // lone \r is only a *parse-time* line break - renderLrc always emits the
  // model's chosen `newline` ("\n" or "\r\n"), so a lone-\r source is
  // normalized to that newline on render, matching Python splitlines
  // semantics (which does not preserve which terminator variant was used).
  const endsWithNewline = content !== "" && rawLines[rawLines.length - 1] === "";
  const lineTexts = endsWithNewline ? rawLines.slice(0, -1) : rawLines;
  return { lines: lineTexts.map(parseLine), newline, endsWithNewline };
}

export function renderLrc(model: LrcModel): string {
  const body = model.lines
    .map((line) => {
      if (!line.isLyric) return line.raw;

      const allTimed = line.words.length > 0 && line.words.every((word) => word.time !== null);
      if (allTimed) {
        const prefixTime = line.lineStart ?? (line.words[0].time as number);
        let output = `[${formatTimestamp(prefixTime)}]`;
        let cursor = 0;
        for (const word of line.words) {
          output += `<${formatTimestamp(word.time as number)}>`;
          output += line.text.slice(cursor, word.startIndex);
          output += word.text;
          cursor = word.endIndex;
        }
        output += line.text.slice(cursor);
        return output;
      }

      // Partially timed: some words have a time, some don't. Discarding all
      // tags here (falling straight to the line-timed/plain branches below)
      // would silently lose per-word timing progress made so far. Preserve
      // it instead: emit a <ts> tag before each word that HAS a time, and
      // simply omit the tag for words that don't - this is the same
      // rendering loop as the all-timed branch above, just with the tag
      // made conditional. Requires a line timestamp to anchor the line (in
      // practice always true here: setWordTime/nudgeWordTime/tapStamp all
      // recompute lineStart from the first timed word whenever any word
      // gets timed).
      const anyTimed = line.words.some((word) => word.time !== null);
      if (anyTimed && line.lineStart !== null) {
        let output = `[${formatTimestamp(line.lineStart)}]`;
        let cursor = 0;
        for (const word of line.words) {
          if (word.time !== null) output += `<${formatTimestamp(word.time)}>`;
          output += line.text.slice(cursor, word.startIndex);
          output += word.text;
          cursor = word.endIndex;
        }
        output += line.text.slice(cursor);
        return output;
      }

      if (line.lineStart !== null) return `[${formatTimestamp(line.lineStart)}]${line.text}`;
      return line.text;
    })
    .join(model.newline);

  return body + (model.lines.length > 0 && model.endsWithNewline ? model.newline : "");
}

function cloneModel(model: LrcModel): LrcModel {
  return {
    ...model,
    lines: model.lines.map((line) => ({ ...line, words: line.words.map((word) => ({ ...word })) })),
  };
}

interface FlatWordRef {
  lineIndex: number;
  wordIndex: number;
}

function flattenWords(model: LrcModel): FlatWordRef[] {
  const refs: FlatWordRef[] = [];
  model.lines.forEach((line, lineIndex) => {
    line.words.forEach((_word, wordIndex) => refs.push({ lineIndex, wordIndex }));
  });
  return refs;
}

function neighborTimes(model: LrcModel, lineIndex: number, wordIndex: number): { previous: number | null; next: number | null } {
  const flat = flattenWords(model);
  const position = flat.findIndex((ref) => ref.lineIndex === lineIndex && ref.wordIndex === wordIndex);

  let previous: number | null = null;
  for (let i = position - 1; i >= 0; i--) {
    const ref = flat[i];
    const time = model.lines[ref.lineIndex].words[ref.wordIndex].time;
    if (time !== null) {
      previous = time;
      break;
    }
  }

  let next: number | null = null;
  for (let i = position + 1; i < flat.length; i++) {
    const ref = flat[i];
    const time = model.lines[ref.lineIndex].words[ref.wordIndex].time;
    if (time !== null) {
      next = time;
      break;
    }
  }

  return { previous, next };
}

export function deriveLineTimestamp(line: LrcLine): number | null {
  const firstTimedWord = line.words.find((word) => word.time !== null);
  return line.lineStart ?? (firstTimedWord ? (firstTimedWord.time as number) : null);
}

function recomputeLineStart(line: LrcLine): LrcLine {
  return { ...line, lineStart: deriveLineTimestamp(line) };
}

export function setWordTime(model: LrcModel, lineIndex: number, wordIndex: number, time: number): LrcModel {
  const { previous, next } = neighborTimes(model, lineIndex, wordIndex);
  const lowerBound = Math.max(previous ?? 0, model.lines[lineIndex].lineStart ?? 0);
  const upperBound = next ?? Infinity;
  const clamped = Math.min(Math.max(time, lowerBound), upperBound);

  const updated = cloneModel(model);
  updated.lines[lineIndex].words[wordIndex].time = clamped;
  if (updated.lines[lineIndex].lineStart === null) {
    updated.lines[lineIndex] = recomputeLineStart(updated.lines[lineIndex]);
  }
  return updated;
}

export function setLineStart(model: LrcModel, lineIndex: number, time: number): LrcModel {
  let previous: number | null = null;
  for (let index = lineIndex - 1; index >= 0; index--) {
    if (model.lines[index].lineStart !== null) {
      previous = model.lines[index].lineStart;
      break;
    }
  }
  let next: number | null = null;
  for (let index = lineIndex + 1; index < model.lines.length; index++) {
    if (model.lines[index].lineStart !== null) {
      next = model.lines[index].lineStart;
      break;
    }
  }

  const firstTimedWord = model.lines[lineIndex].words.find((word) => word.time !== null)?.time ?? Infinity;
  const upperBound = Math.min(next ?? Infinity, firstTimedWord);
  const clamped = Math.min(Math.max(time, previous ?? 0), upperBound);
  const updated = cloneModel(model);
  const line = updated.lines[lineIndex];
  line.lineStart = clamped;
  if (!line.isLyric) {
    const stamp = `[${formatTimestamp(clamped)}]`;
    line.raw = LINE_TIMESTAMP_RE.test(line.raw) ? line.raw.replace(LINE_TIMESTAMP_RE, stamp) : `${stamp}${line.raw}`;
  }
  return updated;
}

export const NUDGE_STEP_SECONDS = 0.01;

export function nudgeWordTime(model: LrcModel, lineIndex: number, wordIndex: number, deltaSeconds: number): LrcModel {
  const current = model.lines[lineIndex].words[wordIndex].time ?? 0;
  return setWordTime(model, lineIndex, wordIndex, current + deltaSeconds);
}

export const TAP_REACTION_COMPENSATION_SECONDS = 0.1;

export function tapStamp(
  model: LrcModel, lineIndex: number, wordIndex: number, tapTime: number,
  compensationSeconds: number = TAP_REACTION_COMPENSATION_SECONDS,
): LrcModel {
  return setWordTime(model, lineIndex, wordIndex, tapTime - compensationSeconds);
}

export class LrcEditController {
  private current: LrcModel;
  private undoStack: LrcModel[] = [];
  private redoStack: LrcModel[] = [];

  constructor(initial: LrcModel) {
    this.current = initial;
  }

  get model(): LrcModel {
    return this.current;
  }

  apply(next: LrcModel): void {
    this.undoStack.push(this.current);
    this.redoStack = [];
    this.current = next;
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  undo(): void {
    const previous = this.undoStack.pop();
    if (previous === undefined) return;
    this.redoStack.push(this.current);
    this.current = previous;
  }

  redo(): void {
    const next = this.redoStack.pop();
    if (next === undefined) return;
    this.undoStack.push(this.current);
    this.current = next;
  }
}

/** Legacy glyph for an instrumental-section marker line, as a prior version
 * of this editor used to WRITE it: a single-word line rendering as either
 * `[mm:ss.xx]<mm:ss.xx>♪` or the bare `[mm:ss.xx]♪`. `insertInstrumentalLine`
 * no longer produces this form - a break is now always written as a bare
 * `[mm:ss.xx]` line with no word at all (see `insertInstrumentalLine`),
 * matching how real .lrc files actually represent a break: a line
 * consisting of only a start timestamp, with the file NEVER containing "♪"
 * or "[break]". `isInstrumentalLine` still recognizes this legacy glyph form
 * for back-compat with documents saved by that prior version. */
export const INSTRUMENTAL_GLYPH = "♪";

/** True when the line at `lineIndex` is an instrumental break: either the
 * canonical on-disk form (a line with a line timestamp and no words at all -
 * a bare `[mm:ss.xx]` line, since a truly empty/whitespace-only remainder
 * never tokenizes into any word), or the legacy single-♪-word form written
 * by a prior version of this editor (kept for back-compat with previously
 * saved documents; `insertInstrumentalLine` never writes it again). */
export function isInstrumentalLine(model: LrcModel, lineIndex: number): boolean {
  const line = model.lines[lineIndex];
  if (!line || line.lineStart === null) return false;
  if (line.words.length === 0) return true;
  return line.words.length === 1 && line.words[0].text === INSTRUMENTAL_GLYPH;
}

/** Inserts a new break marker line - a bare `[mm:ss.xx]` line with no words
 * and no text - immediately after `afterLineIndex` (-1 inserts before the
 * first line). This is the canonical on-disk shape for a break in a real
 * .lrc file: a line consisting of only a start timestamp with nothing
 * after it. The "[break]" label and × control are purely display-side
 * substitutions (see KaraokeDisplay/LineBandStrip) - the file itself never
 * contains "♪" or "[break]". */
export function insertInstrumentalLine(model: LrcModel, afterLineIndex: number, timeSeconds: number): LrcModel {
  const newLine: LrcLine = {
    raw: `[${formatTimestamp(timeSeconds)}]`,
    text: "",
    isLyric: false,
    lineStart: timeSeconds,
    words: [],
  };
  const updated = cloneModel(model);
  updated.lines.splice(afterLineIndex + 1, 0, newLine);
  return updated;
}

/** Removes an instrumental marker line (bare-timestamp or legacy ♪ form).
 * Throws if the line at `lineIndex` is not one (per `isInstrumentalLine`) -
 * the UI must never offer removal for an ordinary lyric line, but this
 * guards the model layer regardless. */
export function removeInstrumentalLine(model: LrcModel, lineIndex: number): LrcModel {
  if (!isInstrumentalLine(model, lineIndex)) {
    throw new Error(`Line ${lineIndex} is not an instrumental line`);
  }
  const updated = cloneModel(model);
  updated.lines.splice(lineIndex, 1);
  return updated;
}

export interface ActiveWordRef {
  lineIndex: number;
  wordIndex: number;
}

export function findActiveWord(model: LrcModel, currentTime: number): ActiveWordRef | null {
  let active: ActiveWordRef | null = null;
  let bestTime = -Infinity;
  model.lines.forEach((line, lineIndex) => {
    line.words.forEach((word, wordIndex) => {
      if (word.time !== null && word.time <= currentTime && word.time > bestTime) {
        bestTime = word.time;
        active = { lineIndex, wordIndex };
      }
    });
  });
  return active;
}

/** Finds the most recent lyric line whose line timestamp has started. This
 * is the playback fallback for line-timed LRCs, where no individual word has
 * a timestamp and findActiveWord() therefore cannot identify anything. */
export function findActiveLine(model: LrcModel, currentTime: number): number | null {
  let activeLineIndex: number | null = null;
  let bestTime = -Infinity;
  model.lines.forEach((line, lineIndex) => {
    if (line.isLyric && line.lineStart !== null && line.lineStart <= currentTime && line.lineStart > bestTime) {
      activeLineIndex = lineIndex;
      bestTime = line.lineStart;
    }
  });
  return activeLineIndex;
}
