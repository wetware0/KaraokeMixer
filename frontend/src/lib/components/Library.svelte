<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import { fetchJob, fetchLibraryFolders, moveTrack, partAudioUrl } from "../api";
  import { buildFolderTree, flattenTree } from "../folderTree";
  import {
    clampLibraryColumnWidth, defaultLibraryColumnsState, filterTracks, loadLibraryColumnsState, saveLibraryColumnsState,
    sortTracks, visibleColumnsInOrder, type LibraryColumnConfig, type LibraryColumnKey, type LibraryColumnsState,
  } from "../libraryColumns";
  import { jobsStore } from "../jobsStore.svelte";
  import { tracksStore } from "../tracksStore.svelte";
  import type { JobDetail, LibraryFolder, Track } from "../types";
  import ProcessDialog from "./ProcessDialog.svelte";
  import DeleteTrackDialog from "./DeleteTrackDialog.svelte";
  import DeleteFolderDialog from "./DeleteFolderDialog.svelte";
  import FolderDialog from "./FolderDialog.svelte";
  import RenameTrackDialog from "./RenameTrackDialog.svelte";
  import TagsDialog from "./TagsDialog.svelte";
  import TrackRow from "./TrackRow.svelte";

  // `device` is typed non-null, but Svelte prop defaults only apply when the
  // caller passes `undefined` — App.svelte's device state is `null` until the
  // system probe resolves, and `null` bypasses the `= "auto"` default below.
  // Coerced defensively at the single point that needs a concrete value: the
  // ProcessDialog invocation further down.
  let {
    device = "auto",
    whisperxAvailable = null,
    active = true,
    onOpenMixer = () => {},
    onOpenEditor = () => {},
    onPlaybackChange = () => {},
  }: {
    device?: "auto" | "cuda" | "cpu";
    whisperxAvailable?: boolean | null;
    active?: boolean;
    onOpenMixer?: (track: Track) => void;
    onOpenEditor?: (track: Track) => void;
    onPlaybackChange?: (playing: boolean) => void;
  } = $props();

  let selectedFolder = $state<string | null>(null);
  let collapsedFolderPaths = $state<Set<string>>(new Set());
  let initialFolderCollapseApplied = $state(false);
  let selectedTrackIds = $state<Set<number>>(new Set());
  let showProcessDialog = $state(false);
  let editingTagsTrack = $state<Track | null>(null);
  let deletingTrack = $state<Track | null>(null);
  let renamingTrack = $state<Track | null>(null);
  let folders = $state<LibraryFolder[]>([]);
  let folderDialogMode = $state<"create" | "rename" | null>(null);
  let deletingFolder = $state<LibraryFolder | null>(null);
  let draggingTrack = $state<Track | null>(null);
  let dragOverFolderPath = $state<string | null>(null);
  let folderOperationMessage = $state<string | null>(null);
  let folderOperationError = $state<string | null>(null);
  let hoverExpandTimer: ReturnType<typeof setTimeout> | undefined;
  let hoverExpandPath: string | null = null;
  let rescanStartError = $state<string | null>(null);
  let activeJobDetails = $state<JobDetail[]>([]);
  let activeJobDetailRequest = 0;
  let tableScrollEl = $state<HTMLDivElement | undefined>();
  let tableScrollTop = $state(0);
  let tableViewportHeight = $state(720);
  let lyricReconcileTimer: ReturnType<typeof setTimeout> | undefined;
  const lyricReconciledAt = new Map<number, number>();
  const PROCESSING_STAGE_LABELS: Record<string, string> = {
    demucs_separate: "Separating stems",
    karaoke_instrumental: "Creating karaoke instrumental",
    fetch_lyrics: "Downloading lyrics",
    align_lyrics: "Enhancing lyric timing",
    fetch_tags: "Updating tags and artwork",
    uvr_vocal_split: "Separating lead and backing vocals",
    youtube_import: "Downloading track",
  };
  const scanStatus = $derived(tracksStore.scanStatus);
  const rescanning = $derived(scanStatus?.status === "queued" || scanStatus?.status === "running");
  const rescanMessage = $derived.by(() => {
    if (!scanStatus || scanStatus.scan_id === 0 || scanStatus.status === "failed") return null;
    if (scanStatus.status === "queued") return "Library scan queued in the background…";
    if (scanStatus.status === "running") {
      const rootProgress = scanStatus.media_roots_total > 0
        ? ` ${scanStatus.media_roots_scanned} of ${scanStatus.media_roots_total} folders complete.`
        : "";
      return `Scanning in the background — ${scanStatus.tracks_found} track${scanStatus.tracks_found === 1 ? "" : "s"} found so far.${rootProgress}`;
    }
    let message = `Found ${scanStatus.tracks_found} tracks across ${scanStatus.media_roots_scanned} media root${scanStatus.media_roots_scanned === 1 ? "" : "s"}.`;
    if (scanStatus.unavailable_roots.length) message += ` Unavailable: ${scanStatus.unavailable_roots.join(", ")}`;
    return message;
  });
  const rescanError = $derived(
    rescanStartError ?? (scanStatus?.status === "failed" ? scanStatus.error ?? "Library rescan failed" : null),
  );
  const processingStateByTrack = $derived.by(() => {
    const states = new Map<number, "queued" | "running" | "waiting">();
    const priority = { waiting: 1, queued: 2, running: 3 } as const;
    for (const detail of activeJobDetails) {
      // A failed/transient detail response should not take down the 80k-row
      // Library. The API contract supplies items, but treat malformed data as
      // an empty detail until the next socket-driven refresh repairs it.
      const items = detail.items ?? [];
      const phaseName = items.find((item) => item.status === "running")?.current_stage ?? null;
      for (const item of items) {
        if (item.track_id === null || (item.status !== "queued" && item.status !== "running")) continue;
        if (item.status === "queued" && phaseName) {
          const phaseIndex = item.stages.findIndex((stage) => stage.name === phaseName);
          const phase = phaseIndex >= 0 ? item.stages[phaseIndex] : null;
          // Stage-major batches deliberately return an item to the backend's
          // queued state between phases. Keep that resolved track visible as
          // waiting for its next phase so the whole recipe never appears to
          // have completed and restarted.
          if (phase && (phase.status === "completed" || phase.status === "skipped" || phase.status === "failed")) {
            const hasLaterPhase = item.stages.slice(phaseIndex + 1).some((stage) => stage.status === "pending");
            const existing = states.get(item.track_id);
            if (hasLaterPhase && (!existing || priority.waiting > priority[existing])) {
              states.set(item.track_id, "waiting");
            }
            continue;
          }
        }
        // If a track occurs in multiple active jobs, show its most immediate
        // state: Processing, then Queued, then Waiting for next phase.
        const existing = states.get(item.track_id);
        if (!existing || priority[item.status] > priority[existing]) states.set(item.track_id, item.status);
      }
    }
    return states;
  });

  function processingErrorFor(trackId: number): string | null {
    const failure = jobsStore.trackFailures?.[trackId];
    if (!failure) return null;
    const stage = failure.stage
      ? PROCESSING_STAGE_LABELS[failure.stage] ?? failure.stage.replaceAll("_", " ")
      : "Processing";
    return `${stage}: ${failure.message}`;
  }

  // Job summaries contain counts but not their track ids. Fetch the small
  // active-job detail set whenever the socket-backed jobsStore changes, then
  // derive row state from each individual item. Terminal jobs disappear from
  // this set immediately, clearing their highlights without a page reload.
  $effect(() => {
    const activeJobIds = jobsStore.jobs
      .filter((job) => job.status === "queued" || job.status === "running")
      .map((job) => job.id);
    const request = ++activeJobDetailRequest;
    if (activeJobIds.length === 0) {
      activeJobDetails = [];
      return;
    }
    Promise.all(activeJobIds.map((jobId) => fetchJob(jobId).catch(() => null)))
      .then((details) => {
        if (request === activeJobDetailRequest) {
          activeJobDetails = details.filter((detail): detail is JobDetail => detail !== null);
        }
      });
  });

  let columnsState = $state<LibraryColumnsState>(defaultLibraryColumnsState());
  let showColumnMenu = $state(false);
  let columnMenuEl = $state<HTMLDivElement | undefined>();
  let columnsButtonEl = $state<HTMLButtonElement | undefined>();
  let restoreColumnMenuFocus = false;
  let openHeaderFilterKey = $state<LibraryColumnKey | null>(null);
  let headerFilterDraft = $state("");
  let headerFilterControlEl = $state<HTMLInputElement | HTMLSelectElement | undefined>();
  let resizingColumn = $state<{ key: LibraryColumnKey; startX: number; startWidth: number } | null>(null);
  let draggingColumn = $state<LibraryColumnKey | null>(null);
  let dragOverColumn = $state<LibraryColumnKey | null>(null);

  onMount(() => {
    columnsState = loadLibraryColumnsState();
  });

  function persistColumns(next: LibraryColumnsState): void {
    columnsState = next;
    saveLibraryColumnsState(next);
  }

  async function openColumnMenu(restoreFocus: boolean): Promise<void> {
    restoreColumnMenuFocus = restoreFocus;
    showColumnMenu = true;
    await tick();
    columnMenuEl?.focus();
  }

  function onHeaderContextMenu(event: MouseEvent): void {
    event.preventDefault();
    void openColumnMenu(false);
  }

  async function closeColumnMenu(): Promise<void> {
    showColumnMenu = false;
    await tick();
    if (restoreColumnMenuFocus) columnsButtonEl?.focus();
  }

  function toggleColumnVisible(key: LibraryColumnKey): void {
    persistColumns({
      ...columnsState,
      columns: columnsState.columns.map((column) => (column.key === key ? { ...column, visible: !column.visible } : column)),
    });
  }

  function moveColumn(key: LibraryColumnKey, direction: -1 | 1): void {
    const ordered = [...columnsState.columns].sort((a, b) => a.order - b.order);
    const index = ordered.findIndex((column) => column.key === key);
    const target = index + direction;
    if (target < 0 || target >= ordered.length) return;
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    persistColumns({ ...columnsState, columns: ordered.map((column, i) => ({ ...column, order: i })) });
  }

  function reorderColumn(sourceKey: LibraryColumnKey, targetKey: LibraryColumnKey): void {
    if (sourceKey === targetKey) return;
    const ordered = [...columnsState.columns].sort((a, b) => a.order - b.order);
    const sourceIndex = ordered.findIndex((column) => column.key === sourceKey);
    const targetIndex = ordered.findIndex((column) => column.key === targetKey);
    if (sourceIndex < 0 || targetIndex < 0) return;
    const [moved] = ordered.splice(sourceIndex, 1);
    ordered.splice(targetIndex, 0, moved);
    persistColumns({ ...columnsState, columns: ordered.map((column, index) => ({ ...column, order: index })) });
  }

  function beginColumnOrderDrag(event: DragEvent, key: LibraryColumnKey): void {
    draggingColumn = key;
    dragOverColumn = null;
    event.dataTransfer?.setData("text/plain", key);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }

  function moveColumnOrderDrag(event: DragEvent, targetKey: LibraryColumnKey): void {
    if (!draggingColumn || draggingColumn === targetKey) return;
    event.preventDefault();
    dragOverColumn = targetKey;
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  }

  function finishColumnOrderDrag(event: DragEvent, targetKey: LibraryColumnKey): void {
    event.preventDefault();
    const sourceKey = draggingColumn ?? (event.dataTransfer?.getData("text/plain") as LibraryColumnKey | undefined);
    if (sourceKey) reorderColumn(sourceKey, targetKey);
    draggingColumn = null;
    dragOverColumn = null;
  }

  function cancelColumnOrderDrag(): void {
    draggingColumn = null;
    dragOverColumn = null;
  }

  function setSort(key: LibraryColumnKey, direction: "asc" | "desc"): void {
    if (key === "artwork") return;
    persistColumns({ ...columnsState, sortKey: key, sortDirection: direction });
  }

  function cycleSort(key: LibraryColumnKey): void {
    if (key === "artwork") return;
    if (columnsState.sortKey !== key) {
      setSort(key, "asc");
    } else if (columnsState.sortDirection === "asc") {
      setSort(key, "desc");
    } else {
      persistColumns({ ...columnsState, sortKey: null, sortDirection: "asc" });
    }
  }

  function clearSort(): void {
    persistColumns({ ...columnsState, sortKey: null, sortDirection: "asc" });
  }

  function sortAriaLabel(key: LibraryColumnKey, label: string): string {
    if (columnsState.sortKey !== key) return `Sort by ${label}, currently unsorted`;
    return `Sort by ${label}, currently ${columnsState.sortDirection === "asc" ? "ascending" : "descending"}`;
  }

  function setColumnFilter(key: LibraryColumnKey, value: string): void {
    persistColumns({
      ...columnsState,
      columns: columnsState.columns.map((column) => (column.key === key ? { ...column, filter: value } : column)),
    });
  }

  function filterOptionsFor(key: LibraryColumnKey): Array<{ value: string; label: string }> | null {
    if (key === "artwork") return [
      { value: "", label: "All artwork" },
      { value: "has", label: "Has artwork" },
      { value: "missing", label: "Missing artwork" },
      { value: "unknown", label: "Not checked" },
    ];
    if (key === "instrumental") return [
      { value: "", label: "All" },
      { value: "high_quality", label: "High Quality" },
      { value: "balanced", label: "Balanced" },
      { value: "fast", label: "Fast" },
      { value: "ready", label: "Ready" },
      { value: "missing", label: "Missing" },
    ];
    if (key === "lyrics") return [
      { value: "", label: "All" },
      { value: "high_quality", label: "High Quality timing" },
      { value: "review", label: "Needs review" },
      { value: "audited", label: "Confidence checked" },
      { value: "not_audited", label: "Not confidence checked" },
      { value: "enhanced", label: "All enhanced" },
      { value: "line_timed", label: "Line timed" },
      { value: "untimed", label: "Untimed" },
      { value: "empty", label: "Empty" },
      { value: "unknown", label: "Unknown" },
      { value: "missing", label: "Missing" },
    ];
    if (key === "stems") return [
      { value: "", label: "All" },
      { value: "has", label: "Has stems" },
      { value: "none", label: "No stems" },
    ];
    return null;
  }

  const activeFilterCount = $derived(
    columnsState.columns.filter((column) => column.filter.trim() !== "").length
  );

  async function openHeaderFilter(column: LibraryColumnConfig): Promise<void> {
    openHeaderFilterKey = column.key;
    headerFilterDraft = column.filter;
    await tick();
    headerFilterControlEl?.focus();
  }

  function closeHeaderFilter(): void {
    openHeaderFilterKey = null;
  }

  function applyHeaderFilter(key: LibraryColumnKey): void {
    setColumnFilter(key, headerFilterDraft);
    closeHeaderFilter();
  }

  function clearHeaderFilter(key: LibraryColumnKey): void {
    headerFilterDraft = "";
    setColumnFilter(key, "");
    closeHeaderFilter();
  }

  function clearAllColumnFilters(): void {
    persistColumns({
      ...columnsState,
      columns: columnsState.columns.map((column) => ({ ...column, filter: "" })),
    });
    headerFilterDraft = "";
    closeHeaderFilter();
  }

  function setColumnWidth(key: LibraryColumnKey, width: number, save: boolean): void {
    const next = {
      ...columnsState,
      columns: columnsState.columns.map((column) =>
        column.key === key ? { ...column, width: clampLibraryColumnWidth(width) } : column
      ),
    };
    columnsState = next;
    if (save) saveLibraryColumnsState(next);
  }

  function beginColumnResize(event: PointerEvent, key: LibraryColumnKey): void {
    event.preventDefault();
    event.stopPropagation();
    const header = (event.currentTarget as HTMLElement).parentElement;
    const configured = columnsState.columns.find((column) => column.key === key)?.width ?? 100;
    resizingColumn = {
      key,
      startX: event.clientX,
      startWidth: header?.getBoundingClientRect().width || configured,
    };
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    document.body.classList.add("library-column-resizing");
  }

  function moveColumnResize(event: PointerEvent): void {
    if (!resizingColumn) return;
    setColumnWidth(resizingColumn.key, resizingColumn.startWidth + event.clientX - resizingColumn.startX, false);
  }

  function finishColumnResize(event: PointerEvent): void {
    if (!resizingColumn) return;
    setColumnWidth(resizingColumn.key, resizingColumn.startWidth + event.clientX - resizingColumn.startX, true);
    resizingColumn = null;
    document.body.classList.remove("library-column-resizing");
  }

  function cancelColumnResize(): void {
    if (!resizingColumn) return;
    saveLibraryColumnsState(columnsState);
    resizingColumn = null;
    document.body.classList.remove("library-column-resizing");
  }

  function onResizeHandleKeydown(event: KeyboardEvent, column: LibraryColumnConfig): void {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.stopPropagation();
    setColumnWidth(column.key, column.width + (event.key === "ArrowRight" ? 10 : -10), true);
  }

  function resetColumnWidths(): void {
    const defaults = new Map(defaultLibraryColumnsState().columns.map((column) => [column.key, column.width]));
    persistColumns({
      ...columnsState,
      columns: columnsState.columns.map((column) => ({ ...column, width: defaults.get(column.key) ?? column.width })),
    });
  }

  // Single shared HTMLAudioElement for row preview playback - one per row
  // would mean N buffered streams sitting idle; a lone instance managed here
  // means starting a preview on one row simply steals it from whichever row
  // held it before. `previewAudio` itself isn't reactive state (nothing
  // renders from the element directly); `previewingTrackId` is the reactive
  // flag each row's `previewing` prop derives from.
  let previewingTrackId = $state<number | null>(null);
  let previewAudio: HTMLAudioElement | null = null;

  function stopPreview(): void {
    if (previewAudio) {
      previewAudio.onended = null;
      previewAudio.pause();
      // pause() alone leaves the underlying media fetch/decode alive in
      // some engines; clearing the source and calling load() is the
      // MDN-recommended way to make the browser actually release the
      // stream (https://developer.mozilla.org/docs/Web/API/HTMLMediaElement/load).
      previewAudio.removeAttribute("src");
      previewAudio.load();
      previewAudio = null;
    }
    previewingTrackId = null;
    onPlaybackChange(false);
  }

  // History keeps the Library mounted to preserve its filters and scroll
  // position, so leaving the visible Library does not naturally destroy the
  // preview element. Stop it explicitly at the visibility boundary.
  let wasActive = true;
  $effect(() => {
    const isActive = active;
    const leavingLibrary = wasActive && !isActive;
    wasActive = isActive;
    if (leavingLibrary) stopPreview();
  });

  function togglePreview(track: Track): void {
    const wasPlayingThisTrack = previewingTrackId === track.id;
    stopPreview();
    if (wasPlayingThisTrack) return;

    const audio = new Audio(partAudioUrl(track.id, "original"));
    audio.onended = () => stopPreview();
    previewAudio = audio;
    previewingTrackId = track.id;
    // play() is async and can reject (autoplay block, decode failure, the
    // stream 404ing, ...); left unhandled that's both an unhandled-rejection
    // warning and a row stuck showing "Pause" with nothing audible. Recover
    // by clearing preview state - but only if this rejection still belongs
    // to the currently active preview, since a slow rejection can arrive
    // after the user already stopped this preview or started another one.
    void audio.play()
      .then(() => {
        if (previewAudio === audio && !audio.paused) onPlaybackChange(true);
      })
      .catch(() => {
        if (previewAudio === audio) {
          previewAudio = null;
          previewingTrackId = null;
          onPlaybackChange(false);
        }
      });
  }

  // Any navigation away from the list (opening the Mixer or the Lyric
  // Editor) should stop a playing preview - wrap the two callbacks passed
  // down to TrackRow rather than duplicating the stop call at every call
  // site (dblclick, Edit lyrics, Create-lyrics-then-open-editor).
  function handleOpenMixer(track: Track): void {
    stopPreview();
    onOpenMixer(track);
  }

  function handleOpenEditor(track: Track): void {
    stopPreview();
    onOpenEditor(track);
  }

  onDestroy(() => {
    activeJobDetailRequest += 1;
    if (lyricReconcileTimer !== undefined) clearTimeout(lyricReconcileTimer);
    if (hoverExpandTimer !== undefined) clearTimeout(hoverExpandTimer);
    stopPreview();
    document.body.classList.remove("library-column-resizing");
  });

  // relative_path uses backslashes on Windows; tree paths always use "/"
  function fullPath(track: Track): string {
    return `${track.media_root}/${track.relative_path}`.replace(/\\/g, "/");
  }

  // tracksStore (module-level singleton, see tracksStore.svelte.ts) owns the
  // list itself and refreshes it in the background whenever a job completes -
  // regardless of whether Library is currently mounted - so the badges are
  // already current by the time the user navigates back here. Library just
  // renders whatever it currently holds and asks it to refresh on mount/search.
  const folderTree = $derived(buildFolderTree(tracksStore.tracks, folders));
  const allFolderRows = $derived(flattenTree(folderTree));
  const folderRows = $derived(flattenTree(folderTree, collapsedFolderPaths));
  const hasCollapsedFolders = $derived(collapsedFolderPaths.size > 0);
  const selectedFolderInfo = $derived(
    selectedFolder ? folders.find((folder) => folder.path === selectedFolder) ?? null : null
  );
  const selectedFolderIsRoot = $derived(selectedFolderInfo?.relative_path === "");

  // Start a large library as a compact set of media roots. Apply this once
  // when the first tree arrives so later scans/refetches do not undo folders
  // the user deliberately expanded during this Library session.
  $effect(() => {
    const collapsiblePaths = allFolderRows
      .filter((row) => row.node.children.length > 0)
      .map((row) => row.node.path);
    if (!initialFolderCollapseApplied && collapsiblePaths.length > 0) {
      collapsedFolderPaths = new Set(collapsiblePaths);
      initialFolderCollapseApplied = true;
    }
  });
  const visibleTracks = $derived(
    selectedFolder
      ? tracksStore.tracks.filter((track) => fullPath(track).startsWith(`${selectedFolder!}/`))
      : tracksStore.tracks
  );

  const orderedColumns = $derived(visibleColumnsInOrder(columnsState));
  const displayedTracks = $derived(sortTracks(filterTracks(visibleTracks, columnsState), columnsState));

  // A full 80,000-row DOM is unusable even when the data itself fits easily
  // in memory. Keep small result sets simple, but window large ones to the
  // viewport plus a modest keyboard/scroll buffer. Fixed row geometry keeps
  // native table columns, sticky headers and horizontal scrolling intact.
  const VIRTUALIZATION_THRESHOLD = 200;
  const VIRTUAL_ROW_HEIGHT = 58;
  const VIRTUAL_OVERSCAN = 8;
  const virtualWindow = $derived.by(() => {
    const total = displayedTracks.length;
    if (total <= VIRTUALIZATION_THRESHOLD) {
      return { active: false, start: 0, end: total, top: 0, bottom: 0 };
    }
    const start = Math.min(
      Math.max(0, total - 1),
      Math.max(0, Math.floor(tableScrollTop / VIRTUAL_ROW_HEIGHT) - VIRTUAL_OVERSCAN),
    );
    const end = Math.min(
      total,
      Math.ceil((tableScrollTop + tableViewportHeight) / VIRTUAL_ROW_HEIGHT) + VIRTUAL_OVERSCAN,
    );
    return {
      active: true,
      start,
      end: Math.max(start + 1, end),
      top: start * VIRTUAL_ROW_HEIGHT,
      bottom: Math.max(0, (total - Math.max(start + 1, end)) * VIRTUAL_ROW_HEIGHT),
    };
  });
  const renderedTracks = $derived(displayedTracks.slice(virtualWindow.start, virtualWindow.end));
  const tableColumnCount = $derived(orderedColumns.length + 4);

  // The catalogue is intentionally cached for fast sorting/searching across
  // an eventual 80,000 tracks. Re-read only the LRC sidecars for the mounted
  // virtual window so externally retimed lyrics get a truthful badge as the
  // creator reaches them, without a whole-library filesystem walk.
  $effect(() => {
    const now = Date.now();
    const trackIds = renderedTracks
      .map((track) => track.id)
      .filter((trackId) => now - (lyricReconciledAt.get(trackId) ?? 0) >= 30_000)
      .slice(0, 64);
    if (trackIds.length === 0) return;
    if (lyricReconcileTimer !== undefined) clearTimeout(lyricReconcileTimer);
    lyricReconcileTimer = setTimeout(() => {
      lyricReconcileTimer = undefined;
      const checkedAt = Date.now();
      trackIds.forEach((trackId) => lyricReconciledAt.set(trackId, checkedAt));
      tracksStore.reconcileLrcStates(trackIds).catch(() => {
        // A brief file/server race should be retryable when the row is drawn
        // again; do not leave a rejected background promise in the console.
        trackIds.forEach((trackId) => lyricReconciledAt.delete(trackId));
      });
    }, 120);
  });

  const columnMenuRows = $derived([...columnsState.columns].sort((a, b) => a.order - b.order));

  onMount(() => {
    tracksStore.refresh("");
    void refreshFolders();
  });

  onMount(() => {
    function measureTableViewport(): void {
      if (tableScrollEl && tableScrollEl.clientHeight > 0) {
        tableViewportHeight = tableScrollEl.clientHeight;
      }
    }
    measureTableViewport();
    if (typeof ResizeObserver === "undefined" || !tableScrollEl) return;
    const observer = new ResizeObserver(measureTableViewport);
    observer.observe(tableScrollEl);
    return () => observer.disconnect();
  });

  function onTableScroll(event: Event): void {
    tableScrollTop = (event.currentTarget as HTMLDivElement).scrollTop;
  }

  const SEARCH_DEBOUNCE_MS = 200;
  let searchDebounceTimer: ReturnType<typeof setTimeout> | undefined;

  function onSearch(event: Event) {
    const query = (event.target as HTMLInputElement).value;
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => tracksStore.refresh(query), SEARCH_DEBOUNCE_MS);
  }

  function selectFolder(path: string) {
    selectedFolder = selectedFolder === path ? null : path;
  }

  function toggleFolder(path: string) {
    const next = new Set(collapsedFolderPaths);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    collapsedFolderPaths = next;
  }

  function toggleAllFolders() {
    if (hasCollapsedFolders) {
      collapsedFolderPaths = new Set();
      return;
    }
    collapsedFolderPaths = new Set(
      allFolderRows
        .filter((row) => row.node.children.length > 0)
        .map((row) => row.node.path),
    );
  }

  async function refreshFolders(): Promise<void> {
    try {
      folders = await fetchLibraryFolders();
    } catch (error) {
      folderOperationError = error instanceof Error ? error.message : "Could not load folders";
    }
  }

  function currentFolderForTrack(track: Track): string {
    const normalizedRoot = track.media_root.replace(/\\/g, "/").replace(/\/$/, "");
    const parts = track.relative_path.replace(/\\/g, "/").split("/");
    parts.pop();
    return parts.length ? `${normalizedRoot}/${parts.join("/")}` : normalizedRoot;
  }

  function folderTrackCount(path: string): number {
    return tracksStore.tracks.filter((track) => {
      const full = fullPath(track);
      return full === path || full.startsWith(`${path}/`);
    }).length;
  }

  function folderHasActiveTrack(path: string): boolean {
    return tracksStore.tracks.some((track) => {
      const full = fullPath(track);
      const inside = full.startsWith(`${path}/`);
      const status = processingStateByTrack.get(track.id);
      return inside && (status === "queued" || status === "running" || status === "waiting");
    });
  }

  function defaultNewFolderParent(): string {
    return selectedFolderInfo?.path
      ?? folders.find((folder) => folder.relative_path === "")?.path
      ?? folders[0]?.path
      ?? "";
  }

  function openCreateFolder(): void {
    folderOperationError = null;
    folderDialogMode = "create";
  }

  function openRenameFolder(): void {
    if (!selectedFolderInfo || selectedFolderIsRoot) return;
    folderOperationError = null;
    folderDialogMode = "rename";
  }

  async function handleFolderSaved(saved: LibraryFolder): Promise<void> {
    const mode = folderDialogMode;
    const previous = selectedFolderInfo;
    folderDialogMode = null;
    folderOperationError = null;
    if (mode === "create") {
      folders = [...folders.filter((folder) => folder.path !== saved.path), saved];
      const parentPath = saved.relative_path.includes("/")
        ? `${saved.media_root}/${saved.relative_path.split("/").slice(0, -1).join("/")}`
        : saved.media_root;
      const next = new Set(collapsedFolderPaths);
      next.delete(parentPath);
      collapsedFolderPaths = next;
      selectedFolder = saved.path;
      folderOperationMessage = `Created ${saved.name}`;
      return;
    }

    if (previous) {
      collapsedFolderPaths = new Set(
        [...collapsedFolderPaths].map((path) => path === previous.path || path.startsWith(`${previous.path}/`)
          ? `${saved.path}${path.slice(previous.path.length)}`
          : path)
      );
    }
    selectedFolder = saved.path;
    await Promise.all([tracksStore.refresh(), refreshFolders()]);
    folderOperationMessage = `Renamed folder to ${saved.name}`;
  }

  async function handleFolderDeleted(trackIds: number[]): Promise<void> {
    const name = deletingFolder?.name ?? "folder";
    if (previewingTrackId !== null && trackIds.includes(previewingTrackId)) stopPreview();
    trackIds.forEach((trackId) => tracksStore.removeTrack(trackId));
    selectedTrackIds = new Set([...selectedTrackIds].filter((trackId) => !trackIds.includes(trackId)));
    deletingFolder = null;
    selectedFolder = null;
    await refreshFolders();
    folderOperationMessage = `${name} moved to the Recycle Bin`;
  }

  function requestTrackRename(track: Track): void {
    if (previewingTrackId === track.id) stopPreview();
    renamingTrack = track;
  }

  function handleTrackRenamed(updated: Track): void {
    tracksStore.replaceTrack(updated);
    renamingTrack = null;
    folderOperationError = null;
    folderOperationMessage = `Saved ${updated.relative_path.split(/[\\/]/).at(-1) ?? updated.title}`;
    void refreshFolders();
  }

  function beginTrackDrag(track: Track): void {
    stopPreview();
    draggingTrack = track;
    folderOperationError = null;
    folderOperationMessage = `Move ${track.title}: drop it on a folder`;
  }

  function clearFolderHover(): void {
    if (hoverExpandTimer !== undefined) clearTimeout(hoverExpandTimer);
    hoverExpandTimer = undefined;
    hoverExpandPath = null;
  }

  function finishTrackDrag(): void {
    clearFolderHover();
    draggingTrack = null;
    dragOverFolderPath = null;
  }

  function scheduleFolderExpansion(path: string, hasChildren: boolean): void {
    if (!hasChildren || !collapsedFolderPaths.has(path) || hoverExpandPath === path) return;
    clearFolderHover();
    hoverExpandPath = path;
    hoverExpandTimer = setTimeout(() => {
      const next = new Set(collapsedFolderPaths);
      next.delete(path);
      collapsedFolderPaths = next;
      hoverExpandTimer = undefined;
      hoverExpandPath = null;
    }, 650);
  }

  function dragOverFolder(event: DragEvent, path: string, hasChildren: boolean): void {
    if (!draggingTrack) return;
    event.preventDefault();
    dragOverFolderPath = path;
    scheduleFolderExpansion(path, hasChildren);
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  }

  function leaveFolder(event: DragEvent, path: string): void {
    const current = event.currentTarget as HTMLElement;
    if (event.relatedTarget instanceof Node && current.contains(event.relatedTarget)) return;
    if (dragOverFolderPath === path) dragOverFolderPath = null;
    if (hoverExpandPath === path) clearFolderHover();
  }

  async function dropTrackOnFolder(event: DragEvent, destinationFolder: string): Promise<void> {
    event.preventDefault();
    const track = draggingTrack;
    finishTrackDrag();
    if (!track) return;
    if (currentFolderForTrack(track) === destinationFolder) {
      folderOperationMessage = `${track.title} is already in that folder`;
      return;
    }
    try {
      const updated = await moveTrack(track.id, destinationFolder);
      tracksStore.replaceTrack(updated);
      await refreshFolders();
      folderOperationMessage = `Moved ${track.title}`;
      folderOperationError = null;
    } catch (error) {
      folderOperationError = error instanceof Error ? error.message : "Could not move track";
      folderOperationMessage = null;
    }
  }

  function toggleTrackSelection(id: number) {
    const next = new Set(selectedTrackIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedTrackIds = next;
  }

  function selectAllInFolder() {
    const next = new Set(selectedTrackIds);
    for (const track of displayedTracks) next.add(track.id);
    selectedTrackIds = next;
  }

  // Header select-all/deselect-all control, scoped to whatever is actually
  // displayed in the table right now - post-search-filter, post-folder-filter,
  // and post-column-filter (the same `displayedTracks` set the table body
  // renders), so it never selects a row the user can't currently see.
  const allVisibleSelected = $derived(
    displayedTracks.length > 0 && displayedTracks.every((track) => selectedTrackIds.has(track.id))
  );
  const someVisibleSelected = $derived(
    !allVisibleSelected && displayedTracks.some((track) => selectedTrackIds.has(track.id))
  );

  function toggleSelectAllVisible() {
    const next = new Set(selectedTrackIds);
    if (allVisibleSelected) {
      for (const track of displayedTracks) next.delete(track.id);
    } else {
      for (const track of displayedTracks) next.add(track.id);
    }
    selectedTrackIds = next;
  }

  // `indeterminate` is a DOM property, not an HTML attribute, so there's no
  // plain markup way to set it - this action assigns it directly on the
  // checkbox node whenever the derived value changes.
  function indeterminate(node: HTMLInputElement, value: boolean) {
    node.indeterminate = value;
    return {
      update(newValue: boolean) {
        node.indeterminate = newValue;
      },
    };
  }

  // Selections intentionally persist across search/folder changes so a user
  // can build a cross-folder selection. Clear gives an explicit way to reset.
  function clearSelection() {
    selectedTrackIds = new Set();
  }

  async function runLibraryRescan(): Promise<void> {
    stopPreview();
    rescanStartError = null;
    // A scan may remove files. Clear the explicit selection at start so an
    // id removed by final cleanup cannot later be submitted to processing.
    // Do not try to reconcile against tracksStore.tracks: that list may be
    // search-filtered, and hidden selections intentionally persist.
    selectedTrackIds = new Set();
    try {
      await tracksStore.startRescan();
    } catch (error) {
      rescanStartError = error instanceof Error ? error.message : "Library rescan failed";
    }
  }

  function onJobSubmitted() {
    showProcessDialog = false;
    selectedTrackIds = new Set();
  }

  function handleTagsSaved(updated: Track): void {
    tracksStore.replaceTrack(updated);
    editingTagsTrack = null;
  }

  function handleTrackDeleted(trackId: number): void {
    if (previewingTrackId === trackId) stopPreview();
    selectedTrackIds = new Set([...selectedTrackIds].filter((id) => id !== trackId));
    if (editingTagsTrack?.id === trackId) editingTagsTrack = null;
    deletingTrack = null;
    tracksStore.removeTrack(trackId);
  }

  function requestTrackDelete(track: Track): void {
    if (previewingTrackId === track.id) stopPreview();
    deletingTrack = track;
  }
</script>

<div class="library">
  <section class="library-command-bar" aria-labelledby="library-title">
    <div class="library-heading">
      <p class="library-eyebrow">Karaoke workspace</p>
      <h1 id="library-title">Track library</h1>
      <p>{displayedTracks.length} track{displayedTracks.length === 1 ? "" : "s"} ready to review</p>
    </div>
    <label class="library-search">
      <span class="visually-hidden">Search tracks</span>
      <input type="search" placeholder="Search tracks..." oninput={onSearch} />
    </label>
  </section>
  <div class="library-body">
    <nav class="folder-tree" aria-label="Media folders">
      <div class="folder-tree-heading-row">
        <p class="folder-tree-heading">Folders</p>
        <button
          type="button"
          class="folder-tree-new"
          onclick={openCreateFolder}
          disabled={rescanning || folders.length === 0}
          title={rescanning ? "Wait for the library scan to finish" : "Create a folder inside the library"}
        >+ New</button>
      </div>
      <div class="folder-tree-view-actions">
        {#if allFolderRows.some((row) => row.node.children.length > 0)}
          <button type="button" class="folder-tree-toggle-all" onclick={toggleAllFolders}>
            {hasCollapsedFolders ? "Expand all" : "Collapse all"}
          </button>
        {/if}
        {#if selectedFolderInfo && !selectedFolderIsRoot}
          <button
            type="button"
            onclick={openRenameFolder}
            disabled={rescanning || folderHasActiveTrack(selectedFolderInfo.path)}
          >Rename</button>
          <button
            type="button"
            class="folder-tree-delete"
            onclick={() => (deletingFolder = selectedFolderInfo)}
            disabled={rescanning || folderHasActiveTrack(selectedFolderInfo.path)}
          >Delete</button>
        {/if}
      </div>
      <div class="folder-tree-scroll" aria-label="Folder list">
        <button class="folder-tree-all" class:selected={selectedFolder === null} onclick={() => (selectedFolder = null)}>All tracks</button>
        {#each folderRows as row (row.node.path)}
          <div
            class="folder-tree-row"
            role="group"
            aria-label={row.node.path}
            class:folder-tree-drop-target={dragOverFolderPath === row.node.path}
            style="padding-left: {row.depth * 12 + 2}px"
            ondragenter={(event) => dragOverFolder(event, row.node.path, row.node.children.length > 0)}
            ondragover={(event) => dragOverFolder(event, row.node.path, row.node.children.length > 0)}
            ondragleave={(event) => leaveFolder(event, row.node.path)}
            ondrop={(event) => void dropTrackOnFolder(event, row.node.path)}
          >
            {#if row.node.children.length > 0}
              <button
                type="button"
                class="folder-tree-disclosure"
                aria-label={`${collapsedFolderPaths.has(row.node.path) ? "Expand" : "Collapse"} ${row.node.name}`}
                aria-expanded={!collapsedFolderPaths.has(row.node.path)}
                onclick={() => toggleFolder(row.node.path)}
                title={`${collapsedFolderPaths.has(row.node.path) ? "Expand" : "Collapse"} subfolders`}
              ><span aria-hidden="true">{collapsedFolderPaths.has(row.node.path) ? "▸" : "▾"}</span></button>
            {:else}
              <span class="folder-tree-disclosure-spacer" aria-hidden="true"></span>
            {/if}
            <button
              type="button"
              class="folder-tree-label"
              class:selected={selectedFolder === row.node.path}
              onclick={() => selectFolder(row.node.path)}
              title={draggingTrack ? `Move ${draggingTrack.title} here` : row.node.path}
            >{row.node.name}</button>
          </div>
        {/each}
      </div>
    </nav>
    <div class="track-list-panel">
      <div class="track-list-actions">
        <span class="track-list-selection-summary">
          {selectedTrackIds.size === 0 ? "Select tracks to prepare" : `${selectedTrackIds.size} selected`}
          · {displayedTracks.length.toLocaleString()} of {visibleTracks.length.toLocaleString()} shown
        </span>
        {#if selectedFolder}
          <button onclick={selectAllInFolder}>Select all in folder</button>
        {/if}
        {#if selectedTrackIds.size > 0}
          <button onclick={clearSelection}>Clear</button>
        {/if}
        {#if activeFilterCount > 0}
          <button class="library-clear-filters" onclick={clearAllColumnFilters}>Clear filters ({activeFilterCount})</button>
        {/if}
        <button
          class="library-columns-button"
          bind:this={columnsButtonEl}
          onclick={() => void openColumnMenu(true)}
          aria-haspopup="dialog"
          aria-expanded={showColumnMenu}
        >
          Columns
        </button>
        <button type="button" onclick={() => void runLibraryRescan()} disabled={rescanning}>
          {rescanning ? "Scanning in background…" : "Rescan library"}
        </button>
        <button class="library-primary-action" disabled={selectedTrackIds.size === 0} onclick={() => (showProcessDialog = true)}>
          Prepare selected ({selectedTrackIds.size})
        </button>
      </div>
      {#if rescanMessage}<p class="library-rescan-message" aria-live="polite">{rescanMessage}</p>{/if}
      {#if rescanError}<p class="library-rescan-error" role="alert">{rescanError}</p>{/if}
      {#if folderOperationMessage}<p class="library-file-operation-message" aria-live="polite">{folderOperationMessage}</p>{/if}
      {#if folderOperationError}<p class="library-rescan-error" role="alert">{folderOperationError}</p>{/if}
      <div class="library-table-scroll" bind:this={tableScrollEl} onscroll={onTableScroll}>
        <table class="library-table" aria-rowcount={displayedTracks.length + 1}>
          <thead>
            <tr class="library-table-header-row" oncontextmenu={onHeaderContextMenu}>
              <th class="library-table-header-cell library-table-select-cell">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  use:indeterminate={someVisibleSelected}
                  onchange={toggleSelectAllVisible}
                  disabled={displayedTracks.length === 0}
                  aria-label={allVisibleSelected ? "Deselect all tracks" : "Select all tracks"}
                />
              </th>
              <th class="library-table-header-cell library-table-preview-cell"><span class="visually-hidden">Preview</span></th>
              {#each orderedColumns as column (column.key)}
                <th
                  class="library-table-header-cell"
                  class:library-table-header-filtered={column.filter.trim() !== ""}
                  class:library-table-header-filter-open={openHeaderFilterKey === column.key}
                  style={`width: ${column.width}px; min-width: ${column.width}px; max-width: ${column.width}px;`}
                >
                  <div class="library-table-header-content">
                    {#if column.key === "artwork"}
                      <span class="library-table-visual-header">{column.label}</span>
                    {:else}
                      <button
                        type="button"
                        class="library-table-sort-button"
                        aria-label={sortAriaLabel(column.key, column.label)}
                        onclick={() => cycleSort(column.key)}
                      >
                        <span>{column.label}</span>
                        <span class="library-sort-indicator" aria-hidden="true">
                          {columnsState.sortKey === column.key ? (columnsState.sortDirection === "asc" ? "↑" : "↓") : "↕"}
                        </span>
                      </button>
                    {/if}
                    <button
                      type="button"
                      class="library-header-filter-button"
                      class:active={column.filter.trim() !== ""}
                      aria-label={`Open ${column.label} filter`}
                      aria-haspopup="dialog"
                      aria-expanded={openHeaderFilterKey === column.key}
                      title={`Filter ${column.label}`}
                      onclick={(event) => {
                        event.stopPropagation();
                        if (openHeaderFilterKey === column.key) closeHeaderFilter();
                        else void openHeaderFilter(column);
                      }}
                    >
                      <span aria-hidden="true">▾</span>
                    </button>
                  </div>
                  {#if openHeaderFilterKey === column.key}
                    <div
                      class="library-header-filter-popover"
                      role="dialog"
                      tabindex="-1"
                      aria-labelledby={`library-filter-${column.key}-title`}
                      onkeydown={(event) => {
                        event.stopPropagation();
                        if (event.key === "Escape") closeHeaderFilter();
                        if (event.key === "Enter") applyHeaderFilter(column.key);
                      }}
                    >
                      <div class="library-header-filter-title-row">
                        <strong id={`library-filter-${column.key}-title`}>Filter {column.label}</strong>
                        <button type="button" aria-label={`Close ${column.label} filter`} onclick={closeHeaderFilter}>×</button>
                      </div>
                      {#if filterOptionsFor(column.key)}
                        <select
                          bind:this={headerFilterControlEl}
                          bind:value={headerFilterDraft}
                          aria-label={`${column.label} filter value`}
                        >
                          {#each filterOptionsFor(column.key) ?? [] as option}
                            <option value={option.value}>{option.label}</option>
                          {/each}
                        </select>
                      {:else}
                        <input
                          bind:this={headerFilterControlEl}
                          bind:value={headerFilterDraft}
                          type="text"
                          placeholder={`Contains ${column.label.toLowerCase()}…`}
                          aria-label={`${column.label} filter value`}
                        />
                      {/if}
                      <p>Filters in different columns are combined.</p>
                      <div class="library-header-filter-actions">
                        <button type="button" onclick={() => clearHeaderFilter(column.key)}>Clear</button>
                        <button type="button" class="primary" onclick={() => applyHeaderFilter(column.key)}>Apply</button>
                      </div>
                    </div>
                  {/if}
                  <button
                    type="button"
                    class="library-column-resize-handle"
                    aria-label={`Resize ${column.label} column`}
                    title={`Drag to resize ${column.label}`}
                    onpointerdown={(event) => beginColumnResize(event, column.key)}
                    onpointermove={moveColumnResize}
                    onpointerup={finishColumnResize}
                    onpointercancel={cancelColumnResize}
                    onkeydown={(event) => onResizeHandleKeydown(event, column)}
                  ></button>
                </th>
              {/each}
              <th class="library-table-header-cell library-table-status-cell">Status</th>
              <th class="library-table-header-cell library-table-actions-cell">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#if virtualWindow.active && virtualWindow.top > 0}
              <tr class="library-virtual-spacer" aria-hidden="true">
                <td colspan={tableColumnCount} style={`height: ${virtualWindow.top}px;`}></td>
              </tr>
            {/if}
            {#each renderedTracks as track, renderedIndex (track.id)}
              <TrackRow
                {track}
                rowIndex={virtualWindow.start + renderedIndex + 2}
                revision={tracksStore.revisionFor(track.id)}
                columns={orderedColumns}
                selected={selectedTrackIds.has(track.id)}
                previewing={previewingTrackId === track.id}
                processingStatus={processingStateByTrack.get(track.id) ?? (jobsStore.trackFailures?.[track.id] ? "failed" : null)}
                processingError={processingErrorFor(track.id)}
                deleteDisabledReason={rescanning ? "Wait for the library rescan to finish before changing files" : null}
                dragging={draggingTrack?.id === track.id}
                onToggle={() => toggleTrackSelection(track.id)}
                onOpenMixer={handleOpenMixer}
                onOpenEditor={handleOpenEditor}
                onTogglePreview={togglePreview}
                onEditTags={(track) => (editingTagsTrack = track)}
                onRequestRename={requestTrackRename}
                onRequestDelete={requestTrackDelete}
                onTrackDragStart={beginTrackDrag}
                onTrackDragEnd={finishTrackDrag}
              />
            {/each}
            {#if virtualWindow.active && virtualWindow.bottom > 0}
              <tr class="library-virtual-spacer" aria-hidden="true">
                <td colspan={tableColumnCount} style={`height: ${virtualWindow.bottom}px;`}></td>
              </tr>
            {/if}
          </tbody>
        </table>
        {#if displayedTracks.length === 0}
          <div class="library-empty-state">
            <strong>No tracks match this view</strong>
            <span>Try clearing the folder, search, or column filters.</span>
          </div>
        {/if}
      </div>
    </div>
  </div>
  {#if showColumnMenu}
    <div
      class="library-column-menu-overlay"
      role="presentation"
    >
      <div
        class="library-column-menu"
        role="dialog"
        aria-modal="true"
        aria-labelledby="library-columns-title"
        tabindex="-1"
        bind:this={columnMenuEl}
        onkeydown={(event) => {
          event.stopPropagation();
          if (event.key === "Escape") void closeColumnMenu();
        }}
      >
        <div class="library-column-menu-header">
          <div>
            <p class="library-column-menu-eyebrow">Library layout</p>
            <h2 id="library-columns-title">Columns</h2>
          </div>
          <button type="button" onclick={() => void closeColumnMenu()} aria-label="Close columns">×</button>
        </div>
        <p class="library-column-menu-help">Drag ⠿ to reorder columns, use the arrows for precise keyboard control, or drag a table-header edge to resize it.</p>
        {#each columnMenuRows as column (column.key)}
          <div
            class="library-column-menu-row"
            class:dragging={draggingColumn === column.key}
            class:drag-over={dragOverColumn === column.key}
            data-column-key={column.key}
            role="group"
            aria-label={`${column.label} column controls`}
            ondragover={(event) => moveColumnOrderDrag(event, column.key)}
            ondrop={(event) => finishColumnOrderDrag(event, column.key)}
          >
            <span
              class="library-column-drag-handle"
              draggable="true"
              title={`Drag ${column.label} to reorder`}
              aria-hidden="true"
              ondragstart={(event) => beginColumnOrderDrag(event, column.key)}
              ondragend={cancelColumnOrderDrag}
            >⠿</span>
            <label>
              <input
                type="checkbox"
                checked={column.visible}
                onchange={() => toggleColumnVisible(column.key)}
              />
              <span>{column.label}</span>
            </label>
            <button type="button" aria-label={`Move ${column.label} left`} onclick={() => moveColumn(column.key, -1)}>◀</button>
            <button type="button" aria-label={`Move ${column.label} right`} onclick={() => moveColumn(column.key, 1)}>▶</button>
            <button
              type="button"
              class="library-column-sort-ascending"
              aria-label={column.key === "artwork" ? "Artwork cannot be sorted" : "Sort ascending"}
              disabled={column.key === "artwork"}
              class:active={columnsState.sortKey === column.key && columnsState.sortDirection === "asc"}
              onclick={() => setSort(column.key, "asc")}
            >Sort ▲</button>
            <button
              type="button"
              class="library-column-sort-descending"
              aria-label={column.key === "artwork" ? "Artwork cannot be sorted descending" : "Sort descending"}
              disabled={column.key === "artwork"}
              class:active={columnsState.sortKey === column.key && columnsState.sortDirection === "desc"}
              onclick={() => setSort(column.key, "desc")}
            >Sort ▼</button>
            {#if filterOptionsFor(column.key)}
              <select
                class="library-column-menu-filter"
                value={column.filter}
                aria-label={`Filter ${column.label}`}
                onchange={(event) => setColumnFilter(column.key, (event.target as HTMLSelectElement).value)}
              >
                {#each filterOptionsFor(column.key) ?? [] as option}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            {:else}
              <input
                type="text"
                class="library-column-menu-filter"
                placeholder="Filter…"
                value={column.filter}
                disabled={column.key === "artwork"}
                aria-label={`Filter ${column.label}`}
                oninput={(event) => setColumnFilter(column.key, (event.target as HTMLInputElement).value)}
              />
            {/if}
          </div>
        {/each}
        <div class="library-column-menu-footer">
          <div>
            <button type="button" class="library-column-clear-sort" disabled={columnsState.sortKey === null} onclick={clearSort}>Clear sort</button>
            <button type="button" disabled={activeFilterCount === 0} onclick={clearAllColumnFilters}>Clear filters ({activeFilterCount})</button>
            <button type="button" onclick={resetColumnWidths}>Reset widths</button>
          </div>
          <button type="button" class="library-column-done" onclick={() => void closeColumnMenu()}>Done</button>
        </div>
      </div>
    </div>
  {/if}
  {#if showProcessDialog}
    <ProcessDialog
      trackIds={[...selectedTrackIds]}
      device={device ?? "auto"}
      {whisperxAvailable}
      onSubmitted={onJobSubmitted}
      onClose={() => (showProcessDialog = false)}
    />
  {/if}
  {#if editingTagsTrack}
    <TagsDialog track={editingTagsTrack} onSaved={handleTagsSaved} onClose={() => (editingTagsTrack = null)} />
  {/if}

  {#if deletingTrack}
    <DeleteTrackDialog
      track={deletingTrack}
      onDeleted={handleTrackDeleted}
      onClose={() => (deletingTrack = null)}
    />
  {/if}

  {#if folderDialogMode}
    <FolderDialog
      mode={folderDialogMode}
      folder={folderDialogMode === "rename" ? selectedFolderInfo : null}
      {folders}
      defaultParent={defaultNewFolderParent()}
      onSaved={(folder) => void handleFolderSaved(folder)}
      onClose={() => (folderDialogMode = null)}
    />
  {/if}

  {#if renamingTrack}
    <RenameTrackDialog
      track={renamingTrack}
      currentFolder={currentFolderForTrack(renamingTrack)}
      {folders}
      onRenamed={handleTrackRenamed}
      onClose={() => (renamingTrack = null)}
    />
  {/if}

  {#if deletingFolder}
    <DeleteFolderDialog
      folder={deletingFolder}
      trackCount={folderTrackCount(deletingFolder.path)}
      onDeleted={(trackIds) => void handleFolderDeleted(trackIds)}
      onClose={() => (deletingFolder = null)}
    />
  {/if}
</div>
