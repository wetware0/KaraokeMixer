<script lang="ts">
  import { fetchJob } from "../api";
  import { jobsStore } from "../jobsStore.svelte";
  import type { JobDetail, JobItem, JobSummary } from "../types";

  let { showFailed = true }: { showFailed?: boolean } = $props();

  let details = $state<Record<number, JobDetail>>({});
  let cancelling = $state<Set<number>>(new Set());

  // Failed jobs stay in the database forever, so without persistence a
  // dismissal only ever lived in memory: a page reload (a fresh JobTray
  // instance, even though it's mounted once per app lifetime - see
  // App.svelte) would start from an empty set and every previously-dismissed
  // error would resurface at the bottom of the screen. Persisted to
  // localStorage under this key as a JSON array of job ids so a dismissal
  // survives a reload.
  const DISMISSED_JOBS_STORAGE_KEY = "karaoke-mm.dismissedJobs";

  const STAGE_LABELS: Record<string, string> = {
    demucs_separate: "Separating stems",
    karaoke_instrumental: "Creating karaoke instrumental",
    fetch_lyrics: "Downloading lyrics",
    align_lyrics: "Enhancing lyric timing",
    fetch_tags: "Updating tags and artwork",
    uvr_vocal_split: "Separating lead and backing vocals",
    youtube_import: "Downloading track",
  };

  interface ActivePhase {
    name: string;
    label: string;
    step: number;
    stepCount: number;
    done: number;
    trackCount: number;
  }

  function loadDismissedIds(): Set<number> {
    try {
      const raw = localStorage.getItem(DISMISSED_JOBS_STORAGE_KEY);
      if (!raw) return new Set();
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(parsed.filter((id): id is number => typeof id === "number"));
    } catch {
      // Corrupt JSON, or localStorage unavailable (private browsing, quota) -
      // fall back to "nothing dismissed yet" rather than let a bad stored
      // value break the tray entirely.
      return new Set();
    }
  }

  function persistDismissedIds(ids: Set<number>): void {
    try {
      localStorage.setItem(DISMISSED_JOBS_STORAGE_KEY, JSON.stringify([...ids]));
    } catch {
      // Storage can throw (quota exceeded, disabled) - dismissal still works
      // for the rest of this session via the in-memory $state below, it just
      // won't survive a reload. Not worth surfacing to the user.
    }
  }

  const initialDismissedIds = loadDismissedIds();
  let dismissedJobIds = $state<Set<number>>(initialDismissedIds);

  // Plain (non-$state) mirror of `dismissedJobIds`, updated every time
  // `dismissedJobIds` is - deliberately NOT reactive state, for the same
  // reason as `fetchedDetailIds` below: the pruning $effect further down
  // needs to read the *current* dismissed ids to prune them, but must never
  // do so via a reactive read of `dismissedJobIds` itself, since that same
  // effect is also what writes `dismissedJobIds` - reading it reactively
  // would make the effect depend on its own write and re-run forever (the
  // exact regression this file has already been bitten by once; see
  // JobTray.test.ts). Built from `initialDismissedIds`, not from
  // `dismissedJobIds` itself, so this line is never a reactive read either.
  let dismissedIdsMirror = new Set(initialDismissedIds);

  function setDismissedIds(next: Set<number>): void {
    dismissedJobIds = next;
    dismissedIdsMirror = next;
    persistDismissedIds(next);
  }

  // Plain (non-$state) instance-scoped set - deliberately NOT reactive state.
  // A failed job's detail never changes once failed, so this just remembers
  // which failed-job ids have already been fetched. Critically, the $effect
  // below must never READ from $state in its "already fetched?" checks - if
  // it did (as it once did, via `details`), the effect would take a
  // dependency on `details`, and every `details = {...}` reassignment inside
  // the effect's own running-job fetch would re-trigger the effect at
  // microtask speed, forever (regression: see JobTray.test.ts).
  const fetchedDetailIds = new Set<number>();

  const activeJobs = $derived(
    jobsStore.jobs.filter((job) => job.status === "queued" || job.status === "running"),
  );

  const failedJobs = $derived(
    showFailed
      ? jobsStore.jobs.filter((job) => job.status === "failed" && !dismissedJobIds.has(job.id))
      : [],
  );

  // Drop any dismissed id that no longer corresponds to a job jobsStore
  // knows about (job history rotated/cleared server-side, a dev-only DB
  // reset, ...) so the persisted list can't grow without bound forever.
  // Reads `jobsStore.jobs` (the intended reactive trigger) and
  // `dismissedIdsMirror` - the plain, non-reactive mirror from above, NOT
  // `dismissedJobIds` itself - so this effect never takes a dependency on
  // its own write via setDismissedIds(). The size check further guards
  // against reassigning (and re-persisting) when nothing actually changed.
  //
  // `jobsStore.jobs` starts out empty and only gets populated once its
  // initial /api/jobs fetch resolves, and this effect runs immediately on
  // mount - without the length guard below, that transient empty list would
  // look exactly like "every dismissed job is stale" and wipe out a
  // perfectly valid persisted set before the real data ever arrived.
  $effect(() => {
    if (jobsStore.jobs.length === 0) return;
    const liveIds = new Set(jobsStore.jobs.map((job) => job.id));
    const pruned = new Set([...dismissedIdsMirror].filter((id) => liveIds.has(id)));
    if (pruned.size !== dismissedIdsMirror.size) {
      setDismissedIds(pruned);
    }
  });

  $effect(() => {
    for (const job of activeJobs) {
      if (job.status === "running") {
        fetchJob(job.id)
          .then((detail) => {
            details = { ...details, [job.id]: detail };
          })
          .catch(() => {});
      }
    }
    // A failed job's detail (and its error_text) never changes once failed,
    // so fetch it once per newly-seen failed job rather than on every effect
    // re-run. The guard reads `fetchedDetailIds` (plain Set, not $state) -
    // never `details` - so this loop cannot make the effect depend on its
    // own write.
    for (const job of failedJobs) {
      if (!fetchedDetailIds.has(job.id)) {
        fetchedDetailIds.add(job.id);
        fetchJob(job.id)
          .then((detail) => {
            details = { ...details, [job.id]: detail };
          })
          .catch(() => {});
      }
    }
  });

  function stageLabel(name: string): string {
    return STAGE_LABELS[name] ?? name
      .split("_")
      .filter(Boolean)
      .map((word, index) => index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word)
      .join(" ");
  }

  function activePhase(job: JobSummary): ActivePhase | null {
    const detail = details[job.id];
    if (!detail) return null;
    const runningItem = detail.items.find((item) => item.status === "running" && item.current_stage !== null);
    if (!runningItem?.current_stage) return null;
    const stageIndex = runningItem.stages.findIndex((stage) => stage.name === runningItem.current_stage);
    if (stageIndex < 0) return null;
    const name = runningItem.current_stage;
    const done = detail.items.filter((item) => {
      const stage = item.stages.find((candidate) => candidate.name === name);
      if (stage && (stage.status === "completed" || stage.status === "skipped" || stage.status === "failed")) {
        return true;
      }
      // An item which ended in an earlier phase will not enter this phase,
      // but still must count as resolved so phase progress can reach 100%.
      return item.status === "failed" || item.status === "cancelled";
    }).length;
    return {
      name,
      label: stageLabel(name),
      step: stageIndex + 1,
      stepCount: runningItem.stages.length,
      done,
      trackCount: detail.items.length,
    };
  }

  function itemSummary(job: JobSummary): string {
    const phase = activePhase(job);
    if (phase) return `${phase.done} of ${phase.trackCount} tracks`;

    const counts = job.item_counts;
    const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
    const done = counts.completed + counts.failed + counts.skipped + counts.cancelled;
    return `${done} of ${total} items`;
  }

  function currentStage(job: JobSummary): string | null {
    const phase = activePhase(job);
    return phase ? `Step ${phase.step} of ${phase.stepCount} · ${phase.label}` : null;
  }

  function failedItems(job: JobSummary): JobItem[] {
    const detail = details[job.id];
    return detail?.items.filter((item) => item.status === "failed") ?? [];
  }

  function failedTrackLabel(item: JobItem): string {
    if (/^https?:\/\//i.test(item.source_path)) return item.source_path;
    return item.source_path.split(/[\\/]/).pop() ?? item.source_path;
  }

  function failedStageLabel(item: JobItem): string {
    const failedStage = item.stages.find((stage) => stage.status === "failed");
    return failedStage ? stageLabel(failedStage.name) : "Processing";
  }

  function conciseError(item: JobItem): string {
    const error = item.error_text ?? "Processing failed";
    const lowered = error.toLowerCase();
    if (lowered.includes("stereo needs to be set to true")) {
      return "Surround audio was not accepted by the stereo separation model";
    }
    if (lowered.includes("pytorchstreamreader") && lowered.includes("central directory")) {
      return "A UVR model download was incomplete or corrupt";
    }
    return error.split(/\r?\n/).find((line) => line.trim())?.trim() ?? "Processing failed";
  }

  async function cancel(jobId: number) {
    cancelling = new Set(cancelling).add(jobId);
    try {
      await jobsStore.cancel(jobId);
    } catch {
      const next = new Set(cancelling);
      next.delete(jobId);
      cancelling = next;
    }
  }

  function dismiss(jobId: number) {
    const next = new Set(dismissedJobIds);
    next.add(jobId);
    setDismissedIds(next);
  }
</script>

{#if activeJobs.length > 0 || failedJobs.length > 0}
  <div class="job-tray">
    {#each activeJobs as job (job.id)}
      <div class="job-tray-item">
        <span class="job-tray-recipe">{job.recipe}</span>
        <span class="job-tray-status job-tray-status-{job.status}">{job.status}</span>
        {#if currentStage(job)}
          <span class="job-tray-stage">{currentStage(job)}</span>
        {/if}
        <span class="job-tray-progress">{itemSummary(job)}</span>
        {#if jobsStore.stageDetails[job.id]}
          <span class="job-tray-detail">{jobsStore.stageDetails[job.id]}</span>
        {/if}
        <button
          class="job-tray-cancel"
          disabled={cancelling.has(job.id)}
          onclick={() => cancel(job.id)}
        >
          Cancel
        </button>
      </div>
    {/each}
    {#if failedJobs.length > 0}
      <span class="job-tray-section-label">Failed</span>
      {#each failedJobs as job (job.id)}
        <div class="job-tray-item job-tray-item-failed">
          <span class="job-tray-recipe">{job.recipe}</span>
          <span class="job-tray-status job-tray-status-failed">{job.status}</span>
          {#if failedItems(job).length > 0}
            <span class="job-tray-error-count">
              {failedItems(job).length} track{failedItems(job).length === 1 ? "" : "s"} failed
            </span>
            <ul class="job-tray-failure-list">
              {#each failedItems(job).slice(0, 5) as item (item.id)}
                <li title={item.error_text ?? "Processing failed"}>
                  <strong>{failedTrackLabel(item)}</strong>
                  <span>{failedStageLabel(item)}: {conciseError(item)}</span>
                </li>
              {/each}
              {#if failedItems(job).length > 5}
                <li>and {failedItems(job).length - 5} more</li>
              {/if}
            </ul>
          {/if}
          <button
            class="job-tray-dismiss"
            onclick={() => dismiss(job.id)}
            aria-label={`Dismiss ${job.recipe}`}
          >
            ×
          </button>
        </div>
      {/each}
    {/if}
  </div>
{/if}
