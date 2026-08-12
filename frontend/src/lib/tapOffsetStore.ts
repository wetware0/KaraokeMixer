export const TAP_OFFSET_STORAGE_KEY = "karaoke-mm.tapOffset";
export const DEFAULT_TAP_OFFSET_SECONDS = 0.1;
export const MIN_TAP_OFFSET_SECONDS = 0;
export const MAX_TAP_OFFSET_SECONDS = 0.5;

export function clampTapOffsetSeconds(value: number): number {
  return Math.min(MAX_TAP_OFFSET_SECONDS, Math.max(MIN_TAP_OFFSET_SECONDS, value));
}

/** Reads the calibrated tap-reaction offset from `storage` (real
 * `window.localStorage` by default; injectable so tests never depend on the
 * real global). Falls back to `DEFAULT_TAP_OFFSET_SECONDS` when nothing is
 * stored, or when the stored value isn't a finite number (e.g. corrupted by
 * hand-editing devtools storage). */
export function loadTapOffsetSeconds(storage: Storage = window.localStorage): number {
  const raw = storage.getItem(TAP_OFFSET_STORAGE_KEY);
  if (raw === null) return DEFAULT_TAP_OFFSET_SECONDS;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_TAP_OFFSET_SECONDS;
  return clampTapOffsetSeconds(parsed);
}

export function saveTapOffsetSeconds(value: number, storage: Storage = window.localStorage): void {
  storage.setItem(TAP_OFFSET_STORAGE_KEY, String(clampTapOffsetSeconds(value)));
}
