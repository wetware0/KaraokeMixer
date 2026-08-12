import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadMp3, downloadWav, encodeMp3, encodeWav, renderMix } from "./exportMix";
import { createFakeOfflineAudioContext, makeFakeAudioBuffer } from "./testFakes";

describe("encodeWav", () => {
  it("encodes a 16-bit PCM WAV with a correct RIFF header and sample data", () => {
    const buffer = makeFakeAudioBuffer({ duration: 2 / 8000, sampleRate: 8000, numberOfChannels: 1 });
    buffer.getChannelData(0).set([1, -1]);

    const wav = encodeWav(buffer);
    const view = new DataView(wav);

    expect(new TextDecoder().decode(wav.slice(0, 4))).toBe("RIFF");
    expect(new TextDecoder().decode(wav.slice(8, 12))).toBe("WAVE");
    expect(view.getUint16(20, true)).toBe(1); // PCM format
    expect(view.getUint16(22, true)).toBe(1); // numChannels
    expect(view.getUint32(24, true)).toBe(8000); // sampleRate
    expect(view.getUint16(34, true)).toBe(16); // bitsPerSample
    expect(view.getUint32(40, true)).toBe(4); // dataSize: 2 frames * 1 channel * 2 bytes
    expect(view.getInt16(44, true)).toBe(32767); // sample 1.0 -> max positive int16
    expect(view.getInt16(46, true)).toBe(-32768); // sample -1.0 -> max negative int16
  });
});

describe("renderMix", () => {
  it("sums soloed/muted lanes correctly through a fake offline context", async () => {
    const laneA = makeFakeAudioBuffer({ duration: 2 / 8000, sampleRate: 8000 });
    laneA.getChannelData(0).set([1, 1]);
    const laneB = makeFakeAudioBuffer({ duration: 2 / 8000, sampleRate: 8000 });
    laneB.getChannelData(0).set([1, 1]);

    const rendered = await renderMix(
      [
        { id: "a", buffer: laneA, gain: 0.5, muted: false, solo: false },
        { id: "b", buffer: laneB, gain: 1, muted: true, solo: false },
      ],
      createFakeOfflineAudioContext,
    );

    // b is muted -> 0 contribution; a contributes 1 * 0.5 = 0.5 per sample
    expect(Array.from(rendered.getChannelData(0))).toEqual([0.5, 0.5]);
  });

  it("solos one lane, silencing the rest", async () => {
    const laneA = makeFakeAudioBuffer({ duration: 1 / 8000, sampleRate: 8000 });
    laneA.getChannelData(0).set([1]);
    const laneB = makeFakeAudioBuffer({ duration: 1 / 8000, sampleRate: 8000 });
    laneB.getChannelData(0).set([1]);

    const rendered = await renderMix(
      [
        { id: "a", buffer: laneA, gain: 1, muted: false, solo: true },
        { id: "b", buffer: laneB, gain: 1, muted: false, solo: false },
      ],
      createFakeOfflineAudioContext,
    );

    expect(rendered.getChannelData(0)[0]).toBe(1);
  });

  it("throws when given no lanes", async () => {
    await expect(renderMix([], createFakeOfflineAudioContext)).rejects.toThrow();
  });

  it("excludes a muted vocal lane's contribution when rendering a karaoke-preset-shaped mix (vocals off, original stays muted, other stems audible)", async () => {
    const vocals = makeFakeAudioBuffer({ duration: 1 / 8000, sampleRate: 8000 });
    vocals.getChannelData(0).set([1]);
    const original = makeFakeAudioBuffer({ duration: 1 / 8000, sampleRate: 8000 });
    original.getChannelData(0).set([1]);
    const drums = makeFakeAudioBuffer({ duration: 1 / 8000, sampleRate: 8000 });
    drums.getChannelData(0).set([1]);

    const rendered = await renderMix(
      [
        { id: "lead_vocals", buffer: vocals, gain: 1, muted: true, solo: false },
        { id: "original", buffer: original, gain: 1, muted: true, solo: false },
        { id: "drums", buffer: drums, gain: 1, muted: false, solo: false },
      ],
      createFakeOfflineAudioContext,
    );

    // Only drums (unmuted) contributes; both vocals and original are muted.
    expect(rendered.getChannelData(0)[0]).toBe(1);
  });

  it("derives sampleRate as the max across lanes and length from the longest lane's duration at that rate (not lanes[0]'s rate with a mismatched frame-count max)", async () => {
    // Equal durations, different native sample rates - length must reflect
    // duration * the CHOSEN (max) rate, not a max-frame-count computed
    // against the wrong lane's rate (which would produce phantom
    // silence/truncation when rates differ).
    const durationSeconds = 0.5;
    const laneLoRate = makeFakeAudioBuffer({ duration: durationSeconds, sampleRate: 44100 });
    const laneHiRate = makeFakeAudioBuffer({ duration: durationSeconds, sampleRate: 48000 });

    const rendered = await renderMix(
      [
        { id: "lo", buffer: laneLoRate, gain: 1, muted: false, solo: false },
        { id: "hi", buffer: laneHiRate, gain: 1, muted: false, solo: false },
      ],
      createFakeOfflineAudioContext,
    );

    expect(rendered.sampleRate).toBe(48000);
    expect(rendered.length).toBe(Math.ceil(durationSeconds * 48000));
  });

  it("resamples high-resolution sources to 48 kHz for MP3 but preserves their native rate for WAV", async () => {
    const highResolution = makeFakeAudioBuffer({ duration: 0.01, sampleRate: 192000 });
    const lanes = [{ id: "hi-res", buffer: highResolution, gain: 1, muted: false, solo: false }];

    const mp3Render = await renderMix(lanes, createFakeOfflineAudioContext, "mp3");
    const wavRender = await renderMix(lanes, createFakeOfflineAudioContext, "wav");

    expect(mp3Render.sampleRate).toBe(48000);
    expect(mp3Render.length).toBe(Math.ceil(0.01 * 48000));
    expect(wavRender.sampleRate).toBe(192000);
    expect(wavRender.length).toBe(Math.ceil(0.01 * 192000));
  });
});

describe("downloadWav", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("creates a blob URL, clicks a download anchor, and revokes the URL", () => {
    const createObjectURL = vi.fn(() => "blob:fake-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadWav("Song.mix.wav", new ArrayBuffer(8));

    expect(createObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake-url");
  });
});

describe("encodeMp3", () => {
  it("produces an ArrayBuffer starting with a valid MPEG audio frame sync word", () => {
    const buffer = makeFakeAudioBuffer({
      duration: 0.5, sampleRate: 8000, numberOfChannels: 1,
      fill: (_channel, index, sampleRate) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
    });

    const mp3 = encodeMp3(buffer);
    const view = new DataView(mp3);

    expect(mp3.byteLength).toBeGreaterThan(0);
    expect(view.getUint8(0)).toBe(0xff); // MPEG frame sync byte 1
    expect(view.getUint8(1) & 0xe0).toBe(0xe0); // frame sync byte 2's top 3 bits
  });

  it("encodes stereo input using both channels without throwing", () => {
    const buffer = makeFakeAudioBuffer({
      duration: 0.5, sampleRate: 8000, numberOfChannels: 2,
      fill: (channel, index, sampleRate) => Math.sin((2 * Math.PI * (440 + channel * 220) * index) / sampleRate) * 0.5,
    });

    const mp3 = encodeMp3(buffer);

    expect(mp3.byteLength).toBeGreaterThan(0);
  });

  it("produces meaningfully smaller output than the equivalent WAV encoding (sanity check that compression actually happened)", () => {
    const buffer = makeFakeAudioBuffer({
      duration: 1, sampleRate: 8000, numberOfChannels: 1,
      fill: (_channel, index, sampleRate) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
    });

    const mp3 = encodeMp3(buffer);
    const wav = encodeWav(buffer);

    expect(mp3.byteLength).toBeLessThan(wav.byteLength);
  });

  it("defaults to 192kbps but accepts an explicit bitrate", () => {
    const buffer = makeFakeAudioBuffer({
      duration: 0.5, sampleRate: 8000, numberOfChannels: 1,
      fill: (_channel, index, sampleRate) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
    });

    const default192 = encodeMp3(buffer);
    const low64 = encodeMp3(buffer, 64);

    expect(default192.byteLength).toBeGreaterThan(0);
    expect(low64.byteLength).toBeGreaterThan(0);
  });

  it("throws for unsupported sample rates and does not throw for supported rates", () => {
    const supportedBuffer = makeFakeAudioBuffer({
      duration: 0.5, sampleRate: 44100, numberOfChannels: 1,
      fill: (_channel, index, sampleRate) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
    });

    const unsupportedBuffer = makeFakeAudioBuffer({
      duration: 0.5, sampleRate: 44000, numberOfChannels: 1,
      fill: (_channel, index, sampleRate) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
    });

    expect(() => encodeMp3(supportedBuffer)).not.toThrow();
    expect(() => encodeMp3(unsupportedBuffer)).toThrow(/MP3 export does not support.*44000.*Hz/);
  });
});

describe("downloadMp3", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("creates a blob URL with the audio/mpeg type, clicks a download anchor, and revokes the URL", () => {
    const createObjectURL = vi.fn(() => "blob:fake-mp3-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadMp3("Song.mix.mp3", new ArrayBuffer(8));

    expect(createObjectURL).toHaveBeenCalled();
    const [blobArg] = createObjectURL.mock.calls[0];
    expect((blobArg as Blob).type).toBe("audio/mpeg");
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake-mp3-url");
  });
});
