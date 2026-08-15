<script lang="ts">
  import { artworkUrl } from "../api";
  import { displayValue, instrumentalProvenanceTitle, lyricTimingTitle, type LibraryColumnConfig } from "../libraryColumns";
  import type { Track } from "../types";
  import CreateLyricsDialog from "./CreateLyricsDialog.svelte";

  let {
    track,
    columns,
    revision = 0,
    selected = false,
    previewing = false,
    processingStatus = null,
    processingError = null,
    deleteDisabledReason = null,
    dragging = false,
    rowIndex = undefined,
    onToggle = () => {},
    onOpenMixer = () => {},
    onOpenEditor = () => {},
    onTogglePreview = () => {},
    onEditTags = () => {},
    onRequestRename = () => {},
    onRequestDelete = () => {},
    onTrackDragStart = () => {},
    onTrackDragEnd = () => {},
  }: {
    track: Track;
    columns: LibraryColumnConfig[];
    revision?: number;
    selected?: boolean;
    previewing?: boolean;
    processingStatus?: "queued" | "running" | "waiting" | "failed" | null;
    processingError?: string | null;
    deleteDisabledReason?: string | null;
    dragging?: boolean;
    rowIndex?: number;
    onToggle?: () => void;
    onOpenMixer?: (track: Track) => void;
    onOpenEditor?: (track: Track) => void;
    onTogglePreview?: (track: Track) => void;
    onEditTags?: (track: Track) => void;
    onRequestRename?: (track: Track) => void;
    onRequestDelete?: (track: Track) => void;
    onTrackDragStart?: (track: Track, event: DragEvent) => void;
    onTrackDragEnd?: () => void;
  } = $props();

  const stemLabel = $derived(`${track.stem_count} stem${track.stem_count === 1 ? "" : "s"}`);
  const activelyProcessing = $derived(
    processingStatus === "queued" || processingStatus === "running" || processingStatus === "waiting"
  );

  let showCreateDialog = $state(false);
  let artworkMissing = $state(false);

  // Tag saves can add or replace artwork without changing the track id, so
  // the keyed row remains mounted. Reset the failure placeholder and add a
  // per-track cache key whenever the shared library store marks it changed.
  $effect(() => {
    revision;
    artworkMissing = false;
  });

  function editLyrics(event: MouseEvent): void {
    event.stopPropagation();
    onOpenEditor(track);
  }

  function togglePreview(event: MouseEvent): void {
    event.stopPropagation();
    onTogglePreview(track);
  }

  // The Mixer was previously reachable only by double-clicking a row, which
  // user feedback found non-obvious ("It was not clear to me how I play the
  // tracks in the mixer."). This button makes the same destination explicit
  // without removing the dblclick shortcut. stopPropagation keeps a click
  // here from also bubbling into the row's own ondblclick/onkeydown wiring.
  function openMixer(event: MouseEvent): void {
    event.stopPropagation();
    onOpenMixer(track);
  }

  function openCreateDialog(event: MouseEvent): void {
    event.stopPropagation();
    showCreateDialog = true;
  }

  function editTags(event: MouseEvent): void {
    event.stopPropagation();
    onEditTags(track);
  }

  function openDeleteDialog(event: MouseEvent): void {
    event.stopPropagation();
    onRequestDelete(track);
  }

  function openRenameDialog(event: MouseEvent): void {
    event.stopPropagation();
    onRequestRename(track);
  }

  function beginTrackDrag(event: DragEvent): void {
    if (activelyProcessing || deleteDisabledReason !== null) {
      event.preventDefault();
      return;
    }
    event.dataTransfer?.setData("application/x-karaoke-track-id", String(track.id));
    event.dataTransfer?.setData("text/plain", track.title);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
    onTrackDragStart(track, event);
  }

  function onLyricsCreated(createdTrack: Track): void {
    showCreateDialog = false;
    onOpenEditor(createdTrack);
  }

  // Keyboard equivalent of the row's mouse interactions: double-click opens
  // the Mixer, but a keyboard user has no "double-press" gesture, so Enter
  // on a focused row opens the Lyric Editor instead - the more commonly
  // keyboard-driven of the two destinations (retiming lyrics is inherently
  // a keyboard-heavy task; mixing is inherently mouse/slider-heavy). This
  // fires even for a track with no lrc yet (an empty editor) - the
  // "Create lyrics" button remains the guided path for that case; Enter is
  // a direct-to-editor shortcut, not a replacement for it.
  function onRowKeydown(event: KeyboardEvent): void {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter") {
      event.preventDefault();
      onOpenEditor(track);
    }
  }
</script>

<tr
  class="track-row"
  class:track-row-playing={previewing}
  class:track-row-queued={processingStatus === "queued"}
  class:track-row-running={processingStatus === "running"}
  class:track-row-waiting={processingStatus === "waiting"}
  class:track-row-failed={processingStatus === "failed"}
  class:track-row-dragging={dragging}
  class:track-row-draggable={!activelyProcessing && deleteDisabledReason === null}
  draggable={!activelyProcessing && deleteDisabledReason === null}
  tabindex="0"
  aria-rowindex={rowIndex}
  ondblclick={() => onOpenMixer(track)}
  onkeydown={onRowKeydown}
  ondragstart={beginTrackDrag}
  ondragend={onTrackDragEnd}
>
  <td class="track-row-fixed-cell track-row-cell-select">
    <input
      type="checkbox"
      checked={selected}
      onchange={onToggle}
      aria-label={`Select ${track.title}`}
    />
  </td>
  <td class="track-row-fixed-cell track-row-cell-preview">
    <button
      type="button"
      class="track-row-preview"
      aria-label={previewing ? "Pause preview" : "Play preview"}
      onclick={togglePreview}
    >{previewing ? "⏸" : "▶"}</button>
  </td>
  {#each columns as column (column.key)}
    {#if column.key === "artwork"}
      <td
        class="track-row-cell track-row-cell-artwork"
        style={`width: ${column.width}px; min-width: ${column.width}px; max-width: ${column.width}px;`}
      >
        {#if artworkMissing || track.has_artwork === false}
          <span class="track-row-artwork-placeholder" aria-label={`No artwork for ${track.title}`}>♪</span>
        {:else}
          {#key `${track.id}:${revision}`}
            <img
              class="track-row-artwork"
              src={`${artworkUrl(track.id)}?v=${revision}`}
              alt={`${track.title} artwork`}
              loading="lazy"
              onerror={() => (artworkMissing = true)}
            />
          {/key}
        {/if}
      </td>
    {:else}
      <td
        class="track-row-cell track-row-cell-{column.key}"
        style={`width: ${column.width}px; min-width: ${column.width}px; max-width: ${column.width}px;`}
        title={column.key === "instrumental"
          ? instrumentalProvenanceTitle(track)
          : column.key === "lyrics"
            ? lyricTimingTitle(track)
            : displayValue(track, column.key)}
      >
        {#if column.key === "instrumental"}
          <span class="badge" class:badge-instrumental={track.outputs.instrumental} class:badge-output-missing={!track.outputs.instrumental}>
            {displayValue(track, column.key)}
          </span>
        {:else if column.key === "lyrics"}
          <span class="badge" class:badge-output-missing={!track.lrc_state} class:badge-lyrics-high-quality={track.lyric_timing_provenance?.quality === "high_quality"} class:badge-lrc-enhanced={track.lrc_state === "enhanced" && track.lyric_timing_provenance?.quality !== "high_quality"} class:badge-lrc-line_timed={track.lrc_state === "line_timed"} class:badge-lrc-untimed={track.lrc_state === "untimed"} class:badge-lrc-empty={track.lrc_state === "empty"} class:badge-lrc-unknown={track.lrc_state === "unknown"}>
            {displayValue(track, column.key)}
          </span>
        {:else if column.key === "stems"}
          <span class="badge badge-stems">{stemLabel}</span>
        {:else}
          {displayValue(track, column.key)}
        {/if}
      </td>
    {/if}
  {/each}
  <td class="track-row-fixed-cell track-row-cell-status">
    <span class="badges">
      {#if processingStatus === "queued"}
        <span class="badge badge-processing badge-processing-queued">Queued</span>
      {:else if processingStatus === "running"}
        <span class="badge badge-processing badge-processing-running">Processing</span>
      {:else if processingStatus === "waiting"}
        <span class="badge badge-processing badge-processing-waiting">Waiting for next phase</span>
      {:else if processingStatus === "failed"}
        <span
          class="badge badge-processing badge-processing-failed"
          title={processingError ?? "Processing failed"}
          aria-label={`Processing error: ${processingError ?? "Processing failed"}`}
        >Error</span>
      {/if}
    </span>
  </td>
  <td class="track-row-fixed-cell track-row-cell-actions">
    <span class="track-row-actions">
      {#if track.lrc_state}
        <button type="button" class="track-row-edit-lyrics" onclick={editLyrics}>Edit lyrics</button>
      {:else}
        <button type="button" class="track-row-edit-lyrics" onclick={openCreateDialog}>Create lyrics</button>
      {/if}
      <button type="button" class="track-row-edit-lyrics" onclick={openMixer}>Mixer</button>
      <button type="button" class="track-row-edit-lyrics" onclick={editTags}>Tags</button>
      <button
        type="button"
        class="track-row-edit-lyrics"
        onclick={openRenameDialog}
        disabled={activelyProcessing || deleteDisabledReason !== null}
        title={deleteDisabledReason ?? (activelyProcessing ? "Wait for processing to finish before renaming" : "Rename the source file and matching lyrics/stems")}
      >Move / rename…</button>
      <button
        type="button"
        class="track-row-delete"
        onclick={openDeleteDialog}
        disabled={activelyProcessing || deleteDisabledReason !== null}
        title={deleteDisabledReason ?? (!activelyProcessing ? "Move track to Recycle Bin" : "Wait for processing to finish before deleting")}
      >Delete…</button>
    </span>
  </td>
</tr>

{#if showCreateDialog}
  <CreateLyricsDialog track={track} onCreated={onLyricsCreated} onClose={() => (showCreateDialog = false)} />
{/if}
