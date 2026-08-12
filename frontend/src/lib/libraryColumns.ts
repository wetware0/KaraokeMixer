import type { Track } from "./types";

const INSTRUMENTAL_QUALITY_LABELS = {
  fast: "Fast",
  balanced: "Balanced",
  high_quality: "High Quality",
} as const;

const INSTRUMENTAL_QUALITY_RANK = {
  fast: 1,
  balanced: 2,
  high_quality: 3,
} as const;

function instrumentalQualityLabel(track: Track): string | null {
  const quality = track.instrumental_provenance?.quality;
  return quality ? INSTRUMENTAL_QUALITY_LABELS[quality] : null;
}

export function instrumentalProvenanceTitle(track: Track): string {
  if (!track.outputs.instrumental) return "No instrumental output";
  const provenance = track.instrumental_provenance;
  if (!provenance) return "Instrumental ready · quality unknown";
  if (provenance.attribution === "manual") {
    return `${instrumentalQualityLabel(track) ?? "Ready"} · manually confirmed by ${provenance.confirmed_by ?? "user"}`;
  }
  const engine = provenance.engine === "uvr_karaoke_ensemble" ? "UVR karaoke ensemble" : "Demucs";
  const model = provenance.models.length > 0 ? provenance.models.join(" + ") : provenance.model;
  const device = provenance.device ? ` · ${provenance.device.toUpperCase()}` : "";
  const attribution = provenance.attribution === "inferred" ? "inferred from processing history" : "recorded when created";
  const job = provenance.job_id === null ? "" : ` · Job ${provenance.job_id}`;
  return `${instrumentalQualityLabel(track) ?? "Ready"} · ${engine} · ${model}${device}${job} · ${attribution}`;
}

export const LIBRARY_COLUMNS_STORAGE_KEY = "karaoke-mm.libraryColumns";
const LIBRARY_COLUMNS_SCHEMA_VERSION = 4;
export const MIN_LIBRARY_COLUMN_WIDTH = 64;
export const MAX_LIBRARY_COLUMN_WIDTH = 720;

export type LibraryColumnKey =
  | "artwork" | "folder" | "filename" | "artist" | "title" | "album" | "year" | "duration"
  | "instrumental" | "lyrics" | "stems";
export type SortDirection = "asc" | "desc";

export interface LibraryColumnConfig {
  key: LibraryColumnKey;
  label: string;
  visible: boolean;
  order: number;
  filter: string;
  width: number;
}

export interface LibraryColumnsState {
  version: number;
  columns: LibraryColumnConfig[];
  sortKey: LibraryColumnKey | null;
  sortDirection: SortDirection;
}

const DEFAULT_LABELS: Record<LibraryColumnKey, string> = {
  artwork: "Artwork",
  folder: "Folder",
  filename: "Filename",
  artist: "Artist",
  title: "Title",
  album: "Album",
  year: "Year",
  duration: "Duration",
  instrumental: "Instrumental",
  lyrics: "Lyrics",
  stems: "Stems",
};

const DEFAULT_KEY_ORDER: LibraryColumnKey[] = [
  "artwork", "folder", "filename", "artist", "title", "instrumental", "lyrics", "stems", "album", "year", "duration",
];
const DEFAULT_WIDTHS: Record<LibraryColumnKey, number> = {
  artwork: 78,
  folder: 240,
  filename: 220,
  artist: 150,
  title: 260,
  album: 200,
  year: 90,
  duration: 100,
  instrumental: 120,
  lyrics: 130,
  stems: 90,
};

export function clampLibraryColumnWidth(width: number): number {
  if (!Number.isFinite(width)) return MIN_LIBRARY_COLUMN_WIDTH;
  return Math.round(Math.min(MAX_LIBRARY_COLUMN_WIDTH, Math.max(MIN_LIBRARY_COLUMN_WIDTH, width)));
}

export function defaultLibraryColumnsState(): LibraryColumnsState {
  return {
    version: LIBRARY_COLUMNS_SCHEMA_VERSION,
    columns: DEFAULT_KEY_ORDER.map((key, index) => ({
      key,
      label: DEFAULT_LABELS[key],
      // Source folder is primary provenance for a media creator. Album is
      // secondary during preparation and remains available in Columns.
      visible: key !== "album",
      order: index,
      filter: "",
      width: DEFAULT_WIDTHS[key],
    })),
    sortKey: null,
    sortDirection: "asc",
  };
}

function isLibraryColumnKey(value: unknown): value is LibraryColumnKey {
  return typeof value === "string" && (DEFAULT_KEY_ORDER as string[]).includes(value);
}

/** Merges a stored value with the current default key set: a stored state
 * from before a column existed (or missing one for any other reason) is
 * repaired column-by-column rather than rejected outright, matching
 * tapOffsetStore's "corrupt storage falls back to a safe default" approach
 * but at column granularity instead of the whole value. */
function sanitize(raw: unknown): LibraryColumnsState {
  const fallback = defaultLibraryColumnsState();
  if (typeof raw !== "object" || raw === null) return fallback;

  const candidate = raw as Partial<LibraryColumnsState>;
  const isPreFolderDefaultState = candidate.version === undefined || candidate.version < 2;
  const rawColumns = Array.isArray(candidate.columns) ? candidate.columns : [];
  const byKey = new Map<LibraryColumnKey, LibraryColumnConfig>();
  for (const entry of rawColumns as Array<Partial<LibraryColumnConfig>>) {
    if (!entry || !isLibraryColumnKey(entry.key)) continue;
    byKey.set(entry.key, {
      key: entry.key,
      label: DEFAULT_LABELS[entry.key],
      visible: typeof entry.visible === "boolean" ? entry.visible : entry.key !== "album",
      order: typeof entry.order === "number" ? entry.order : DEFAULT_KEY_ORDER.indexOf(entry.key),
      filter: entry.key !== "artwork" && typeof entry.filter === "string" ? entry.filter : "",
      width: typeof entry.width === "number"
        ? clampLibraryColumnWidth(entry.width)
        : DEFAULT_WIDTHS[entry.key],
    });
  }
  for (const key of DEFAULT_KEY_ORDER) {
    if (!byKey.has(key)) byKey.set(key, fallback.columns.find((column) => column.key === key)!);
  }
  // Version 1 hid Folder by default. Make the new provenance-first default
  // visible once, while version 2 continues to respect a user's later choice.
  if (isPreFolderDefaultState) byKey.get("folder")!.visible = true;

  const columns = DEFAULT_KEY_ORDER.map((key) => byKey.get(key)!);
  renormalizeOrder(columns);
  const sortKey = isLibraryColumnKey(candidate.sortKey) && candidate.sortKey !== "artwork" ? candidate.sortKey : null;
  const sortDirection = candidate.sortDirection === "desc" ? "desc" : "asc";
  return { version: LIBRARY_COLUMNS_SCHEMA_VERSION, columns, sortKey, sortDirection };
}

/** A stored column's `order` may collide with another column's (a stored
 * custom order colliding with a just-backfilled default's fallback order, or
 * two backfilled defaults sharing the same fallback), which would leave
 * `visibleColumnsInOrder`'s tie-breaking ambiguous. Reassigns `order` to
 * consecutive integers 0..n-1 in place, ranked by each column's current
 * `order` value; `Array.prototype.sort` is stable, so columns that were
 * already tied (or already consecutive) keep their existing relative
 * order - this only resolves collisions, it never reorders two columns
 * whose order values already differed. Mutates `columns`' elements, not the
 * array itself: the returned/stored `columns` array stays in canonical
 * DEFAULT_KEY_ORDER (key) order regardless of the display `order` field. */
function renormalizeOrder(columns: LibraryColumnConfig[]): void {
  const byDisplayOrder = [...columns].sort((a, b) => a.order - b.order);
  byDisplayOrder.forEach((column, index) => {
    column.order = index;
  });
}

export function loadLibraryColumnsState(storage: Storage = window.localStorage): LibraryColumnsState {
  const raw = storage.getItem(LIBRARY_COLUMNS_STORAGE_KEY);
  if (raw === null) return defaultLibraryColumnsState();
  try {
    return sanitize(JSON.parse(raw));
  } catch {
    return defaultLibraryColumnsState();
  }
}

export function saveLibraryColumnsState(state: LibraryColumnsState, storage: Storage = window.localStorage): void {
  storage.setItem(LIBRARY_COLUMNS_STORAGE_KEY, JSON.stringify(state));
}

export function visibleColumnsInOrder(state: LibraryColumnsState): LibraryColumnConfig[] {
  return state.columns.filter((column) => column.visible).sort((a, b) => a.order - b.order);
}

export function formatDuration(seconds: number | null): string {
  // "—" (em dash) marks a value that cannot be rendered as a duration at
  // all - missing (null), not-a-number, or negative (a negative duration is
  // nonsensical data, not a real elapsed time) - matching how a missing
  // value already renders elsewhere in this module. Zero is a real,
  // renderable duration and must not be folded into that case.
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function folderOf(track: Track): string {
  const normalizedRoot = track.media_root.replace(/\\/g, "/").replace(/\/$/, "");
  const normalized = track.relative_path.replace(/\\/g, "/");
  const lastSlash = normalized.lastIndexOf("/");
  return lastSlash === -1 ? normalizedRoot : `${normalizedRoot}/${normalized.slice(0, lastSlash)}`;
}

export function displayValue(track: Track, key: LibraryColumnKey): string {
  // `!= null` (loose) deliberately catches both `null` and `undefined` -
  // Track.album/year/duration_seconds are required, non-optional fields,
  // but a test fixture built before this milestone (or one an engineer
  // extends carelessly later) can still omit them; treating that the same
  // as an explicit `null` is more forgiving than crashing on `String(undefined)`.
  switch (key) {
    case "artwork": return "";
    case "folder": return folderOf(track);
    case "filename": return track.relative_path.replace(/\\/g, "/").split("/").pop() ?? track.relative_path;
    case "artist": return track.artist ?? "";
    case "title": return track.title;
    case "album": return track.album ?? "";
    case "year": return track.year != null ? String(track.year) : "";
    case "duration": return formatDuration(track.duration_seconds);
    case "instrumental": return track.outputs.instrumental ? (instrumentalQualityLabel(track) ?? "Ready") : "Missing";
    case "lyrics": {
      if (!track.lrc_state) return "Missing";
      const label = track.lrc_state.replace("_", " ");
      return `${label[0].toUpperCase()}${label.slice(1)}`;
    }
    case "stems": return String(track.stem_count);
  }
}

// Whether a track has no real value to sort on for this column - a null
// year/duration, or an empty-string text column (itself only reachable via a
// null underlying field per displayValue's ?? ""). Checked separately from
// sortValue below so missing data can be pinned to the bottom independent of
// direction/type, rather than relying on a per-type sentinel (e.g. Infinity)
// that only pins nulls last in ascending order and flips to "first" under a
// descending multiply-by--1 - the exact asymmetry this replaces.
function isMissingValue(track: Track, key: LibraryColumnKey): boolean {
  if (key === "year") return track.year == null;
  if (key === "duration") return track.duration_seconds == null;
  if (key === "lyrics") return track.lrc_state == null;
  if (key === "instrumental") return !track.outputs.instrumental;
  if (key === "stems") return false;
  return displayValue(track, key) === "";
}

function sortValue(track: Track, key: LibraryColumnKey): string | number {
  if (key === "year") return track.year ?? 0;
  if (key === "duration") return track.duration_seconds ?? 0;
  if (key === "instrumental") {
    const quality = track.instrumental_provenance?.quality;
    return quality ? INSTRUMENTAL_QUALITY_RANK[quality] : 0;
  }
  if (key === "stems") return track.stem_count;
  return displayValue(track, key).toLowerCase();
}

function matchesColumnFilter(track: Track, key: LibraryColumnKey, rawFilter: string): boolean {
  const filter = rawFilter.trim().toLowerCase();
  if (key === "instrumental") {
    if (filter === "ready") return track.outputs.instrumental && !track.instrumental_provenance?.quality;
    if (filter === "fast" || filter === "balanced" || filter === "high_quality") {
      return track.outputs.instrumental && track.instrumental_provenance?.quality === filter;
    }
    if (filter === "missing") return !track.outputs.instrumental;
  }
  if (key === "lyrics") {
    if (filter === "missing") return track.lrc_state == null;
    return track.lrc_state === filter;
  }
  if (key === "stems") {
    if (filter === "has") return track.stem_count > 0;
    if (filter === "none") return track.stem_count === 0;
  }
  return displayValue(track, key).toLowerCase().includes(filter);
}

export function filterTracks(tracks: Track[], state: LibraryColumnsState): Track[] {
  const activeFilters = state.columns.filter((column) => column.filter.trim() !== "");
  if (activeFilters.length === 0) return tracks;
  return tracks.filter((track) =>
    activeFilters.every((column) =>
      matchesColumnFilter(track, column.key, column.filter)
    )
  );
}

export function sortTracks(tracks: Track[], state: LibraryColumnsState): Track[] {
  if (!state.sortKey) return tracks;
  const key = state.sortKey;
  const direction = state.sortDirection === "desc" ? -1 : 1;
  return [...tracks].sort((a, b) => {
    const aMissing = isMissingValue(a, key);
    const bMissing = isMissingValue(b, key);
    // Missing data always sinks to the bottom, unconditionally - not
    // multiplied by `direction` - so a null value sorts last whether the
    // column is ascending or descending, and consistently for both numeric
    // and text columns (previously text nulls rendered "" and sorted FIRST
    // ascending via plain string comparison, while numeric nulls used an
    // Infinity sentinel that only sorted last in ascending order).
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (aMissing && bMissing) return 0;

    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * direction;
    return String(va).localeCompare(String(vb)) * direction;
  });
}
