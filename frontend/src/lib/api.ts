import type {
  JobDetail, JobHistoryPage, JobHistoryStatus, JobItemStatus, JobItemsPage, JobSubmission, JobSummary, LibraryFolder, LibraryScanStatus, LrcReadResponse, LrcWriteResponse, RecipeInfo, Settings, SystemInfo, TagSuggestion, TagsWritePayload,
  Track, TrackPart, TrackPartsResponse, TrackProcessingFailure, YoutubeImportRequest, YoutubeProbeResult,
} from "./types";

const BASE = "/api";

// FastAPI error responses carry actionable detail in a JSON `{"detail": "..."}`
// body (e.g. "Configure a downloads root", or the age-restricted-video/cookies
// guidance) - falling back to a bare status code throws that detail away.
// Reading the body can itself fail (non-JSON error page, empty body), so any
// parse failure just falls back to the generic status-code message.
async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message below
  }
  return `${fallback}: ${response.status}`;
}

export async function fetchTracks(query?: string): Promise<Track[]> {
  const url = query ? `${BASE}/tracks?query=${encodeURIComponent(query)}` : `${BASE}/tracks`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to fetch tracks: ${response.status}`);
  const body = (await response.json()) as { tracks: Track[] };
  return body.tracks;
}

export async function reconcileTrackLyrics(trackIds: number[]): Promise<Track[]> {
  const response = await fetch(`${BASE}/tracks/reconcile-lyrics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ track_ids: trackIds }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to refresh lyric timing states"));
  const body = (await response.json()) as { tracks: Track[] };
  return body.tracks;
}

export async function deleteTrack(trackId: number, includeOutputs: boolean): Promise<{ track_id: number; moved_to_recycle_bin: string[] }> {
  const response = await fetch(`${BASE}/tracks/${trackId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include_outputs: includeOutputs }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, `Failed to delete track ${trackId}`));
  return response.json();
}

export async function fetchLibraryFolders(): Promise<LibraryFolder[]> {
  const response = await fetch(`${BASE}/folders`);
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to fetch library folders"));
  const body = (await response.json()) as { folders: LibraryFolder[] };
  // Older test/deployed mocks may answer every GET with a tracks-shaped body.
  // Treat a missing collection as empty rather than taking down the Library.
  return body.folders ?? [];
}

export async function createLibraryFolder(parentPath: string, name: string): Promise<LibraryFolder> {
  const response = await fetch(`${BASE}/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_path: parentPath, name }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to create folder"));
  return (await response.json()) as LibraryFolder;
}

export async function renameLibraryFolder(path: string, name: string): Promise<LibraryFolder> {
  const response = await fetch(`${BASE}/folders/rename`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to rename folder"));
  const body = (await response.json()) as { folder: LibraryFolder };
  return body.folder;
}

export async function deleteLibraryFolder(path: string): Promise<{ deleted_track_ids: number[]; moved_to_recycle_bin: string[] }> {
  const response = await fetch(`${BASE}/folders?path=${encodeURIComponent(path)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to delete folder"));
  return response.json();
}

export async function moveTrack(
  trackId: number,
  destinationFolder: string,
  filenameStem?: string,
): Promise<Track> {
  const response = await fetch(`${BASE}/tracks/${trackId}/location`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      destination_folder: destinationFolder,
      filename_stem: filenameStem ?? null,
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to move track"));
  const body = (await response.json()) as { track: Track };
  return body.track;
}

export async function fetchSettings(): Promise<Settings> {
  const response = await fetch(`${BASE}/settings`);
  if (!response.ok) throw new Error(`Failed to fetch settings: ${response.status}`);
  return (await response.json()) as Settings;
}

export async function updateSettings(settings: Settings): Promise<Settings> {
  const response = await fetch(`${BASE}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error(`Failed to update settings: ${response.status}`);
  return (await response.json()) as Settings;
}

export async function browseForFolder(initialPath?: string): Promise<string | null> {
  const response = await fetch(`${BASE}/settings/browse-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_path: initialPath?.trim() || null }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Folder picker is unavailable"));
  const body = (await response.json()) as { path: string | null };
  return body.path;
}

export async function fetchSystem(): Promise<SystemInfo> {
  const response = await fetch(`${BASE}/system`);
  if (!response.ok) throw new Error(`Failed to fetch system info: ${response.status}`);
  return (await response.json()) as SystemInfo;
}

export async function fetchRecipes(): Promise<RecipeInfo[]> {
  const response = await fetch(`${BASE}/recipes`);
  if (!response.ok) throw new Error(`Failed to fetch recipes: ${response.status}`);
  const body = (await response.json()) as { recipes: RecipeInfo[] };
  return body.recipes;
}

export async function rescan(): Promise<LibraryScanStatus> {
  const response = await fetch(`${BASE}/rescan`, { method: "POST" });
  if (!response.ok) throw new Error(`Rescan failed: ${response.status}`);
  return response.json();
}

export async function fetchRescanStatus(): Promise<LibraryScanStatus> {
  const response = await fetch(`${BASE}/rescan`);
  if (!response.ok) throw new Error(`Failed to fetch rescan status: ${response.status}`);
  return response.json();
}

export function audioUrl(trackId: number): string {
  return `${BASE}/audio/${trackId}`;
}

export async function submitJob(submission: JobSubmission): Promise<{ job_id: number }> {
  const response = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(submission),
  });
  if (!response.ok) throw new Error(`Failed to submit job: ${response.status}`);
  return response.json();
}

export async function fetchJobs(): Promise<JobSummary[]> {
  const response = await fetch(`${BASE}/jobs`);
  if (!response.ok) throw new Error(`Failed to fetch jobs: ${response.status}`);
  const body = (await response.json()) as { jobs: JobSummary[] };
  return body.jobs;
}

export async function fetchJob(jobId: number): Promise<JobDetail> {
  const response = await fetch(`${BASE}/jobs/${jobId}`);
  if (!response.ok) throw new Error(`Failed to fetch job ${jobId}: ${response.status}`);
  return response.json();
}

export async function fetchJobHistory(options: {
  status?: JobHistoryStatus;
  query?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<JobHistoryPage> {
  const params = new URLSearchParams({
    status: options.status ?? "all",
    limit: String(options.limit ?? 25),
    offset: String(options.offset ?? 0),
  });
  if (options.query?.trim()) params.set("query", options.query.trim());
  const response = await fetch(`${BASE}/jobs/history?${params.toString()}`);
  if (!response.ok) throw new Error(`Failed to fetch processing history: ${response.status}`);
  return response.json();
}

export async function fetchJobItems(jobId: number, options: {
  status?: JobItemStatus | "all";
  query?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<JobItemsPage> {
  const params = new URLSearchParams({
    status: options.status ?? "all",
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  });
  if (options.query?.trim()) params.set("query", options.query.trim());
  const response = await fetch(`${BASE}/jobs/${jobId}/items?${params.toString()}`);
  if (!response.ok) throw new Error(`Failed to fetch processing items for job ${jobId}: ${response.status}`);
  return response.json();
}

export async function fetchTrackFailures(): Promise<TrackProcessingFailure[]> {
  const response = await fetch(`${BASE}/jobs/track-failures`);
  if (!response.ok) throw new Error(`Failed to fetch track processing failures: ${response.status}`);
  const body = (await response.json()) as { failures: TrackProcessingFailure[] };
  return body.failures;
}

export async function cancelJob(jobId: number): Promise<void> {
  const response = await fetch(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" });
  if (!response.ok) throw new Error(`Failed to cancel job ${jobId}: ${response.status}`);
}

export async function probeYoutube(url: string): Promise<YoutubeProbeResult> {
  const response = await fetch(`${BASE}/youtube/probe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to probe YouTube URL"));
  return response.json();
}

export async function importFromYoutube(request: YoutubeImportRequest): Promise<{ job_id: number }> {
  const response = await fetch(`${BASE}/youtube/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Failed to import from YouTube"));
  return response.json();
}

export async function fetchTrackParts(trackId: number): Promise<TrackPart[]> {
  const response = await fetch(`${BASE}/tracks/${trackId}/parts`);
  if (!response.ok) throw new Error(`Failed to fetch parts for track ${trackId}: ${response.status}`);
  const body = (await response.json()) as TrackPartsResponse;
  return body.parts;
}

export function partAudioUrl(trackId: number, part: string): string {
  return `${BASE}/audio/${trackId}/part/${encodeURIComponent(part)}`;
}

export async function fetchLrc(trackId: number): Promise<LrcReadResponse> {
  const response = await fetch(`${BASE}/tracks/${trackId}/lrc`);
  if (!response.ok) throw new Error(`Failed to fetch lrc for track ${trackId}: ${response.status}`);
  return (await response.json()) as LrcReadResponse;
}

export async function saveLrc(
  trackId: number,
  content: string,
  options?: { create?: "beside"; suffix?: string },
): Promise<LrcWriteResponse> {
  const params = new URLSearchParams();
  if (options?.create) params.set("create", options.create);
  if (options?.suffix) params.set("suffix", options.suffix);
  const query = params.toString();
  const response = await fetch(`${BASE}/tracks/${trackId}/lrc${query ? `?${query}` : ""}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) throw new Error(await errorMessage(response, `Failed to save lrc for track ${trackId}`));
  return (await response.json()) as LrcWriteResponse;
}

export function artworkUrl(trackId: number): string {
  return `${BASE}/tracks/${trackId}/artwork`;
}

export async function saveTrackTags(trackId: number, tags: TagsWritePayload): Promise<Track> {
  const response = await fetch(`${BASE}/tracks/${trackId}/tags`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(tags),
  });
  if (!response.ok) throw new Error(await errorMessage(response, `Failed to save tags for track ${trackId}`));
  return (await response.json()) as Track;
}

export async function fetchTagSuggestion(
  trackId: number,
  query: { artist: string | null; title: string; include_artwork?: boolean },
): Promise<TagSuggestion> {
  const response = await fetch(`${BASE}/tracks/${trackId}/tags/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!response.ok) throw new Error(await errorMessage(response, "Tag lookup failed"));
  return (await response.json()) as TagSuggestion;
}

export async function uploadTrackArtwork(trackId: number, file: File | Blob): Promise<void> {
  const response = await fetch(`${BASE}/tracks/${trackId}/artwork`, {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  if (!response.ok) throw new Error(await errorMessage(response, `Failed to upload artwork for track ${trackId}`));
}
