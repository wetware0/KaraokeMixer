import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import LyricEditor from "./LyricEditor.svelte";
import { fetchLrc } from "../api";
import { makeFakeAudioBuffer, makeFakeEngine } from "../audio/testFakes";
import * as waveform from "../audio/waveform";
import { TAP_OFFSET_STORAGE_KEY } from "../tapOffsetStore";
import type { Track } from "../types";

const { mockSaveLrc, mockSubmitJob, mockFetchJob, mockConfirmLyricTimingQuality, completionCallbacks, backgroundCompletionCallbacks } = vi.hoisted(() => ({
  mockSaveLrc: vi.fn().mockResolvedValue({ path: "D:/Media/Song.lrc" }),
  mockSubmitJob: vi.fn().mockResolvedValue({ job_id: 42 }),
  mockFetchJob: vi.fn().mockResolvedValue({ id: 42, status: "running", items: [] }),
  mockConfirmLyricTimingQuality: vi.fn(),
  completionCallbacks: new Map<number, () => void>(),
  backgroundCompletionCallbacks: new Set<(jobIds: readonly number[]) => void>(),
}));

vi.mock("../api", () => ({
  fetchTrackParts: vi.fn().mockResolvedValue([
    { part: "lead_vocals", exists: true, duration: 10 },
    { part: "original", exists: true, duration: 10 },
  ]),
  partAudioUrl: (trackId: number, part: string) => `/api/audio/${trackId}/part/${part}`,
  fetchLrc: vi.fn().mockResolvedValue({ exists: true, content: "Hi there\n", state: "untimed" }),
  saveLrc: mockSaveLrc,
  submitJob: mockSubmitJob,
  fetchJob: mockFetchJob,
  confirmLyricTimingQuality: mockConfirmLyricTimingQuality,
}));

vi.mock("../jobsStore.svelte", () => ({
  jobsStore: {
    jobs: [],
    onJobCompleted: vi.fn((callback: (jobIds: readonly number[]) => void) => {
      backgroundCompletionCallbacks.add(callback);
      return () => backgroundCompletionCallbacks.delete(callback);
    }),
    onJobCompletedFor: vi.fn((jobId: number, callback: () => void) => {
      completionCallbacks.set(jobId, callback);
      return () => completionCallbacks.delete(jobId);
    }),
    onTrackChanged: vi.fn(() => () => {}),
  },
}));

const fakeCtx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };

const track: Track = {
  id: 1, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
  outputs: {
    instrumental: false, vocals: false, lead_vocals: true, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: true,
  },
  lrc_state: "untimed", stem_count: 1,
};

// vi.restoreAllMocks() is deliberately NOT called here (unlike most other
// component test files in this plan): fetchTrackParts/fetchLrc/saveLrc are
// configured once, above, via the module-level vi.mock(...) factory rather
// than re-applied per test (there's no need for per-test variation - every
// test in this file wants the same canned track/parts/lrc). restoreAllMocks
// would call .mockRestore() on those vi.fn()s too, wiping their
// mockResolvedValue and leaving every test after the first with `undefined`
// responses. vi.clearAllMocks() only resets call history (so each test's
// `mock.calls[0]` is its own first call, not one left over from a previous
// test) without touching any mock's configured implementation, which is
// exactly what every test here needs.
afterEach(() => {
  vi.clearAllMocks();
  completionCallbacks.clear();
  backgroundCompletionCallbacks.clear();
  localStorage.clear();
});

async function renderEditor(whisperxAvailable: boolean | null = null, overrides: Partial<Record<string, unknown>> = {}) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
  });
  const engine = makeFakeEngine();
  const result = render(LyricEditor, { props: { track, whisperxAvailable, engineFactory: () => engine, ...overrides } });
  await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());
  return { ...result, engine };
}

describe("LyricEditor", () => {
  it("requires confirmation before marking enhanced timing High Quality", async () => {
    const enhancedTrack = { ...track, lrc_state: "enhanced" as const };
    mockConfirmLyricTimingQuality.mockResolvedValueOnce({
      ...enhancedTrack,
      lyric_timing_provenance: {
        schema_version: 1,
        part: "lyrics",
        quality: "high_quality",
        timing_state: "enhanced",
        lrc_sha256: "abc",
        engine: "manual_review",
        model: null,
        method: "listen_through",
        device: null,
        words: 2,
        matched: 2,
        interpolated: 0,
        coverage: 1,
        median_confidence: null,
        low_confidence_words: 0,
        attribution: "manual",
        confirmed_by: "user",
        recorded_at: "2026-08-15T00:00:00Z",
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await renderEditor(null, { track: enhancedTrack });

    await fireEvent.click(screen.getByText("Confirm High Quality timing"));

    await waitFor(() => expect(mockConfirmLyricTimingQuality).toHaveBeenCalledWith(1));
    expect(screen.getByText("High Quality timing ✓")).toBeTruthy();
    expect(screen.getByText(/recorded for this exact LRC file/)).toBeTruthy();
  });

  it("reloads changed lyrics when another background job completes for the open track", async () => {
    await renderEditor();
    mockFetchJob.mockResolvedValueOnce({
      id: 77, recipe: "lyrics_only", status: "completed", items: [{ track_id: 1 }],
    });
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true, content: "[00:01.00]<00:01.00>Fresh<00:01.50> words\n", state: "enhanced",
    });

    backgroundCompletionCallbacks.forEach((callback) => callback([77]));

    await waitFor(() => expect(screen.getByText("Fresh")).toBeTruthy());
    expect(screen.getByText(/Lyrics refreshed after background processing completed/)).toBeTruthy();
    expect(vi.mocked(fetchLrc)).toHaveBeenCalledTimes(2);
  });

  it("refreshes a new confidence report even when no word timestamp changed", async () => {
    await renderEditor();
    mockFetchJob.mockResolvedValueOnce({
      id: 80, recipe: "improve_lyrics", status: "completed", items: [{ track_id: 1 }],
    });
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "Hi there\n",
      state: "untimed",
      timing_report: {
        summary: {
          schema_version: 2, part: "lyrics", quality: "review", timing_state: "enhanced",
          lrc_sha256: "a".repeat(64), engine: "whisperx", model: "align",
          method: "dual_audio_consensus_v1", device: "cuda", words: 2, matched: 2,
          interpolated: 0, coverage: 1, median_confidence: 0.81,
          low_confidence_words: 1, confidence_score: 81, verified_words: 1,
          review_words: 1, corrected_words: 0, review_lines: 1,
          attribution: "automatic", confirmed_by: null, recorded_at: "2026-08-15T00:00:00Z",
        },
        words: [{
          word_number: 1, line_index: 0, word_index: 0, word: "Hi",
          previous_seconds: 0, selected_seconds: 0, original_seconds: 0,
          residual_seconds: 1, agreement_seconds: 1, original_score: 0.5,
          residual_score: 0.5, confidence: 42, status: "review", corrected: false,
        }],
      },
    });

    backgroundCompletionCallbacks.forEach((callback) => callback([80]));

    await waitFor(() => expect(screen.getByText(/Confidence 81\/100/)).toBeTruthy());
    expect(screen.getByText("Hi").className).toContain("karaoke-word-review");
    expect(screen.getByText(/confidence refreshed after background processing completed/)).toBeTruthy();
  });

  it("ignores background jobs for a different track", async () => {
    await renderEditor();
    mockFetchJob.mockResolvedValueOnce({
      id: 78, recipe: "lyrics_only", status: "completed", items: [{ track_id: 999 }],
    });

    backgroundCompletionCallbacks.forEach((callback) => callback([78]));

    await waitFor(() => expect(mockFetchJob).toHaveBeenCalledWith(78));
    expect(vi.mocked(fetchLrc)).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Lyrics refreshed after background processing completed/)).toBeNull();
  });

  it("preserves unsaved edits until the user chooses to load newer background lyrics", async () => {
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    mockFetchJob.mockResolvedValueOnce({
      id: 79, recipe: "lyrics_only", status: "completed", items: [{ track_id: 1 }],
    });
    const latest = {
      exists: true, content: "[00:01.00]<00:01.00>Newest<00:01.50> lyrics\n", state: "enhanced" as const,
    };
    vi.mocked(fetchLrc).mockResolvedValueOnce(latest).mockResolvedValueOnce(latest);

    backgroundCompletionCallbacks.forEach((callback) => callback([79]));

    await waitFor(() => expect(screen.getByText("Load latest lyrics")).toBeTruthy());
    expect(screen.getByText("Hi")).toBeTruthy();
    expect(screen.queryByText("Newest")).toBeNull();

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await fireEvent.click(screen.getByText("Load latest lyrics"));
    await waitFor(() => expect(screen.getByText("Newest")).toBeTruthy());
    expect(screen.getByText(/Latest background lyrics loaded/)).toBeTruthy();
  });

  it("loads the best-available source (lead_vocals over original) and renders the karaoke pane", async () => {
    await renderEditor();
    expect(screen.getByText("Hi")).toBeTruthy();
    expect(screen.getByText("there")).toBeTruthy();
  });

  it("wraps the karaoke pane in a scrollable lyric box so the chart above stays visible", async () => {
    const { container } = await renderEditor();
    const box = container.querySelector(".lyric-scroll-box");
    expect(box).toBeTruthy();
    expect(box?.querySelector(".karaoke-display")).toBeTruthy();
  });

  it("clicking a word inside the scroll box still selects it (word click/zoom unaffected by the wrapper)", async () => {
    const { container } = await renderEditor();
    const box = container.querySelector(".lyric-scroll-box") as HTMLElement;

    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    await fireEvent.click(screen.getByText("there"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content).toContain("<00:00.01>Hi<00:00.01> there");
    // sanity: the word buttons really are inside the box, not rendered elsewhere
    expect(box.textContent).toContain("Hi");
    expect(box.textContent).toContain("there");
  });

  it("scrolls the box (not the page) when the active line changes during playback: the active line's element sits inside .lyric-scroll-box, and scrollIntoView fires on the line change", async () => {
    // jsdom's scrollIntoView is a no-op with no real layout/scrollTop, so
    // the mechanism is verified the same way KaraokeDisplay.test.ts already
    // does: spy on scrollIntoView and assert it fires on an active-line
    // change. What THIS test adds is the integration point - confirming
    // that element is actually inside .lyric-scroll-box (the nearest
    // overflow:auto ancestor once wrapped, per app.css), which is what
    // makes that scrollIntoView call target the box instead of the page.
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "[00:01.00]<00:01.00>Hi<00:01.50> there\n[00:05.00]<00:05.00>Bye<00:05.50> now\n",
      state: "enhanced",
    });
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
      getBuffer: () => makeFakeAudioBuffer({ duration: 10 }),
      getDuration: () => 10,
    });

    const { container } = render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    expect(screen.getByText("Hi").closest(".lyric-scroll-box")).toBe(container.querySelector(".lyric-scroll-box"));

    await fireEvent.click(screen.getByText("Play"));
    simulateTick?.(1.2); // activates line 0 ("Hi there")
    await Promise.resolve();
    expect(scrollSpy).toHaveBeenCalledTimes(1);

    simulateTick?.(5.2); // activates line 1 ("Bye now") - one more scroll
    await Promise.resolve();
    expect(scrollSpy).toHaveBeenCalledTimes(2);
  });

  it("clicking a word in the karaoke pane selects it, enabling arrow-key nudge", async () => {
    await renderEditor();

    // lrcModel's own contract (Tasks 11-12) only renders enhanced <mm:ss.xx>
    // word tags once EVERY word in the line is timed; a line with only one
    // word timed falls back to a bare [mm:ss.xx]text render instead. So both
    // words of "Hi there" are nudged here, not just one - this is what makes
    // the enhanced-render assertion below legitimate rather than
    // contradicting that contract.
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" }); // word 0: 0 -> 0.01
    await fireEvent.click(screen.getByText("there"));
    await fireEvent.keyDown(window, { key: "ArrowRight" }); // word 1: clamped up to word 0's 0.01

    // No direct getter on the DOM for word time; verify indirectly via Save
    // sending the rendered content.
    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content).toContain("<00:00.01>Hi<00:00.01> there");
  });

  it("toggles tap mode with the T key and stamps successive words on Space", async () => {
    const { engine } = await renderEditor();
    const getCurrentTimeSpy = vi.spyOn(engine, "getCurrentTime");

    // Same reasoning as the nudge test above: both words of the line need a
    // time before renderLrc legitimately produces the enhanced <mm:ss.xx>
    // form, so this simulates two successive Space taps (as tap-along
    // typing would), not just one.
    await fireEvent.keyDown(window, { key: "t" });
    getCurrentTimeSpy.mockReturnValue(1.5);
    await fireEvent.keyDown(window, { code: "Space" }); // stamps "Hi" at 1.5 - 0.1 = 1.4
    getCurrentTimeSpy.mockReturnValue(2.0);
    await fireEvent.keyDown(window, { code: "Space" }); // stamps "there" at 2.0 - 0.1 = 1.9
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content).toContain("<00:01.40>Hi<00:01.90> there");
  });

  it("undo reverts the last edit", async () => {
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    await fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content).toBe("Hi there\n");
  });

  it("Save calls saveLrc with the rendered LRC content and clears the dirty marker", async () => {
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Unsaved changes")).toBeTruthy();

    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalledWith(1, expect.stringContaining("Hi")));
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("undoing back to the original content clears the dirty marker (dirty is a baseline comparison, not an edit-happened flag)", async () => {
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Unsaved changes")).toBeTruthy();

    await fireEvent.keyDown(window, { key: "z", ctrlKey: true }); // undo back to the original, untouched state

    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("an edit made after a successful Save makes the document dirty again", async () => {
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());

    await fireEvent.click(screen.getByText("there"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    expect(screen.getByText("Unsaved changes")).toBeTruthy();
  });

  it("prompts for confirmation before navigating back with unsaved changes", async () => {
    const onBack = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(LyricEditor, { props: { track, onBack, engineFactory: () => makeFakeEngine() } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    await fireEvent.click(screen.getByText("← Back"));

    expect(confirmSpy).toHaveBeenCalled();
    expect(onBack).not.toHaveBeenCalled();
  });

  it("renders an error message when Save fails, and clears it on a successful retry", async () => {
    mockSaveLrc.mockRejectedValueOnce(new Error("disk full"));
    await renderEditor();
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(screen.getByText("disk full")).toBeTruthy());
    expect(screen.getByText("Unsaved changes")).toBeTruthy(); // still dirty

    await fireEvent.click(screen.getByText("Save")); // retry, now resolves per the default mock
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
    expect(screen.queryByText("disk full")).toBeNull();
  });

  it("shows per-stage load progress text: Fetching audio… then Decoding N of M…", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });

    let resolveLoad: () => void = () => {};
    const deferred = new Promise<void>((resolve) => {
      resolveLoad = resolve;
    });
    let progressCb: ((loaded: number, total: number) => void) | undefined;
    const engine = makeFakeEngine({
      load: async (tracks, onProgress) => {
        progressCb = onProgress;
        await deferred;
      },
      // The bespoke load() above never populates the base fake's internal
      // buffers map, so give getBuffer/getDuration something real to read
      // once loading "completes".
      getBuffer: () => makeFakeAudioBuffer({ duration: 3 }),
      getDuration: () => 3,
    });

    render(LyricEditor, { props: { track, engineFactory: () => engine } });

    await waitFor(() => expect(screen.getByText("Fetching audio…")).toBeTruthy());

    progressCb?.(1, 1);
    await waitFor(() => expect(screen.getByText("Decoding 1 of 1…")).toBeTruthy());

    resolveLoad();
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());
  });

  it("clicking Play seeks to viewStart and starts playback when no word is selected", async () => {
    const { engine } = await renderEditor();
    const seekSpy = vi.spyOn(engine, "seek");
    const playSpy = vi.spyOn(engine, "play");

    await fireEvent.click(screen.getByText("Play"));

    expect(seekSpy).toHaveBeenCalledWith(0);
    expect(playSpy).toHaveBeenCalled();
    expect(screen.getByText("Pause")).toBeTruthy();
  });

  it("clicking Play seeks to (selected word's time - 0.5s) when a timed word is selected", async () => {
    const { engine } = await renderEditor();
    const getCurrentTimeSpy = vi.spyOn(engine, "getCurrentTime").mockReturnValue(3.0);
    await fireEvent.keyDown(window, { key: "t" }); // tap mode on
    await fireEvent.keyDown(window, { code: "Space" }); // stamps "Hi" at 3.0 - 0.1 = 2.9, selects it
    await fireEvent.keyDown(window, { key: "t" }); // tap mode off
    getCurrentTimeSpy.mockRestore();

    const seekSpy = vi.spyOn(engine, "seek");
    await fireEvent.click(screen.getByText("Play"));

    expect(seekSpy).toHaveBeenCalledWith(2.4); // 2.9 - 0.5
  });

  it("the Play/Pause button toggles: pausing calls engine.pause and flips the label back to Play", async () => {
    const onPlaybackChange = vi.fn();
    const { engine } = await renderEditor(null, { onPlaybackChange });
    const pauseSpy = vi.spyOn(engine, "pause");

    await fireEvent.click(screen.getByText("Play"));
    await fireEvent.click(screen.getByText("Pause"));

    expect(pauseSpy).toHaveBeenCalled();
    expect(screen.getByText("Play")).toBeTruthy();
    expect(onPlaybackChange).toHaveBeenNthCalledWith(1, true);
    expect(onPlaybackChange).toHaveBeenLastCalledWith(false);
  });

  it("Space toggles play/pause when not in tap mode", async () => {
    const { engine } = await renderEditor();
    const playSpy = vi.spyOn(engine, "play");

    await fireEvent.keyDown(window, { code: "Space" });

    expect(playSpy).toHaveBeenCalled();
    expect(screen.getByText("Pause")).toBeTruthy();
  });

  it("follow mode advances the view window forward once the playhead passes viewEnd during playback", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    // A 100s track (far longer than the default 10s view window) so this
    // mid-track advance is nowhere near the duration clamp - that's covered
    // separately below.
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
      getBuffer: () => makeFakeAudioBuffer({ duration: 100 }),
      getDuration: () => 100,
    });

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    // viewEnd defaults to min(10, duration) = 10.
    await fireEvent.click(screen.getByText("Play"));
    extractSpy.mockClear();

    simulateTick?.(12); // past viewEnd (10) while playing
    await Promise.resolve();

    const lastCall = extractSpy.mock.calls.at(-1);
    expect(lastCall?.[2]).toBeCloseTo(10, 5); // viewStart advanced to the old viewEnd
    expect(lastCall?.[3]).toBeCloseTo(20, 5); // viewEnd advanced by the same span (10)
  });

  it("clamps the follow-mode window to end exactly at the track duration instead of overshooting past it", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    // The fake engine's loaded lane is a 3s buffer, so viewEnd defaults to 3
    // (min(10, 3)) - the whole track already fits in one window's span, so
    // reaching the end must clamp back to the same full-track window rather
    // than sliding forward into empty space past duration.
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
    });

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    await fireEvent.click(screen.getByText("Play"));
    extractSpy.mockClear();

    simulateTick?.(4); // past viewEnd (3) while playing, at the track's end
    await Promise.resolve();

    // The clamped result is [0, 3] - identical to the window before this
    // tick (the whole 3s track already fit in one span) - so viewStart/
    // viewEnd never actually change, and the reactive peak extraction never
    // re-runs. That absence of a call is itself the proof there was no
    // overshoot past duration: an unclamped implementation would have
    // advanced to [3, 6], a real prop change that WOULD have re-triggered
    // extractPeaksRange.
    expect(extractSpy).not.toHaveBeenCalled();
  });

  it("does not advance the view window on ticks while paused", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    let simulateTick: ((time: number) => void) | undefined;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => false,
    });

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());
    extractSpy.mockClear();

    simulateTick?.(4); // way past viewEnd (3), but engine reports paused
    await Promise.resolve();

    // viewStart/viewEnd never changed, so the (reactive) peak extraction
    // never re-ran at all - the strongest possible evidence the window did
    // not advance while paused.
    expect(extractSpy).not.toHaveBeenCalled();
  });

  it("re-centers the view window on the loop when the engine reports a backward time jump (wraparound) while playing, instead of leaving the window stuck far away", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "[00:15.00]<00:15.00>Hi<00:15.60> there\n",
      state: "enhanced",
    });

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
      getBuffer: () => makeFakeAudioBuffer({ duration: 1000, sampleRate: 1 }),
      getDuration: () => 1000,
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    // Loop the "Hi there" band: [15, 15.6 + 0.4 = 16.0].
    await fireEvent.dblClick(screen.getByText("Hi there"));

    // Pan the view far away from the loop (~[495, 505]) via the overview
    // strip, before playing - simulating the user having scrolled
    // elsewhere in the chart while a loop is set.
    const overview = document.querySelector(".overview-strip") as HTMLElement;
    await fireEvent.click(overview, { clientX: 400 }); // 400/800*1000 = 500

    await fireEvent.click(screen.getByText("Play"));
    extractSpy.mockClear();

    simulateTick?.(9); // engine wrapped back to loop.start (15) - reported time drops well below the current viewStart (~495)
    await Promise.resolve();

    const lastCall = extractSpy.mock.calls.at(-1);
    expect(lastCall?.[2]).toBeCloseTo(10, 5); // centerWindow(15, span=10, 1000) -> viewStart=10
    expect(lastCall?.[3]).toBeCloseTo(20, 5); // viewEnd=20
  });

  it("clamps follow-mode advancement when the active loop is WIDER than the view span, then recenters cleanly on wraparound - across a full simulated repetition", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "[00:00.00]<00:00.00>Hi<00:14.60> there\n",
      state: "enhanced",
    });

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
      getBuffer: () => makeFakeAudioBuffer({ duration: 100 }),
      getDuration: () => 100,
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    // Loop the "Hi there" band: [0, 14.6 + 0.4 = 15.0] - wider than the
    // default 10s view window ([0, 10)).
    await fireEvent.dblClick(screen.getByText("Hi there"));

    await fireEvent.click(screen.getByText("Play"));
    extractSpy.mockClear();

    // Playhead reaches the current viewEnd (10) - a naive full-span advance
    // would jump to [10,20], well past the loop's end (15) into dead time.
    simulateTick?.(10);
    await Promise.resolve();

    let lastCall = extractSpy.mock.calls.at(-1);
    const clampedViewEnd = lastCall?.[3] as number;
    expect(clampedViewEnd).toBeLessThanOrEqual(15 + (10 * 0.05)); // loop.end + 5% margin
    expect(clampedViewEnd).toBeGreaterThan(10); // it did advance, just not past the loop

    // Playhead continues forward but stays within the now-clamped window -
    // no further change.
    extractSpy.mockClear();
    simulateTick?.(clampedViewEnd - 0.1);
    await Promise.resolve();
    expect(extractSpy).not.toHaveBeenCalled();

    // Engine wraps back to loop.start (0) - reported time drops well below
    // the current (clamped, advanced) viewStart.
    extractSpy.mockClear();
    simulateTick?.(0.2);
    await Promise.resolve();

    lastCall = extractSpy.mock.calls.at(-1);
    expect(lastCall?.[2]).toBeCloseTo(0, 5); // centerWindow(0, span=10, 100) -> viewStart clamped to 0
    expect(lastCall?.[3]).toBeCloseTo(10, 5); // viewEnd=10
  });

  it("skips the forward follow-advance once the active loop's end is already within the view, instead of running the window away from the loop", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "[00:02.00]<00:02.00>Hi<00:02.50> there\n",
      state: "enhanced",
    });

    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
      getBuffer: () => makeFakeAudioBuffer({ duration: 100 }),
      getDuration: () => 100,
    });
    const extractSpy = vi.spyOn(waveform, "extractPeaksRange");

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    // Loop the "Hi there" band: [2, 2.5 + 0.4 = 2.9] - well within the
    // default [0, 10) view window.
    await fireEvent.dblClick(screen.getByText("Hi there"));

    await fireEvent.click(screen.getByText("Play"));
    extractSpy.mockClear();

    simulateTick?.(12); // past viewEnd (10) - would normally advance the window forward
    await Promise.resolve();

    // The window must NOT have advanced (the loop is already fully shown) -
    // the reactive peak extraction never re-runs, same evidence pattern the
    // existing paused/duration-clamp tests above use.
    expect(extractSpy).not.toHaveBeenCalled();
  });

  it("double-clicking a line band sets the loop region on the engine and shows a Clear loop button", async () => {
    const { engine } = await renderEditor();
    const setLoopRegionSpy = vi.spyOn(engine, "setLoopRegion");

    // No line has a band until some word is timed - time "Hi" first (band
    // then spans [0.01, 0.41], the +0.4s tail past its one timed word).
    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    await fireEvent.dblClick(screen.getByText("Hi there")); // the line band's label is the full line text

    expect(setLoopRegionSpy).toHaveBeenCalledTimes(1);
    const [loopArg] = setLoopRegionSpy.mock.calls[0];
    expect(loopArg?.start).toBeCloseTo(0.01, 5);
    expect(loopArg?.end).toBeCloseTo(0.41, 5);
    expect(screen.getByText("Clear loop")).toBeTruthy();
  });

  it("Clear loop clears the engine's loop region and hides the button", async () => {
    const { engine } = await renderEditor();
    const setLoopRegionSpy = vi.spyOn(engine, "setLoopRegion");

    await fireEvent.click(screen.getByText("Hi"));
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    await fireEvent.dblClick(screen.getByText("Hi there"));
    await fireEvent.click(screen.getByText("Clear loop"));

    expect(setLoopRegionSpy).toHaveBeenLastCalledWith(null);
    expect(screen.queryByText("Clear loop")).toBeNull();
  });

  it("clicking a line band selects the line without silently selecting or editing its first word", async () => {
    vi.mocked(fetchLrc).mockResolvedValueOnce({ exists: true, content: "[00:02.00]Hi there\n", state: "line_timed" });
    const { container } = await renderEditor();

    await fireEvent.click(screen.getByRole("button", { name: "Line: Hi there" }));
    expect(container.querySelector(".line-band-selected")).toBeTruthy();
    expect(container.querySelector(".waveform-marker-line.waveform-marker-selected")).toBeTruthy();

    // Arrow keys only nudge an explicitly selected word. A line selection
    // must not mutate the first word behind the user's back.
    await fireEvent.keyDown(window, { key: "ArrowRight" });

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content).toBe("[00:02.00]Hi there\n");
  });

  it("Add break inserts a bare-timestamp break line at the view window's center when paused - no ♪, no [break] on disk", async () => {
    await renderEditor();

    await fireEvent.click(screen.getByText("Add break")); // viewStart=0, viewEnd=min(10,duration=3)=3 -> center 1.5
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content.split("\n")).toContain("[00:01.50]");
    expect(content).not.toContain("♪");
    expect(content).not.toContain("[break]");
  });

  it("Add break inserts a bare-timestamp break line at the current playhead time when playing", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });
    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) {
        simulateTick = cb;
        return () => {};
      },
      isPlaying: () => playingFlag,
      play: async () => {
        playingFlag = true;
      },
      pause: () => {
        playingFlag = false;
      },
    });

    render(LyricEditor, { props: { track, engineFactory: () => engine } });
    await waitFor(() => expect(screen.getByText("Hi")).toBeTruthy());

    await fireEvent.click(screen.getByText("Play"));
    simulateTick?.(2.2);
    await Promise.resolve();

    await fireEvent.click(screen.getByText("Add break"));
    await fireEvent.click(screen.getByText("Pause"));
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content.split("\n")).toContain("[00:02.20]");
    expect(content).not.toContain("♪");
  });

  it("Add break always inserts the bare-timestamp form, even when the document already has other word-timed content", async () => {
    vi.mocked(fetchLrc).mockResolvedValueOnce({
      exists: true,
      content: "[00:01.00]<00:01.00>Hi<00:01.50> there\n",
      state: "enhanced",
    });
    await renderEditor();

    await fireEvent.click(screen.getByText("Add break")); // viewEnd=min(10,duration=3)=3 -> center 1.5

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    const [, content] = mockSaveLrc.mock.calls[0];
    expect(content.split("\n")).toContain("[00:01.50]");
    expect(content).not.toContain("♪");
  });

  it("clicking a break band's remove control removes the break line, and undo restores it", async () => {
    await renderEditor();

    await fireEvent.click(screen.getByText("Add break")); // inserts a bare [00:01.50] line
    const removeControl = await screen.findByLabelText(/Remove instrumental section/);
    await fireEvent.click(removeControl);
    await fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    let [, content] = mockSaveLrc.mock.calls[0];
    expect(content.split("\n")).not.toContain("[00:01.50]");

    await fireEvent.keyDown(window, { key: "z", ctrlKey: true }); // undo the remove
    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalledTimes(2));
    [, content] = mockSaveLrc.mock.calls[1];
    expect(content.split("\n")).toContain("[00:01.50]");
  });

  it("the karaoke pane shows [break] (not the raw bare timestamp) for a break line", async () => {
    const { container } = await renderEditor();

    await fireEvent.click(screen.getByText("Add break")); // inserts a bare [00:01.50] line

    const box = container.querySelector(".lyric-scroll-box") as HTMLElement;
    expect(box.textContent).toContain("[break]");
    expect(box.textContent).not.toContain("[00:01.50]");
    const breakLine = box.querySelector(".karaoke-break-label")?.closest(".karaoke-line") as HTMLElement;
    expect(breakLine).toBeTruthy();
    expect(breakLine.querySelector(".karaoke-word")).toBeNull(); // no word buttons for a break line
  });

  it("selects and shades a break consistently in the timeline and lyric pane", async () => {
    const { container } = await renderEditor();
    await fireEvent.click(screen.getByText("Add break"));
    const lyricBreak = container.querySelector(".lyric-scroll-box .karaoke-break-label") as HTMLElement;

    await fireEvent.click(lyricBreak);

    expect(lyricBreak.closest(".karaoke-line")?.className).toContain("karaoke-line-selected");
    expect(container.querySelector(".line-band-instrumental")?.className).toContain("line-band-selected");
  });

  it("clicking the karaoke pane's × on a break line removes it via the same undoable path, and undo restores it", async () => {
    const { container } = await renderEditor();

    await fireEvent.click(screen.getByText("Add break")); // inserts a bare [00:01.50] line
    const box = container.querySelector(".lyric-scroll-box") as HTMLElement;
    expect(box.textContent).toContain("[break]");

    const removeControl = await screen.findByLabelText("Remove break");
    await fireEvent.click(removeControl);
    expect(box.textContent).not.toContain("[break]");

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
    let [, content] = mockSaveLrc.mock.calls[0];
    expect(content.split("\n")).not.toContain("[00:01.50]");

    await fireEvent.keyDown(window, { key: "z", ctrlKey: true }); // undo the remove
    expect(box.textContent).toContain("[break]");

    await fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockSaveLrc).toHaveBeenCalledTimes(2));
    [, content] = mockSaveLrc.mock.calls[1];
    expect(content.split("\n")).toContain("[00:01.50]");
  });

  it("double-clicking the waveform seeks then awaits play, matching togglePlayback's async contract (onSeekAndPlay is async)", async () => {
    const { engine } = await renderEditor();
    const seekSpy = vi.spyOn(engine, "seek");
    const playSpy = vi.spyOn(engine, "play");

    // document.querySelector("canvas") would grab OverviewStrip's canvas
    // (rendered first) instead of the waveform's - scope to the inspector.
    const canvas = document.querySelector(".waveform-inspector canvas") as HTMLCanvasElement;
    await fireEvent.dblClick(canvas, { clientX: 400 });

    expect(seekSpy).toHaveBeenCalled();
    expect(playSpy).toHaveBeenCalled();
    expect(seekSpy.mock.invocationCallOrder[0]).toBeLessThan(playSpy.mock.invocationCallOrder[0]);
    // playing state reflects the ENGINE's state once play() actually
    // resolves (await, not fire-and-forget), same as togglePlayback.
    await waitFor(() => expect(screen.getByText("Pause")).toBeTruthy());
  });

  it("disposes the engine exactly once and subscribes no onTick listener when unmounted while load() is still pending", async () => {
    let resolveLoad: () => void = () => {};
    const deferredLoad = new Promise<void>((resolve) => {
      resolveLoad = resolve;
    });
    const engine = makeFakeEngine({ load: () => deferredLoad });
    const disposeSpy = vi.spyOn(engine, "dispose");
    const onTickSpy = vi.spyOn(engine, "onTick");
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 120, width: 800, height: 120, x: 0, y: 0, toJSON: () => ({}),
    });

    const { unmount } = render(LyricEditor, { props: { track, engineFactory: () => engine } });

    await Promise.resolve();
    await Promise.resolve();

    unmount();
    resolveLoad();
    await Promise.resolve();
    await Promise.resolve();

    expect(disposeSpy).toHaveBeenCalledTimes(1);
    expect(onTickSpy).not.toHaveBeenCalled();
  });

  describe("LyricEditor tap offset calibration", () => {
    it("uses the default 0.1s offset for tap-stamping when nothing has been calibrated yet", async () => {
      const { engine } = await renderEditor();
      const getCurrentTimeSpy = vi.spyOn(engine, "getCurrentTime").mockReturnValue(1.5);

      await fireEvent.keyDown(window, { key: "t" });
      await fireEvent.keyDown(window, { code: "Space" });
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      expect(content).toContain("[00:01.40]<00:01.40>Hi there"); // 1.5 - 0.1
      getCurrentTimeSpy.mockRestore();
    });

    it("uses a previously-saved calibrated offset instead of the 0.1s default", async () => {
      localStorage.setItem(TAP_OFFSET_STORAGE_KEY, "0.25");
      const { engine } = await renderEditor();
      const getCurrentTimeSpy = vi.spyOn(engine, "getCurrentTime").mockReturnValue(1.5);

      await fireEvent.keyDown(window, { key: "t" });
      await fireEvent.keyDown(window, { code: "Space" });
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      expect(content).toContain("[00:01.25]<00:01.25>Hi there"); // 1.5 - 0.25
      getCurrentTimeSpy.mockRestore();
    });

    it("Space does not toggle playback while the calibration panel is open (it taps instead)", async () => {
      const { engine } = await renderEditor();
      const playSpy = vi.spyOn(engine, "play");

      await fireEvent.click(screen.getByText("Calibrate tap timing"));
      await fireEvent.click(screen.getByText("Start calibration"));
      await fireEvent.keyDown(window, { code: "Space" });

      expect(playSpy).not.toHaveBeenCalled();
      await fireEvent.click(screen.getByText("Cancel"));
    });

    it("opens and closes the calibration panel via its toolbar button and Cancel", async () => {
      await renderEditor();

      await fireEvent.click(screen.getByText("Calibrate tap timing"));
      expect(screen.getByText("Start calibration")).toBeTruthy();

      await fireEvent.click(screen.getByText("Start calibration"));
      // The default beep scheduler uses the real clock/timers - exercising it
      // end-to-end here would be slow; its own timing contract is unit-tested
      // in calibration.test.ts, and the panel's compute/apply wiring is
      // unit-tested in TapCalibrationPanel.test.ts. Cancelling immediately
      // clears the real (never-fired) timers with no wall-clock wait.
      await fireEvent.click(screen.getByText("Cancel"));

      expect(screen.queryByText("Start calibration")).toBeNull();
    });

    it("Space still toggles play/pause once the calibration panel is closed again", async () => {
      const { engine } = await renderEditor();
      const playSpy = vi.spyOn(engine, "play");

      await fireEvent.click(screen.getByText("Calibrate tap timing"));
      await fireEvent.click(screen.getByText("Cancel"));

      await fireEvent.keyDown(window, { code: "Space" });
      expect(playSpy).toHaveBeenCalled();
    });
  });

  describe("LyricEditor keyboard nav", () => {
    it("Shift+Arrow nudges the selected word by 10x the normal step", async () => {
      await renderEditor();
      await fireEvent.click(screen.getByText("Hi"));

      await fireEvent.keyDown(window, { key: "ArrowRight", shiftKey: true }); // +0.10 (10 * 0.01)
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      expect(content).toContain("[00:00.10]<00:00.10>Hi there"); // untimed word starts at 0, nudged to 0.10
    });

    it("[ and ] select the previous/next word in document order, independent of the current line", async () => {
      vi.mocked(fetchLrc).mockResolvedValueOnce({
        exists: true,
        content: "[00:01.00]<00:01.00>Hi<00:01.50> there\n[00:05.00]<00:05.00>Bye<00:05.50> now\n",
        state: "enhanced",
      });
      await renderEditor();

      await fireEvent.keyDown(window, { key: "]" }); // selects the first word ("Hi", at 1.00)
      await fireEvent.keyDown(window, { key: "ArrowRight" }); // nudges "Hi": 1.00 -> 1.01
      await fireEvent.keyDown(window, { key: "]" }); // selects the next word ("there", at 1.50)
      await fireEvent.keyDown(window, { key: "ArrowRight" }); // nudges "there": 1.50 -> 1.51
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      expect(content).toContain("<00:01.01>Hi<00:01.51> there");
    });

    it("] past the last word is a no-op (clamped, not wrapping)", async () => {
      await renderEditor(); // "Hi there" - 2 words total
      await fireEvent.keyDown(window, { key: "]" }); // "Hi"
      await fireEvent.keyDown(window, { key: "]" }); // "there"
      await fireEvent.keyDown(window, { key: "]" }); // still "there" - nothing past it
      await fireEvent.keyDown(window, { key: "ArrowRight" }); // nudges "there", not "Hi"
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      // Only "there" got a time (0.01); "Hi" stays untimed - renderLrc's
      // partially-timed branch emits the line timestamp, then "Hi" with no
      // tag (it has no time), then "there"'s own <00:00.01> tag immediately
      // before it.
      expect(content).toContain("[00:00.01]Hi<00:00.01> there");
    });

    it("Escape clears an active loop", async () => {
      // computeLineBands (and therefore LineBandStrip, which the dblclick
      // below targets) renders no band at all for an untimed line - the
      // default renderEditor() fixture ("Hi there\n") is untimed, so this
      // test needs its own timed fetchLrc fixture for a real band to exist.
      vi.mocked(fetchLrc).mockResolvedValueOnce({
        exists: true,
        content: "[00:01.00]<00:01.00>Hi<00:01.50> there\n",
        state: "enhanced",
      });
      const { engine } = await renderEditor();
      const setLoopRegionSpy = vi.spyOn(engine, "setLoopRegion");

      await fireEvent.dblClick(screen.getByText("Hi there")); // LineBandStrip's existing dblclick->onLoopChange sets a loop
      expect(setLoopRegionSpy).toHaveBeenCalled();
      setLoopRegionSpy.mockClear();

      await fireEvent.keyDown(window, { key: "Escape" });

      expect(setLoopRegionSpy).toHaveBeenCalledWith(null);
    });

    it("Escape with no active loop does nothing (no crash, no spurious setLoopRegion call)", async () => {
      const { engine } = await renderEditor();
      const setLoopRegionSpy = vi.spyOn(engine, "setLoopRegion");

      await fireEvent.keyDown(window, { key: "Escape" });

      expect(setLoopRegionSpy).not.toHaveBeenCalled();
    });

    it("keydown Space on a SELECT element does not trigger play/pause (guard includes SELECT and isContentEditable)", async () => {
      const { engine, container } = await renderEditor();
      const playSpy = vi.spyOn(engine, "play");

      // Create a real select element and fire keydown on it
      const select = document.createElement("select");
      select.add(new Option("Option 1"));
      container.appendChild(select);

      // Fire keydown with the select as the target - the handler should ignore it
      const event = new KeyboardEvent("keydown", { code: "Space", bubbles: true });
      Object.defineProperty(event, "target", { value: select, enumerable: true });
      window.dispatchEvent(event);

      expect(playSpy).not.toHaveBeenCalled();
    });

    it("sets the selected word time with Shift+click and makes the edit undoable", async () => {
      const { container } = await renderEditor();
      await fireEvent.click(screen.getByText("Hi"));

      const waveformCanvas = container.querySelector(".waveform-inspector canvas") as HTMLCanvasElement;
      await fireEvent.click(waveformCanvas, { clientX: 400, shiftKey: true });
      await fireEvent.click(screen.getByText("Undo"));
      expect(screen.getByText("Save")).toBeTruthy();

      await fireEvent.click(waveformCanvas, { clientX: 400, shiftKey: true });
      await fireEvent.click(screen.getByText("Save"));

      await waitFor(() => expect(mockSaveLrc).toHaveBeenCalled());
      const [, content] = mockSaveLrc.mock.calls[0];
      expect(content).toContain("[00:01.50]<00:01.50>Hi there");
    });

    it("submits the hidden alignment recipe and reloads lyrics when that job completes", async () => {
      await renderEditor();
      mockFetchJob
        .mockResolvedValueOnce({ id: 42, status: "running", items: [] })
        .mockResolvedValueOnce({ id: 42, status: "completed", items: [] });

      await fireEvent.click(screen.getByText("Re-time every word with AI"));
      await waitFor(() => expect(mockSubmitJob).toHaveBeenCalledWith({
        recipe: "align_only",
        track_ids: [1],
        options: { device: "auto" },
      }));
      expect(screen.getByText(/rebuilding enhanced timing for every lyric word/)).toBeTruthy();
      expect(screen.getByText(/Existing line, break, and word markers will be replaced/)).toBeTruthy();

      vi.mocked(fetchLrc).mockResolvedValueOnce({
        exists: true,
        content: "[00:01.00]<00:01.00>Hi<00:01.50> there\n",
        state: "enhanced",
      });
      completionCallbacks.get(42)?.();

      await waitFor(() => expect(screen.getByText(/Enhanced per-word timing loaded/)).toBeTruthy());
      expect(vi.mocked(fetchLrc)).toHaveBeenCalledTimes(2);
    });

    it("does not report success when the completed job leaves only line timing", async () => {
      await renderEditor();
      mockFetchJob
        .mockResolvedValueOnce({ id: 42, status: "running", items: [] })
        .mockResolvedValueOnce({ id: 42, status: "completed", items: [] });

      await fireEvent.click(screen.getByText("Re-time every word with AI"));
      vi.mocked(fetchLrc).mockResolvedValueOnce({
        exists: true,
        content: "[00:01.00]Hi there\n",
        state: "line_timed",
      });
      completionCallbacks.get(42)?.();

      await waitFor(() => expect(screen.getByText(/did not produce enhanced per-word timing/)).toBeTruthy());
    });

    it("protects unsaved manual work by disabling AI re-timing while dirty", async () => {
      await renderEditor();
      await fireEvent.click(screen.getByText("Hi"));
      await fireEvent.keyDown(window, { key: "ArrowRight" });

      const button = screen.getByText("Re-time every word with AI") as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      expect(mockSubmitJob).not.toHaveBeenCalled();
    });

    it("disables enhanced retiming with a clear explanation when WhisperX is unavailable", async () => {
      await renderEditor(false);

      const button = screen.getByText("Re-time every word with AI") as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      expect(button.title).toContain("WhisperX worker is installed");
      expect(screen.getByText(/Enhanced per-word timing is unavailable until the WhisperX worker is installed/)).toBeTruthy();
    });
  });
});
