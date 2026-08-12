import { describe, expect, it } from "vitest";
import { computeEffectiveGain } from "./mixGain";

describe("computeEffectiveGain", () => {
  it("returns the lane's own gain when nothing is soloed or muted", () => {
    expect(computeEffectiveGain({ gain: 0.7, muted: false, solo: false }, false)).toBe(0.7);
  });

  it("returns 0 when the lane itself is muted, regardless of solo state", () => {
    expect(computeEffectiveGain({ gain: 1, muted: true, solo: false }, false)).toBe(0);
    expect(computeEffectiveGain({ gain: 1, muted: true, solo: true }, true)).toBe(0);
  });

  it("returns 0 for a non-soloed lane when some other lane is soloed", () => {
    expect(computeEffectiveGain({ gain: 1, muted: false, solo: false }, true)).toBe(0);
  });

  it("returns the lane's own gain for the soloed lane itself", () => {
    expect(computeEffectiveGain({ gain: 0.5, muted: false, solo: true }, true)).toBe(0.5);
  });
});
