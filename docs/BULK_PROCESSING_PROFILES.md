# Bulk processing profiles

_Decision record: 2026-08-05_

## Outcome

KaraokeMixer now offers three processing profiles in **Prepare selected** and
runs multi-track jobs in model-specific phases. Demucs stays loaded while all
selected tracks are separated, is released, and then WhisperX stays loaded
while all eligible lyrics are timed. This makes a large selection a real batch
rather than a sequence of cold, unrelated model starts.

| Profile | Separation | Vocal treatment | Transcription | Intended use |
| --- | --- | --- | --- | --- |
| **Fast bulk** | `mdx` | remove all vocals | `base.en` | large first-pass preparation and triage |
| **Balanced** | `htdemucs` | remove all vocals | `small.en` | normal creator workflow; default |
| **High quality** | `htdemucs_ft` | UVR best lead-vocal removal | `medium` | final output where quality matters more than elapsed time |

For **All editable stems**, High quality also enables the optional UVR
lead/backing-vocal split. Expert model choices remain available under
**Advanced options** and override the selected profile.

The Whisper ASR choice only affects files that require transcription. A
line-timed LRC uses forced alignment and therefore does not become more
accurate merely by selecting a larger ASR model.

## Why this design

- Model startup was the primary avoidable bulk cost. KaraokeMixer previously
  started a new Python process and loaded Demucs or WhisperX for every track.
  The new job-scoped worker pool loads a selected model once and reuses it.
- Workers are scoped to one model phase within one job, not the lifetime of the
  application. Demucs is released before WhisperX starts, avoiding simultaneous
  model accumulation in GPU memory and preventing settings from one job leaking
  into another.
- Cancellation terminates the owned worker process tree and discards that
  worker. Completed per-track outputs remain published; subsequent tracks are
  marked cancelled. A later job starts a clean worker.
- A worker crash fails only the current track. The next track gets a fresh
  worker, preserving the queue's existing isolation and resumability.
- **Complete karaoke preparation** now derives fast/balanced stems and the
  instrumental from the same Demucs inference. Previously it separated the
  same source twice. High quality deliberately retains a separate UVR pass
  because it is a different specialist ensemble, not duplicate work.
- The UI leads with the three creator decisions and moves model names into
  Advanced options. This keeps the normal path light while retaining expert
  control.

## Safety and verification

- Every final media/LRC file continues through the existing atomic publish
  path. A cancellation or crash cannot replace a final output with a partial
  file.
- Existing-output skipping still happens per stage and per track, so rerunning
  a large selection resumes rather than starting from zero unless **Replace
  outputs that already exist** is selected.
- Automated verification completed with 442 backend tests and 586 frontend
  tests, plus a clean production frontend build.
- Persistent process reuse, multi-request response handling, cancellation and
  restart were tested with dependency-free workers. No real GPU benchmark was
  run while the separate legacy batch was active, to avoid competing for or
  destabilising the RTX 3060 workload.

## Operational guidance

- Do not run the legacy separator batch and a KaraokeMixer GPU batch together.
- Use **Fast bulk** to prepare a broad library, then review failures and weak
  results in the Mixer/Lyric Editor.
- Re-run only the tracks that need improvement with **High quality**. This
  two-pass creator workflow avoids spending UVR/medium-model time on tracks
  whose fast result is already satisfactory.
- Use **Balanced** when you want one unattended pass without subsequent triage.
