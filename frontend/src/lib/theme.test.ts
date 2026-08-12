import { describe, expect, it } from "vitest";
import {
  THEME_STORAGE_KEY,
  applyThemePreference,
  loadThemePreference,
  setThemePreference,
} from "./theme";

class FakeStorage implements Storage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  clear(): void { this.data.clear(); }
  getItem(key: string): string | null { return this.data.get(key) ?? null; }
  key(index: number): string | null { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string): void { this.data.delete(key); }
  setItem(key: string, value: string): void { this.data.set(key, value); }
}

describe("theme preference", () => {
  it("defaults invalid or missing values to system", () => {
    const storage = new FakeStorage();
    expect(loadThemePreference(storage)).toBe("system");
    storage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(loadThemePreference(storage)).toBe("system");
  });

  it("persists and applies light, dark, and system choices", () => {
    const storage = new FakeStorage();
    const root = document.createElement("html");

    for (const preference of ["light", "dark", "system"] as const) {
      setThemePreference(preference, storage, root);
      expect(storage.getItem(THEME_STORAGE_KEY)).toBe(preference);
      expect(root.dataset.theme).toBe(preference);
    }
  });

  it("can apply a preference without changing storage", () => {
    const root = document.createElement("html");
    applyThemePreference("dark", root);
    expect(root.dataset.theme).toBe("dark");
  });
});
