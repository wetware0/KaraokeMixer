import { cancelJob as apiCancelJob, fetchJobs, fetchTrackFailures } from "./api";
import { connectJobsSocket, type JobsSocketHandle } from "./jobsSocket";
import type { JobEvent, JobSummary, Track, TrackProcessingFailure } from "./types";

type CompletionListener = (jobIds: readonly number[]) => void;
type JobListener = () => void;
type TrackChangeListener = (track: Track) => void;

// A job event with one of these statuses means the job has finished (in
// whatever final state) - completed, failed, or cancelled all matter to the
// UI (e.g. failed jobs can still have partial outputs worth surfacing).
const TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "cancelled"]);

// Fire-and-forget helper: routes a promise's rejection into a no-op so that
// background refreshes (triggered from socket/poll callbacks, which cannot
// be awaited by their caller) never surface as unhandled promise rejections.
// Silent drop is acceptable for 2a - the poll/socket loop retries naturally.
function swallow(p: Promise<unknown>): void {
  p.catch(() => {});
}

export function createJobsStore() {
  let jobs = $state<JobSummary[]>([]);
  let stageDetails = $state<Record<number, string>>({});
  let trackFailures = $state<Record<number, TrackProcessingFailure>>({});
  let socketHandle: JobsSocketHandle | null = null;
  const completionListeners = new Set<CompletionListener>();
  const jobCompletionListeners = new Map<number, Set<JobListener>>();
  const notifiedTerminalJobIds = new Set<number>();
  const trackChangeListeners = new Set<TrackChangeListener>();
  let hasLoadedJobs = false;

  // Monotonic request id: guards against an older, slower fetchJobs() call
  // overwriting the results of a newer one when responses arrive out of order.
  let refreshSeq = 0;
  let failureRefreshSeq = 0;

  async function refreshList(): Promise<number[]> {
    const seq = ++refreshSeq;
    const result = await fetchJobs();
    if (seq === refreshSeq) {
      const previousStatuses = new Map(jobs.map((job) => [job.id, job.status]));
      const transitionedJobIds = hasLoadedJobs
        ? result
            .filter((job) => TERMINAL_JOB_STATUSES.has(job.status) && !TERMINAL_JOB_STATUSES.has(previousStatuses.get(job.id) ?? ""))
            .map((job) => job.id)
        : [];
      jobs = result;
      hasLoadedJobs = true;
      return transitionedJobIds;
    }
    return [];
  }

  async function refreshFailures(): Promise<void> {
    const seq = ++failureRefreshSeq;
    const failures = await fetchTrackFailures();
    if (seq === failureRefreshSeq) {
      trackFailures = Object.fromEntries(failures.map((failure) => [failure.track_id, failure]));
    }
  }

  function notifyCompletions(jobIds: readonly number[]): void {
    const freshJobIds = [...new Set(jobIds)].filter((jobId) => !notifiedTerminalJobIds.has(jobId));
    if (freshJobIds.length === 0) return;
    freshJobIds.forEach((jobId) => notifiedTerminalJobIds.add(jobId));
    completionListeners.forEach((listener) => listener(freshJobIds));
    for (const jobId of freshJobIds) {
      jobCompletionListeners.get(jobId)?.forEach((listener) => listener());
      jobCompletionListeners.delete(jobId);
    }
  }

  // Rapid bursts of job/item/stage events (e.g. several stage transitions
  // landing within milliseconds of each other) each used to trigger their
  // own fetchJobs() round trip. This trailing debounce coalesces any events
  // that land within REFRESH_DEBOUNCE_MS of each other into a single
  // refresh, fired REFRESH_DEBOUNCE_MS after the *last* one - completion
  // notifications piggyback on that same coalesced refresh so a listener
  // never fires before the list has actually been updated.
  const REFRESH_DEBOUNCE_MS = 200;
  let debounceTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingNotifyCompletion = false;
  const pendingTerminalJobIds = new Set<number>();

  function scheduleRefresh(completedJobId: number | null): void {
    if (completedJobId !== null) {
      pendingNotifyCompletion = true;
      pendingTerminalJobIds.add(completedJobId);
    }
    if (debounceTimer !== undefined) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = undefined;
      const shouldNotify = pendingNotifyCompletion;
      const completedJobIds = [...pendingTerminalJobIds];
      pendingNotifyCompletion = false;
      pendingTerminalJobIds.clear();
      swallow(
        refreshList().then((transitionedJobIds) => {
          if (shouldNotify) {
            notifyCompletions([...completedJobIds, ...transitionedJobIds]);
          } else {
            notifyCompletions(transitionedJobIds);
          }
          if (shouldNotify || transitionedJobIds.length > 0) swallow(refreshFailures());
        }),
      );
    }, REFRESH_DEBOUNCE_MS);
  }

  function applyEvent(event: JobEvent): void {
    // Library-scan progress shares the socket transport but is monitored by
    // tracksStore. It must not trigger a jobs-list refetch for every scan
    // batch, and it has no job_id by design.
    if (event.type === "library_scan") return;
    if (event.type === "track_updated") {
      const updatedTrack = event.track;
      if (updatedTrack) trackChangeListeners.forEach((listener) => listener(updatedTrack));
      return;
    }
    if (event.type === "stage_progress") {
      // A live per-stage detail line, not a change to the job list itself -
      // update it directly and skip the debounced refreshList() entirely, so
      // frequent progress lines never spam extra fetchJobs() round trips.
      stageDetails = { ...stageDetails, [event.job_id!]: event.detail ?? "" };
      return;
    }
    // event.status is now optional on JobEvent (stage_progress events carry
    // no status), so TERMINAL_JOB_STATUSES.has(...) - a Set<string> - needs
    // an explicit undefined guard first, or strict TS rejects passing
    // `string | undefined` where `string` is expected.
    const terminalJobId = event.type === "job" && event.status !== undefined && TERMINAL_JOB_STATUSES.has(event.status)
      ? event.job_id ?? null
      : null;
    if (event.type === "item" && event.status === "failed") swallow(refreshFailures());
    scheduleRefresh(terminalJobId);
  }

  function start(): void {
    swallow(refreshList());
    swallow(refreshFailures());
    socketHandle = connectJobsSocket({
      onEvent: (event) => applyEvent(event),
      onPollFallback: () => swallow(refreshList().then((jobIds) => {
        notifyCompletions(jobIds);
        if (jobIds.length > 0) return refreshFailures();
      })),
    });
  }

  function stop(): void {
    socketHandle?.close();
    socketHandle = null;
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer);
      debounceTimer = undefined;
    }
    pendingNotifyCompletion = false;
    pendingTerminalJobIds.clear();
    notifiedTerminalJobIds.clear();
    hasLoadedJobs = false;
    trackFailures = {};
  }

  async function cancel(jobId: number): Promise<void> {
    await apiCancelJob(jobId);
  }

  function onJobCompleted(listener: CompletionListener): () => void {
    completionListeners.add(listener);
    return () => completionListeners.delete(listener);
  }

  function onJobCompletedFor(jobId: number, listener: JobListener): () => void {
    const listeners = jobCompletionListeners.get(jobId) ?? new Set<JobListener>();
    listeners.add(listener);
    jobCompletionListeners.set(jobId, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) jobCompletionListeners.delete(jobId);
    };
  }

  function onTrackChanged(listener: TrackChangeListener): () => void {
    trackChangeListeners.add(listener);
    return () => trackChangeListeners.delete(listener);
  }

  return {
    get jobs() {
      return jobs;
    },
    get stageDetails() {
      return stageDetails;
    },
    get trackFailures() {
      return trackFailures;
    },
    start,
    stop,
    cancel,
    onJobCompleted,
    onJobCompletedFor,
    onTrackChanged,
  };
}

export const jobsStore = createJobsStore();
