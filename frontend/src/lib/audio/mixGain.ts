export interface MixGainLane {
  gain: number;
  muted: boolean;
  solo: boolean;
}

/** The karaoke-mixer solo/mute rule, shared verbatim between the real-time
 * engine (engine.ts) and the offline exporter (exportMix.ts) so the two
 * code paths can never drift: a muted lane is always silent; when any lane
 * is soloed, every non-soloed lane is silent too; otherwise a lane plays at
 * its own gain. */
export function computeEffectiveGain(lane: MixGainLane, anySoloed: boolean): number {
  if (lane.muted) return 0;
  if (anySoloed && !lane.solo) return 0;
  return lane.gain;
}
