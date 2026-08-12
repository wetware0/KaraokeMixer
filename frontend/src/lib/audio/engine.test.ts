import { describe, expect, it, vi } from "vitest";
import { createEngine } from "./engine";
import { createFakeAudioContext, makeFakeAudioBuffer, encodeFakeAudioBuffer } from "./testFakes";

function manualScheduler() {
  let pending: (() => void) | null = null;
  return {
    scheduleFrame: (cb: () => void) => { pending = cb; return 1; },
    cancelFrame: () => { pending = null; },
    pump: () => { const cb = pending; pending = null; cb?.(); },
  };
}

function fakeFetch(buffersByUrl: Record<string, ReturnType<typeof makeFakeAudioBuffer>>) {
  return async (url: string) => encodeFakeAudioBuffer(buffersByUrl[url]);
}

describe("createEngine", () => {
  it("loads each lane and computes duration as the longest lane", async () => {
    const ctx = createFakeAudioContext();
    const short = makeFakeAudioBuffer({ duration: 2 });
    const long = makeFakeAudioBuffer({ duration: 3 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": short, "b.mp3": long }),
    });

    await engine.load([{ id: "a", url: "a.mp3" }, { id: "b", url: "b.mp3" }]);

    expect(engine.getDuration()).toBeCloseTo(3, 5);
    expect(engine.getBuffer("a")?.duration).toBeCloseTo(2, 5);
    expect(engine.getLanes().map((l) => l.id).sort()).toEqual(["a", "b"]);
  });

  it("advances currentTime with the context clock while playing, and freezes it on pause", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame, pump } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": buffer }),
      scheduleFrame,
      cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    await engine.play();
    ctx.advanceTime(1.5);
    pump();
    expect(engine.getCurrentTime()).toBeCloseTo(1.5, 5);

    engine.pause();
    ctx.advanceTime(5);
    expect(engine.getCurrentTime()).toBeCloseTo(1.5, 5);
    expect(engine.isPlaying()).toBe(false);
  });

  it("seek() sets the playhead while paused and keeps it while playing", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer }), scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    engine.seek(4);
    expect(engine.getCurrentTime()).toBeCloseTo(4, 5);

    await engine.play();
    engine.seek(6);
    expect(engine.getCurrentTime()).toBeCloseTo(6, 5);
  });

  it("solo mutes every other lane; mute always wins for the muted lane itself", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 1 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer, "b.mp3": buffer }),
      scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }, { id: "b", url: "b.mp3" }]);

    engine.setSolo("a", true);
    expect(engine.getEffectiveGain("a")).toBe(1);
    expect(engine.getEffectiveGain("b")).toBe(0);

    engine.setMuted("a", true);
    expect(engine.getEffectiveGain("a")).toBe(0);
  });

  it("onTick fires with the current playhead time and unsubscribe stops it", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame, pump } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer }), scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    const listener = vi.fn();
    const unsubscribe = engine.onTick(listener);
    await engine.play();
    ctx.advanceTime(1);
    pump();
    expect(listener).toHaveBeenCalledWith(expect.closeTo(1, 5));

    unsubscribe();
    listener.mockClear();
    ctx.advanceTime(1);
    pump();
    expect(listener).not.toHaveBeenCalled();
  });

  it("loops back to the loop start once the playhead reaches the loop end", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame, pump } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer }), scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    engine.setLoopRegion({ start: 0.5, end: 1 });
    await engine.play();
    ctx.advanceTime(2); // overshoots the loop end
    pump();

    expect(engine.getCurrentTime()).toBeCloseTo(0.5, 5);
  });

  it("pauses and rewinds to 0 once the playhead reaches the end with no loop region", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame, pump } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 2 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer }), scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    await engine.play();
    ctx.advanceTime(3);
    pump();

    expect(engine.isPlaying()).toBe(false);
    expect(engine.getCurrentTime()).toBe(0);
  });

  it("load() stops and disposes every prior lane's source and resets transport state", async () => {
    const ctx = createFakeAudioContext();
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const bufferA = makeFakeAudioBuffer({ duration: 5 });
    const bufferB = makeFakeAudioBuffer({ duration: 4 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": bufferA, "b.mp3": bufferB }),
      scheduleFrame,
      cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);
    await engine.play();
    ctx.advanceTime(2);

    const sourcesForA = [...ctx.createdSources];
    expect(sourcesForA.length).toBeGreaterThan(0);
    expect(sourcesForA.every((s) => s.stopped)).toBe(false);
    expect(engine.isPlaying()).toBe(true);

    await engine.load([{ id: "b", url: "b.mp3" }]);

    expect(sourcesForA.every((s) => s.stopped)).toBe(true);
    expect(engine.isPlaying()).toBe(false);
    expect(engine.getCurrentTime()).toBe(0);
  });

  it("resumes a suspended AudioContext before starting playback", async () => {
    const ctx = createFakeAudioContext({ initialState: "suspended" });
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": buffer }),
      scheduleFrame,
      cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);
    expect(ctx.state).toBe("suspended");

    const resumeSpy = vi.spyOn(ctx, "resume");
    const createBufferSourceSpy = vi.spyOn(ctx, "createBufferSource");

    await engine.play();

    expect(resumeSpy).toHaveBeenCalledTimes(1);
    expect(ctx.state).toBe("running");
    expect(createBufferSourceSpy).toHaveBeenCalled();
    // sources must be started AFTER resume() was invoked (real browsers
    // buffer-underrun/drop audio if a source starts on a still-suspended
    // context)
    expect(resumeSpy.mock.invocationCallOrder[0]).toBeLessThan(
      createBufferSourceSpy.mock.invocationCallOrder[0],
    );
    expect(engine.isPlaying()).toBe(true);
  });

  it("load() fetches/decodes all lanes concurrently, inserting them in the original request order regardless of which one resolves first", async () => {
    const ctx = createFakeAudioContext();
    const bufferA = makeFakeAudioBuffer({ duration: 2 });
    const bufferB = makeFakeAudioBuffer({ duration: 3 });
    const fetchCalls: string[] = [];
    const resolvers = new Map<string, (data: ArrayBuffer) => void>();
    const fetchImpl = (url: string) =>
      new Promise<ArrayBuffer>((resolve) => {
        fetchCalls.push(url);
        resolvers.set(url, resolve);
      });
    const engine = createEngine({ audioContextFactory: () => ctx, fetchImpl });

    const loadPromise = engine.load([{ id: "a", url: "a.mp3" }, { id: "b", url: "b.mp3" }]);

    // Both fetches are issued up-front (concurrently), before either settles
    // - not one-at-a-time waiting for the previous lane's decode to finish.
    expect(fetchCalls).toEqual(["a.mp3", "b.mp3"]);

    // Resolve "b" (the second lane) first - a staggered, out-of-order
    // completion - yet lane insertion order must still follow the original
    // request order, not resolution order.
    resolvers.get("b.mp3")!(encodeFakeAudioBuffer(bufferB));
    await Promise.resolve();
    resolvers.get("a.mp3")!(encodeFakeAudioBuffer(bufferA));
    await loadPromise;

    expect(engine.getLanes().map((l) => l.id)).toEqual(["a", "b"]);
    expect(engine.getDuration()).toBeCloseTo(3, 5);
  });

  it("load() calls onProgress once per completed lane, with counts reaching (total, total)", async () => {
    const ctx = createFakeAudioContext();
    const bufferA = makeFakeAudioBuffer({ duration: 2 });
    const bufferB = makeFakeAudioBuffer({ duration: 3 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": bufferA, "b.mp3": bufferB }),
    });
    const onProgress = vi.fn();

    await engine.load([{ id: "a", url: "a.mp3" }, { id: "b", url: "b.mp3" }], onProgress);

    expect(onProgress).toHaveBeenCalledTimes(2);
    // Call order across concurrently-decoding lanes is not deterministic;
    // only the counts matter - every call reports the correct total, and
    // the set of `loaded` values covers 1..total exactly once each.
    expect(onProgress.mock.calls.every(([, total]) => total === 2)).toBe(true);
    expect(onProgress.mock.calls.map(([loaded]) => loaded).sort()).toEqual([1, 2]);
  });

  it("load() defaults onProgress to a no-op when omitted (backward compatible)", async () => {
    const ctx = createFakeAudioContext();
    const buffer = makeFakeAudioBuffer({ duration: 2 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": buffer }),
    });

    await expect(engine.load([{ id: "a", url: "a.mp3" }])).resolves.toBeUndefined();
  });

  it("dispose() landing inside play()'s suspended-resume gap prevents any source from starting once resume() settles", async () => {
    const ctx = createFakeAudioContext({ initialState: "suspended" });
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx, fetchImpl: fakeFetch({ "a.mp3": buffer }), scheduleFrame, cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    let resolveResume: () => void = () => {};
    const deferredResume = new Promise<void>((resolve) => {
      resolveResume = resolve;
    });
    vi.spyOn(ctx, "resume").mockReturnValue(deferredResume);
    const createBufferSourceSpy = vi.spyOn(ctx, "createBufferSource");

    const playPromise = engine.play();
    engine.dispose(); // lands inside play()'s await-resume gap

    resolveResume();
    await playPromise;

    expect(createBufferSourceSpy).not.toHaveBeenCalled();
    expect(engine.isPlaying()).toBe(false);
  });

  it("guards against a second overlapping play() starting sources again while the first awaits resume()", async () => {
    const ctx = createFakeAudioContext({ initialState: "suspended" });
    const { scheduleFrame, cancelFrame } = manualScheduler();
    const buffer = makeFakeAudioBuffer({ duration: 10 });
    const engine = createEngine({
      audioContextFactory: () => ctx,
      fetchImpl: fakeFetch({ "a.mp3": buffer }),
      scheduleFrame,
      cancelFrame,
    });
    await engine.load([{ id: "a", url: "a.mp3" }]);

    // A deferred, externally-controlled resume() so both play() calls can
    // be issued before resume() settles - reproducing the concurrent-play
    // race across the await gap.
    let resolveResume: () => void = () => {};
    const deferredResume = new Promise<void>((resolve) => {
      resolveResume = resolve;
    });
    vi.spyOn(ctx, "resume").mockReturnValue(deferredResume);
    const createBufferSourceSpy = vi.spyOn(ctx, "createBufferSource");

    const first = engine.play();
    const second = engine.play(); // overlaps the first call's still-pending resume()

    resolveResume();
    await Promise.all([first, second]);

    // Exactly one source per lane (one lane here) - the reentrant second
    // call must have been guarded out, not started its own sources and
    // orphaned the first call's.
    expect(createBufferSourceSpy).toHaveBeenCalledTimes(1);
    expect(ctx.createdSources).toHaveLength(1);
    expect(engine.isPlaying()).toBe(true);
  });
});
