import { describe, expect, it } from "vitest";
import {
  LIBRARY_COLUMNS_STORAGE_KEY,
  MAX_LIBRARY_COLUMN_WIDTH,
  MIN_LIBRARY_COLUMN_WIDTH,
  clampLibraryColumnWidth,
  defaultLibraryColumnsState,
  displayValue,
  filterTracks,
  formatDuration,
  instrumentalProvenanceTitle,
  loadLibraryColumnsState,
  saveLibraryColumnsState,
  sortTracks,
  visibleColumnsInOrder,
  type LibraryColumnsState,
} from "./libraryColumns";
import type { Track } from "./types";

function track(overrides: Partial<Track> = {}): Track {
  return {
    id: 1,
    media_root: "D:/Media",
    relative_path: "ABBA/Dancing Queen.flac",
    artist: "ABBA",
    title: "Dancing Queen",
    outputs: {
      instrumental: false, vocals: false, lead_vocals: false, backing_vocals: false,
      drums: false, bass: false, guitar: false, piano: false, other: false, lrc: false,
    },
    lrc_state: null,
    stem_count: 0,
    album: "Arrival",
    year: 1976,
    duration_seconds: 213,
    ...overrides,
  };
}

function provenance(quality: "fast" | "balanced" | "high_quality") {
  return {
    schema_version: 1,
    part: "instrumental" as const,
    quality,
    engine: quality === "high_quality" ? "uvr_karaoke_ensemble" : "demucs",
    engine_version: quality === "high_quality" ? "audio-separator==0.44.5" : null,
    model: quality === "high_quality" ? "karaoke" : quality === "fast" ? "mdx" : "htdemucs",
    models: quality === "high_quality" ? ["model-a.ckpt", "model-b.ckpt", "model-c.ckpt"] : [],
    backing_vocal_mode: quality === "high_quality" ? "best" : "stripped",
    device: "cuda",
    job_id: 94,
    stage: "karaoke_instrumental",
    attribution: "confirmed" as const,
    recorded_at: "2026-08-10T02:00:00Z",
  };
}

class FakeStorage implements Storage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  clear(): void { this.data.clear(); }
  getItem(key: string): string | null { return this.data.get(key) ?? null; }
  key(index: number): string | null { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string): void { this.data.delete(key); }
  setItem(key: string, value: string): void { this.data.set(key, value); }
}

describe("defaultLibraryColumnsState", () => {
  it("shows source folder and track facts while keeping album optional", () => {
    const state = defaultLibraryColumnsState();
    expect(state.columns.map((c) => c.key)).toEqual([
      "artwork", "folder", "filename", "artist", "title", "instrumental", "lyrics", "stems", "album", "year", "duration",
    ]);
    expect(state.columns.find((c) => c.key === "artwork")?.visible).toBe(true);
    expect(state.columns.find((c) => c.key === "filename")?.visible).toBe(true);
    expect(state.columns.find((c) => c.key === "folder")?.visible).toBe(true);
    expect(state.columns.find((c) => c.key === "album")?.visible).toBe(false);
    expect(state.columns.filter((c) => c.key !== "album").every((c) => c.visible)).toBe(true);
    expect(state.columns.every((c) => c.filter === "")).toBe(true);
    expect(state.columns.every((c) => c.width >= MIN_LIBRARY_COLUMN_WIDTH)).toBe(true);
    expect(state.sortKey).toBeNull();
    expect(state.sortDirection).toBe("asc");
  });
});

describe("loadLibraryColumnsState / saveLibraryColumnsState", () => {
  it("returns the default state when nothing is stored", () => {
    const storage = new FakeStorage();
    expect(loadLibraryColumnsState(storage)).toEqual(defaultLibraryColumnsState());
  });

  it("round-trips a saved state under the exact storage key", () => {
    const storage = new FakeStorage();
    const state = defaultLibraryColumnsState();
    state.columns[0].visible = false;
    state.sortKey = "year";
    state.sortDirection = "desc";

    saveLibraryColumnsState(state, storage);

    expect(storage.getItem(LIBRARY_COLUMNS_STORAGE_KEY)).not.toBeNull();
    expect(loadLibraryColumnsState(storage)).toEqual(state);
  });

  it("falls back to defaults when the stored value is corrupt JSON", () => {
    const storage = new FakeStorage();
    storage.setItem(LIBRARY_COLUMNS_STORAGE_KEY, "{not json");
    expect(loadLibraryColumnsState(storage)).toEqual(defaultLibraryColumnsState());
  });

  it("repairs a stored state missing a column later added to the default set", () => {
    const storage = new FakeStorage();
    storage.setItem(
      LIBRARY_COLUMNS_STORAGE_KEY,
      JSON.stringify({
        columns: [{ key: "title", label: "Title", visible: true, order: 0, filter: "" }],
        sortKey: null,
        sortDirection: "asc",
      })
    );

    const state = loadLibraryColumnsState(storage);

    expect(state.columns.map((c) => c.key).sort()).toEqual(
      ["album", "artist", "artwork", "duration", "filename", "folder", "instrumental", "lyrics", "stems", "title", "year"]
    );
  });

  it("makes Folder visible once when migrating the old hidden-folder default", () => {
    const storage = new FakeStorage();
    const legacy = defaultLibraryColumnsState();
    delete (legacy as Partial<LibraryColumnsState>).version;
    legacy.columns.find((column) => column.key === "folder")!.visible = false;
    storage.setItem(LIBRARY_COLUMNS_STORAGE_KEY, JSON.stringify(legacy));

    expect(loadLibraryColumnsState(storage).columns.find((column) => column.key === "folder")?.visible).toBe(true);
  });

  it("adds default widths to version 2 state without overriding its folder visibility", () => {
    const storage = new FakeStorage();
    const version2 = defaultLibraryColumnsState();
    version2.version = 2;
    version2.columns.find((column) => column.key === "folder")!.visible = false;
    for (const column of version2.columns) delete (column as Partial<typeof column>).width;
    storage.setItem(LIBRARY_COLUMNS_STORAGE_KEY, JSON.stringify(version2));

    const migrated = loadLibraryColumnsState(storage);

    expect(migrated.version).toBe(4);
    expect(migrated.columns.find((column) => column.key === "folder")!.visible).toBe(false);
    expect(migrated.columns.every((column) => column.width >= MIN_LIBRARY_COLUMN_WIDTH)).toBe(true);
  });

  it("adds the creator output columns to a saved pre-version-4 layout", () => {
    const storage = new FakeStorage();
    const version3 = defaultLibraryColumnsState();
    version3.version = 3;
    version3.columns = version3.columns.filter((column) => !["instrumental", "lyrics", "stems"].includes(column.key));
    storage.setItem(LIBRARY_COLUMNS_STORAGE_KEY, JSON.stringify(version3));

    const migrated = loadLibraryColumnsState(storage);

    for (const key of ["instrumental", "lyrics", "stems"] as const) {
      expect(migrated.columns.find((column) => column.key === key)?.visible).toBe(true);
    }
  });

  it("clamps corrupt or extreme stored widths", () => {
    expect(clampLibraryColumnWidth(20)).toBe(MIN_LIBRARY_COLUMN_WIDTH);
    expect(clampLibraryColumnWidth(5000)).toBe(MAX_LIBRARY_COLUMN_WIDTH);
    expect(clampLibraryColumnWidth(183.6)).toBe(184);
  });

  it("renormalizes colliding orders to unique, consecutive integers, preserving stored columns' relative order", () => {
    // Only "title" and "year" are stored, with orders 0 and 1; the other
    // four columns get merged back in from the defaults, whose fallback
    // order (DEFAULT_KEY_ORDER.indexOf(key)) can collide with these stored
    // values (e.g. "folder"'s fallback order is also 0).
    const storage = new FakeStorage();
    storage.setItem(
      LIBRARY_COLUMNS_STORAGE_KEY,
      JSON.stringify({
        columns: [
          { key: "year", label: "Year", visible: true, order: 1, filter: "" },
          { key: "title", label: "Title", visible: true, order: 0, filter: "" },
        ],
        sortKey: null,
        sortDirection: "asc",
      })
    );

    const state = loadLibraryColumnsState(storage);

    const orders = state.columns.map((c) => c.order).sort((a, b) => a - b);
    expect(orders).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);

    const titleOrder = state.columns.find((c) => c.key === "title")!.order;
    const yearOrder = state.columns.find((c) => c.key === "year")!.order;
    expect(titleOrder).toBeLessThan(yearOrder);
  });
});

describe("visibleColumnsInOrder", () => {
  it("excludes hidden columns and respects the order field", () => {
    const state = defaultLibraryColumnsState();
    state.columns.find((c) => c.key === "album")!.visible = false;
    state.columns.find((c) => c.key === "title")!.order = -1;

    const visible = visibleColumnsInOrder(state);

    expect(visible.map((c) => c.key)).toEqual([
      "title", "artwork", "folder", "filename", "artist", "instrumental", "lyrics", "stems", "year", "duration",
    ]);
  });
});

describe("formatDuration", () => {
  it("formats whole seconds as m:ss", () => {
    expect(formatDuration(213)).toBe("3:33");
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(9)).toBe("0:09");
  });

  it("formats zero as 0:00", () => {
    expect(formatDuration(0)).toBe("0:00");
  });

  it("returns an em dash for null, non-finite, or negative values", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(NaN)).toBe("—");
    expect(formatDuration(-5)).toBe("—");
  });
});

describe("displayValue", () => {
  it("renders the folder as the full original source directory with forward slashes", () => {
    const t = track({ relative_path: "ABBA\\Arrival\\Dancing Queen.flac" });
    expect(displayValue(t, "folder")).toBe("D:/Media/ABBA/Arrival");
  });

  it("renders the original filename separately from the editable title tag", () => {
    const t = track({ relative_path: "ABBA\\Arrival\\01 - Dancing Queen.flac", title: "Dancing Queen" });
    expect(displayValue(t, "filename")).toBe("01 - Dancing Queen.flac");
    expect(displayValue(t, "title")).toBe("Dancing Queen");
    expect(displayValue(t, "artwork")).toBe("");
  });

  it("renders the media root for a file directly under it", () => {
    expect(displayValue(track({ relative_path: "Dancing Queen.flac" }), "folder")).toBe("D:/Media");
  });

  it("renders artist/title/album as-is, falling back to empty string when null", () => {
    expect(displayValue(track(), "artist")).toBe("ABBA");
    expect(displayValue(track({ artist: null }), "artist")).toBe("");
    expect(displayValue(track(), "title")).toBe("Dancing Queen");
    expect(displayValue(track({ album: null }), "album")).toBe("");
  });

  it("renders year as a plain string, empty when null", () => {
    expect(displayValue(track(), "year")).toBe("1976");
    expect(displayValue(track({ year: null }), "year")).toBe("");
  });

  it("renders duration via formatDuration", () => {
    expect(displayValue(track({ duration_seconds: 65 }), "duration")).toBe("1:05");
    expect(displayValue(track({ duration_seconds: null }), "duration")).toBe("—");
  });

  it("renders creator output states as concise column values", () => {
    const ready = track({
      outputs: { ...track().outputs, instrumental: true, lrc: true },
      lrc_state: "line_timed",
      stem_count: 4,
    });
    expect(displayValue(ready, "instrumental")).toBe("Ready");
    expect(displayValue(ready, "lyrics")).toBe("Line timed");
    expect(displayValue(ready, "stems")).toBe("4");
    expect(displayValue(track(), "instrumental")).toBe("Missing");
    expect(displayValue(track(), "lyrics")).toBe("Missing");
  });

  it("shows known instrumental quality while retaining Ready for unknown provenance", () => {
    const output = { ...track().outputs, instrumental: true };
    expect(displayValue(track({ outputs: output, instrumental_provenance: provenance("high_quality") }), "instrumental"))
      .toBe("High Quality");
    expect(displayValue(track({ outputs: output, instrumental_provenance: provenance("balanced") }), "instrumental"))
      .toBe("Balanced");
    expect(displayValue(track({ outputs: output }), "instrumental")).toBe("Ready");
  });

  it("explains the exact instrumental engine, models, device, job, and attribution on hover", () => {
    const output = { ...track().outputs, instrumental: true };
    const title = instrumentalProvenanceTitle(track({
      outputs: output,
      instrumental_provenance: provenance("high_quality"),
    }));

    expect(title).toContain("High Quality · UVR karaoke ensemble");
    expect(title).toContain("model-a.ckpt + model-b.ckpt + model-c.ckpt");
    expect(title).toContain("CUDA · Job 94 · recorded when created");
  });

  it("describes a manual quality confirmation without inventing an engine or job", () => {
    const output = { ...track().outputs, instrumental: true };
    const manual = {
      ...provenance("high_quality"),
      attribution: "manual" as const,
      confirmed_by: "Peter",
      job_id: null,
      engine: "manual_confirmation",
      model: "user_confirmed",
    };

    expect(instrumentalProvenanceTitle(track({ outputs: output, instrumental_provenance: manual })))
      .toBe("High Quality · manually confirmed by Peter");
  });
});

describe("filterTracks", () => {
  const tracks = [
    track({ id: 1, title: "Dancing Queen", artist: "ABBA" }),
    track({ id: 2, title: "Bohemian Rhapsody", artist: "Queen" }),
  ];

  it("returns all tracks when no column has a filter", () => {
    expect(filterTracks(tracks, defaultLibraryColumnsState())).toEqual(tracks);
  });

  it("filters case-insensitively on a single column's text", () => {
    const state = defaultLibraryColumnsState();
    state.columns.find((c) => c.key === "title")!.filter = "queen";

    expect(filterTracks(tracks, state).map((t) => t.id)).toEqual([1]);
  });

  it("requires every active filter to match (AND semantics across columns)", () => {
    const state = defaultLibraryColumnsState();
    state.columns.find((c) => c.key === "title")!.filter = "queen";
    state.columns.find((c) => c.key === "artist")!.filter = "abba";

    expect(filterTracks(tracks, state).map((t) => t.id)).toEqual([1]);
  });

  it("filters instrumental, lyrics, and stems by their creator-facing states", () => {
    const ready = track({
      id: 3,
      outputs: { ...track().outputs, instrumental: true, lrc: true },
      lrc_state: "enhanced",
      stem_count: 4,
    });
    const states = defaultLibraryColumnsState();
    states.columns.find((column) => column.key === "instrumental")!.filter = "ready";
    states.columns.find((column) => column.key === "lyrics")!.filter = "enhanced";
    states.columns.find((column) => column.key === "stems")!.filter = "has";

    expect(filterTracks([...tracks, ready], states).map((item) => item.id)).toEqual([3]);
  });

  it("filters instrumental quality separately from unknown Ready outputs", () => {
    const output = { ...track().outputs, instrumental: true };
    const fast = track({ id: 3, outputs: output, instrumental_provenance: provenance("fast") });
    const high = track({ id: 4, outputs: output, instrumental_provenance: provenance("high_quality") });
    const unknown = track({ id: 5, outputs: output });
    const state = defaultLibraryColumnsState();
    const column = state.columns.find((candidate) => candidate.key === "instrumental")!;

    column.filter = "high_quality";
    expect(filterTracks([...tracks, fast, high, unknown], state).map((item) => item.id)).toEqual([4]);
    column.filter = "ready";
    expect(filterTracks([...tracks, fast, high, unknown], state).map((item) => item.id)).toEqual([5]);
  });
});

describe("sortTracks", () => {
  const tracks = [
    track({ id: 1, title: "B Song", year: 2000, duration_seconds: 100 }),
    track({ id: 2, title: "A Song", year: 1990, duration_seconds: 300 }),
    track({ id: 3, title: "C Song", year: null, duration_seconds: null }),
  ];

  it("returns tracks unchanged when sortKey is null", () => {
    expect(sortTracks(tracks, defaultLibraryColumnsState())).toEqual(tracks);
  });

  it("sorts text columns alphabetically, ascending by default", () => {
    const state: LibraryColumnsState = { ...defaultLibraryColumnsState(), sortKey: "title" };
    expect(sortTracks(tracks, state).map((t) => t.id)).toEqual([2, 1, 3]);
  });

  it("sorts numeric columns numerically, with a null value sorting last regardless of direction", () => {
    const ascending: LibraryColumnsState = { ...defaultLibraryColumnsState(), sortKey: "year", sortDirection: "asc" };
    expect(sortTracks(tracks, ascending).map((t) => t.id)).toEqual([2, 1, 3]);

    // Unified null handling: unlike a naive "reverse everything for desc"
    // sort, the null year (id 3) stays last here too, instead of jumping to
    // the front.
    const descending: LibraryColumnsState = { ...defaultLibraryColumnsState(), sortKey: "year", sortDirection: "desc" };
    expect(sortTracks(tracks, descending).map((t) => t.id)).toEqual([1, 2, 3]);
  });

  it("sorts text columns with a null (empty-string) value last regardless of direction", () => {
    const withAlbum = [
      track({ id: 1, album: "Zulu" }),
      track({ id: 2, album: null }),
      track({ id: 3, album: "Alpha" }),
    ];

    const ascending: LibraryColumnsState = { ...defaultLibraryColumnsState(), sortKey: "album", sortDirection: "asc" };
    expect(sortTracks(withAlbum, ascending).map((t) => t.id)).toEqual([3, 1, 2]);

    const descending: LibraryColumnsState = { ...defaultLibraryColumnsState(), sortKey: "album", sortDirection: "desc" };
    expect(sortTracks(withAlbum, descending).map((t) => t.id)).toEqual([1, 3, 2]);
  });

  it("does not mutate the input array", () => {
    const original = [...tracks];
    sortTracks(tracks, { ...defaultLibraryColumnsState(), sortKey: "title" });
    expect(tracks).toEqual(original);
  });

  it("sorts creator output columns by quality and stem count with missing last", () => {
    const outputTracks = [
      track({
        id: 1, outputs: { ...track().outputs, instrumental: true }, stem_count: 2,
        instrumental_provenance: provenance("balanced"),
      }),
      track({ id: 2, stem_count: 0 }),
      track({
        id: 3, outputs: { ...track().outputs, instrumental: true }, stem_count: 5,
        instrumental_provenance: provenance("high_quality"),
      }),
      track({ id: 4, outputs: { ...track().outputs, instrumental: true }, stem_count: 1 }),
    ];
    expect(sortTracks(outputTracks, { ...defaultLibraryColumnsState(), sortKey: "stems" }).map((item) => item.id)).toEqual([2, 4, 1, 3]);
    expect(sortTracks(outputTracks, { ...defaultLibraryColumnsState(), sortKey: "instrumental" }).map((item) => item.id)).toEqual([4, 1, 3, 2]);
    expect(sortTracks(outputTracks, {
      ...defaultLibraryColumnsState(), sortKey: "instrumental", sortDirection: "desc",
    }).map((item) => item.id)).toEqual([3, 1, 4, 2]);
  });
});
