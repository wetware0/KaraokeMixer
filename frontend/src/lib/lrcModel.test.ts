import { describe, expect, it } from "vitest";
import { formatTimestamp, parseLrc, renderLrc } from "./lrcModel";
import {
  LrcEditController, NUDGE_STEP_SECONDS, TAP_REACTION_COMPENSATION_SECONDS,
  deriveLineTimestamp, findActiveWord, insertInstrumentalLine, isInstrumentalLine,
  nudgeWordTime, removeInstrumentalLine, setLineStart, setWordTime, tapStamp,
} from "./lrcModel";

describe("formatTimestamp", () => {
  it("formats whole seconds with two-digit minutes/seconds and a centisecond fraction", () => {
    expect(formatTimestamp(0)).toBe("00:00.00");
    expect(formatTimestamp(61.5)).toBe("01:01.50");
  });

  it("does not truncate minutes past 99", () => {
    expect(formatTimestamp(6000)).toBe("100:00.00");
  });
});

describe("parseLrc", () => {
  it("parses an enhanced line into per-word times, preserving inter-word spacing on render", () => {
    const content = "[00:01.00]<00:01.00>Hello<00:01.50> world\n";
    const model = parseLrc(content);

    expect(model.lines).toHaveLength(1);
    const line = model.lines[0];
    expect(line.isLyric).toBe(true);
    expect(line.text).toBe("Hello world");
    expect(line.words).toEqual([
      { text: "Hello", startIndex: 0, endIndex: 5, time: 1.0 },
      { text: "world", startIndex: 6, endIndex: 11, time: 1.5 },
    ]);
    expect(line.lineStart).toBe(1.0);
  });

  it("parses a line-timed (no word tags) line with lineStart but no word times", () => {
    const model = parseLrc("[00:05.00]Some lyrics here\n");
    const line = model.lines[0];
    expect(line.lineStart).toBe(5.0);
    expect(line.words.every((w) => w.time === null)).toBe(true);
    expect(line.words.map((w) => w.text)).toEqual(["Some", "lyrics", "here"]);
  });

  it("parses an untimed lyric line as plain text with no lineStart", () => {
    const model = parseLrc("Some lyrics here\n");
    const line = model.lines[0];
    expect(line.isLyric).toBe(true);
    expect(line.lineStart).toBeNull();
    expect(line.words.every((w) => w.time === null)).toBe(true);
  });

  it("preserves blank lines and [key: value] metadata lines verbatim as non-lyric", () => {
    const model = parseLrc("[ar: ABBA]\n\n[00:01.00]Hi\n");
    expect(model.lines[0]).toMatchObject({ raw: "[ar: ABBA]", isLyric: false });
    expect(model.lines[1]).toMatchObject({ raw: "", isLyric: false });
    expect(model.lines[2].isLyric).toBe(true);
  });

  it("treats a lone trailing carriage return as a line break, not data loss", () => {
    // Regression: a prior version split on /\r\n|\n/ only (lone \r never
    // matched as a separator) while endsWithNewline was computed via a
    // separate endsWith("\r") check - "Hi\r" produced endsWithNewline=true
    // with no split-generated sentinel to drop, so the only real line was
    // sliced away and the model round-tripped to nothing.
    const model = parseLrc("Hi\r");
    expect(model.lines).toHaveLength(1);
    expect(model.lines[0].raw).toBe("Hi");
    expect(model.lines[0].isLyric).toBe(true);
    expect(model.endsWithNewline).toBe(true);
  });

  it("parses a lone mid-content carriage return as a line break distinct from a following \\n", () => {
    const model = parseLrc("A\rB\n");
    expect(model.lines).toHaveLength(2);
    expect(model.lines[0].raw).toBe("A");
    expect(model.lines[1].raw).toBe("B");
  });
});

describe("renderLrc", () => {
  it("round-trips an enhanced line back to the identical tag placement, including the trailing newline", () => {
    const content = "[00:01.00]<00:01.00>Hello<00:01.50> world\n";
    expect(renderLrc(parseLrc(content))).toBe(content);
  });

  it("renders a fully-timed line even when the source had no word tags yet, using the first word's time as the prefix", () => {
    const model = parseLrc("[00:05.00]Hi there\n");
    model.lines[0].words[0].time = 5.0;
    model.lines[0].words[1].time = 5.4;

    expect(renderLrc(model)).toBe("[00:05.00]<00:05.00>Hi<00:05.40> there\n");
  });

  it("preserves per-word timing info for a partially-timed line, tagging only the timed words", () => {
    const model = parseLrc("[00:05.00]Hi there\n");
    model.lines[0].words[0].time = 5.0; // only the first word timed ("there" stays untimed)

    expect(renderLrc(model)).toBe("[00:05.00]<00:05.00>Hi there\n");
  });

  it("round-trips a partially-timed (enhanced-partial) line: parseLrc re-associates each tag to the word it precedes, not by tag-count==token-count", () => {
    let model = parseLrc("Hi there world\n");
    model = setWordTime(model, 0, 1, 3.0); // only the middle word ("there") is timed

    const rendered = renderLrc(model);
    const reparsed = parseLrc(rendered);

    expect(reparsed.lines[0].words.map((w) => w.time)).toEqual([null, 3.0, null]);
    expect(reparsed.lines[0].words.map((w) => w.text)).toEqual(["Hi", "there", "world"]);
    expect(renderLrc(reparsed)).toBe(rendered); // stable round-trip
  });

  it("preserves non-lyric lines verbatim, including a trailing newline", () => {
    const content = "[ar: ABBA]\n\n[00:01.00]Hi\n";
    expect(renderLrc(parseLrc(content))).toBe(content);
  });

  it("does not add a trailing newline when the source content had none", () => {
    const content = "Hi there";
    expect(renderLrc(parseLrc(content))).toBe(content);
  });

  it("round-trips a lone-\\r source without losing content (normalizing \\r to the model's chosen newline, matching Python splitlines semantics)", () => {
    const rendered = renderLrc(parseLrc("Hi\r"));
    expect(rendered).not.toBe("");
    expect(rendered).toBe("Hi\n");
  });
});

describe("setWordTime", () => {
  it("keeps an existing line start independent and clamps its words after it", () => {
    const model = parseLrc("[00:05.00]Hi there\n");
    const next = setWordTime(model, 0, 0, 3.0);
    expect(next.lines[0].words[0].time).toBe(5.0);
    expect(next.lines[0].lineStart).toBe(5.0);
  });

  it("derives a line start when timing a previously untimed line", () => {
    const next = setWordTime(parseLrc("Hi there\n"), 0, 0, 3.0);
    expect(next.lines[0].lineStart).toBe(3.0);
  });

  it("does not mutate the original model (immutable)", () => {
    const model = parseLrc("[00:05.00]Hi there\n");
    setWordTime(model, 0, 0, 3.0);
    expect(model.lines[0].words[0].time).toBeNull();
  });

  it("clamps to the previous timed word's time, never allowing an earlier time", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 0, 2.0);
    model = setWordTime(model, 0, 1, 1.0); // attempt to set word 1 earlier than word 0
    expect(model.lines[0].words[1].time).toBe(2.0);
  });

  it("clamps to the next timed word's time, never allowing a later time", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 1, 5.0);
    model = setWordTime(model, 0, 0, 8.0); // attempt to set word 0 later than word 1
    expect(model.lines[0].words[0].time).toBe(5.0);
  });

  it("clamps across line boundaries, not just within one line", () => {
    let model = parseLrc("Hi\nthere\n");
    model = setWordTime(model, 0, 0, 2.0);
    model = setWordTime(model, 1, 0, 1.0); // earlier than the previous line's word
    expect(model.lines[1].words[0].time).toBe(2.0);
  });
});

describe("setLineStart", () => {
  it("moves a lyric line start independently of its word markers", () => {
    const next = setLineStart(parseLrc("[00:05.00]<00:05.20>Hi<00:05.80> there\n"), 0, 4.5);
    expect(next.lines[0].lineStart).toBe(4.5);
    expect(next.lines[0].words.map((word) => word.time)).toEqual([5.2, 5.8]);
    expect(renderLrc(next)).toBe("[00:04.50]<00:05.20>Hi<00:05.80> there\n");
  });

  it("moves a bare instrumental break while preserving its trailing whitespace", () => {
    const next = setLineStart(parseLrc("[00:03.00] \n"), 0, 4.25);
    expect(renderLrc(next)).toBe("[00:04.25] \n");
  });
});

describe("nudgeWordTime", () => {
  it("moves a timed word by the given delta", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 0, 1.0);
    model = nudgeWordTime(model, 0, 0, NUDGE_STEP_SECONDS);
    expect(model.lines[0].words[0].time).toBeCloseTo(1.01, 5);
  });

  it("treats an untimed word as starting at 0", () => {
    const model = parseLrc("Hi there\n");
    const next = nudgeWordTime(model, 0, 0, NUDGE_STEP_SECONDS);
    expect(next.lines[0].words[0].time).toBeCloseTo(0.01, 5);
  });
});

describe("tapStamp", () => {
  it("stamps the word at tapTime minus the reaction compensation", () => {
    const model = parseLrc("Hi there\n");
    const next = tapStamp(model, 0, 0, 2.0);
    expect(next.lines[0].words[0].time).toBeCloseTo(2.0 - TAP_REACTION_COMPENSATION_SECONDS, 5);
  });

  it("accepts a custom compensation value", () => {
    const model = parseLrc("Hi there\n");
    const next = tapStamp(model, 0, 0, 2.0, 0.2);
    expect(next.lines[0].words[0].time).toBeCloseTo(1.8, 5);
  });
});

describe("deriveLineTimestamp", () => {
  it("returns the first timed word's time when any word is timed", () => {
    const model = setWordTime(parseLrc("Hi there\n"), 0, 1, 3.0);
    expect(deriveLineTimestamp(model.lines[0])).toBe(3.0);
  });

  it("falls back to the line's own lineStart when no word is timed", () => {
    const model = parseLrc("[00:04.00]Hi there\n");
    expect(deriveLineTimestamp(model.lines[0])).toBe(4.0);
  });

  it("uses an independently moved line start before the first word", () => {
    const model = setLineStart(parseLrc("[00:04.00]<00:05.00>Hi there\n"), 0, 3.5);
    expect(deriveLineTimestamp(model.lines[0])).toBe(3.5);
  });
});

describe("LrcEditController", () => {
  it("applies an edit, then undoes and redoes it", () => {
    const initial = parseLrc("Hi there\n");
    const controller = new LrcEditController(initial);

    const edited = setWordTime(controller.model, 0, 0, 1.0);
    controller.apply(edited);
    expect(controller.model.lines[0].words[0].time).toBe(1.0);
    expect(controller.canUndo()).toBe(true);
    expect(controller.canRedo()).toBe(false);

    controller.undo();
    expect(controller.model.lines[0].words[0].time).toBeNull();
    expect(controller.canRedo()).toBe(true);

    controller.redo();
    expect(controller.model.lines[0].words[0].time).toBe(1.0);
  });

  it("clears the redo stack on a new apply after an undo", () => {
    const controller = new LrcEditController(parseLrc("Hi there\n"));
    controller.apply(setWordTime(controller.model, 0, 0, 1.0));
    controller.undo();
    controller.apply(setWordTime(controller.model, 0, 0, 2.0));
    expect(controller.canRedo()).toBe(false);
  });

  it("undo/redo on an empty stack is a no-op", () => {
    const controller = new LrcEditController(parseLrc("Hi there\n"));
    controller.undo();
    controller.redo();
    expect(controller.model.lines[0].words[0].time).toBeNull();
  });
});

describe("findActiveWord", () => {
  it("returns the most recent timed word at or before currentTime", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 0, 1.0);
    model = setWordTime(model, 0, 1, 2.0);

    expect(findActiveWord(model, 1.5)).toEqual({ lineIndex: 0, wordIndex: 0 });
    expect(findActiveWord(model, 2.5)).toEqual({ lineIndex: 0, wordIndex: 1 });
  });

  it("returns null before any timed word has started", () => {
    let model = parseLrc("Hi there\n");
    model = setWordTime(model, 0, 0, 5.0);
    expect(findActiveWord(model, 1.0)).toBeNull();
  });
});

describe("isInstrumentalLine", () => {
  it("identifies a bare-timestamp line (a line timestamp with no words at all) as a break", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:03.00]\n");
    expect(isInstrumentalLine(model, 0)).toBe(false);
    expect(isInstrumentalLine(model, 1)).toBe(true);
    expect(isInstrumentalLine(model, 99)).toBe(false);
    // The bare form parses as non-lyric (empty text) - isInstrumentalLine
    // does not require isLyric, only lineStart + no words.
    expect(model.lines[1]).toMatchObject({ isLyric: false, lineStart: 3.0 });
    expect(model.lines[1].words).toEqual([]);
  });

  it("still identifies a bare-timestamp line with a trailing space as a break", () => {
    const model = parseLrc("[00:18.97] \n");
    expect(isInstrumentalLine(model, 0)).toBe(true);
  });

  it("does not flag an ordinary blank line or [key: value] metadata line (no lineStart)", () => {
    const model = parseLrc("[ar: ABBA]\n\n[00:01.00]Hi\n");
    expect(isInstrumentalLine(model, 0)).toBe(false);
    expect(isInstrumentalLine(model, 1)).toBe(false);
  });

  it("still identifies the legacy single-♪-word form (back-compat with documents saved by a prior version)", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:03.00]<00:03.00>♪\n");
    expect(isInstrumentalLine(model, 0)).toBe(false);
    expect(isInstrumentalLine(model, 1)).toBe(true);
  });
});

describe("insertInstrumentalLine / removeInstrumentalLine", () => {
  it("insertInstrumentalLine inserts a bare timestamp line (no words, no text) after afterLineIndex", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:05.00]<00:05.00>there\n");
    const updated = insertInstrumentalLine(model, 0, 3.0);

    expect(updated.lines).toHaveLength(3);
    expect(updated.lines[1]).toMatchObject({ text: "", isLyric: false, lineStart: 3.0, raw: "[00:03.00]" });
    expect(updated.lines[1].words).toEqual([]);
    // original model is untouched (immutability)
    expect(model.lines).toHaveLength(2);
  });

  it("insertInstrumentalLine(-1, ...) inserts before the first line", () => {
    const model = parseLrc("[00:05.00]<00:05.00>Hi\n");
    const updated = insertInstrumentalLine(model, -1, 1.0);
    expect(updated.lines[0].text).toBe("");
    expect(updated.lines[1].text).toBe("Hi");
  });

  it("writes the bare form regardless of the rest of the document's timing state (word-timed, line-timed, or fully untimed)", () => {
    for (const content of ["[00:01.00]<00:01.00>Hi\n", "[00:01.00]Hi there\n", "Hi there\n"]) {
      const model = parseLrc(content);
      const updated = insertInstrumentalLine(model, -1, 2.0);
      expect(updated.lines[0]).toMatchObject({ text: "", isLyric: false, lineStart: 2.0 });
      expect(updated.lines[0].words).toEqual([]);
      expect(renderLrc(updated)).toContain("[00:02.00]");
      expect(renderLrc(updated)).not.toMatch(/[♪]/);
    }
  });

  it("round-trips a newly inserted break line through renderLrc + parseLrc as the same bare timestamp shape", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n");
    const updated = insertInstrumentalLine(model, 0, 3.5);
    const rendered = renderLrc(updated);

    expect(rendered).toContain("[00:03.50]");
    expect(rendered).not.toContain("♪");

    const reparsed = parseLrc(rendered);
    expect(reparsed.lines[1]).toMatchObject({ text: "", isLyric: false, lineStart: 3.5 });
    expect(reparsed.lines[1].words).toEqual([]);
    expect(isInstrumentalLine(reparsed, 1)).toBe(true);
  });

  it("removeInstrumentalLine removes a bare-timestamp break line", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:03.00]\n");
    const updated = removeInstrumentalLine(model, 1);
    expect(updated.lines).toHaveLength(1);
    expect(updated.lines[0].text).toBe("Hi");
    // original untouched
    expect(model.lines).toHaveLength(2);
  });

  it("removeInstrumentalLine still removes the legacy single-♪-word form", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n[00:03.00]<00:03.00>♪\n");
    const updated = removeInstrumentalLine(model, 1);
    expect(updated.lines).toHaveLength(1);
    expect(updated.lines[0].text).toBe("Hi");
  });

  it("removeInstrumentalLine throws when the target line is not an instrumental marker", () => {
    const model = parseLrc("[00:01.00]<00:01.00>Hi\n");
    expect(() => removeInstrumentalLine(model, 0)).toThrow();
  });

  it("undo/redo round-trips an instrumental insert+remove through LrcEditController", () => {
    const controller = new LrcEditController(parseLrc("[00:01.00]<00:01.00>Hi\n"));
    controller.apply(insertInstrumentalLine(controller.model, 0, 5.0));
    expect(controller.model.lines).toHaveLength(2);

    controller.undo();
    expect(controller.model.lines).toHaveLength(1);

    controller.redo();
    expect(controller.model.lines).toHaveLength(2);

    controller.apply(removeInstrumentalLine(controller.model, 1));
    expect(controller.model.lines).toHaveLength(1);
    controller.undo();
    expect(controller.model.lines).toHaveLength(2);
  });

  // MINOR fold: the explicit insert -> render -> parse -> remove -> render
  // round trip, byte-for-byte back to the original source.
  it("insert then remove round-trips back to the exact original rendered bytes", () => {
    const original = "[00:01.00]<00:01.00>Hi\n[00:05.00]<00:05.00>there\n";
    const model = parseLrc(original);
    const inserted = insertInstrumentalLine(model, 0, 3.0);
    const reparsedInserted = parseLrc(renderLrc(inserted));
    const removed = removeInstrumentalLine(reparsedInserted, 1);
    expect(renderLrc(removed)).toBe(original);
  });

  // The user's real .lrc files already contain bare-timestamp break lines,
  // some with a trailing space after the timestamp - both shapes must
  // survive parse -> render byte-for-byte, since renderLrc emits `line.raw`
  // verbatim for any non-lyric line (which a bare break always is - see
  // isInstrumentalLine's doc comment on why words.length===0 implies
  // text==="").
  it("round-trips a real-file-shaped document with mixed word-timed lines and both bare-break spellings byte-identically", () => {
    const content =
      "[00:01.00]<00:01.00>Hi<00:01.50> there\n" +
      "[00:18.97] \n" +
      "[00:20.00]<00:20.00>Second<00:20.50> verse\n" +
      "[03:16.96]\n";
    const model = parseLrc(content);

    expect(isInstrumentalLine(model, 1)).toBe(true);
    expect(isInstrumentalLine(model, 3)).toBe(true);
    expect(model.lines[1].raw).toBe("[00:18.97] "); // trailing space preserved
    expect(model.lines[3].raw).toBe("[03:16.96]"); // no trailing space

    expect(renderLrc(model)).toBe(content);
  });
});
