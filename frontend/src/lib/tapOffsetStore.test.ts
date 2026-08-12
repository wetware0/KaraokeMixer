import { afterEach, describe, expect, it } from "vitest";
import {
  DEFAULT_TAP_OFFSET_SECONDS, TAP_OFFSET_STORAGE_KEY,
  clampTapOffsetSeconds, loadTapOffsetSeconds, saveTapOffsetSeconds,
} from "./tapOffsetStore";

afterEach(() => {
  localStorage.clear();
});

describe("clampTapOffsetSeconds", () => {
  it("clamps to [0, 0.5]", () => {
    expect(clampTapOffsetSeconds(-1)).toBe(0);
    expect(clampTapOffsetSeconds(0.3)).toBe(0.3);
    expect(clampTapOffsetSeconds(10)).toBe(0.5);
  });
});

describe("loadTapOffsetSeconds", () => {
  it("returns the default when nothing is stored", () => {
    expect(loadTapOffsetSeconds()).toBe(DEFAULT_TAP_OFFSET_SECONDS);
  });

  it("returns a previously saved, clamped value", () => {
    saveTapOffsetSeconds(0.25);
    expect(loadTapOffsetSeconds()).toBe(0.25);
  });

  it("falls back to the default when the stored value is not a finite number", () => {
    localStorage.setItem(TAP_OFFSET_STORAGE_KEY, "not-a-number");
    expect(loadTapOffsetSeconds()).toBe(DEFAULT_TAP_OFFSET_SECONDS);
  });

  it("reads from an injected storage instead of the real localStorage", () => {
    const fake = new Map<string, string>();
    const fakeStorage = {
      getItem: (key: string) => fake.get(key) ?? null,
      setItem: (key: string, value: string) => void fake.set(key, value),
    } as Storage;

    saveTapOffsetSeconds(0.4, fakeStorage);

    expect(loadTapOffsetSeconds(fakeStorage)).toBe(0.4);
    expect(localStorage.getItem(TAP_OFFSET_STORAGE_KEY)).toBeNull(); // real storage untouched
  });
});

describe("saveTapOffsetSeconds", () => {
  it("clamps before persisting", () => {
    saveTapOffsetSeconds(999);
    expect(localStorage.getItem(TAP_OFFSET_STORAGE_KEY)).toBe("0.5");
  });
});
