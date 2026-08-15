import { fetchJob, fetchRescanStatus, fetchTracks, reconcileTrackLyrics, rescan } from "./api";
import { jobsStore } from "./jobsStore.svelte";
import type { LibraryScanStatus, Track } from "./types";

// Fire-and-forget helper: routes a promise's rejection into a no-op so a
// background refresh (triggered from the onJobCompleted callback below,
// which cannot be awaited by its caller) never surfaces as an unhandled
// promise rejection.
function swallow(p: Promise<unknown>): void {
  p.catch(() => {});
}

function sameTrack(left: Track, right: Track): boolean {
  const leftProvenance = left.instrumental_provenance;
  const rightProvenance = right.instrumental_provenance;
  const leftLyricProvenance = left.lyric_timing_provenance;
  const rightLyricProvenance = right.lyric_timing_provenance;
  return left.id === right.id
    && left.media_root === right.media_root
    && left.relative_path === right.relative_path
    && left.artist === right.artist
    && left.title === right.title
    && left.lrc_state === right.lrc_state
    && left.stem_count === right.stem_count
    && left.album === right.album
    && left.year === right.year
    && left.duration_seconds === right.duration_seconds
    && left.outputs.instrumental === right.outputs.instrumental
    && left.outputs.vocals === right.outputs.vocals
    && left.outputs.lead_vocals === right.outputs.lead_vocals
    && left.outputs.backing_vocals === right.outputs.backing_vocals
    && left.outputs.drums === right.outputs.drums
    && left.outputs.bass === right.outputs.bass
    && left.outputs.guitar === right.outputs.guitar
    && left.outputs.piano === right.outputs.piano
    && left.outputs.other === right.outputs.other
    && left.outputs.lrc === right.outputs.lrc
    // Reprocessing can replace an instrumental without changing any output
    // presence flag. Quality/provenance is user-visible in the Instrumental
    // column, so a terminal database reconciliation must still mark this row
    // changed when only that metadata differs.
    && leftProvenance?.schema_version === rightProvenance?.schema_version
    && leftProvenance?.quality === rightProvenance?.quality
    && leftProvenance?.engine === rightProvenance?.engine
    && leftProvenance?.engine_version === rightProvenance?.engine_version
    && leftProvenance?.model === rightProvenance?.model
    && leftProvenance?.models.join("\u0000") === rightProvenance?.models.join("\u0000")
    && leftProvenance?.backing_vocal_mode === rightProvenance?.backing_vocal_mode
    && leftProvenance?.device === rightProvenance?.device
    && leftProvenance?.job_id === rightProvenance?.job_id
    && leftProvenance?.stage === rightProvenance?.stage
    && leftProvenance?.attribution === rightProvenance?.attribution
    && leftProvenance?.confirmed_by === rightProvenance?.confirmed_by
    && leftProvenance?.recorded_at === rightProvenance?.recorded_at
    && leftLyricProvenance?.quality === rightLyricProvenance?.quality
    && leftLyricProvenance?.lrc_sha256 === rightLyricProvenance?.lrc_sha256
    && leftLyricProvenance?.engine === rightLyricProvenance?.engine
    && leftLyricProvenance?.model === rightLyricProvenance?.model
    && leftLyricProvenance?.method === rightLyricProvenance?.method
    && leftLyricProvenance?.coverage === rightLyricProvenance?.coverage
    && leftLyricProvenance?.median_confidence === rightLyricProvenance?.median_confidence
    && leftLyricProvenance?.confidence_score === rightLyricProvenance?.confidence_score
    && leftLyricProvenance?.verified_words === rightLyricProvenance?.verified_words
    && leftLyricProvenance?.review_words === rightLyricProvenance?.review_words
    && leftLyricProvenance?.corrected_words === rightLyricProvenance?.corrected_words
    && leftLyricProvenance?.review_lines === rightLyricProvenance?.review_lines
    && leftLyricProvenance?.agreement_within_0_25 === rightLyricProvenance?.agreement_within_0_25
    && leftLyricProvenance?.median_agreement_seconds === rightLyricProvenance?.median_agreement_seconds
    && leftLyricProvenance?.attribution === rightLyricProvenance?.attribution
    && leftLyricProvenance?.confirmed_by === rightLyricProvenance?.confirmed_by
    && leftLyricProvenance?.recorded_at === rightLyricProvenance?.recorded_at;
}

function compareTracks(left: Track, right: Track): number {
  return (left.artist ?? "").localeCompare(right.artist ?? "", undefined, { sensitivity: "base" })
    || left.title.localeCompare(right.title, undefined, { sensitivity: "base" });
}

export function createTracksStore() {
  let tracks = $state<Track[]>([]);
  let trackIndexById = new Map<number, number>();
  let trackRevisions = $state<Record<number, number>>({});
  let currentQuery = $state("");
  let scanStatus = $state<LibraryScanStatus | null>(null);
  let scanPollTimer: ReturnType<typeof setTimeout> | undefined;
  let scanMonitorEpoch = 0;
  let scanRefreshInFlight = false;
  let scanRefreshPending = false;

  // Monotonic request id: guards against an older, slower fetchTracks() call
  // overwriting the results of a newer one when responses arrive out of
  // order (same pattern as jobsStore's refreshList()).
  let requestSeq = 0;

  function rebuildTrackIndex(): void {
    trackIndexById = new Map(tracks.map((track, index) => [track.id, index]));
  }

  async function refresh(query?: string): Promise<void> {
    if (query !== undefined) currentQuery = query;
    const seq = ++requestSeq;
    const result = await fetchTracks(currentQuery || undefined);
    if (seq === requestSeq) {
      const previousById = new Map(tracks.map((track) => [track.id, track]));
      for (const track of result) {
        const previous = previousById.get(track.id);
        if (previous && !sameTrack(previous, track)) touchTrack(track.id);
      }
      tracks = result;
      rebuildTrackIndex();
    }
  }

  function touchTrack(trackId: number): void {
    trackRevisions = {
      ...trackRevisions,
      [trackId]: (trackRevisions[trackId] ?? 0) + 1,
    };
  }

  function touchTracks(trackIds: Iterable<number>): void {
    const next = { ...trackRevisions };
    let changed = false;
    for (const trackId of trackIds) {
      next[trackId] = (next[trackId] ?? 0) + 1;
      changed = true;
    }
    if (changed) trackRevisions = next;
  }

  function replaceTrack(updated: Track): void {
    const query = currentQuery.trim().toLocaleLowerCase();
    const stillMatches = !query || [updated.artist, updated.title, updated.relative_path]
      .some((value) => value?.toLocaleLowerCase().includes(query));
    if (!stillMatches) {
      tracks = tracks.filter((track) => track.id !== updated.id);
      rebuildTrackIndex();
    } else if (trackIndexById.has(updated.id)) {
      const index = trackIndexById.get(updated.id)!;
      const previous = tracks[index];
      if (previous.artist !== updated.artist || previous.title !== updated.title) {
        tracks = tracks.map((track) => (track.id === updated.id ? updated : track)).sort(compareTracks);
        rebuildTrackIndex();
      } else {
        // Output stages can publish tens of thousands of row updates. Svelte's
        // proxied array supports an indexed assignment, avoiding a full array
        // scan and sort when processing did not change the sort keys.
        tracks[index] = updated;
      }
    } else {
      tracks = [...tracks, updated].sort(compareTracks);
      rebuildTrackIndex();
    }
    // A successful tag save may also have replaced embedded artwork. Touch
    // even when the text fields are unchanged so a previously cached image
    // (or the missing-artwork placeholder) is retried for this row only.
    touchTrack(updated.id);
  }

  function removeTrack(trackId: number): void {
    tracks = tracks.filter((track) => track.id !== trackId);
    rebuildTrackIndex();
    if (trackRevisions[trackId] !== undefined) {
      const next = { ...trackRevisions };
      delete next[trackId];
      trackRevisions = next;
    }
  }

  async function reconcileLrcStates(trackIds: readonly number[]): Promise<void> {
    const uniqueIds = [...new Set(trackIds)].slice(0, 64);
    if (uniqueIds.length === 0) return;
    const changed = await reconcileTrackLyrics(uniqueIds);
    for (const track of changed) replaceTrack(track);
  }

  const ACTIVE_SCAN_STATUSES = new Set(["queued", "running"]);
  const SCAN_POLL_INTERVAL_MS = 750;

  function scanIsActive(status: LibraryScanStatus | null): boolean {
    return status !== null && ACTIVE_SCAN_STATUSES.has(status.status);
  }

  function scheduleScanPoll(epoch: number): void {
    if (scanPollTimer !== undefined) clearTimeout(scanPollTimer);
    scanPollTimer = setTimeout(() => {
      scanPollTimer = undefined;
      swallow(pollScanStatus(epoch));
    }, SCAN_POLL_INTERVAL_MS);
  }

  function requestScanRefresh(): void {
    if (scanRefreshInFlight) {
      scanRefreshPending = true;
      return;
    }
    scanRefreshInFlight = true;
    refresh()
      .catch(() => {})
      .finally(() => {
        scanRefreshInFlight = false;
        if (scanRefreshPending) {
          scanRefreshPending = false;
          requestScanRefresh();
        }
      });
  }

  function applyScanStatus(next: LibraryScanStatus, epoch: number): void {
    if (epoch !== scanMonitorEpoch) return;
    const previous = scanStatus;
    scanStatus = next;

    // Each published batch is already committed, so refresh while the scan
    // runs instead of waiting for its terminal state. A terminal refresh is
    // always performed as well to surface stale-file/root cleanup.
    const publishedNewTracks = previous?.scan_id !== next.scan_id
      ? next.tracks_found > 0
      : previous.tracks_found !== next.tracks_found || previous.media_roots_scanned !== next.media_roots_scanned;
    const becameTerminal = (next.status === "completed" || next.status === "failed")
      && (previous?.scan_id !== next.scan_id || previous.status !== next.status);
    if (publishedNewTracks || becameTerminal) requestScanRefresh();

    if (scanIsActive(next)) scheduleScanPoll(epoch);
  }

  async function pollScanStatus(epoch: number): Promise<void> {
    try {
      applyScanStatus(await fetchRescanStatus(), epoch);
    } catch {
      // Keep monitoring an already-known active scan through a brief server
      // or network interruption. The next poll will reconcile live state.
      if (epoch === scanMonitorEpoch && scanIsActive(scanStatus)) scheduleScanPoll(epoch);
    }
  }

  async function startRescan(): Promise<LibraryScanStatus> {
    const epoch = ++scanMonitorEpoch;
    if (scanPollTimer !== undefined) {
      clearTimeout(scanPollTimer);
      scanPollTimer = undefined;
    }
    const status = await rescan();
    applyScanStatus(status, epoch);
    return status;
  }

  async function resumeRescan(): Promise<void> {
    const epoch = ++scanMonitorEpoch;
    if (scanPollTimer !== undefined) clearTimeout(scanPollTimer);
    scanPollTimer = undefined;
    applyScanStatus(await fetchRescanStatus(), epoch);
  }

  function stopRescanMonitoring(): void {
    scanMonitorEpoch += 1;
    if (scanPollTimer !== undefined) clearTimeout(scanPollTimer);
    scanPollTimer = undefined;
    scanRefreshPending = false;
  }

  async function reconcileMetadataJobs(jobIds: readonly number[]): Promise<void> {
    // A final /tracks fetch cannot reveal an artwork-only change because the
    // Track contract intentionally contains no embedded image bytes. Read the
    // small completed-job details as well, then invalidate exactly the rows
    // that completed or skipped metadata work. This makes the terminal refresh
    // a real missed-WebSocket fallback without reloading every cover in a
    // large library.
    const [details] = await Promise.all([
      Promise.allSettled(jobIds.map((jobId) => fetchJob(jobId))),
      refresh(),
    ]);
    const affectedTrackIds = new Set<number>();
    for (const detail of details) {
      if (detail.status !== "fulfilled") continue;
      for (const item of detail.value.items) {
        if (item.track_id !== null && (item.status === "completed" || item.status === "skipped")) {
          affectedTrackIds.add(item.track_id);
        }
      }
    }
    touchTracks(affectedTrackIds);
  }

  // Processing stages reconcile their affected catalogue row at the backend
  // stage boundary and publish track_updated. A single database refresh at
  // terminal state is only a missed-WebSocket safety net; never start a full
  // filesystem rescan merely because a job completed.
  //
  // This subscription is made once, here at module scope (backing the
  // exported `tracksStore` singleton below), rather than inside Library.svelte
  // - Library unmounts whenever the user opens the Mixer or the Lyric Editor,
  // and a job can easily finish while that's the current view. A listener
  // that only lived for Library's mounted lifetime would miss exactly that
  // completion, and the user would come back to a Library that reloads on
  // mount but still shows stale badges (the rescan never ran). Living at
  // module scope means this keeps firing in the background no matter which
  // view is showing, so Library always has fresh data by the time the user
  // returns to it.
  jobsStore.onJobCompleted((jobIds) => {
    const finishedJobs = jobIds.map((jobId) => jobsStore.jobs.find((job) => job.id === jobId));
    const metadataOnly = finishedJobs.length > 0
      && finishedJobs.every((job) => job?.recipe === "fetch_tags");
    if (metadataOnly) {
      // Final safety reconciliation: individual socket events give immediate
      // feedback, while this one normal list fetch guarantees the finished
      // result even if the connection briefly missed an event. The queue has
      // already updated each metadata row, so no filesystem rescan is needed.
      swallow(reconcileMetadataJobs(jobIds));
    } else {
      swallow(refresh());
    }
  });

  // Metadata processing is the one background operation that can publish a
  // complete fresh row cheaply. Apply each row as soon as its tag/artwork
  // stage finishes; replaceTrack also invalidates that row's artwork cache.
  jobsStore.onTrackChanged((track) => {
    replaceTrack(track);
  });

  return {
    get tracks() {
      return tracks;
    },
    get query() {
      return currentQuery;
    },
    get scanStatus() {
      return scanStatus;
    },
    revisionFor(trackId: number) {
      return trackRevisions[trackId] ?? 0;
    },
    refresh,
    replaceTrack,
    removeTrack,
    reconcileLrcStates,
    startRescan,
    resumeRescan,
    stopRescanMonitoring,
  };
}

export const tracksStore = createTracksStore();
