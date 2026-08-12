import { Mp3Encoder } from "@breezystack/lamejs";
import { computeEffectiveGain } from "./mixGain";
import type { EngineAudioBuffer, EngineAudioContext } from "./engine";

function floatSampleToInt16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return Math.round(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
}

export function encodeWav(buffer: EngineAudioBuffer): ArrayBuffer {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const numFrames = buffer.length;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numFrames * blockAlign;
  const arrayBuffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(arrayBuffer);

  function writeString(offset: number, text: string): void {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true); // byte rate
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  const channelData: Float32Array[] = [];
  for (let c = 0; c < numChannels; c++) channelData.push(buffer.getChannelData(c));

  let offset = 44;
  for (let frame = 0; frame < numFrames; frame++) {
    for (let channel = 0; channel < numChannels; channel++) {
      view.setInt16(offset, floatSampleToInt16(channelData[channel][frame]), true);
      offset += 2;
    }
  }

  return arrayBuffer;
}

export const MP3_ENCODE_BLOCK_SIZE = 1152; // samples per MP3 frame - lamejs's fixed block size
export const MP3_SUPPORTED_SAMPLE_RATES = new Set([8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000]);

export function encodeMp3(buffer: EngineAudioBuffer, kbps = 192): ArrayBuffer {
  if (!MP3_SUPPORTED_SAMPLE_RATES.has(buffer.sampleRate)) {
    throw new Error(
      `MP3 export does not support ${buffer.sampleRate} Hz audio; export WAV instead`,
    );
  }

  const numChannels = Math.min(2, buffer.numberOfChannels);
  const numFrames = buffer.length;
  const encoder = new Mp3Encoder(numChannels, buffer.sampleRate, kbps);

  const left = buffer.getChannelData(0);
  const right = numChannels === 2 ? buffer.getChannelData(1) : null;

  const chunks: Uint8Array[] = [];
  for (let start = 0; start < numFrames; start += MP3_ENCODE_BLOCK_SIZE) {
    const end = Math.min(start + MP3_ENCODE_BLOCK_SIZE, numFrames);
    const leftBlock = new Int16Array(end - start);
    for (let i = start; i < end; i++) leftBlock[i - start] = floatSampleToInt16(left[i]);

    let mp3Chunk: Uint8Array;
    if (right) {
      const rightBlock = new Int16Array(end - start);
      for (let i = start; i < end; i++) rightBlock[i - start] = floatSampleToInt16(right[i]);
      mp3Chunk = encoder.encodeBuffer(leftBlock, rightBlock);
    } else {
      mp3Chunk = encoder.encodeBuffer(leftBlock);
    }
    if (mp3Chunk.length > 0) chunks.push(mp3Chunk);
  }

  const finalChunk = encoder.flush();
  if (finalChunk.length > 0) chunks.push(finalChunk);

  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Uint8Array(totalLength);
  let mergeOffset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, mergeOffset);
    mergeOffset += chunk.length;
  }
  return merged.buffer;
}

export interface EngineOfflineAudioContext extends EngineAudioContext {
  startRendering(): Promise<EngineAudioBuffer>;
}

export type OfflineContextFactory = (
  numberOfChannels: number, length: number, sampleRate: number,
) => EngineOfflineAudioContext;

export function defaultOfflineContextFactory(
  numberOfChannels: number, length: number, sampleRate: number,
): EngineOfflineAudioContext {
  const Ctor = (globalThis as unknown as {
    OfflineAudioContext?: new (channels: number, length: number, sampleRate: number) => unknown;
  }).OfflineAudioContext;
  if (!Ctor) {
    throw new Error(
      "No OfflineAudioContext available; pass offlineContextFactory (in tests, inject a fake - see testFakes.ts)",
    );
  }
  return new Ctor(numberOfChannels, length, sampleRate) as unknown as EngineOfflineAudioContext;
}

export interface MixLane {
  id: string;
  buffer: EngineAudioBuffer;
  gain: number;
  muted: boolean;
  solo: boolean;
}

export async function renderMix(
  lanes: MixLane[],
  offlineContextFactory: OfflineContextFactory,
  format: "wav" | "mp3" = "wav",
): Promise<EngineAudioBuffer> {
  if (lanes.length === 0) throw new Error("renderMix requires at least one lane");
  // Lanes can come from stems decoded at different native sample rates (e.g.
  // a 44.1kHz original alongside 48kHz-decoded stems). Deriving length from
  // lanes[0]'s rate while taking the max frame COUNT across lanes mixes
  // frame counts computed at different rates, which is meaningless and can
  // under- or over-allocate the render (phantom silence or truncated audio).
  // Instead: pick the highest sample rate present (for output quality), then
  // derive the output length from the longest lane's *duration* re-expressed
  // at that chosen rate.
  const nativeSampleRate = Math.max(...lanes.map((lane) => lane.buffer.sampleRate));
  // lamejs supports rates only up to 48 kHz. Asking OfflineAudioContext for
  // 48 kHz performs the resampling during the mix render, so high-resolution
  // sources can still be exported as MP3 without a late encoder failure.
  // WAV keeps the highest native rate and therefore remains lossless.
  const sampleRate = format === "mp3" ? Math.min(nativeSampleRate, 48000) : nativeSampleRate;
  const numberOfChannels = Math.max(...lanes.map((lane) => lane.buffer.numberOfChannels));
  const longestDuration = Math.max(...lanes.map((lane) => lane.buffer.duration));
  const length = Math.ceil(longestDuration * sampleRate);
  const offlineCtx = offlineContextFactory(numberOfChannels, length, sampleRate);

  const anySoloed = lanes.some((lane) => lane.solo);
  for (const lane of lanes) {
    const effectiveGain = computeEffectiveGain(lane, anySoloed);
    if (effectiveGain === 0) continue;
    const source = offlineCtx.createBufferSource();
    source.buffer = lane.buffer;
    const gainNode = offlineCtx.createGain();
    gainNode.gain.value = effectiveGain;
    source.connect(gainNode);
    gainNode.connect(offlineCtx.destination);
    source.start(0, 0);
  }

  return offlineCtx.startRendering();
}

function triggerDownload(filename: string, data: ArrayBuffer, mimeType: string): void {
  const blob = new Blob([data], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function downloadWav(filename: string, wavData: ArrayBuffer): void {
  triggerDownload(filename, wavData, "audio/wav");
}

export function downloadMp3(filename: string, mp3Data: ArrayBuffer): void {
  triggerDownload(filename, mp3Data, "audio/mpeg");
}
