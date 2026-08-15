# Human-reference lyric timing evaluation

_Decision record: 2026-08-15_

## Purpose

Two listening-corrected ABBA tracks were compared with their automatic timing
inputs. The manually edited LRCs are treated as local evaluation references,
not as training data and not as proof that every unedited word is perfect.
The song text is not copied into the repository.

## Results

| Track and method | Mean absolute word error | Median absolute word error | Words over 250 ms | Words over 500 ms | Words over 1 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Does Your Mother Know — old dual-audio result | 1.317 s | 1.320 s | 304 / 361 | 288 | 251 |
| Does Your Mother Know — best exploratory isolated-vocal candidate | 0.098 s | 0.040 s | 39 / 361 | 11 | 2 |
| Does Your Mother Know — selected deterministic production method | 0.148 s | 0.052 s | 59 / 361 | 35 | 7 |
| Chiquitita — existing input before confidence repair | 0.073 s | 0.020 s | 27 / 304 | 9 | 2 |
| Chiquitita — unguarded isolated-vocal candidate | 0.146 s | 0.030 s | 34 / 304 | 18 | 12 |

The isolated-vocal method fixed the broadly displaced Does Your Mother Know
timing. The lowest-error exploratory run used Demucs' default random time
shift, so it was not selected as the production basis. A bounded benchmark
measured deterministic no-shift at 148 ms mean error, fixed one-shift trials
at 147 ms and 247 ms, and a fixed two-shift trial at 155 ms. No-shift was
chosen because it was reproducible, nearly tied the best fixed result, and did
not multiply bulk separation cost. Chiquitita was already close: most proposed changes were harmless, but
twelve isolated word matches jumped by more than one second. This proves that
the application must decide whether the whole track needs replacement and
must reject isolated outliers even when the median result looks strong.

The older dual-audio `gross_directional` rule was also measured directly on
Chiquitita. Its 39 corrections averaged 783 ms from the human timing; 23 were
more than 500 ms away. Words whose existing marker was retained averaged only
54 ms away. Same-direction disagreement is therefore retained as review
evidence but is no longer allowed to move a word without independent support.

## Production decision

**High Accuracy — isolated vocal** is the default Improve lyric timing profile:

1. Demucs `htdemucs_ft` creates a temporary vocal stem from the lossless
   original. The existing lossy instrumental is not subtracted.
2. Whisper Medium transcribes that vocal without being given the lyrics.
3. Ordered matching places each supplied lyric line in the song.
4. WhisperX force-aligns the exact supplied words inside those discovered
   windows.
5. A second local match prevents identical choruses from attaching to another
   occurrence.
6. If the candidate and input differ by a median of no more than 100 ms, the
   input is preserved. Agreement becomes confidence evidence rather than a
   reason to rewrite an already-good file.
7. A line with fewer than 50% direct transcript anchors remains Review. If the
   track globally agrees, its input markers are preserved; if the track is
   broadly displaced, exact forced markers are applied but are not certified.
   On locally stable lines, a word more than one second from the line's median
   movement is retained as an isolated outlier.
8. Automatic line markers use the first word onset. This matches both human
   reference files and avoids inventing a fixed pre-roll.

For timing work, Demucs' random shift is disabled. This trades at most a small
stem-separation improvement for repeatable timestamp evidence; normal karaoke
instrumental processing keeps its existing quality setting.

Demucs and Whisper run as separate batch phases. The queue releases the Demucs
worker before loading Whisper, preventing both model families from occupying
GPU memory together while still keeping each model warm across a large batch.

The legacy Deep and Quick profiles remain available for comparison. Quick is
the cheaper dual-audio audit. Legacy Deep retains its original three-signal
implementation but is no longer the recommended default.

## Limits and next evidence

Two songs expose important failure modes but are not a representative catalogue.
Before automatic High Quality certification is relaxed, retain at least ten to
twenty listening-corrected references covering male and female singers,
backing-vocal-heavy choruses, spoken introductions, duets, held notes, sparse
arrangements and repeated outros. Evaluate changes by word error and by line,
not by transcript coverage alone.

Human confirmation remains the release gate. A low numerical error can still
feel wrong on a held syllable or when a karaoke display changes words at an
unnatural phonetic boundary.
