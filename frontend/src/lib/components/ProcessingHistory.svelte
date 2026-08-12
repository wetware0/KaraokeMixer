<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { fetchJobHistory, fetchJobItems } from "../api";
  import type { JobHistoryStatus, JobItem, JobItemStatus, JobSummary } from "../types";

  let { onBack = () => {} }: { onBack?: () => void } = $props();

  const PAGE_SIZE = 25;
  const ITEM_PAGE_SIZE = 50;
  const RECIPE_LABELS: Record<string, string> = {
    karaoke: "Karaoke instrumental",
    full_stems: "All editable stems",
    lyrics_only: "Lyrics and enhanced timing",
    fetch_tags: "Tags and artwork",
    full_prep: "Complete karaoke preparation",
    align_only: "Re-time lyrics with AI",
    youtube_import: "YouTube import",
  };
  const STAGE_LABELS: Record<string, string> = {
    demucs_separate: "Separate stems",
    karaoke_instrumental: "Create karaoke instrumental",
    fetch_lyrics: "Download lyrics",
    align_lyrics: "Enhance lyric timing",
    fetch_tags: "Update tags and artwork",
    uvr_vocal_split: "Separate lead/backing vocals",
    youtube_import: "Download track",
  };

  interface ItemPageState {
    items: JobItem[];
    total: number;
    offset: number;
    status: JobItemStatus | "all";
    query: string;
    appliedQuery: string;
    loading: boolean;
    error: string | null;
  }

  let jobs = $state<JobSummary[]>([]);
  let total = $state(0);
  let offset = $state(0);
  let status = $state<JobHistoryStatus>("all");
  let query = $state("");
  let appliedQuery = $state("");
  let loading = $state(true);
  let error = $state<string | null>(null);
  let expandedJobIds = $state<Set<number>>(new Set());
  let itemPages = $state<Record<number, ItemPageState>>({});
  let historyRequest = 0;
  const itemRequests = new Map<number, number>();
  let refreshTimer: ReturnType<typeof setInterval> | undefined;

  const firstResult = $derived(total === 0 ? 0 : offset + 1);
  const lastResult = $derived(Math.min(offset + jobs.length, total));

  onMount(() => {
    void loadHistory();
    refreshTimer = setInterval(() => void loadHistory(false), 30_000);
  });

  onDestroy(() => {
    if (refreshTimer !== undefined) clearInterval(refreshTimer);
  });

  async function loadHistory(showLoading = true): Promise<void> {
    const request = ++historyRequest;
    if (showLoading) loading = true;
    error = null;
    try {
      const page = await fetchJobHistory({ status, query: appliedQuery, limit: PAGE_SIZE, offset });
      if (request !== historyRequest) return;
      jobs = page.jobs;
      total = page.total;
      if (offset > 0 && page.jobs.length === 0 && page.total > 0) {
        offset = Math.max(0, Math.floor((page.total - 1) / PAGE_SIZE) * PAGE_SIZE);
        await loadHistory();
      }
    } catch (reason) {
      if (request === historyRequest) {
        error = reason instanceof Error ? reason.message : "Could not load processing history";
      }
    } finally {
      if (request === historyRequest) loading = false;
    }
  }

  function submitSearch(event: SubmitEvent): void {
    event.preventDefault();
    appliedQuery = query.trim();
    offset = 0;
    void loadHistory();
  }

  function changeStatus(): void {
    offset = 0;
    void loadHistory();
  }

  function changePage(nextOffset: number): void {
    offset = Math.max(0, nextOffset);
    void loadHistory();
  }

  function recipeLabel(recipe: string): string {
    return RECIPE_LABELS[recipe] ?? recipe
      .split("_")
      .filter(Boolean)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  }

  function stageLabel(stage: string): string {
    return STAGE_LABELS[stage] ?? recipeLabel(stage);
  }

  function statusLabel(value: string): string {
    if (value === "queued") return "Queued";
    if (value === "running") return "Processing";
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function itemTotal(job: JobSummary): number {
    return Object.values(job.item_counts).reduce((sum, count) => sum + count, 0);
  }

  function resolvedTotal(job: JobSummary): number {
    return job.item_counts.completed + job.item_counts.failed + job.item_counts.skipped + job.item_counts.cancelled;
  }

  function formatDate(value: string | null): string {
    if (!value) return "Not started";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }

  function formatDuration(job: JobSummary): string {
    const start = job.started_at ? new Date(job.started_at).getTime() : new Date(job.created_at).getTime();
    const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
    const seconds = Math.round((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  function processingProfile(job: JobSummary): string | null {
    const profile = job.options.processing_profile;
    if (typeof profile !== "string") return null;
    return profile.split("_").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
  }

  function initialItemPage(initialStatus: JobItemStatus | "all" = "all"): ItemPageState {
    return {
      items: [], total: 0, offset: 0, status: initialStatus, query: "", appliedQuery: "", loading: true, error: null,
    };
  }

  async function toggleJob(jobId: number): Promise<void> {
    const next = new Set(expandedJobIds);
    if (next.has(jobId)) {
      next.delete(jobId);
      expandedJobIds = next;
      return;
    }
    next.add(jobId);
    expandedJobIds = next;
    if (!itemPages[jobId]) {
      const initialStatus = jobs.find((job) => job.id === jobId)?.status === "failed" ? "failed" : "all";
      itemPages = { ...itemPages, [jobId]: initialItemPage(initialStatus) };
      await loadItems(jobId);
    }
  }

  async function loadItems(jobId: number): Promise<void> {
    const request = (itemRequests.get(jobId) ?? 0) + 1;
    itemRequests.set(jobId, request);
    const current = itemPages[jobId] ?? initialItemPage();
    itemPages = { ...itemPages, [jobId]: { ...current, loading: true, error: null } };
    try {
      const page = await fetchJobItems(jobId, {
        status: current.status,
        query: current.appliedQuery,
        limit: ITEM_PAGE_SIZE,
        offset: current.offset,
      });
      if (request !== itemRequests.get(jobId)) return;
      const latest = itemPages[jobId] ?? current;
      itemPages = {
        ...itemPages,
        [jobId]: { ...latest, items: page.items, total: page.total, loading: false, error: null },
      };
    } catch (reason) {
      if (request !== itemRequests.get(jobId)) return;
      const latest = itemPages[jobId] ?? current;
      itemPages = {
        ...itemPages,
        [jobId]: {
          ...latest,
          loading: false,
          error: reason instanceof Error ? reason.message : "Could not load track results",
        },
      };
    }
  }

  function changeItemStatus(jobId: number, value: JobItemStatus | "all"): void {
    const current = itemPages[jobId];
    if (!current) return;
    itemPages = { ...itemPages, [jobId]: { ...current, status: value, offset: 0 } };
    void loadItems(jobId);
  }

  function submitItemSearch(jobId: number, event: SubmitEvent): void {
    event.preventDefault();
    const current = itemPages[jobId];
    if (!current) return;
    itemPages = {
      ...itemPages,
      [jobId]: { ...current, appliedQuery: current.query.trim(), offset: 0 },
    };
    void loadItems(jobId);
  }

  function changeItemQuery(jobId: number, value: string): void {
    const current = itemPages[jobId];
    if (!current) return;
    itemPages = { ...itemPages, [jobId]: { ...current, query: value } };
  }

  function changeItemPage(jobId: number, nextOffset: number): void {
    const current = itemPages[jobId];
    if (!current) return;
    itemPages = { ...itemPages, [jobId]: { ...current, offset: Math.max(0, nextOffset) } };
    void loadItems(jobId);
  }

  function filename(path: string): string {
    return path.split(/[\\/]/).pop() ?? path;
  }

  function folder(path: string): string {
    const parts = path.split(/[\\/]/);
    return parts.length > 1 ? parts.slice(0, -1).join("\\") : "";
  }

  function conciseError(item: JobItem): string {
    if (!item.error_text) return "No error detail was recorded";
    const lowered = item.error_text.toLowerCase();
    if (lowered.includes("stereo needs to be set to true")) {
      return "Surround audio was not accepted by the stereo separation model";
    }
    if (lowered.includes("pytorchstreamreader") && lowered.includes("central directory")) {
      return "A UVR model download was incomplete or corrupt";
    }
    return item.error_text.split(/\r?\n/).find((line) => line.trim())?.trim() ?? "Processing failed";
  }
</script>

<section class="processing-history" aria-labelledby="processing-history-title">
  <header class="processing-history-header">
    <div>
      <button type="button" class="history-back" onclick={onBack}>← Library</button>
      <p class="library-eyebrow">Production log</p>
      <h1 id="processing-history-title">Processing history</h1>
      <p>Review every preparation run, then expand it to see the result for each track and phase.</p>
    </div>
    <button type="button" class="history-refresh" onclick={() => loadHistory()} disabled={loading}>
      {loading ? "Refreshing…" : "Refresh"}
    </button>
  </header>

  <form class="history-filters" onsubmit={submitSearch}>
    <label>
      <span>Status</span>
      <select bind:value={status} onchange={changeStatus}>
        <option value="all">All runs</option>
        <option value="active">In progress</option>
        <option value="completed">Completed</option>
        <option value="failed">Failed</option>
        <option value="cancelled">Cancelled</option>
      </select>
    </label>
    <label class="history-search">
      <span>Find a job or track</span>
      <input type="search" bind:value={query} placeholder="Workflow, job number, or filename" />
    </label>
    <button type="submit">Search</button>
  </form>

  {#if error}
    <div class="history-message history-message-error" role="alert">
      <strong>History could not be loaded.</strong>
      <span>{error}</span>
      <button type="button" onclick={() => loadHistory()}>Try again</button>
    </div>
  {:else if loading && jobs.length === 0}
    <div class="history-message" aria-live="polite">Loading processing history…</div>
  {:else if jobs.length === 0}
    <div class="history-message">
      <strong>No processing runs match this view.</strong>
      <span>Clear the search or choose another status.</span>
    </div>
  {:else}
    <div class="history-result-summary" aria-live="polite">
      Showing {firstResult}–{lastResult} of {total} run{total === 1 ? "" : "s"}
    </div>

    <div class="history-job-list">
      {#each jobs as job (job.id)}
        <article class="history-job history-job-{job.status}">
          <button
            type="button"
            class="history-job-summary"
            aria-expanded={expandedJobIds.has(job.id)}
            aria-controls={`history-job-${job.id}`}
            onclick={() => toggleJob(job.id)}
          >
            <span class="history-disclosure" aria-hidden="true">{expandedJobIds.has(job.id) ? "▾" : "▸"}</span>
            <span class="history-job-primary">
              <strong>{recipeLabel(job.recipe)}</strong>
              <span>Job {job.id} · {formatDate(job.started_at ?? job.created_at)}</span>
            </span>
            <span class="history-job-meta">
              {#if processingProfile(job)}<span>{processingProfile(job)}</span>{/if}
              <span>{formatDuration(job)}</span>
            </span>
            <span class="history-job-counts">
              <strong>{resolvedTotal(job)} of {itemTotal(job)}</strong>
              <span>
                {job.item_counts.completed} completed
                {#if job.item_counts.skipped > 0} · {job.item_counts.skipped} skipped{/if}
                {#if job.item_counts.failed > 0} · {job.item_counts.failed} failed{/if}
                {#if job.item_counts.cancelled > 0} · {job.item_counts.cancelled} cancelled{/if}
              </span>
            </span>
            <span class="history-status history-status-{job.status}">{statusLabel(job.status)}</span>
          </button>

          {#if expandedJobIds.has(job.id)}
            {@const itemPage = itemPages[job.id]}
            <section class="history-job-detail" id={`history-job-${job.id}`} aria-label={`Track results for job ${job.id}`}>
              {#if itemPage}
                <form class="history-item-filters" onsubmit={(event) => submitItemSearch(job.id, event)}>
                  <label>
                    <span class="visually-hidden">Filter track results</span>
                    <select
                      value={itemPage.status}
                      onchange={(event) => changeItemStatus(job.id, event.currentTarget.value as JobItemStatus | "all")}
                    >
                      <option value="all">All track results</option>
                      <option value="completed">Completed</option>
                      <option value="failed">Failed</option>
                      <option value="skipped">Skipped</option>
                      <option value="cancelled">Cancelled</option>
                      <option value="running">Processing</option>
                      <option value="queued">Queued</option>
                    </select>
                  </label>
                  <label class="history-item-search">
                    <span class="visually-hidden">Find a track in this job</span>
                    <input
                      type="search"
                      value={itemPage.query}
                      oninput={(event) => changeItemQuery(job.id, event.currentTarget.value)}
                      placeholder="Find a track in this run"
                    />
                  </label>
                  <button type="submit">Find track</button>
                </form>

                {#if itemPage.error}
                  <div class="history-item-message history-message-error" role="alert">{itemPage.error}</div>
                {:else if itemPage.loading}
                  <div class="history-item-message" aria-live="polite">Loading track results…</div>
                {:else if itemPage.items.length === 0}
                  <div class="history-item-message">No tracks match this detail filter.</div>
                {:else}
                  <div class="history-item-table-wrap">
                    <table class="history-item-table">
                      <thead>
                        <tr><th>Track</th><th>Result</th><th>Phases</th><th>Details</th></tr>
                      </thead>
                      <tbody>
                        {#each itemPage.items as item (item.id)}
                          <tr class:history-item-failed={item.status === "failed"}>
                            <td>
                              <strong title={item.source_path}>{filename(item.source_path)}</strong>
                              <span title={folder(item.source_path)}>{folder(item.source_path)}</span>
                            </td>
                            <td><span class="history-status history-status-{item.status}">{statusLabel(item.status)}</span></td>
                            <td>
                              <span class="history-stage-list">
                                {#each item.stages as stage (stage.name)}
                                  <span class="history-stage history-stage-{stage.status}" title={`${stageLabel(stage.name)}: ${statusLabel(stage.status)}`}>
                                    {stageLabel(stage.name)}
                                  </span>
                                {/each}
                              </span>
                            </td>
                            <td>
                              {#if item.status === "failed"}
                                <details class="history-error-detail">
                                  <summary>{conciseError(item)}</summary>
                                  <pre>{item.error_text ?? "No error detail was recorded"}</pre>
                                </details>
                              {:else if item.current_stage}
                                {stageLabel(item.current_stage)}
                              {:else}
                                —
                              {/if}
                            </td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  </div>
                  <div class="history-pagination history-item-pagination">
                    <span>
                      {itemPage.total === 0 ? 0 : itemPage.offset + 1}–{Math.min(itemPage.offset + itemPage.items.length, itemPage.total)}
                      of {itemPage.total} tracks
                    </span>
                    <div>
                      <button type="button" disabled={itemPage.offset === 0} onclick={() => changeItemPage(job.id, itemPage.offset - ITEM_PAGE_SIZE)}>Previous</button>
                      <button type="button" disabled={itemPage.offset + itemPage.items.length >= itemPage.total} onclick={() => changeItemPage(job.id, itemPage.offset + ITEM_PAGE_SIZE)}>Next</button>
                    </div>
                  </div>
                {/if}
              {/if}
            </section>
          {/if}
        </article>
      {/each}
    </div>

    <div class="history-pagination">
      <span>Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
      <div>
        <button type="button" disabled={offset === 0 || loading} onclick={() => changePage(offset - PAGE_SIZE)}>Previous</button>
        <button type="button" disabled={offset + jobs.length >= total || loading} onclick={() => changePage(offset + PAGE_SIZE)}>Next</button>
      </div>
    </div>
  {/if}
</section>
