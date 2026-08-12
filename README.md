# Karaoke Media Manager

Karaoke Media Manager is a local, Windows-first application for turning a
music library into reviewable karaoke media. It indexes existing audio,
separates vocals and instruments, finds and enhances lyrics, repairs tags and
cover artwork, previews stems in a mixer, and provides a waveform-based lyric
timing editor.

The application runs locally in a web browser. The backend is FastAPI with a
SQLite catalogue; the frontend is Svelte. Audio separation uses isolated
Demucs and UVR workers, while enhanced lyric timing uses an isolated WhisperX
worker.

## Contents

- [What it can do](#what-it-can-do)
- [Before you begin](#before-you-begin)
- [Getting started](#getting-started)
- [First-run configuration](#first-run-configuration)
- [Quick-start workflow](#quick-start-workflow)
- [Feature guide](#feature-guide)
- [User guide](#user-guide)
- [Files, outputs, and safety](#files-outputs-and-safety)
- [Large libraries](#large-libraries)
- [Troubleshooting](#troubleshooting)
- [Backup, update, and recovery](#backup-update-and-recovery)
- [Developer and API reference](#developer-and-api-reference)
- [Security and licence](#security-and-licence)

## What it can do

- Index FLAC, MP3, M4A, WAV, AAC, and OGG libraries without copying the
  originals.
- Display artwork, folder, filename, contributing artist, title, album, year,
  duration, lyric state, separated outputs, and processing state.
- Search, filter, sort, resize, show/hide, and reorder Library columns.
- Handle large catalogues with a virtualized table. An automated 80,000-track
  test mounts fewer than 40 rows at once.
- Rescan media roots in the background and publish newly discovered tracks
  while the scan is still running.
- Produce a karaoke instrumental, editable stems, lyrics with enhanced
  per-word timing, repaired metadata/artwork, or all of these in one job.
- Process multi-track batches while keeping compatible AI models loaded across
  the batch instead of reloading them for every song.
- Review past and current runs in a searchable Processing History, including
  per-track phase results and full error details.
- Import individual YouTube videos or selected entries from a playlist.
- Review and balance the original track and generated parts in the Mixer, then
  export WAV or MP3.
- Review line, word, and instrumental-break timing against a waveform; drag
  timing markers, tap timings, create loops, undo changes, and save LRC files.
- Use System, Light, or Dark appearance.

## Before you begin

### Platform

The supported setup path is Windows 10/11 with PowerShell. The Python backend
itself is portable, but the included worker bootstrap, native folder picker,
paths, and GPU verification are currently Windows-oriented.

### Required software

Install these before cloning the repository:

1. **Git**.
2. **Python 3.11 x64**. Python 3.11 is recommended for the main application
   and required for the tested worker setup. The worker script creates its
   environments from the main backend environment, so using Python 3.11 for
   everything avoids version drift.
3. **Node.js 20 or newer**, including npm.
4. **FFmpeg and ffprobe on `PATH`**. Verify both commands from a new terminal:

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

5. **Deno** if YouTube import will be used. yt-dlp uses it for current YouTube
   JavaScript challenges:

   ```powershell
   winget install DenoLand.Deno
   ```

Confirm the installed command-line tools before continuing:

```powershell
git --version
py -3.11 --version
node --version
npm --version
ffmpeg -version
ffprobe -version
deno --version  # optional when YouTube import is not needed
```

### Optional NVIDIA GPU

The Library, scanning, metadata editor, lyric download, manual lyric editing,
Mixer, export, and YouTube import work without an NVIDIA GPU. AI separation
and enhanced timing are practical on a supported CUDA GPU.

For the tested GPU installation:

- Install a current NVIDIA driver.
- Confirm `nvidia-smi` works.
- Allow substantial disk space. The three worker environments, CUDA-enabled
  Torch packages, cached model weights, and temporary processing data can use
  tens of gigabytes.
- The included bootstrap installs CUDA 12.8 Torch packages and deliberately
  fails its final verification if CUDA is unavailable.

The app's `cuda` indicator reports the detected processing device; it does not
mean FFmpeg itself is doing the separation. Demucs, UVR, and WhisperX perform
the GPU work through PyTorch. FFmpeg handles media probing, decoding, and
transcoding.

### Network access

Internet access is needed for initial Python/npm installation, first-use model
downloads, metadata/artwork lookup, lyric providers, and YouTube import.

### Security boundary

The app has no authentication. The bundled launcher binds it to `127.0.0.1`,
and the backend rejects non-loopback clients, untrusted Host headers, and
cross-origin browser requests by default. Do not bind it to `0.0.0.0` or
expose port 8000 to a LAN or the internet. See [SECURITY.md](SECURITY.md).

## Getting started

### Recommended Windows installer

Clone the repository or download and extract the latest source release, then
open PowerShell in that folder. The bootstrap creates the Python environment,
installs the backend, installs and builds the frontend, validates dependencies,
and creates a desktop shortcut:

```powershell
git clone https://github.com/wetware0/KaraokeMixer.git
Set-Location .\KaraokeMixer
.\install.ps1
```

If Python 3.11, Node.js, FFmpeg, or ffprobe is missing and `winget` is
available, the installer can add those prerequisites:

```powershell
.\install.ps1 -InstallPrerequisites
```

AI workers are optional because their CUDA packages and models can consume
tens of gigabytes. Install all three during setup only when a compatible
NVIDIA GPU is available:

```powershell
.\install.ps1 -InstallPrerequisites -Worker all -Start
```

The default installer deliberately skips AI workers, so library management,
metadata, manual lyric editing, playback, mixing, export, and downloads can be
used without a large model installation. Add a worker later with
`backend\workers\setup-worker-venvs.ps1`.

Start and stop a launcher-managed instance with:

```powershell
.\scripts\Start-KaraokeMixer.ps1
.\scripts\Stop-KaraokeMixer.ps1
```

### Manual installation

The following steps are the transparent equivalent of the installer.

#### 1. Clone the repository

```powershell
git clone https://github.com/wetware0/KaraokeMixer.git
Set-Location .\KaraokeMixer
```

#### 2. Create the main Python environment

Run from the repository root:

```powershell
py -3.11 -m venv .\backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install --upgrade pip
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
```

#### 3. Install and build the frontend

```powershell
Set-Location .\frontend
npm ci
npm run build
Set-Location ..
```

The production build is written to `frontend\dist`. The FastAPI backend serves
that directory automatically.

#### 4. Install AI workers (optional, one-time)

The workers are isolated from the main backend so their incompatible Torch and
audio dependencies do not contaminate one another.

To install all three tested CUDA workers:

```powershell
.\backend\workers\setup-worker-venvs.ps1 -Worker all
```

Or install one worker at a time. The script is resumable:

```powershell
.\backend\workers\setup-worker-venvs.ps1 -Worker demucs
.\backend\workers\setup-worker-venvs.ps1 -Worker uvr
.\backend\workers\setup-worker-venvs.ps1 -Worker whisperx
```

The script creates:

| Environment | Purpose | Used by |
| --- | --- | --- |
| `backend\.venv-demucs` | Demucs separation | Karaoke instrumental, editable stems, complete preparation |
| `backend\.venv-uvr` | Specialist lead/backing vocal separation | High-quality backing removal and optional vocal split |
| `backend\.venv-whisperx` | Transcription and forced alignment | Enhanced per-word lyric timing and AI re-timing |

The setup ends with `pip check`, import checks, and `torch.cuda.is_available()`.
If it stops at the CUDA assertion, the environment may have installed but is
not considered verified; fix the driver/CUDA visibility before relying on it.

First use also downloads model weights. Demucs weights are approximately
80–330 MB per model. The UVR karaoke ensemble is approximately 3.3 GiB and is
stored under `%USERPROFILE%\.karaoke-media-manager\uvr-models` unless the data
directory is overridden. WhisperX downloads its ASR/alignment models on demand.

If the workers are not installed, the app still starts. Workflows that
explicitly request separation or enhanced timing will fail with setup guidance;
lyrics-only work can still run with enhanced timing turned off.

#### 5. Start the application

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Keep that terminal open, then browse to:

<http://127.0.0.1:8000>

Verify the backend independently at <http://127.0.0.1:8000/api/health>. A
healthy response is:

```json
{"status":"ok"}
```

## First-run configuration

Open **Settings** from the gear button in the top-right corner.

### Appearance

Choose **System**, **Light**, or **Dark**. System follows the browser/Windows
colour preference. The choice is stored in the browser.

### Media roots

A media root contains source audio to index. Type or browse to a folder, then
click **Add**. Multiple roots are supported.

Examples:

```text
D:\Music
D:\Karaoke Sources
```

The scanner recursively indexes:

- `.flac`
- `.mp3`
- `.m4a`
- `.wav`
- `.aac`
- `.ogg`

Files whose filename starts with `.` are ignored. Generated files following
the app's `{name}.{part}.mp3` convention are treated as outputs rather than new
source tracks.

### Mirror roots

A mirror root keeps generated stems and LRC files away from the source
library. Add at least one mirror root if **Save files → In the mirror library**
will be used.

Only the first configured mirror root is currently used for new outputs. The
relative folder layout beneath the media root is reproduced beneath that
mirror root. If mirror mode cannot resolve a configured mirror destination,
the app safely falls back to writing beside the original.

### Device preference

- **auto** uses CUDA when detected, otherwise CPU.
- **cuda** explicitly requests the CUDA workers.
- **cpu** explicitly requests CPU processing.

The preference becomes the default in preparation dialogs and can be changed
per job under **Advanced options**.

### Downloads root

Choose where YouTube imports are saved. If blank, YouTube import falls back to
the first media root.

### YouTube cookies

- **none** works for unrestricted videos.
- **browser** asks yt-dlp to read a logged-in browser profile, for example
  `chrome`.
- **cookies.txt file** uses a Netscape-format exported cookie file.

The file mode is usually more reliable when the backend runs under a different
Windows account or browser-cookie decryption fails.

### Save and scan

Click **Save**, then **Rescan**. Scanning runs in the background and publishes
tracks in small batches, so the Library becomes usable before a large scan has
finished. You can also start a scan later with **Rescan library** in the
Library.

Removing or changing a media root does not update the list until a successful
rescan. A completed scan also removes database entries for source files that no
longer exist.

## Quick-start workflow

For a first karaoke track:

1. Add a media root in **Settings**, save, and rescan.
2. Search for a track in the Library.
3. Select its checkbox and click **Prepare selected**.
4. Choose **Complete karaoke preparation**.
5. Leave **Balanced** selected for a sensible first pass.
6. Choose **Beside each original** or **In the mirror library**.
7. Click **Start preparation**.
8. Watch the job tray and the row's **Queued**/**Processing** state.
9. Open **Mixer** to review the separated audio.
10. Open **Edit lyrics** to review timing.
11. Re-run only weak tracks with **High quality** rather than applying the
    slowest models to the entire library immediately.

## Feature guide

### Library

The Library is the main creator workspace.

- **Search** matches artist, title, and source path through the backend.
- The **Folders** tree begins collapsed. Expand only the branches needed; it
  scrolls independently from the track table.
- Use **+ New** to create a folder beneath any configured media folder. Select
  a non-root folder to reveal **Rename** and **Delete**. Folder deletion uses
  the Windows Recycle Bin and requires a separate confirmation.
- Drag a track row onto a folder to move the original together with its known
  lyrics and generated stems. Hover over a collapsed folder for a moment while
  dragging and it expands automatically. The row's **Move / rename…** action
  provides the same location choice without drag-and-drop and can also rename
  the file; its audio extension remains fixed.
- Click a column heading to cycle ascending, descending, and unsorted.
- Open **Columns** to show/hide fields, apply per-column filters, reorder
  columns by dragging, use arrow buttons for keyboard ordering, reset widths,
  or clear sorting.
- Drag the handle at the right edge of a heading to resize a column. A focused
  resize handle also accepts Left/Right in 10-pixel steps.
- Column visibility, order, width, sort, and filters are stored in browser
  local storage.
- The Artwork and Filename columns are separate from Title. Folder shows the
  original source location. Instrumental, Lyrics, and Stems are also separate
  creator-readiness columns: sort them from the heading or use the Columns
  panel's quality/readiness, timing-state, and Has stems/No stems filters.
- Instrumental reports **High Quality**, **Balanced**, or **Fast** when the app
  can safely identify how the current file was produced. Hover the value for
  the separation engine, model, source job, and whether the attribution was
  recorded at creation time or inferred from processing history. An existing
  instrumental whose provenance cannot be proved remains **Ready**; it is not
  mislabeled as low quality. **Missing** means no instrumental is present.
- Artist reads the contributing-artist tag first, preserving multiple values,
  and falls back to Album Artist.
- Duration is read from the original file without a full audio decode.
- Invalid release years are treated as missing. Accepted years are 1860
  through next year.
- Queued rows use an amber treatment; the actively processing row uses blue.
- A track whose latest processing attempt failed keeps a restrained red
  **Error** state after the batch ends. Hover the badge for the failed phase
  and a concise reason. Starting a retry replaces the error with its live
  Queued/Processing state; a successful retry clears it.
- **Mixer**, **Edit lyrics/Create lyrics**, **Tags**, and **Move / rename…** are explicit row
  actions. Double-clicking a row also opens the Mixer; Enter on a focused row
  opens the Lyric Editor.
- **Delete…** opens a second confirmation before moving the original track to
  the Windows Recycle Bin. Generated stems and lyric files are included by
  default and can be retained by clearing the checkbox. Delete is disabled
  while that track is queued or processing, or while a library rescan is
  active.
- Returning from the Mixer or Lyric Editor restores the same search, selected
  tracks, expanded folders, and scroll position.

The table is virtualized above 200 results. Only the visible window plus a
small buffer exists in the DOM, so scrolling and returning from a focused view
do not scale with the total track count.

### Tag and artwork editor

Click **Tags** on a track row.

- Edit Artist, Title, Album, and Year.
- **Auto-correct tags** searches online and stages corrected values for review.
- **Fetch artwork** searches online and stages a cover for review.
- **Replace artwork** selects a local JPEG or PNG.
- Nothing from an online suggestion is written until **Save changes**.
- Artwork must be JPEG or PNG and no larger than 20 MB.
- Tag/artwork writing is supported for FLAC, MP3, and M4A. WAV, AAC, and OGG
  can be indexed and processed, but their tags/artwork cannot be written by
  this app.
- Changes update metadata containers in the original source file. The encoded
  audio stream is not re-encoded.
- Text tags and artwork are separate writes. If the second write fails, the
  first may already have succeeded; the dialog remains open and shows the
  error.

Important concurrency rule: do not click **Save changes** for a track whose row
is currently marked **Processing** in a Tags and artwork batch. Manual and
batch metadata writers do not yet share a per-file lock. Saving another track
is fine; fetching a suggestion without saving is also fine.

### Preparation workflows

Select one or more tracks and click **Prepare selected**.
**Karaoke instrumental** is selected by default. If you choose Fast bulk,
Balanced, or High quality and then change to another compatible workflow, that
quality choice is retained rather than silently reverting to the new
workflow's default.

| Workflow | Result | Worker lane |
| --- | --- | --- |
| **Karaoke instrumental** | Ready-to-sing instrumental, with optional lyric download and enhanced timing | GPU |
| **All editable stems** | Vocals, drums, bass, other, and model-dependent guitar/piano; optional lead/backing vocal split | GPU |
| **Lyrics and enhanced timing** | LRC download plus optional full per-word timing | GPU queue; WhisperX runs only when alignment is requested |
| **Tags and artwork** | Missing/replacement metadata and embedded cover art | CPU |
| **Complete karaoke preparation** | Stems, instrumental, lyrics/timing, tags, and artwork | GPU |

The CPU and GPU queues are independent, with one job running in each lane. A
metadata job can therefore run alongside a separation job. Compatible models
stay loaded for a multi-track phase, and Demucs is released before WhisperX is
loaded.

#### Processing profiles

| Profile | Separation | Vocal treatment | ASR model | Best use |
| --- | --- | --- | --- | --- |
| **Fast bulk** | `mdx` | Remove all vocals | `base.en` | Large first pass and triage |
| **Balanced** | `htdemucs` | Remove all vocals | `small.en` | Default unattended workflow |
| **High quality** | `htdemucs_ft` | UVR best lead-vocal removal | `medium` | Selected final-quality reruns |

For **All editable stems**, High quality also enables the optional UVR
lead/backing split. Model names remain available under **Advanced options**.

The ASR model matters when lyrics must be transcribed. Existing line-timed
lyrics use forced alignment, so selecting a larger ASR model does not
automatically improve them.

#### Advanced options

- **Processing device** selects Automatic, CUDA, or CPU for this job.
- **Replace outputs that already exist** disables normal resumable skipping.
- **Save files** chooses beside or mirror output.
- Recipe-specific settings control separation model, backing-vocal treatment,
  lyric download, enhanced timing, transcription model, and vocal split.

With replacement off, existing output files are skipped. A Tags and artwork
batch also skips a track when its album, plausible year, and artwork are
already present. Enable replacement when the intention is to replace an
existing cover or metadata. The batch uses current Artist and Title as its
search identity; use the manual editor when Title itself needs correction and
review.

### Job tray and cancellation

Active and failed jobs appear at the bottom of the app.

- While a stage is running, the tray shows **Step N of M**, a plain-language
  phase name, and progress through the selected tracks. The count advances
  after every track and restarts only when the clearly numbered next phase
  begins.
- Library highlighting follows the whole recipe: the active row shows
  **Processing**, tracks not yet reached in the current phase show **Queued**,
  and tracks which finished this phase remain highlighted as **Waiting for
  next phase**. Status clears only when the whole recipe finishes.
- **Cancel** requests cancellation. The current model subprocess is terminated
  where supported, completed outputs remain, and remaining items become
  cancelled.
- Failed jobs name each failed track (up to five in the compact tray), its
  failed phase, and a concise actionable reason. They can be dismissed, and
  dismissal persists in that browser. Dismissing the job notification does
  not erase the track row's unresolved Error state.
- Restarting the backend re-queues jobs that were left running or queued.
  Already published outputs are skipped on resume unless replacement was
  requested.
- When a lyrics, instrumental, or stems stage finishes, the backend updates
  only that track's output flags and publishes the fresh row immediately. Job
  completion performs a database reconciliation only; it does not start a
  filesystem rescan. Use **Rescan library** explicitly for files added,
  removed, renamed, or changed outside the app.

Final output publication is atomic: data is written to a `.part` sibling and
renamed only after completion. A crash should not replace a valid output with a
truncated one.

### Processing history

Click **Processing history** in the application header.

- Runs are listed newest first with their workflow, profile, start time,
  duration, overall result, and completed/skipped/failed/cancelled counts.
- Filter runs by status or search by workflow, job number, or any source
  filename/path included in the run.
- Expand a run to load its track results. Track data is fetched in 50-row pages
  so opening History remains responsive even after very large batches.
- A failed run opens directly on its failed tracks. Each row shows the failed
  phase and a concise explanation; expand the explanation to read the complete
  worker error retained in the database.
- Filter or search inside one expanded run without changing the main history
  search. Use **← Library** to return to the same mounted Library filters,
  selection, folder expansion, and scroll position.
- History refreshes every 30 seconds while open. The normal jobs connection
  also performs a 30-second safety reconciliation, preventing a browser that
  slept through a WebSocket event from retaining stale Processing state.

### Mixer

Click **Mixer** or double-click a Library row.

- The original mix is always available. Generated instrumental and stem files
  appear as additional synchronized lanes.
- Each lane has a waveform, volume control, **M** (Mute), and **S** (Solo).
  Hover over M or S for a full explanation.
- Muting always silences that lane. Soloing one or more lanes silences all
  non-solo lanes.
- Click a waveform to seek.
- Drag across the overview strip to create a loop.
- **Karaoke preset** mutes `lead_vocals` when available, otherwise `vocals`,
  and leaves the backing lanes audible.
- LRC lyrics appear during playback and follow the active line/word.
- **Export mix…** renders the current gain/mute/solo state offline; it is not a
  real-time recording.

Export choices:

- WAV: signed 16-bit PCM at the source sample rate.
- MP3: resampled to at most 48 kHz because the browser MP3 encoder supports up
  to 48 kHz.

Mixer keyboard controls:

| Key | Action |
| --- | --- |
| Space | Play/pause when focus is not in an input |
| Left/Right | Seek by 1% of track duration when the lane area has focus |

### Lyric Editor

Click **Edit lyrics** for an existing LRC or **Create lyrics** when none exists.

The editor chooses the best available review source in this order:
`lead_vocals`, `vocals`, original mix.

- Click a lyric word to select it and centre the waveform view.
- Click a line or `[break]` band to select its complete timing section.
- The selected word, line, or break is shaded grey in the waveform.
- Solid word markers represent per-word timing.
- Dashed amber markers represent line and instrumental-break starts. They are
  independently draggable and cannot cross neighbouring timing boundaries.
- Drag a marker to retime it. On release, the editor plays a short section
  around the new timestamp.
- Select a word, then Shift+click the waveform to set its time. This also works
  for previously line-timed lyrics.
- Double-click the waveform to seek and play.
- Mouse wheel pans; Ctrl/Cmd+wheel zooms around the pointer.
- Drag the background of the line strip to create a loop. Double-clicking a
  line band loops that whole section. Escape clears the loop.
- **Add break** creates an instrumental section. Breaks are displayed as
  `[break]` but saved as bare timestamp lines in the LRC.
- **Re-time every word with AI** discards every existing line, break, and word
  marker and rebuilds an enhanced LRC for the complete song. Save or undo
  manual changes first.
- **Save** atomically replaces the canonical `.lrc`.
- **Save As…** writes `{name}.{suffix}.lrc` without replacing the canonical
  file.
- Unsaved edits are never autosaved. Back asks before discarding them.
- If a background job changes the open lyric file while manual edits exist,
  the editor offers **Load latest lyrics** rather than silently overwriting the
  editor state.

Lyric Editor keyboard controls:

| Key | Action |
| --- | --- |
| Space | Play/pause, or stamp the next word in Tap mode |
| T | Toggle Tap mode |
| `[` / `]` | Select previous/next word |
| Left/Right | Nudge selected word by 10 ms |
| Shift+Left/Right | Nudge selected word by 100 ms |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z or Ctrl+Y | Redo |
| Escape | Clear loop |

Tap mode compensates for a stored reaction offset. Use **Calibrate tap timing**
to measure your own offset instead of relying on the default.

### Lyrics and alignment

Lyrics are requested from LRCLIB first, then Musixmatch and NetEase fallback
adapters. A provider error or empty result moves to the next provider. No match
is reported as a skipped stage rather than a crash.

- Line-timed LRC: WhisperX aligns the supplied text against the original audio.
- Untimed/no LRC: WhisperX transcribes the complete song, then produces
  enhanced per-word timing.
- Enhanced LRC stores an inline `<mm:ss.xx>` timestamp before every word.
- Bare timestamp lines are hard instrumental-break boundaries and their exact
  whitespace/newline structure is preserved by normal parse/render editing.

Enhanced timing is something to review by listening. Model agreement is not a
guarantee of perceptually perfect timing.

### YouTube import

Click **Add from YouTube**.

1. Paste a video or playlist URL and click the probe action.
2. For a single video, review or edit Artist and Title.
3. For a playlist, select the desired entries. The first 100 are shown.
4. Optionally choose a preparation workflow to run after each download.
5. Start the import and follow it in the job tray.

A normal watch URL containing `&list=` is treated as the single video. A pure
playlist URL opens the playlist picker. Videos longer than 10 minutes, or with
unknown duration, are rejected before download.

Downloads use best audio and are transcoded to `{Artist} - {Title}.m4a`. The
downloads folder is rescanned and an optional follow-up preparation job is
submitted automatically. Playlist entries are submitted independently, so one
failure does not discard successful downloads.

Use only media you are legally permitted to download and transform.

### Themes and dialogs

System, Light, and Dark themes cover the Library, Mixer, Lyric Editor, job
tray, and dialogs. Dialogs do not close when their backdrop is clicked. Close
with the top-right X, Cancel/Close button, or Escape where supported.

## User guide

### Add or change a media folder while the app is running

1. Open **Settings**.
2. Browse or type the new media root.
3. Click **Add** and then **Save**.
4. Click **Rescan**.
5. Leave the dialog open or close it; scanning continues in the background.
6. Watch the Library count increase as scan batches are published.

### Find new or changed files

Click **Rescan library**. The scanner re-reads tags, duration, outputs, and LRC
state; discovers new files; and removes stale catalogue rows after a successful
root scan. It does not stop CPU/GPU processing jobs.

### Prepare a large batch efficiently

1. Filter by folder/search/columns.
2. Use the header checkbox to select the currently displayed result set, or
   select individual rows.
3. Click **Prepare selected**.
4. Use **Fast bulk** for a first pass, with replacement off.
5. Let the model remain loaded across the whole batch.
6. Review results in the Mixer and Lyric Editor.
7. Select only poor results and rerun with **High quality** and replacement on
   where needed.

Avoid running another GPU-heavy separator outside Karaoke Media Manager at the
same time. Competing model workloads may reduce throughput or exhaust GPU
memory.

### Repair missing tags and covers in a batch

1. Select tracks.
2. Choose **Tags and artwork**.
3. Leave replacement off to fill missing album/year/artwork.
4. Turn replacement on only when intentionally replacing existing metadata and
   covers.
5. Rows update as items complete and receive a final reconciliation at job end.

A completed job containing only **Skipped** items usually means those tracks
already had album, a plausible year, and embedded artwork while replacement was
off. It is not a stopped batch.

### Correct one track manually

1. Click **Tags**.
2. Correct Artist and Title first; these are the lookup identity.
3. Run **Auto-correct tags** or **Fetch artwork**.
4. Review the staged result.
5. Click **Save changes**.

Do not save the same track while its row is marked Processing in a metadata
batch.

### Download lyrics without AI timing

1. Choose **Lyrics and enhanced timing**.
2. Leave **Download lyrics** on.
3. Turn **Create enhanced per-word timing** off.
4. Start preparation.

This works without the WhisperX worker and normally produces the best timing
provided by the selected lyric service, often line-level LRC.

### Completely retime a lyric file

Open **Edit lyrics**, ensure there are no unsaved manual edits, and click
**Re-time every word with AI**. This is a destructive timing rebuild: the
existing line, word, and break timing is removed before WhisperX reconstructs
the enhanced file. The editor reloads the result when its specific job
finishes.

### Keep outputs separate from originals

1. Add a mirror root in Settings.
2. In Prepare selected, open **Advanced options**.
3. Choose **Save files → In the mirror library**.

The mirror reproduces the source's relative folder structure. Metadata and
artwork are the exception: they are embedded in the original FLAC/MP3/M4A by
explicit design and cannot be redirected to a mirror.

### Organise folders and files

Create a folder:

1. Click **+ New** above the folder tree.
2. Choose its parent location and enter a name.
3. Click **Create folder**. Empty folders remain visible after restarting the
   app, ready to receive tracks.

Move a track:

1. Drag the track row onto its destination folder. A blue folder highlight
   confirms the drop target.
2. If the destination is collapsed, keep hovering over it for about half a
   second to reveal its subfolders before dropping.

For keyboard use, or to move and rename in one operation, click **Move /
rename…**, choose a location, edit the filename if needed, then save. The
original audio, canonical and Save As lyrics, and app-named stems move together.
Mirror outputs retain the same relative folder layout.

Rename or delete a folder by selecting it, then using **Rename** or **Delete**
above the folder list. Configured media roots cannot be renamed or deleted
here; manage those in Settings. Deleting a folder moves the whole source
folder and its known mirror outputs to the Windows Recycle Bin, including any
unindexed files physically inside that folder, so the dialog always requires
explicit confirmation.

Move, rename, and folder deletion are refused while a library scan is active
or when the affected track/folder contains queued or running work. This keeps
worker input paths stable. Finish or cancel that work and retry.

### Delete a track safely

1. Click **Delete…** on the track row.
2. Leave **Also recycle generated stems and lyric files** selected to remove
   the app's known beside-original and mirror outputs with the source.
3. Click **Move to Recycle Bin** in the confirmation dialog.

The files are sent to the Windows Recycle Bin rather than permanently erased,
and the row disappears immediately after the operation succeeds. The backend
refuses deletion if the track belongs to a queued or running job, or while a
library rescan is active. Cancel or finish the job, or let the scan complete,
then retry. The app never falls back to permanent deletion if the Recycle Bin
is unavailable; it reports the error and leaves the row available for
inspection or retry.

## Files, outputs, and safety

### Output naming

For a source such as:

```text
D:\Music\ABBA\Dancing Queen.flac
```

possible outputs are:

```text
Dancing Queen.instrumental.mp3
Dancing Queen.vocals.mp3
Dancing Queen.lead_vocals.mp3
Dancing Queen.backing_vocals.mp3
Dancing Queen.drums.mp3
Dancing Queen.bass.mp3
Dancing Queen.guitar.mp3
Dancing Queen.piano.mp3
Dancing Queen.other.mp3
Dancing Queen.lrc
```

The selected model determines which stem names are available.

### What modifies originals

- Separation, lyric generation, and export create new files.
- Normal scanning is read-only.
- Tag and artwork editing changes metadata blocks inside original FLAC, MP3,
  and M4A files.
- Confirmed **Delete…** operations move the original and selected generated
  files to the Windows Recycle Bin.
- The encoded audio payload is not re-encoded during metadata writes.

Keep backups of irreplaceable media. Metadata writes are tested to preserve the
audio payload, but no software or storage device is a substitute for a backup.

### Replacement and resumability

With **Replace outputs that already exist** off, an existing complete output is
skipped. This makes interrupted and repeated batches resumable. With it on,
the selected workflow intentionally regenerates existing outputs or replaces
eligible metadata/artwork.

### Dialog and save safety

- Clicking outside a dialog does not close it.
- Lyric saves use atomic replacement.
- Media/stem outputs use atomic publication.
- Cancelling a job does not delete already completed files.
- Manual lyric edits remain unsaved until Save is clicked.
- Closing, refreshing, or navigating away from the app asks for browser
  confirmation while a job, YouTube download, library rescan, or track
  playback is active. Paused audio and finished work do not trigger it.

The browser supplies the confirmation wording; web applications are not
allowed to replace it with custom text. Choosing to leave closes only the
browser view. Backend jobs and downloads continue unless they were explicitly
cancelled.

## Large libraries

The current UI is designed for catalogues around 80,000 tracks:

- Scans run in the background and publish in batches.
- The folder tree starts collapsed.
- The Library table renders only the visible row window.
- Mixer/Lyric Editor navigation keeps the Library mounted, so Back restores
  the same working state instead of refetching and rebuilding every row.
- Column and search configuration persists in the browser.

The full track catalogue is still loaded once when entering the Library so the
folder tree and whole-result column operations remain available. That initial
catalogue fetch is the remaining operation that scales with total track count;
it is not repeated on every Mixer/Editor return.

## Troubleshooting

| Symptom | Meaning and action |
| --- | --- |
| Browser shows a blank page or API JSON only | Build the frontend with `npm run build`, then restart the backend and refresh the browser. |
| Port 8000 is already in use | Find the listener with `Get-NetTCPConnection -LocalPort 8000 -State Listen`; stop only the known Karaoke Media Manager process or choose another port. |
| App shows `cpu` despite an NVIDIA GPU | Run `nvidia-smi` in the same Windows session, update the driver, then restart the backend. |
| Demucs/UVR/WhisperX is unavailable | Run the matching `setup-worker-venvs.ps1 -Worker ...` command and restart the backend. Check `http://127.0.0.1:8000/api/system`. |
| UVR reports `failed finding central directory` | A first-use model download was interrupted. Current builds detect and remove an incomplete karaoke checkpoint automatically, then download it again on the next High quality run. |
| High-quality separation fails on a 5.1/surround source | Current builds probe the original and create a temporary 24-bit stereo WAV for the UVR model; the original remains untouched. Retry the failed track after updating/restarting the app. |
| A completed batch reports Failed | The other tracks may still have succeeded. Read the failed filenames and phases in the bottom tray, then find their persistent Error rows in the Library. A later retry clears each row only when that track has a newer successful result. |
| Enhanced timing cannot start | Install/repair the WhisperX worker, or turn off enhanced timing to download lyrics only. |
| First AI run appears slow | Model weights are downloaded on first use and cached. Watch the backend terminal and disk/network activity. |
| Tags/artwork batch says Completed but every item is Skipped | Replacement was off and the files already had album, plausible year, and artwork, or no confident online match was found. Use the job detail/error and enable replacement only if intentional. |
| Existing cover did not change | Confirm replacement was enabled. Refresh a browser tab that was open before an application update. Current artwork responses disable caching. |
| Manual tag save conflicts with a batch | Do not save the same track while it is marked Processing. Wait for that item to leave the running state. |
| Delete is disabled or rejected | The track is queued/processing or a library rescan is active. Finish or cancel the work, then retry. |
| Delete reports that the Recycle Bin is unavailable | The app does not permanently delete as a fallback. Check that the source drive/share supports the Windows Recycle Bin, or remove the file manually outside the app. |
| Move, rename, or folder delete is rejected | A scan is active, affected work is queued/running, or the destination already contains the same filename. Let work finish, choose another name/location, and retry. |
| WAV/AAC/OGG tag save fails | These formats are indexable but the metadata writer supports only FLAC, MP3, and M4A. |
| New files do not appear | Run Rescan library. Files starting with `.` and generated `{name}.{part}.mp3` outputs are intentionally not indexed as sources. |
| Rescan takes a long time | It is background work and reads metadata/duration for each source. Continue using the app; results appear incrementally. Avoid repeatedly starting new scans—the backend joins the active scan. |
| YouTube fails with a DPAPI/decryption error | Run the backend as the same Windows user as the browser, close/retry the browser, or export a Netscape `cookies.txt` and select file mode in Settings. |
| YouTube age-restricted video fails | Configure browser or file cookies. Verify the account can open the video normally. |
| A job stopped after an app/backend crash | Restart the backend. Running/queued jobs are re-queued, and existing outputs are skipped when replacement is off. |
| A completed job still appears to be processing | Wait up to 30 seconds for safety reconciliation or click Refresh in Processing history. Current builds reconcile even when the WebSocket still appears connected. |
| GPU jobs are unusually slow or fail for memory | Stop other Demucs/Whisper/AI GPU workloads. Use Fast bulk or a lighter model, process fewer tracks, and watch `nvidia-smi`. |
| Back from Mixer/Editor is still slow | Refresh the browser once to load the latest frontend. The current build preserves the mounted Library and virtualizes large results. |

Backend errors are written to the terminal that launched Uvicorn. Keep that
terminal available when diagnosing worker downloads, model errors, or invalid
media.

## Backup, update, and recovery

### Application data

By default, the SQLite catalogue and app settings live under:

```text
%USERPROFILE%\.karaoke-media-manager\library.db
```

UVR models also live beneath `%USERPROFILE%\.karaoke-media-manager` by default.
Override the data location before starting the backend:

```powershell
$env:KARAOKE_MM_DATA_DIR = 'D:\KaraokeManagerData'
Set-Location .\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

The database is an index/cache. If it is deleted, Settings and job history are
also lost, but tracks can be rebuilt by re-entering media roots and rescanning.
Back up the data directory if settings and history matter.

Instrumental provenance is also stored in this database. New processing writes
the chosen profile, engine/model, device, output mode, producing job, and file
signature when the output is published. On the first startup after upgrading,
a one-time conservative migration compares the current instrumental with
successful producer stages in retained job history. It labels only matches
whose output path and modification time agree; everything else stays **Ready**
rather than guessing. A later rescan preserves provenance while that exact file
is unchanged and clears it if the instrumental is replaced or removed.

Also back up:

- Original media, especially before large metadata updates.
- Beside-original or mirror stem/LRC outputs that would be expensive to
  regenerate.
- Any exported YouTube cookie file.

### Update an existing checkout

Stop the backend when no critical job is running, then:

```powershell
git pull --ff-only
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\frontend
npm install
npm run build
Set-Location ..
```

Rerun the worker setup only when worker dependencies changed or a worker check
fails. Start the backend again and refresh the browser.

If the backend is restarted while a job is queued/running, crash recovery
re-queues it. Although outputs publish atomically and normal runs are resumable,
prefer cancelling or waiting for a clean boundary before planned maintenance.

## Developer and API reference

### Development servers

Backend with automatic reload:

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Frontend development server, in another terminal:

```powershell
Set-Location .\frontend
npm run dev
```

Vite proxies `/api` and the jobs WebSocket to `http://localhost:8000`.

### Tests

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q

Set-Location ..\frontend
npm test
npm run build
```

Automated tests never install worker environments or run a real large GPU
batch.

### Useful endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Backend health |
| GET | `/api/system` | Detected device and worker availability |
| GET/PUT | `/api/settings` | Read/update application settings |
| POST/GET | `/api/rescan` | Start/read background scan |
| GET | `/api/tracks` | List/search indexed tracks |
| GET | `/api/recipes` | Available preparation workflows/options |
| POST | `/api/jobs` | Submit a job |
| GET | `/api/jobs` | Job summaries |
| GET | `/api/jobs/history` | Searchable, filtered, paged processing history |
| GET | `/api/jobs/track-failures` | Latest unresolved processing error per track |
| GET | `/api/jobs/{id}` | Job detail/items/stages |
| GET | `/api/jobs/{id}/items` | Searchable, filtered, paged track results for one job |
| POST | `/api/jobs/{id}/cancel` | Request cancellation |
| GET | `/api/tracks/{id}/parts` | Available original/output lanes |
| GET | `/api/audio/{id}` | Stream original audio |
| GET | `/api/audio/{id}/part/{part}` | Stream a generated part |
| GET/PUT | `/api/tracks/{id}/lrc` | Read/save lyrics |
| GET/PUT | `/api/tracks/{id}/artwork` | Read/save embedded artwork |
| POST | `/api/tracks/{id}/tags/suggest` | Fetch a reviewable tag/art suggestion |
| PUT | `/api/tracks/{id}/tags` | Save text tags |
| DELETE | `/api/tracks/{id}` | Move a source and optionally generated outputs to the Recycle Bin |
| POST | `/api/youtube/probe` | Inspect video/playlist metadata |
| POST | `/api/youtube/import` | Submit a YouTube import |
| WebSocket | `/api/ws/jobs` | Live job, scan, and track-update events |

Example PowerShell health and system checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/system
```

Example job submission:

```powershell
$body = @{
    recipe = 'karaoke'
    track_ids = @(1)
    options = @{
        processing_profile = 'balanced'
        fetch_lyrics = $true
        align_lyrics = $true
        device = 'auto'
        overwrite = $false
        output_mode = 'beside'
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/jobs `
    -ContentType 'application/json' `
    -Body $body
```

For further technical detail, see:

- [`docs/BULK_PROCESSING_PROFILES.md`](docs/BULK_PROCESSING_PROFILES.md)

## Security and licence

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Do not attach media, browser cookies, credentials, or personal library paths to
issues. Contributions are described in [CONTRIBUTING.md](CONTRIBUTING.md).

Karaoke Media Manager is licensed under the [MIT License](LICENSE). Third-party
tools, Python/npm packages, AI models, and downloaded media retain their own
licences and terms. Users are responsible for having the rights and permissions
required to download, process, modify, and share their media and lyrics.
