export interface TrackOutputs {
  instrumental: boolean;
  vocals: boolean;
  lead_vocals: boolean;
  backing_vocals: boolean;
  drums: boolean;
  bass: boolean;
  guitar: boolean;
  piano: boolean;
  other: boolean;
  lrc: boolean;
}

export type InstrumentalQuality = "fast" | "balanced" | "high_quality";

export interface InstrumentalProvenance {
  schema_version: number;
  part: "instrumental";
  quality: InstrumentalQuality | null;
  engine: "demucs" | "uvr_karaoke_ensemble" | string;
  engine_version: string | null;
  model: string;
  models: string[];
  backing_vocal_mode: string;
  device: string | null;
  job_id: number | null;
  stage: string;
  attribution: "confirmed" | "inferred" | "manual";
  confirmed_by?: string | null;
  recorded_at: string;
}

export type LrcState = "enhanced" | "line_timed" | "untimed" | "empty" | "unknown";

export interface LyricTimingProvenance {
  schema_version: number;
  part: "lyrics";
  quality: "review" | "high_quality";
  timing_state: "enhanced";
  lrc_sha256: string;
  engine: string | null;
  model: string | null;
  method: string | null;
  device: string | null;
  words: number | null;
  matched: number | null;
  interpolated: number | null;
  coverage: number | null;
  median_confidence: number | null;
  low_confidence_words: number | null;
  confidence_score?: number | null;
  verified_words?: number | null;
  review_words?: number | null;
  corrected_words?: number | null;
  review_lines?: number | null;
  agreement_within_0_25?: number | null;
  median_agreement_seconds?: number | null;
  attribution: "automatic" | "manual";
  confirmed_by: string | null;
  recorded_at: string;
}

export interface Track {
  id: number;
  media_root: string;
  relative_path: string;
  artist: string | null;
  title: string;
  outputs: TrackOutputs;
  lrc_state: LrcState | null;
  stem_count: number;
  album: string | null;
  year: number | null;
  duration_seconds: number | null;
  has_artwork?: boolean | null;
  instrumental_provenance?: InstrumentalProvenance | null;
  lyric_timing_provenance?: LyricTimingProvenance | null;
}

export interface LibraryFolder {
  path: string;
  media_root: string;
  relative_path: string;
  name: string;
}

export interface RecipeOptionSpec {
  type: "select" | "checkbox" | "number";
  choices?: string[];
  default: unknown;
  advanced?: boolean;
  description?: string;
}

export interface RecipeInfo {
  name: string;
  lane: "gpu" | "cpu";
  options_schema: Record<string, RecipeOptionSpec> | null;
}

export interface Settings {
  media_roots: string[];
  mirror_roots: string[];
  device_preference: "auto" | "cuda" | "cpu";
  downloads_root?: string | null;
  youtube_cookies?: { mode: "none" | "browser" | "file"; browser?: string; cookies_file?: string };
}

export interface YoutubePlaylistEntry {
  url: string;
  title: string;
  duration: number;
}

export type YoutubeProbeResult =
  | { is_playlist: false; title: string; duration: number; uploader: string }
  | { is_playlist: true; entries: YoutubePlaylistEntry[]; count: number; total: number };

export interface YoutubeImportRequest {
  url: string;
  artist?: string;
  title?: string;
  process_after?: { recipe: string; options: JobOptions };
}

export interface SystemInfo {
  device: "cuda" | "cpu";
  workers: { demucs: boolean; uvr: boolean; whisperx: boolean };
}

export type LibraryScanState = "idle" | "queued" | "running" | "completed" | "failed";

export interface LibraryScanStatus {
  scan_id: number;
  status: LibraryScanState;
  tracks_found: number;
  media_roots_scanned: number;
  media_roots_total: number;
  current_root: string | null;
  unavailable_roots: string[];
  tracks_purged: number;
  error: string | null;
  updated_at: string;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type JobItemStatus = "queued" | "running" | "completed" | "failed" | "skipped" | "cancelled";
export type JobStageStatus = "pending" | "running" | "completed" | "skipped" | "failed";

export interface JobStage {
  name: string;
  status: JobStageStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface JobItem {
  id: number;
  track_id: number | null;
  source_path: string;
  status: JobItemStatus;
  current_stage: string | null;
  stages: JobStage[];
  error_text: string | null;
}

export interface JobOptions {
  device?: "auto" | "cuda" | "cpu";
  overwrite?: boolean;
  output_mode?: "beside" | "mirror";
  [key: string]: unknown;
}

export interface JobSummary {
  id: number;
  recipe: string;
  options: JobOptions;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  item_counts: Record<JobItemStatus, number>;
}

export interface JobDetail {
  id: number;
  recipe: string;
  options: JobOptions;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  items: JobItem[];
}

export type JobHistoryStatus = "all" | "active" | "completed" | "failed" | "cancelled";

export interface JobHistoryPage {
  jobs: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobItemsPage {
  items: JobItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface TrackProcessingFailure {
  track_id: number;
  job_id: number;
  stage: string | null;
  message: string;
}

export interface JobSubmission {
  recipe: string;
  track_ids?: number[];
  folder?: string;
  options: JobOptions;
}

export interface JobEvent {
  type: "job" | "item" | "stage" | "stage_progress" | "library_scan" | "track_updated";
  job_id?: number;
  status?: string;
  item_id?: number;
  current_stage?: string | null;
  stage?: string;
  detail?: string;
  track_id?: number;
  track?: Track;
}

export interface TrackPart {
  part: string;
  exists: boolean;
  duration: number | null;
}

export interface TrackPartsResponse {
  parts: TrackPart[];
}

export interface LrcReadResponse {
  exists: boolean;
  content: string;
  state: LrcState | null;
  timing_report?: LyricTimingReport | null;
}

export interface LyricTimingWordDetail {
  word_number: number;
  line_index: number;
  word_index: number;
  word: string;
  previous_seconds: number;
  selected_seconds: number;
  original_seconds: number;
  residual_seconds: number;
  agreement_seconds: number;
  original_score: number | null;
  residual_score: number | null;
  confidence: number;
  status: "verified" | "review";
  correction_basis?: "verified_agreement" | "gross_directional" | "retained_existing";
  corrected: boolean;
}

export interface LyricTimingReport {
  summary: LyricTimingProvenance;
  words: LyricTimingWordDetail[];
}

export interface LrcWriteResponse {
  path: string;
  /** Fresh library row for a canonical save; absent for a suffixed Save As. */
  track?: Track | null;
}

export interface TagsWritePayload {
  artist: string | null;
  title: string;
  album: string | null;
  year: number | null;
}

export interface TagSuggestion {
  artist: string | null;
  title: string | null;
  album: string | null;
  year: number | null;
  provider: string;
  artwork_data_url: string | null;
}
