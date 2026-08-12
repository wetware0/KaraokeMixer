export type ThemePreference = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "karaoke-mm.theme";

export function loadThemePreference(storage: Storage = window.localStorage): ThemePreference {
  const value = storage.getItem(THEME_STORAGE_KEY);
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

export function applyThemePreference(
  preference: ThemePreference,
  root: HTMLElement = document.documentElement,
): void {
  root.dataset.theme = preference;
}

export function setThemePreference(
  preference: ThemePreference,
  storage: Storage = window.localStorage,
  root: HTMLElement = document.documentElement,
): void {
  storage.setItem(THEME_STORAGE_KEY, preference);
  applyThemePreference(preference, root);
}

export function initializeTheme(): ThemePreference {
  const preference = loadThemePreference();
  applyThemePreference(preference);
  return preference;
}
