import { afterEach, describe, expect, it, vi } from "vitest";
import { isFakeRecipeEnabled } from "./devFlags";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("isFakeRecipeEnabled", () => {
  it("is false by default", () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "");
    expect(isFakeRecipeEnabled()).toBe(false);
  });

  it("is true when explicitly enabled", () => {
    vi.stubEnv("VITE_ENABLE_FAKE_RECIPE", "true");
    expect(isFakeRecipeEnabled()).toBe(true);
  });
});
