import type {
  AudioEngine, EngineAudioBuffer, EngineAudioContext, EngineBufferSourceNode,
  EngineDestinationNode, EngineGainNode, EngineLane,
} from "./engine";
import type { EngineOfflineAudioContext } from "./exportMix";

export function makeFakeAudioBuffer(options: {
  duration: number;
  sampleRate?: number;
  numberOfChannels?: number;
  fill?: (channel: number, index: number, sampleRate: number) => number;
}): EngineAudioBuffer {
  const sampleRate = options.sampleRate ?? 44100;
  const numberOfChannels = options.numberOfChannels ?? 1;
  const length = Math.max(1, Math.round(options.duration * sampleRate));
  const channels: Float32Array[] = [];
  for (let channel = 0; channel < numberOfChannels; channel++) {
    const data = new Float32Array(length);
    if (options.fill) {
      for (let i = 0; i < length; i++) data[i] = options.fill(channel, i, sampleRate);
    }
    channels.push(data);
  }
  return {
    duration: length / sampleRate,
    length,
    sampleRate,
    numberOfChannels,
    getChannelData: (channel: number) => channels[channel],
  };
}

/** Serializes a fake buffer's raw samples as JSON text inside an ArrayBuffer
 * - a bespoke, tiny, made-up "codec" that only this test double understands.
 * It stands in for `fetch(url).then(r => r.arrayBuffer())` + real audio
 * decoding, neither of which exist in jsdom; it never touches a real codec
 * or a real AudioContext. */
export function encodeFakeAudioBuffer(buffer: EngineAudioBuffer): ArrayBuffer {
  const channels: number[][] = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(Array.from(buffer.getChannelData(c)));
  const json = JSON.stringify({ sampleRate: buffer.sampleRate, channels });
  return new TextEncoder().encode(json).buffer;
}

function decodeFakeAudioBuffer(data: ArrayBuffer): EngineAudioBuffer {
  const parsed = JSON.parse(new TextDecoder().decode(data)) as { sampleRate: number; channels: number[][] };
  const channels = parsed.channels.map((values) => Float32Array.from(values));
  const length = channels[0]?.length ?? 0;
  return {
    sampleRate: parsed.sampleRate,
    numberOfChannels: channels.length,
    length,
    duration: length / parsed.sampleRate,
    getChannelData: (channel: number) => channels[channel],
  };
}

class FakeGainNode implements EngineGainNode {
  gain = { value: 1 };
  connect(): void {}
  disconnect(): void {}
}

class FakeBufferSourceNode implements EngineBufferSourceNode {
  buffer: EngineAudioBuffer | null = null;
  onended: (() => void) | null = null;
  /** Test-observable: set true once `stop()` has been called, so tests can
   * assert that a prior lane's source was actually torn down (e.g. by
   * `engine.load()` reloading tracks) rather than left dangling. */
  stopped = false;
  connect(): void {}
  disconnect(): void {}
  start(): void {}
  stop(): void {
    this.stopped = true;
    this.onended?.();
  }
}

export interface FakeAudioContext extends EngineAudioContext {
  advanceTime(seconds: number): void;
  /** Every `EngineBufferSourceNode` this context has vended via
   * `createBufferSource()`, in creation order - lets tests assert on
   * teardown (`stopped`) of sources from a previous `load()`/`play()`. */
  createdSources: { stopped: boolean }[];
}

/** The one real-time `EngineAudioContext` fake every engine/component test
 * uses: a controllable clock (`advanceTime`) plus no-op audio nodes. Audio
 * *content* correctness for the real-time path is verified via
 * `getEffectiveGain`/`getCurrentTime`, not by inspecting node internals -
 * see `exportMix.test.ts` (Task 9) for a fake that *does* mix real signal,
 * where that matters (offline rendering).
 *
 * `initialState` defaults to `"running"` (unaffected downstream tests keep
 * working unchanged); pass `"suspended"` to simulate a browser's autoplay
 * policy - `resume()` flips the state to `"running"`, matching real
 * `AudioContext` behavior. */
export function createFakeAudioContext(
  options: { initialState?: "running" | "suspended" | "closed" } = {},
): FakeAudioContext {
  let time = 0;
  let state: "running" | "suspended" | "closed" = options.initialState ?? "running";
  const createdSources: FakeBufferSourceNode[] = [];
  return {
    get currentTime() {
      return time;
    },
    get state() {
      return state;
    },
    destination: {} as EngineDestinationNode,
    createBufferSource: () => {
      const source = new FakeBufferSourceNode();
      createdSources.push(source);
      return source;
    },
    createGain: () => new FakeGainNode(),
    decodeAudioData: async (data: ArrayBuffer) => decodeFakeAudioBuffer(data),
    resume: async () => {
      state = "running";
    },
    close: async () => {
      state = "closed";
    },
    advanceTime(seconds: number) {
      time += seconds;
    },
    createdSources,
  };
}

/** A full `AudioEngine` fake for component tests (Mixer/LyricEditor) that
 * don't want to exercise the real engine's clock/scheduling math - just its
 * public surface. `overrides` lets a test replace individual methods (e.g.
 * `onTick`) with a `vi.fn()` to assert on calls. */
export function makeFakeEngine(overrides: Partial<AudioEngine> = {}): AudioEngine {
  const buffers = new Map<string, EngineAudioBuffer>();
  const lanes = new Map<string, EngineLane>();
  let time = 0;
  let playing = false;
  let duration = 0;
  const tickListeners = new Set<(time: number) => void>();

  const base: AudioEngine = {
    async load(tracks, onProgress = () => {}) {
      const total = tracks.length;
      let loadedCount = 0;
      for (const track of tracks) {
        const buffer = makeFakeAudioBuffer({ duration: 3 });
        buffers.set(track.id, buffer);
        lanes.set(track.id, { id: track.id, buffer, gain: 1, muted: false, solo: false });
        duration = Math.max(duration, buffer.duration);
        loadedCount += 1;
        onProgress(loadedCount, total);
      }
    },
    async play() { playing = true; },
    pause() { playing = false; },
    seek(t: number) { time = t; },
    setLoopRegion() {},
    setGain(id, value) { const lane = lanes.get(id); if (lane) lane.gain = value; },
    setMuted(id, muted) { const lane = lanes.get(id); if (lane) lane.muted = muted; },
    setSolo(id, solo) { const lane = lanes.get(id); if (lane) lane.solo = solo; },
    getEffectiveGain(id) {
      const lane = lanes.get(id);
      if (!lane) return 0;
      const anySoloed = [...lanes.values()].some((l) => l.solo);
      if (lane.muted) return 0;
      if (anySoloed && !lane.solo) return 0;
      return lane.gain;
    },
    getLanes: () => [...lanes.values()],
    getBuffer: (id) => buffers.get(id) ?? null,
    getCurrentTime: () => time,
    getDuration: () => duration,
    isPlaying: () => playing,
    onTick(cb) {
      tickListeners.add(cb);
      return () => tickListeners.delete(cb);
    },
    dispose() { tickListeners.clear(); },
  };
  return { ...base, ...overrides };
}

class FakeOfflineGainNode implements EngineGainNode {
  gain = { value: 1 };
  connectedTo: (FakeOfflineGainNode | EngineDestinationNode)[] = [];
  connect(destination: FakeOfflineGainNode | EngineDestinationNode): void {
    this.connectedTo.push(destination);
  }
  disconnect(): void {
    this.connectedTo = [];
  }
}

class FakeOfflineBufferSourceNode implements EngineBufferSourceNode {
  buffer: EngineAudioBuffer | null = null;
  onended: (() => void) | null = null;
  connectedTo: (FakeOfflineGainNode | EngineDestinationNode)[] = [];
  started = false;
  connect(destination: FakeOfflineGainNode | EngineDestinationNode): void {
    this.connectedTo.push(destination);
  }
  disconnect(): void {
    this.connectedTo = [];
  }
  start(): void {
    this.started = true;
  }
  stop(): void {
    this.onended?.();
  }
}

/** A fake `OfflineAudioContext` that actually mixes signal (unlike the
 * realtime `FakeAudioContext` above, whose nodes are no-ops) - this is the
 * only way to verify `renderMix`'s gain/mute/solo wiring produces correct
 * numeric output without a real browser. It only understands the exact
 * connection shape `renderMix` builds: `source -> gain -> destination` (a
 * fixed 2-hop chain, never deeper), which is all this codebase ever needs. */
export function createFakeOfflineAudioContext(
  numberOfChannels: number, length: number, sampleRate: number,
): EngineOfflineAudioContext {
  const destinationMarker: EngineDestinationNode = { __brand: "destination" };
  const sources: FakeOfflineBufferSourceNode[] = [];

  function gainToDestination(source: FakeOfflineBufferSourceNode): number | null {
    for (const next of source.connectedTo) {
      if (next === destinationMarker) return 1;
      if (next instanceof FakeOfflineGainNode && next.connectedTo.includes(destinationMarker)) {
        return next.gain.value;
      }
    }
    return null;
  }

  return {
    currentTime: 0,
    destination: destinationMarker,
    state: "running",
    createBufferSource: () => {
      const source = new FakeOfflineBufferSourceNode();
      sources.push(source);
      return source;
    },
    createGain: () => new FakeOfflineGainNode(),
    decodeAudioData: async (data: ArrayBuffer) => decodeFakeAudioBuffer(data),
    resume: async () => {},
    close: async () => {},
    async startRendering(): Promise<EngineAudioBuffer> {
      const output: Float32Array[] = Array.from({ length: numberOfChannels }, () => new Float32Array(length));
      for (const source of sources) {
        if (!source.started || !source.buffer) continue;
        const gain = gainToDestination(source);
        if (gain === null || gain === 0) continue;
        for (let channel = 0; channel < numberOfChannels; channel++) {
          const sourceChannel = Math.min(channel, source.buffer.numberOfChannels - 1);
          const data = source.buffer.getChannelData(sourceChannel);
          for (let i = 0; i < Math.min(length, data.length); i++) {
            output[channel][i] += data[i] * gain;
          }
        }
      }
      return {
        duration: length / sampleRate, length, sampleRate, numberOfChannels,
        getChannelData: (channel: number) => output[channel],
      };
    },
  };
}
