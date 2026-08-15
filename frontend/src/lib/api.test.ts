import { afterEach, describe, expect, it, vi } from "vitest";
import { artworkUrl, audioUrl, browseForFolder, cancelJob, confirmLyricTimingQuality, createLibraryFolder, deleteLibraryFolder, deleteTrack, fetchJob, fetchJobHistory, fetchJobItems, fetchJobs, fetchLibraryFolders, fetchLrc, fetchRecipes, fetchRescanStatus, fetchSettings, fetchSystem, fetchTagSuggestion, fetchTrackFailures, fetchTracks, fetchTrackParts, importFromYoutube, moveTrack, partAudioUrl, probeYoutube, reconcileTrackLyrics, renameLibraryFolder, rescan, saveLrc, saveTrackTags, submitJob, updateSettings, uploadTrackArtwork } from "./api";
import type { JobDetail, JobSummary, LibraryFolder, RecipeInfo, Settings, Track, YoutubeImportRequest, YoutubeProbeResult } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  } as Response;
}

describe("fetchTracks", () => {
  it("requests the tracks endpoint without a query by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ tracks: [] as Track[] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTracks();

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks");
  });

  it("includes an encoded query string when a query is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ tracks: [] as Track[] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTracks("dancing queen");

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks?query=dancing%20queen");
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(fetchTracks()).rejects.toThrow("Failed to fetch tracks: 500");
  });
});

describe("reconcileTrackLyrics", () => {
  it("posts only the visible track ids and returns changed rows", async () => {
    const changed = [{ id: 7, lrc_state: "enhanced" }] as Track[];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ tracks: changed }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await reconcileTrackLyrics([7, 8]);

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/reconcile-lyrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids: [7, 8] }),
    });
    expect(result).toEqual(changed);
  });
});

describe("confirmLyricTimingQuality", () => {
  it("records a listening review for the exact canonical LRC", async () => {
    const updated = { id: 7, lrc_state: "enhanced" } as Track;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(updated));
    vi.stubGlobal("fetch", fetchMock);

    await expect(confirmLyricTimingQuality(7)).resolves.toEqual(updated);
    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/7/lrc/confirm-quality", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed_by: "user" }),
    });
  });
});

describe("deleteTrack", () => {
  it("requests a recoverable track deletion with the output choice", async () => {
    const body = { track_id: 4, moved_to_recycle_bin: ["D:/Media/Song.flac"] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteTrack(4, true)).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/4", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_outputs: true }),
    });
  });
});

describe("library file and folder management", () => {
  const folder: LibraryFolder = {
    path: "D:/Media/Artist",
    media_root: "D:/Media",
    relative_path: "Artist",
    name: "Artist",
  };

  it("lists and creates persistent library folders", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ folders: [folder] }))
      .mockResolvedValueOnce(jsonResponse(folder));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLibraryFolders()).resolves.toEqual([folder]);
    await expect(createLibraryFolder("D:/Media", "Artist")).resolves.toEqual(folder);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_path: "D:/Media", name: "Artist" }),
    });
  });

  it("renames and recoverably deletes folders", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ folder }))
      .mockResolvedValueOnce(jsonResponse({ deleted_track_ids: [1], moved_to_recycle_bin: [folder.path] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(renameLibraryFolder("D:/Media/Old", "Artist")).resolves.toEqual(folder);
    await expect(deleteLibraryFolder(folder.path)).resolves.toEqual({
      deleted_track_ids: [1], moved_to_recycle_bin: [folder.path],
    });
    expect(fetchMock).toHaveBeenLastCalledWith("/api/folders?path=D%3A%2FMedia%2FArtist", { method: "DELETE" });
  });

  it("moves or renames a track through one endpoint", async () => {
    const moved = { id: 7, relative_path: "Artist/New name.flac" } as Track;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ track: moved }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(moveTrack(7, "D:/Media/Artist", "New name")).resolves.toEqual(moved);
    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/7/location", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination_folder: "D:/Media/Artist", filename_stem: "New name" }),
    });
  });
});

describe("fetchSettings and updateSettings", () => {
  it("fetches settings from /api/settings", async () => {
    const settings: Settings = { media_roots: [], mirror_roots: [], device_preference: "auto" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(settings));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchSettings();

    expect(fetchMock).toHaveBeenCalledWith("/api/settings");
    expect(result).toEqual(settings);
  });

  it("PUTs settings as JSON and returns the response body", async () => {
    const settings: Settings = { media_roots: ["D:/Media"], mirror_roots: [], device_preference: "cpu" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(settings));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateSettings(settings);

    expect(fetchMock).toHaveBeenCalledWith("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    expect(result).toEqual(settings);
  });

  it("opens the backend folder picker with the current path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ path: "D:\\Chosen" }));
    vi.stubGlobal("fetch", fetchMock);

    expect(await browseForFolder("D:/Media")).toBe("D:\\Chosen");
    expect(fetchMock).toHaveBeenCalledWith("/api/settings/browse-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_path: "D:/Media" }),
    });
  });
});

describe("fetchSystem", () => {
  it("fetches device info from /api/system", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ device: "cpu" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchSystem();

    expect(fetchMock).toHaveBeenCalledWith("/api/system");
    expect(result).toEqual({ device: "cpu" });
  });
});

describe("fetchRecipes", () => {
  it("fetches the recipe list", async () => {
    const recipes: RecipeInfo[] = [
      {
        name: "karaoke", lane: "gpu",
        options_schema: { model: { type: "select", choices: ["htdemucs"], default: "htdemucs" } },
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ recipes }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchRecipes();

    expect(fetchMock).toHaveBeenCalledWith("/api/recipes");
    expect(result).toEqual(recipes);
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(fetchRecipes()).rejects.toThrow("Failed to fetch recipes: 500");
  });
});

describe("rescan", () => {
  const status = {
    scan_id: 4, status: "running" as const, tracks_found: 80, media_roots_scanned: 1, media_roots_total: 2,
    current_root: "D:/Music", unavailable_roots: [], tracks_purged: 0, error: null, updated_at: "2026-08-05T00:00:00Z",
  };

  it("POSTs to /api/rescan and returns immediately with background status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(status)
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await rescan();

    expect(fetchMock).toHaveBeenCalledWith("/api/rescan", { method: "POST" });
    expect(result).toEqual(status);
  });

  it("GETs the current background rescan status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status));
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchRescanStatus()).toEqual(status);
    expect(fetchMock).toHaveBeenCalledWith("/api/rescan");
  });
});

describe("audioUrl", () => {
  it("builds the streaming URL for a track ID", () => {
    expect(audioUrl(42)).toBe("/api/audio/42");
  });
});

describe("fetchTagSuggestion", () => {
  it("posts the editable search hints without writing tags", async () => {
    const suggestion = { artist: "ABBA", title: "Dancing Queen", album: "Arrival", year: 1976, provider: "itunes", artwork_data_url: null };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(suggestion));
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchTagSuggestion(7, { artist: "Abba", title: "Dancing queen" })).toEqual(suggestion);
    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/7/tags/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artist: "Abba", title: "Dancing queen" }),
    });
  });
});

describe("submitJob", () => {
  it("POSTs the submission and returns the job id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_id: 7 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await submitJob({ recipe: "fake", track_ids: [1], options: { device: "cpu" } });

    expect(fetchMock).toHaveBeenCalledWith("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipe: "fake", track_ids: [1], options: { device: "cpu" } }),
    });
    expect(result).toEqual({ job_id: 7 });
  });
});

describe("fetchJobs", () => {
  it("fetches the job list", async () => {
    const jobs: JobSummary[] = [
      {
        id: 1, recipe: "fake", options: {}, status: "completed",
        created_at: "t0", started_at: "t1", finished_at: "t2",
        item_counts: { queued: 0, running: 0, completed: 1, failed: 0, skipped: 0, cancelled: 0 },
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ jobs }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchJobs();

    expect(fetchMock).toHaveBeenCalledWith("/api/jobs");
    expect(result).toEqual(jobs);
  });
});

describe("fetchJob", () => {
  it("fetches one job's detail", async () => {
    const detail: JobDetail = {
      id: 1, recipe: "fake", options: {}, status: "running",
      created_at: "t0", started_at: "t1", finished_at: null,
      items: [
        {
          id: 10, track_id: 3, source_path: "a.flac", status: "running",
          current_stage: "fake_publish",
          stages: [{ name: "fake_publish", status: "running", started_at: "t1", finished_at: null, error: null }],
          error_text: null,
        },
      ],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchJob(1);

    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/1");
    expect(result).toEqual(detail);
  });
});

describe("fetchTrackFailures", () => {
  it("fetches unresolved per-track processing failures", async () => {
    const failures = [
      { track_id: 3, job_id: 82, stage: "karaoke_instrumental", message: "Surround audio could not be processed" },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ failures }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchTrackFailures();

    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/track-failures");
    expect(result).toEqual(failures);
  });
});

describe("processing history", () => {
  it("fetches a filtered, paged history", async () => {
    const body = { jobs: [], total: 0, limit: 25, offset: 25 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchJobHistory({ status: "failed", query: "Eleanor Rigby", offset: 25 })).resolves.toEqual(body);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/history?status=failed&limit=25&offset=25&query=Eleanor+Rigby",
    );
  });

  it("fetches one filtered page of job items", async () => {
    const body = { items: [], total: 0, limit: 50, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchJobItems(82, { status: "failed", query: "Beatles" })).resolves.toEqual(body);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/82/items?status=failed&limit=50&offset=0&query=Beatles",
    );
  });
});

describe("cancelJob", () => {
  it("POSTs to the cancel endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_id: 1, status: "cancelling" }));
    vi.stubGlobal("fetch", fetchMock);

    await cancelJob(1);

    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/1/cancel", { method: "POST" });
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(cancelJob(1)).rejects.toThrow("Failed to cancel job 1: 500");
  });
});

describe("submitJob error handling", () => {
  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(submitJob({ recipe: "fake", track_ids: [1], options: { device: "cpu" } })).rejects.toThrow("Failed to submit job: 500");
  });
});

describe("fetchJobs error handling", () => {
  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(fetchJobs()).rejects.toThrow("Failed to fetch jobs: 500");
  });
});

describe("fetchJob error handling", () => {
  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(fetchJob(1)).rejects.toThrow("Failed to fetch job 1: 500");
  });
});

describe("probeYoutube", () => {
  it("POSTs the URL and returns video metadata", async () => {
    const probeResult: YoutubeProbeResult = { is_playlist: false, title: "Chiquitita", duration: 218, uploader: "ABBA" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(probeResult));
    vi.stubGlobal("fetch", fetchMock);

    const result = await probeYoutube("https://youtube.com/watch?v=abc");

    expect(fetchMock).toHaveBeenCalledWith("/api/youtube/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: "https://youtube.com/watch?v=abc" }),
    });
    expect(result).toEqual(probeResult);
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(probeYoutube("https://youtube.com/watch?v=abc")).rejects.toThrow("Failed to probe YouTube URL: 500");
  });

  it("throws the backend's detail message when the error body has one", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Configure a downloads root" }, false)));
    await expect(probeYoutube("https://youtube.com/watch?v=abc")).rejects.toThrow("Configure a downloads root");
  });
});

describe("importFromYoutube", () => {
  it("POSTs the import request and returns the job id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_id: 12 }));
    vi.stubGlobal("fetch", fetchMock);
    const request: YoutubeImportRequest = { url: "https://youtube.com/watch?v=abc", artist: "ABBA", title: "Chiquitita" };

    const result = await importFromYoutube(request);

    expect(fetchMock).toHaveBeenCalledWith("/api/youtube/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    expect(result).toEqual({ job_id: 12 });
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(importFromYoutube({ url: "https://youtube.com/watch?v=abc" })).rejects.toThrow(
      "Failed to import from YouTube: 500"
    );
  });

  it("throws the backend's detail message when the error body has one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Video is age-restricted; configure YouTube cookies in Settings" }, false)
      )
    );
    await expect(importFromYoutube({ url: "https://youtube.com/watch?v=abc" })).rejects.toThrow(
      "Video is age-restricted; configure YouTube cookies in Settings"
    );
  });
});

describe("fetchTrackParts", () => {
  it("requests the parts endpoint and returns the parts array", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ parts: [{ part: "original", exists: true, duration: 180.5 }] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const parts = await fetchTrackParts(1);

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/1/parts");
    expect(parts).toEqual([{ part: "original", exists: true, duration: 180.5 }]);
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, false)));
    await expect(fetchTrackParts(1)).rejects.toThrow("Failed to fetch parts for track 1: 500");
  });
});

describe("partAudioUrl", () => {
  it("builds the part-streaming URL, encoding the part name", () => {
    expect(partAudioUrl(1, "lead_vocals")).toBe("/api/audio/1/part/lead_vocals");
  });
});

describe("fetchLrc", () => {
  it("requests the lrc endpoint and returns the parsed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ exists: true, content: "[00:01.00]Hi\n", state: "line_timed" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLrc(1);

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/1/lrc");
    expect(result).toEqual({ exists: true, content: "[00:01.00]Hi\n", state: "line_timed" });
  });
});

describe("saveLrc", () => {
  it("PUTs the content with no query params by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ path: "D:/Media/Song.lrc" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await saveLrc(1, "[00:01.00]Hi\n");

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/1/lrc", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: "[00:01.00]Hi\n" }),
    });
    expect(result).toEqual({ path: "D:/Media/Song.lrc" });
  });

  it("includes create and suffix as query params when given", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ path: "D:/Media/Song.alt.lrc" }));
    vi.stubGlobal("fetch", fetchMock);

    await saveLrc(1, "content", { create: "beside", suffix: "alt" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tracks/1/lrc?create=beside&suffix=alt",
      expect.objectContaining({ method: "PUT" }),
    );
  });

  it("throws with the backend's detail message on a 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "No .lrc file resolved" }) }),
    );
    await expect(saveLrc(1, "content")).rejects.toThrow("No .lrc file resolved");
  });
});

describe("artworkUrl", () => {
  it("builds the artwork URL for a track id", () => {
    expect(artworkUrl(7)).toBe("/api/tracks/7/artwork");
  });
});

describe("saveTrackTags", () => {
  it("PUTs the tags as JSON and returns the updated track", async () => {
    const updatedTrack = { id: 1, artist: "ABBA", title: "Song", album: "Arrival", year: 1976 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(updatedTrack));
    vi.stubGlobal("fetch", fetchMock);

    const result = await saveTrackTags(1, { artist: "ABBA", title: "Song", album: "Arrival", year: 1976 });

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/1/tags", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artist: "ABBA", title: "Song", album: "Arrival", year: 1976 }),
    });
    expect(result).toEqual(updatedTrack);
  });

  it("throws the backend's detail message on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "title is required" }, false)));
    await expect(saveTrackTags(1, { artist: null, title: "", album: null, year: null })).rejects.toThrow(
      "title is required"
    );
  });
});

describe("uploadTrackArtwork", () => {
  it("PUTs the file's raw bytes with its content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ path: "D:/Media/Song.flac" }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["fake-bytes"], "cover.jpg", { type: "image/jpeg" });

    await uploadTrackArtwork(1, file);

    expect(fetchMock).toHaveBeenCalledWith("/api/tracks/1/artwork", {
      method: "PUT",
      headers: { "Content-Type": "image/jpeg" },
      body: file,
    });
  });

  it("throws the backend's detail message on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Empty artwork upload" }, false)));
    const file = new File([], "empty.jpg", { type: "image/jpeg" });
    await expect(uploadTrackArtwork(1, file)).rejects.toThrow("Empty artwork upload");
  });
});
