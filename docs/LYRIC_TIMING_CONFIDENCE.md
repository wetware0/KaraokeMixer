# Automatic lyric timing improvement and confidence

_Decision record: 2026-08-15_

## Outcome

Karaoke Mixer now has a bulk-safe **Improve lyric timing** workflow for tracks
that already have enhanced per-word LRC timing and a karaoke instrumental. It
does more than identify suspicious timing: it corrects word markers when the
audio evidence supports a change, preserves the existing marker when the
evidence is genuinely ambiguous, and narrows manual review to the remaining
words.

The confidence score is an evidence score from 0 to 100, not a claim of human
perceptual accuracy. The application never raises the score merely because it
made a correction.

## How it works

1. Forced-align the supplied lyric lines against the original mix.
2. Build a temporary vocal residual by subtracting the existing instrumental
   from the original, then align the same lines against that second acoustic
   view.
3. For each word:
   - if the two views agree within 250 ms and have acoustic scores, use their
     midpoint and mark the word **verified**;
   - if they disagree on the exact time but both prove the existing marker is
     at least 250 ms wrong in the same direction, apply their midpoint as a
     conservative repair and keep the word highlighted for review;
   - otherwise retain the existing marker and highlight it for review.
4. Reject the complete pass when either view matches less than 80% of the
   supplied words. The LRC remains unchanged.
5. Prevent corrections from reordering neighbouring words. A conflicting
   proposal is reverted and marked for review.

### Deep review (recommended)

The default profile adds a third signal: the Medium Whisper model transcribes
the complete song without being given the supplied lyric text. Its directly
matched words can corroborate either constrained alignment. A disputed word is
promoted only when:

- ASR directly matched that word rather than interpolating it;
- its marker is within 250 ms of the original-mix or vocal-residual alignment;
- the combined evidence passes the normal acoustic confidence gate; and
- the candidate is within two seconds of both the input timing and the
  pre-audit baseline.

The last rule prevents a repeated chorus or held word from becoming trusted
merely because two model paths chose the same distant occurrence. Larger
repairs may remain applied when supported, but stay visibly marked **Review**.
The report records the ASR coverage, corroborated-word count and large-shift
count separately.

When a valid, hash-bound dual-audio report already exists, Deep review reuses
that evidence and runs only the ASR pass. This makes the follow-up resumable and
avoids repeating two GPU alignments. Choose **Quick dual-audio review** when the
third transcription cost is not justified.

The two passes use the same WhisperX alignment model, so their agreement is
described as **dual-audio evidence**, not independent-model proof. This wording
is deliberate.

## Safety and portability

- Before the first correction, the workflow writes
  `{track}.before-confidence.lrc`. Later runs do not overwrite that original
  backup.
- The canonical LRC and reports are published atomically. If report publication
  fails, the prior LRC and sidecars are restored.
- `{track}.lyrics-quality.json` stores the compact, hash-bound track summary
  used by the Library.
- `{track}.lyrics-quality-details.json` stores per-word evidence and review
  targets. It is loaded only for the open Lyric Editor, not for every Library
  row.
- Moving, renaming, or deleting a track treats the backup and report files as
  related outputs.
- A manual edit invalidates the report immediately because it is bound to the
  exact LRC hash.

## Creator workflow

1. Filter **Instrumental** to **High Quality**.
2. Filter **Lyrics** to **Needs review** or **Not confidence checked**.
3. Select the desired rows and choose **Process… > Improve lyric timing**.
4. Watch the normal queue and Processing History. Each completed track reports
   its score, correction count, and remaining review words/lines.
5. The Lyrics column shows `Review N/100`. Hover it for the audit summary.
6. Open **Edit lyrics**. Pale amber words still need attention; use **Next
   review word** to jump directly between them.
7. Listen through the final result before choosing **Confirm High Quality
   timing**.

Use **Re-time every word with AI** for a complete reset of a fundamentally bad
or line-timed file. Use **Improve lyric timing** when useful enhanced timing
already exists and should be repaired conservatively.

## Known-bad ABBA pilot and production proof

The scratch pilot used `ABBA - ABBA - Does Your Mother Know` without modifying
the library copy. Both acoustic views matched all 361 lyric words. The evidence
score was 72/100, with 262 verified words, 99 words in 26 lines retained for
focused review, and 174 corrected word timings after the line-coherence rule.

The reported problem line changed from a compressed sequence around 46.7-47.7
seconds to:

```text
[00:46.73]<00:46.94>Ah,<00:47.85> but<00:48.61> girl,<00:49.12> you're<00:50.96> only<00:51.34> a<00:51.48> child
```

In particular, both views placed `only`, `a`, and `child` around 51 seconds,
roughly 3.6-3.8 seconds later than the old markers. The pilot therefore
demonstrates a real correction while correctly refusing to label the whole
track High Quality without listening review.

After the change passed the complete local and GitHub test gates and was
deployed, production Job 121 reproduced the scratch result on the real track
in 19 seconds. The Library row refreshed to `Review 72/100`, the per-word
report contains 361 entries, and the original LRC exists as
`.before-confidence.lrc`. Job 122 then began the same recoverable workflow for
the remaining 623 enhanced tracks with High Quality instrumentals; it is
recorded in Processing History and can be cancelled normally if needed.

Job 122 stopped with 622 completed tracks and one safe rejection: Queen's
`Seven Seas of Rhye…` matched only 45% in both views, so its LRC was left
unchanged. Across the completed tracks the first pass corrected 44,524 of
158,996 words, verified 62.6%, and left 37.4% for review. Six tracks met the
strict automatic High Quality gate. The median track score was 68/100. This
was valuable repair and triage, but not broad automatic certification.

The Deep review scratch proof then used real, copied production inputs without
changing the library:

- `Does Your Mother Know`: 72 -> 77, review words 99 -> 75, with 34 ASR
  corroborations and nine large shifts deliberately retained for review.
- `Little Things`: 91 -> 92, review words 6 -> 1, with five ASR
  corroborations and no large shifts.

The persistent worker loaded Medium once and reused it for the second track.
Gain-calibrating the instrumental subtraction was also tested first, but it did
not consistently improve agreement and was rejected.

## Why this design was chosen

- Bulk replacement from one model can make a whole library consistently wrong.
- Pure detection still leaves too much manual work.
- Dual-audio agreement safely automates the strongest corrections, while the
  same-direction rule repairs obvious gross errors without pretending their
  exact timing is verified.
- A score plus word-level review targets is more useful to a karaoke creator
  than a binary Ready/Not Ready flag.
- The separate detail file keeps the 80,000-track Library fast.
- Retaining the pre-confidence LRC makes bulk use recoverable.
- Independent ASR is spent only on the remaining uncertainty, while the
  two-second baseline gate prevents confidence inflation from repeated lyrics.

Automatic **High Quality** remains deliberately strict: every word must be
verified and the track score must be at least 85/100. A human listening
confirmation can still certify a lower-scoring track after its flagged words
are reviewed.
