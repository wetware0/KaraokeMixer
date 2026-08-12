import { computeEffectiveGain } from "./mixGain";

export interface EngineAudioBuffer {
  readonly duration: number;
  readonly length: number;
  readonly sampleRate: number;
  readonly numberOfChannels: number;
  getChannelData(channel: number): Float32Array;
}

export interface EngineDestinationNode {
  readonly __brand?: "destination";
}

export interface EngineGainNode {
  gain: { value: number };
  connect(destination: EngineGainNode | EngineDestinationNode): void;
  disconnect(): void;
}

export interface EngineBufferSourceNode {
  buffer: EngineAudioBuffer | null;
  onended: (() => void) | null;
  connect(destination: EngineGainNode | EngineDestinationNode): void;
  disconnect(): void;
  start(when?: number, offset?: number): void;
  stop(when?: number): void;
}

export interface EngineAudioContext {
  readonly currentTime: number;
  readonly destination: EngineDestinationNode;
  readonly state: "running" | "suspended" | "closed";
  createBufferSource(): EngineBufferSourceNode;
  createGain(): EngineGainNode;
  decodeAudioData(data: ArrayBuffer): Promise<EngineAudioBuffer>;
  resume(): Promise<void>;
  close(): Promise<void>;
}

export interface EngineLane {
  id: string;
  buffer: EngineAudioBuffer;
  gain: number;
  muted: boolean;
  solo: boolean;
}

export interface LoopRegion {
  start: number;
  end: number;
}

export interface CreateEngineOptions {
  audioContextFactory?: () => EngineAudioContext;
  fetchImpl?: (url: string) => Promise<ArrayBuffer>;
  scheduleFrame?: (callback: () => void) => number;
  cancelFrame?: (handle: number) => void;
}

export interface AudioEngine {
  /** `onProgress`, when given, is called once per lane as soon as that
   * lane's fetch+decode completes (in whatever order lanes actually
   * settle - not necessarily the input order), with the running count of
   * lanes decoded so far and the total lane count. Defaults to a no-op, so
   * existing callers are unaffected. */
  load(tracks: { id: string; url: string }[], onProgress?: (loaded: number, total: number) => void): Promise<void>;
  /** Resolves once playback has actually started: if the underlying
   * `AudioContext` is `"suspended"` (the default browser autoplay policy
   * for a freshly-created context), `play()` awaits `ctx.resume()` before
   * starting sources, so callers can rely on `isPlaying()` being true and
   * sources being audible once the returned promise settles. */
  play(): Promise<void>;
  pause(): void;
  seek(time: number): void;
  setLoopRegion(region: LoopRegion | null): void;
  setGain(laneId: string, value: number): void;
  setMuted(laneId: string, muted: boolean): void;
  setSolo(laneId: string, solo: boolean): void;
  getEffectiveGain(laneId: string): number;
  getLanes(): EngineLane[];
  getBuffer(laneId: string): EngineAudioBuffer | null;
  getCurrentTime(): number;
  getDuration(): number;
  isPlaying(): boolean;
  onTick(callback: (time: number) => void): () => void;
  dispose(): void;
}

function defaultAudioContextFactory(): EngineAudioContext {
  const Ctor = (globalThis as unknown as { AudioContext?: new () => unknown }).AudioContext;
  if (!Ctor) {
    throw new Error(
      "No AudioContext available; pass audioContextFactory (in tests, inject a fake - see testFakes.ts)",
    );
  }
  return new Ctor() as unknown as EngineAudioContext;
}

function defaultFetchImpl(url: string): Promise<ArrayBuffer> {
  return fetch(url).then((response) => response.arrayBuffer());
}

function defaultScheduleFrame(callback: () => void): number {
  if (typeof requestAnimationFrame === "function") return requestAnimationFrame(callback);
  return setTimeout(callback, 16) as unknown as number;
}

function defaultCancelFrame(handle: number): void {
  if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(handle);
  else clearTimeout(handle);
}

interface InternalLane {
  id: string;
  buffer: EngineAudioBuffer;
  gainValue: number;
  muted: boolean;
  solo: boolean;
  gainNode: EngineGainNode;
  source: EngineBufferSourceNode | null;
}

export function createEngine(options: CreateEngineOptions = {}): AudioEngine {
  const audioContextFactory = options.audioContextFactory ?? defaultAudioContextFactory;
  const fetchImpl = options.fetchImpl ?? defaultFetchImpl;
  const scheduleFrame = options.scheduleFrame ?? defaultScheduleFrame;
  const cancelFrame = options.cancelFrame ?? defaultCancelFrame;

  const ctx = audioContextFactory();
  const lanes = new Map<string, InternalLane>();
  let duration = 0;
  let pausedAt = 0;
  let playStartContextTime: number | null = null;
  // Guards the await gap in play() below: without it, two overlapping
  // play() calls on a suspended context would both pass the
  // `playStartContextTime !== null` check (which only becomes true *after*
  // the await, inside startSources()), both resume(), and both
  // startSources() - orphaning the first call's sources unstopped.
  let startPending = false;
  let loopRegion: LoopRegion | null = null;
  const tickListeners = new Set<(time: number) => void>();
  let frameHandle: number | null = null;
  // Guards a dispose() landing inside play()'s await-resume gap: without it,
  // a component that unmounts (and disposes the engine) while a suspended
  // context's resume() is still pending would, once resume() settles, go on
  // to start sources on a context that's being (or has been) closed.
  let disposed = false;

  function anySoloed(): boolean {
    return [...lanes.values()].some((lane) => lane.solo);
  }

  function applyGains(): void {
    const soloed = anySoloed();
    for (const lane of lanes.values()) {
      // computeEffectiveGain expects {gain, muted, solo}; InternalLane's
      // field is named `gainValue` (not `gain`) so it can't be passed
      // directly - constructing the shape explicitly here is what keeps
      // this a real compile error (TS2345) if the mapping is ever dropped,
      // instead of a silent `undefined` gain at runtime.
      lane.gainNode.gain.value = computeEffectiveGain(
        { gain: lane.gainValue, muted: lane.muted, solo: lane.solo }, soloed,
      );
    }
  }

  /** Stops and tears down every currently-loaded lane's source and gain
   * node, then clears the lanes map. Used by `load()` so that reloading
   * tracks (e.g. switching songs) can never leave a prior lane's source
   * running underneath the new one - in a real audio graph that would be
   * audible bleed-through of the old track. */
  function stopAndDisposeLanes(): void {
    stopSources();
    for (const lane of lanes.values()) {
      lane.gainNode.disconnect();
    }
    lanes.clear();
  }

  async function load(
    tracks: { id: string; url: string }[],
    onProgress: (loaded: number, total: number) => void = () => {},
  ): Promise<void> {
    stopAndDisposeLanes();
    pausedAt = 0;
    playStartContextTime = null;
    loopRegion = null;
    duration = 0;
    const total = tracks.length;
    let loadedCount = 0;
    // Fetch+decode every lane concurrently (sequential awaiting here was the
    // slow path - each lane's network+decode time added up instead of
    // overlapping). Promise.all preserves the input order in its resolved
    // array regardless of which lane's fetch/decode actually settles first,
    // so lanes are still inserted into the `lanes` map in the original
    // request order once every lane has resolved. onProgress fires in
    // whichever order lanes actually settle - that order isn't guaranteed to
    // match the input order, only the final counts are.
    const decoded = await Promise.all(
      tracks.map(async (track) => {
        const data = await fetchImpl(track.url);
        const buffer = await ctx.decodeAudioData(data);
        loadedCount += 1;
        onProgress(loadedCount, total);
        return { track, buffer };
      }),
    );
    for (const { track, buffer } of decoded) {
      const gainNode = ctx.createGain();
      gainNode.connect(ctx.destination);
      lanes.set(track.id, {
        id: track.id, buffer, gainValue: 1, muted: false, solo: false, gainNode, source: null,
      });
      duration = Math.max(duration, buffer.duration);
    }
    applyGains();
  }

  function startSources(): void {
    playStartContextTime = ctx.currentTime;
    for (const lane of lanes.values()) {
      const source = ctx.createBufferSource();
      source.buffer = lane.buffer;
      source.connect(lane.gainNode);
      source.start(ctx.currentTime, pausedAt);
      lane.source = source;
    }
  }

  function stopSources(): void {
    for (const lane of lanes.values()) {
      if (lane.source) {
        lane.source.onended = null;
        lane.source.stop();
        lane.source = null;
      }
    }
  }

  async function play(): Promise<void> {
    if (disposed || playStartContextTime !== null || startPending) return;
    startPending = true;
    try {
      // Real browsers create AudioContexts "suspended" under autoplay
      // policy; resume() must settle before sources are started, otherwise
      // the first play is silent. When already "running" this branch is
      // skipped and startSources()/ensureTicking() below run synchronously,
      // same as before this fix.
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      // Belt-and-braces: a concurrent load() could have reset transport
      // state (and started fresh playback of its own) while we were
      // awaiting resume() above. Also recheck disposed/lanes: a dispose()
      // landing in this await gap (e.g. an unmounting component) must not
      // let this call go on to start sources on a context that's being
      // closed.
      if (disposed || lanes.size === 0 || playStartContextTime !== null) return;
      startSources();
      ensureTicking();
    } finally {
      startPending = false;
    }
  }

  function pause(): void {
    if (playStartContextTime === null) return;
    pausedAt = getCurrentTime();
    stopSources();
    playStartContextTime = null;
  }

  function seek(time: number): void {
    const clamped = Math.max(0, Math.min(time, duration));
    const wasPlaying = playStartContextTime !== null;
    if (wasPlaying) stopSources();
    pausedAt = clamped;
    if (wasPlaying) startSources();
  }

  function setLoopRegion(region: LoopRegion | null): void {
    loopRegion = region;
  }

  function setGain(laneId: string, value: number): void {
    const lane = lanes.get(laneId);
    if (!lane) return;
    lane.gainValue = value;
    applyGains();
  }

  function setMuted(laneId: string, muted: boolean): void {
    const lane = lanes.get(laneId);
    if (!lane) return;
    lane.muted = muted;
    applyGains();
  }

  function setSolo(laneId: string, solo: boolean): void {
    const lane = lanes.get(laneId);
    if (!lane) return;
    lane.solo = solo;
    applyGains();
  }

  function getEffectiveGain(laneId: string): number {
    const lane = lanes.get(laneId);
    if (!lane) return 0;
    return computeEffectiveGain({ gain: lane.gainValue, muted: lane.muted, solo: lane.solo }, anySoloed());
  }

  function getLanes(): EngineLane[] {
    return [...lanes.values()].map((lane) => ({
      id: lane.id, buffer: lane.buffer, gain: lane.gainValue, muted: lane.muted, solo: lane.solo,
    }));
  }

  function getBuffer(laneId: string): EngineAudioBuffer | null {
    return lanes.get(laneId)?.buffer ?? null;
  }

  function getCurrentTime(): number {
    if (playStartContextTime === null) return pausedAt;
    return pausedAt + (ctx.currentTime - playStartContextTime);
  }

  function getDuration(): number {
    return duration;
  }

  function isPlaying(): boolean {
    return playStartContextTime !== null;
  }

  function tick(): void {
    frameHandle = null;
    if (loopRegion && getCurrentTime() >= loopRegion.end) {
      seek(loopRegion.start);
    } else if (playStartContextTime !== null && getCurrentTime() >= duration) {
      pause();
      seek(0);
    }
    const time = getCurrentTime();
    for (const listener of tickListeners) listener(time);
    ensureTicking();
  }

  function ensureTicking(): void {
    if (frameHandle === null && (playStartContextTime !== null || tickListeners.size > 0)) {
      frameHandle = scheduleFrame(tick);
    }
  }

  function onTick(callback: (time: number) => void): () => void {
    tickListeners.add(callback);
    ensureTicking();
    return () => {
      tickListeners.delete(callback);
    };
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    pause(); // stops any currently-playing sources before we tear anything else down
    startPending = false;
    playStartContextTime = null;
    if (frameHandle !== null) {
      cancelFrame(frameHandle);
      frameHandle = null;
    }
    tickListeners.clear();
    lanes.clear();
    void ctx.close();
  }

  return {
    load, play, pause, seek, setLoopRegion, setGain, setMuted, setSolo, getEffectiveGain,
    getLanes, getBuffer, getCurrentTime, getDuration, isPlaying, onTick, dispose,
  };
}
