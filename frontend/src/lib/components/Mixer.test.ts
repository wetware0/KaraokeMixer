import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import Mixer from "./Mixer.svelte";
import { makeFakeAudioBuffer, makeFakeEngine, createFakeOfflineAudioContext } from "../audio/testFakes";
import * as exportMixModule from "../audio/exportMix";
import type { Track } from "../types";

vi.mock("../api", () => ({
  fetchTrackParts: vi.fn(),
  partAudioUrl: (trackId: number, part: string) => `/api/audio/${trackId}/part/${part}`,
  fetchLrc: vi.fn().mockResolvedValue({ exists: false, content: "", state: null }),
}));

const fakeCtx = { clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };

const track: Track = {
  id: 1, media_root: "D:/Media", relative_path: "Song.flac", artist: "ABBA", title: "Dancing Queen",
  outputs: {
    instrumental: false, vocals: true, lead_vocals: true, backing_vocals: false,
    drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
  },
  lrc_state: null, stem_count: 1,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

async function renderMixer(overrides: Partial<Record<string, unknown>> = {}) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
  const { fetchTrackParts } = await import("../api");
  vi.mocked(fetchTrackParts).mockResolvedValue([
    { part: "lead_vocals", exists: true, duration: 180 },
    { part: "original", exists: true, duration: 180 },
  ]);
  const engine = makeFakeEngine();
  const result = render(Mixer, {
    props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext, ...overrides },
  });
  await waitFor(() => expect(screen.getByText("Lead vocals")).toBeTruthy());
  return { ...result, engine };
}

describe("Mixer", () => {
  it("renders one StemLane per playable part returned by /parts", async () => {
    await renderMixer();
    expect(screen.getByText("Lead vocals")).toBeTruthy();
    expect(screen.getByText("Original")).toBeTruthy();
  });

  it("shows per-stage load progress text: Fetching audio… then Decoding N of M…", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([
      { part: "lead_vocals", exists: true, duration: 180 },
      { part: "original", exists: true, duration: 180 },
    ]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);

    let resolveLoad: () => void = () => {};
    const deferred = new Promise<void>((resolve) => {
      resolveLoad = resolve;
    });
    let progressCb: ((loaded: number, total: number) => void) | undefined;
    const engine = makeFakeEngine({
      load: async (tracks, onProgress) => {
        progressCb = onProgress;
        onProgress?.(1, tracks.length);
        await deferred;
        onProgress?.(tracks.length, tracks.length);
      },
      // load() above is a bespoke stand-in for progress timing, so it never
      // populates the base fake's internal buffers/lanes maps - override
      // getBuffer/getDuration directly so Mixer's post-load lane setup
      // (extractPeaks etc.) still has something real to read.
      getBuffer: () => makeFakeAudioBuffer({ duration: 3 }),
      getDuration: () => 3,
    });

    render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });

    await waitFor(() => expect(screen.getByText("Fetching audio…")).toBeTruthy());

    progressCb?.(1, 2);
    await waitFor(() => expect(screen.getByText("Decoding 1 of 2…")).toBeTruthy());

    resolveLoad();
    await waitFor(() => expect(screen.getByText("Lead vocals")).toBeTruthy());
  });

  it("toggles play/pause on the transport button and calls the engine", async () => {
    const onPlaybackChange = vi.fn();
    const { engine } = await renderMixer({ onPlaybackChange });
    const playSpy = vi.spyOn(engine, "play");

    await fireEvent.click(screen.getByText("Play"));

    expect(playSpy).toHaveBeenCalled();
    expect(onPlaybackChange).toHaveBeenLastCalledWith(true);
    await fireEvent.click(screen.getByText("Pause"));
    expect(onPlaybackChange).toHaveBeenLastCalledWith(false);
  });

  it("enables the karaoke preset button when a lead_vocals lane exists, and mutes it on click", async () => {
    const { engine } = await renderMixer();
    const setMutedSpy = vi.spyOn(engine, "setMuted");

    const presetButton = screen.getByText("Karaoke preset") as HTMLButtonElement;
    expect(presetButton.disabled).toBe(false);

    await fireEvent.click(presetButton);

    expect(setMutedSpy).toHaveBeenCalledWith("lead_vocals", true);
  });

  it("defaults the original lane to muted when a separated part exists (default playback should not double the content)", async () => {
    const { engine } = await renderMixer();
    expect(engine.getLanes().find((l) => l.id === "original")?.muted).toBe(true);

    const originalLane = screen.getByText("Original").closest(".stem-lane") as HTMLElement;
    expect(originalLane.querySelector(".stem-lane-mute")?.className).toContain("active");
  });

  it("leaves the original lane unmuted when it is the only lane (no separated parts)", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([{ part: "original", exists: true, duration: 180 }]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);

    const engine = makeFakeEngine();
    render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });
    await waitFor(() => expect(screen.getByText("Original")).toBeTruthy());

    expect(engine.getLanes().find((l) => l.id === "original")?.muted).toBe(false);
    const originalLane = screen.getByText("Original").closest(".stem-lane") as HTMLElement;
    expect(originalLane.querySelector(".stem-lane-mute")?.className).not.toContain("active");
  });

  it("applying the karaoke preset from a scrambled mute state always produces exactly the karaoke configuration (a true preset, not a toggle)", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([
      { part: "lead_vocals", exists: true, duration: 180 },
      { part: "drums", exists: true, duration: 180 },
      { part: "original", exists: true, duration: 180 },
    ]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);

    const engine = makeFakeEngine();
    render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });
    await waitFor(() => expect(screen.getByText("Lead vocals")).toBeTruthy());

    const muteButtonFor = (label: string) =>
      (screen.getByText(label).closest(".stem-lane") as HTMLElement).querySelector(".stem-lane-mute") as HTMLElement;
    const soloButtonFor = (label: string) =>
      (screen.getByText(label).closest(".stem-lane") as HTMLElement).querySelector(".stem-lane-solo") as HTMLElement;

    // Scramble away from the default state: unmute original, mute drums,
    // AND solo drums - a residual solo must not survive the preset (it
    // would otherwise silence every lane the preset just unmuted, via the
    // engine's "only soloed lanes are audible while anything is soloed"
    // rule).
    await fireEvent.click(muteButtonFor("Original"));
    await fireEvent.click(muteButtonFor("Drums"));
    await fireEvent.click(soloButtonFor("Drums"));
    expect(engine.getLanes().find((l) => l.id === "original")?.muted).toBe(false);
    expect(engine.getLanes().find((l) => l.id === "drums")?.muted).toBe(true);
    expect(engine.getLanes().find((l) => l.id === "drums")?.solo).toBe(true);

    await fireEvent.click(screen.getByText("Karaoke preset"));

    expect(engine.getLanes().find((l) => l.id === "lead_vocals")?.muted).toBe(true);
    expect(engine.getLanes().find((l) => l.id === "drums")?.muted).toBe(false);
    expect(engine.getLanes().find((l) => l.id === "original")?.muted).toBe(true);
    expect(engine.getLanes().every((l) => l.solo === false)).toBe(true);

    // The residual solo is fully cleared, so drums (unmuted, non-vocal) is
    // actually audible at its own slider gain, and vocals are silent.
    expect(engine.getEffectiveGain("drums")).toBeGreaterThan(0);
    expect(engine.getEffectiveGain("drums")).toBe(engine.getLanes().find((l) => l.id === "drums")?.gain);
    expect(engine.getEffectiveGain("lead_vocals")).toBe(0);
  });

  it("exporting after the preset excludes the vocal lane even when a lane was left soloed beforehand", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([
      { part: "lead_vocals", exists: true, duration: 180 },
      { part: "drums", exists: true, duration: 180 },
      { part: "original", exists: true, duration: 180 },
    ]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const renderMixSpy = vi.spyOn(exportMixModule, "renderMix");

    const engine = makeFakeEngine();
    render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });
    await waitFor(() => expect(screen.getByText("Lead vocals")).toBeTruthy());

    // Leave drums soloed BEFORE applying the preset - a residual solo that,
    // uncleared, would silence every lane the preset unmutes (including
    // itself, if the export path also failed to clear it).
    const soloButtonFor = (label: string) =>
      (screen.getByText(label).closest(".stem-lane") as HTMLElement).querySelector(".stem-lane-solo") as HTMLElement;
    await fireEvent.click(soloButtonFor("Drums"));

    await fireEvent.click(screen.getByText("Karaoke preset"));
    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() => expect(renderMixSpy).toHaveBeenCalled());
    const [mixLanes] = renderMixSpy.mock.calls[0];
    expect(mixLanes.every((lane) => lane.solo === false)).toBe(true);
    expect(mixLanes.find((lane) => lane.id === "lead_vocals")?.muted).toBe(true);
    expect(mixLanes.find((lane) => lane.id === "drums")?.muted).toBe(false);

    vi.unstubAllGlobals();
  });

  it("disables the karaoke preset button when neither lead_vocals nor vocals exists", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([{ part: "original", exists: true, duration: 180 }]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);

    render(Mixer, {
      props: { track, engineFactory: () => makeFakeEngine(), offlineContextFactory: createFakeOfflineAudioContext },
    });
    await waitFor(() => expect(screen.getByText("Original")).toBeTruthy());

    expect((screen.getByText("Karaoke preset") as HTMLButtonElement).disabled).toBe(true);
  });

  it("Export mix… renders the offline mix and triggers a download", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await renderMixer();

    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    vi.unstubAllGlobals();
  });

  it("defaults the export format selector to WAV and downloads a .wav file by default", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const downloadWavSpy = vi.spyOn(exportMixModule, "downloadWav");

    await renderMixer();

    expect((screen.getByLabelText("Export format") as HTMLSelectElement).value).toBe("wav");

    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() => expect(downloadWavSpy).toHaveBeenCalled());
    expect(downloadWavSpy.mock.calls[0][0]).toBe("Dancing Queen.mix.wav");
    vi.unstubAllGlobals();
  });

  it("encodes and downloads MP3 when the format selector is set to MP3", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const downloadMp3Spy = vi.spyOn(exportMixModule, "downloadMp3").mockImplementation(() => {});
    const encodeMp3Spy = vi.spyOn(exportMixModule, "encodeMp3").mockReturnValue(new ArrayBuffer(4));

    await renderMixer();

    await fireEvent.change(screen.getByLabelText("Export format"), { target: { value: "mp3" } });
    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() => expect(encodeMp3Spy).toHaveBeenCalled());
    expect(encodeMp3Spy.mock.calls[0][1]).toBe(192);
    expect(downloadMp3Spy.mock.calls[0][0]).toBe("Dancing Queen.mix.mp3");
    vi.unstubAllGlobals();
  });

  it("disables the export format selector and export button while an export is in flight", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    let resolveExport: () => void = () => {};
    const deferredExport = new Promise<void>((resolve) => {
      resolveExport = resolve;
    });
    let renderMixResolve: (buffer: any) => void = () => {};
    const renderMixPromise = new Promise((resolve) => {
      renderMixResolve = resolve;
    });
    vi.spyOn(exportMixModule, "renderMix").mockImplementation(() => renderMixPromise as any);
    vi.spyOn(exportMixModule, "downloadWav").mockImplementation(() => deferredExport);

    await renderMixer();

    const selectEl = screen.getByLabelText("Export format") as HTMLSelectElement;
    const exportButton = screen.getByText("Export mix…") as HTMLButtonElement;

    // Before export, both should be enabled
    expect(selectEl.disabled).toBe(false);
    expect(exportButton.disabled).toBe(false);

    // Start export
    void fireEvent.click(exportButton);
    await Promise.resolve();

    // During export, both should be disabled
    expect(selectEl.disabled).toBe(true);
    expect(exportButton.disabled).toBe(true);

    // Complete export
    renderMixResolve(makeFakeAudioBuffer({ duration: 1, sampleRate: 44100 }));
    resolveExport();
    await waitFor(() => expect(selectEl.disabled).toBe(false));
    expect(exportButton.disabled).toBe(false);

    vi.unstubAllGlobals();
  });

  it("calls onBack when the back button is clicked", async () => {
    const onBack = vi.fn();
    await renderMixer({ onBack });

    await fireEvent.click(screen.getByText("← Back"));

    expect(onBack).toHaveBeenCalled();
  });

  it("disposes the engine exactly once and subscribes no onTick listener when unmounted while load() is still pending", async () => {
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([{ part: "original", exists: true, duration: 180 }]);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);

    let resolveLoad: () => void = () => {};
    const deferredLoad = new Promise<void>((resolve) => {
      resolveLoad = resolve;
    });
    const engine = makeFakeEngine({ load: () => deferredLoad });
    const disposeSpy = vi.spyOn(engine, "dispose");
    const onTickSpy = vi.spyOn(engine, "onTick");

    const { unmount } = render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });

    // Let setup() run past fetchTrackParts and into the still-pending
    // engine.load() call before unmounting.
    await Promise.resolve();
    await Promise.resolve();

    unmount();
    resolveLoad();
    await Promise.resolve();
    await Promise.resolve();

    expect(disposeSpy).toHaveBeenCalledTimes(1);
    expect(onTickSpy).not.toHaveBeenCalled();
  });

  it("shows a playhead line at the correct left position while playing, and none while paused", async () => {
    let simulateTick: ((time: number) => void) | undefined;
    let playingFlag = false;
    const engine = makeFakeEngine({
      onTick(cb) { simulateTick = cb; return () => {}; },
      isPlaying: () => playingFlag,
      play: async () => { playingFlag = true; },
      pause: () => { playingFlag = false; },
      getBuffer: () => makeFakeAudioBuffer({ duration: 10 }),
      getDuration: () => 10,
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeCtx as unknown as CanvasRenderingContext2D);
    const { fetchTrackParts } = await import("../api");
    vi.mocked(fetchTrackParts).mockResolvedValue([{ part: "original", exists: true, duration: 10 }]);

    const { container } = render(Mixer, {
      props: { track, engineFactory: () => engine, offlineContextFactory: createFakeOfflineAudioContext },
    });
    await waitFor(() => expect(screen.getByText("Original")).toBeTruthy());

    expect(container.querySelector(".mixer-playhead")).toBeNull();

    await fireEvent.click(screen.getByText("Play"));
    simulateTick?.(2.5);
    await waitFor(() => expect(container.querySelector(".mixer-playhead")).toBeTruthy());
    const playhead = container.querySelector(".mixer-playhead") as HTMLElement;
    expect(playhead.style.left).toBe("25%"); // 2.5 / 10 * 100

    await fireEvent.click(screen.getByText("Pause"));
    expect(container.querySelector(".mixer-playhead")).toBeNull();
  });

  it("clicking the playback-position strip seeks the engine to the clicked position", async () => {
    const { container, engine } = await renderMixer();
    const seekSpy = vi.spyOn(engine, "seek");
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 400, bottom: 100, width: 400, height: 100, x: 0, y: 0, toJSON: () => ({}),
    });

    const timeline = container.querySelector(".mixer-timeline-strip") as HTMLElement;
    await fireEvent(timeline, new MouseEvent("pointerdown", { clientX: 100, bubbles: true }));
    await fireEvent(timeline, new MouseEvent("pointerup", { clientX: 100, bubbles: true }));

    // makeFakeEngine's default load() always produces 3-second-duration
    // lanes (see testFakes.ts) regardless of the /parts fixture's own
    // duration field; 100/400 of the measured width -> 0.25 * 3s = 0.75.
    expect(seekSpy).toHaveBeenCalledWith(0.75);
  });

  it("clicking a lane's waveform canvas seeks exactly once", async () => {
    const { engine } = await renderMixer();
    const seekSpy = vi.spyOn(engine, "seek");
    seekSpy.mockClear();

    const canvas = document.querySelector(".stem-lane-waveform") as HTMLElement;
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 600, bottom: 48, width: 600, height: 48, x: 0, y: 0, toJSON: () => ({}),
    });

    await fireEvent.click(canvas, { clientX: 300 });

    // StemLane's own onSeek(fraction) fires exactly once.
    expect(seekSpy).toHaveBeenCalledTimes(1);
    expect(seekSpy).toHaveBeenCalledWith(1.5); // 0.5 fraction * 3s duration
  });

  it("the playback-position strip has proper slider attributes for keyboard navigation", async () => {
    const { container } = await renderMixer();
    const timeline = container.querySelector(".mixer-timeline-strip") as HTMLElement;
    expect(timeline.getAttribute("role")).toBe("slider");
    expect(timeline.getAttribute("tabindex")).toBe("0");
    expect(timeline.getAttribute("aria-label")).toBe("Playback position");
  });

  it("pressing Arrow Right/Left on the playback-position strip seeks via keyboard", async () => {
    const { container, engine } = await renderMixer();
    const seekSpy = vi.spyOn(engine, "seek");
    const getCurrentTimeSpy = vi.spyOn(engine, "getCurrentTime").mockReturnValue(1.0);

    const timeline = container.querySelector(".mixer-timeline-strip") as HTMLElement;

    // Arrow Right seeks forward by 1% of duration (3s * 0.01 = 0.03)
    await fireEvent.keyDown(timeline, { key: "ArrowRight" });
    expect(seekSpy).toHaveBeenCalledWith(1.03); // 1.0 + 0.03

    // Arrow Left seeks backward by 1% of duration
    seekSpy.mockClear();
    getCurrentTimeSpy.mockReturnValue(1.5);
    await fireEvent.keyDown(timeline, { key: "ArrowLeft" });
    expect(seekSpy).toHaveBeenCalledWith(1.47); // 1.5 - 0.03
  });

  it("Space on the export-format select does not toggle playback (guard matches LyricEditor's INPUT/TEXTAREA/SELECT/contentEditable set)", async () => {
    const { engine } = await renderMixer();
    const playSpy = vi.spyOn(engine, "play");
    const selectEl = screen.getByLabelText("Export format") as HTMLSelectElement;

    await fireEvent.keyDown(selectEl, { key: " ", code: "Space" });

    expect(playSpy).not.toHaveBeenCalled();
  });

  it("a failed export shows a non-fatal message inline without unmounting the lanes/transport", async () => {
    vi.spyOn(exportMixModule, "renderMix").mockRejectedValue(
      new Error("Sample rate mismatch: lanes must share a common sample rate"),
    );

    const { container } = await renderMixer();

    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() =>
      expect(screen.getByText(/Sample rate mismatch/)).toBeTruthy(),
    );
    // The fatal `error` path (which replaces the whole mixer UI) must not
    // have fired - lanes and the transport controls are still mounted.
    expect(screen.getByText("Lead vocals")).toBeTruthy();
    expect(container.querySelector(".mixer-transport")).toBeTruthy();
    expect(container.querySelector(".mixer-error")).toBeNull();
  });

  it("clears a previous export error message on the next export attempt", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const renderMixSpy = vi.spyOn(exportMixModule, "renderMix").mockRejectedValueOnce(new Error("boom"));

    await renderMixer();

    await fireEvent.click(screen.getByText("Export mix…"));
    await waitFor(() => expect(screen.getByText("boom")).toBeTruthy());

    renderMixSpy.mockResolvedValueOnce(makeFakeAudioBuffer({ duration: 1, sampleRate: 44100 }) as any);
    await fireEvent.click(screen.getByText("Export mix…"));

    await waitFor(() => expect(screen.queryByText("boom")).toBeNull());
    vi.unstubAllGlobals();
  });
});
