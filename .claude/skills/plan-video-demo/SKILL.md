---
name: plan-video-demo
description: Plan and produce a review-ready cut list + Final Cut Pro FCPXML from a raw personal-training session (camera clips + mic recordings), using the pt_cutlist tool. Grills the user through the demo plan and the technical decision tree — probing the media files to auto-resolve anything discoverable instead of asking — then ALWAYS runs a visual verification pass, because coaches narrate off-topic through working sets and audio-only cuts over-cut footage of the client training. Use when turning a PT session recording into an FCP cut list, or when the user mentions planning/cutting the video demo.
---

# Plan & produce a PT video-demo cut

Turn a raw PT session into a review-ready cut list and an FCPXML for Final Cut
Pro with the `pt_cutlist` tool (`main.py` / `sync.py` / `fcpxml.py`). Pipeline:
transcribe the trainer mic → sync to the camera timeline → classify KEEP/CUT →
merge → write `review.md`, `cuts.txt`, `cut_review.fcpxml` → **visually verify**.

## Operating principle (grill-me)

Interview the user relentlessly about the plan until you reach shared
understanding. Walk the decision tree one branch at a time, resolving
dependencies in order. Two rules layered on top:

- **If a question can be answered by probing the media files, probe — don't
  ask.** Which recording is the trainer mic, the frame rate, the embedded
  timecode, whether a second mic will sync: these are facts in the files. Resolve
  them with `ffprobe` / `sync.build_model`, then *tell* the user what you found.
- **For every question you do ask, lead with your recommended answer** (first
  option, marked recommended). Reserve questions for genuine plan/editorial
  forks the files can't settle.

Front-load all of this **before** the long steps (transcription ~5 min,
classification ~30–60 min) so you never burn an hour on the wrong mic or model.

## Phase 1 — Grill the demo plan (intent shapes the cuts)

Before any processing, resolve what the demo is *for* — it changes how
aggressively to cut and what to protect. Ask these in a **single
`AskUserQuestion` call**, recommended option first (per grill-me, lead with your
recommendation):

```
Q1  header: "Purpose"    "What is this demo for?"
    - Trainer marketing reel            (Recommended)
    - Client testimonial
    - Technique reference / coaching library
    - Full-session record

Q2  header: "How tight"  "How aggressively should I cut?"
    - Tight — cut all rest + off-topic, keep coaching & active reps   (Recommended)
    - Highlights only — just the strongest coaching moments
    - Light — only dead air and obvious filler

Q3  header: "Tone"       "Keep rapport, or coaching-only?"
    - Keep a little banter for personality   (Recommended)
    - Coaching-only — cut all banter
```

Then ask, free-form: **anything specific to definitely keep or remove?** (a
featured exercise, banter to preserve, names/personal info to strip).

Record every answer — they govern the "genuine rest vs keep the footage" calls
in Phase 3, and how aggressively the exercise-overlap fork leans toward cutting.

## Phase 2 — Resolve the technical tree (probe-first, ask rarely)

1. **Locate inputs.** Find the session folder; separate camera clips (video)
   from mic recordings (audio). Probe durations. Don't ask where files are if you
   can find them.
2. **Which recording is the trainer/coaching mic?** PROBE — never ask. Run
   `sync.build_model(mic, camera_clips)` on each candidate audio file: the
   trainer mic locks cleanly (low residual, sensible offset, low drift); the
   client lav produces garbage (huge residual). Transcribe the trainer mic.
   Report which you picked and the lock quality.
3. **Models.** Recommend the local whisper weights
   (`models/whisper-large-v3-turbo`, via `WHISPER_MODEL`) and Ollama
   `qwen3.6:35b-a3b-coding-mxfp8`. Only ask/flag if missing.
4. **Timeline sync.** Auto by default — contiguous camera clips, anchor =
   earliest clip, single offset+drift fit. Report offset / drift / residual; a
   few-ms residual is sub-frame. Only surface a question if the lock is weak
   (low confidence or large residual).
5. **Second mic (client lav)?** If present, add it with `--sync-mic`; it aligns
   to the trainer mic via the speech-band method (it shares only faint speech).
   Report the offset, or say it was skipped if it won't lock. No question.
6. **Cut style.** Recommend `--cut-style markers` — FCP greys disabled (bladed)
   clips subtly and **hides their markers/tags**, so markers are the reliable,
   visible review surface.
7. **Run:**
   `WHISPER_MODEL=models/whisper-large-v3-turbo uv run main.py "<trainer_mic>"
   --sync-ref "<session_folder>" --sync-mic "<client_lav>" --cut-style markers`

## Phase 3 — ALWAYS visually verify (non-negotiable)

Audio classification cannot see the picture, and **coaches narrate off-topic
while the client works** — so a large share of "off-topic" cuts are footage of
the client mid-rep (one session: ~half the cut time). Never ship the first-draft
cut list without this pass.

- Map timeline → (camera clip, offset). Extract labeled frames at each CUT
  range's midpoint (and several points for long cuts); tile into contact sheets
  with Pillow; read them.
- Flag **false positives**: any cut where the client is performing or the coach
  is demonstrating. Also scan KEEP ranges for shots an unrelated gym member
  dominates (cuts you might *add*).
- **Also sample long silent KEEP gaps.** A silent gap defaults to KEEP ("quiet
  reps"), and `flag_camera_motion` auto-cuts the ones where the camera is clearly
  being carried/repositioned — but it is deliberately conservative (cut only when
  >35% of frames show global pan, so it never drops reps) and **will miss brief
  or low-texture repositioning** (camera pointed at a wall/floor reads as static).
  So extract frames across every silent KEEP gap ≥8s too, and **cut the dead
  ones** — walking with the camera, repositioning between sets, aiming/adjusting,
  nothing happening. These are the inverse of false positives: footage the audio
  pass had no speech to cut on. Tune borderline rigs with `PT_CAMERA_MOVING_FRAC`.
- **Default rule — mute the audio, don't cut the whole section, whenever the
  video is worth keeping.** A span gets fully cut (video + audio) ONLY when both
  are unwanted (genuine rest: standing/sitting/chatting, empty gym, an unrelated
  member). If the client is training / mid-rep / the shot is otherwise good but
  the *audio* is off-topic, **restore the video to KEEP and add the audio to
  `mute_spans`** — never drop the footage just to lose the talk.
- So the editorial fork for exercise-overlap cuts collapses to: **restore to KEEP
  + mute the off-topic audio** [default], or — only if the footage itself is also
  worthless — **cut anyway**. Don't keep unwanted audio playing.
- Apply with `--keep <timecodes…>` on a re-run, or regenerate `review.md` /
  `cuts.txt` / `cut_review.fcpxml` directly with the surviving cuts.

## Outputs & review

Written next to the trainer mic: `<mic>.srt`, `review.md` (cut table),
`cuts.txt` (in/out list), `cut_review.fcpxml`. The user imports the FCPXML via
**File ▸ Import ▸ XML** (new project, nothing destructive) and reviews via
**Timeline Index ▸ Tags**.

## Phase 4 — The finished cut (`main.build_final_cut`)

Once cuts are reviewed (in `review.md`) and any off-topic-over-exercise spans are
chosen, produce the finished edit with `main.build_final_cut(folder, trainer_mic,
user_mic, mute_spans, dissolve_s)`. It writes `final_cut_dissolves.fcpxml` (+ a
hard-cut fallback) with the **entire audio chain handled automatically** — these
were all hard-won and must stay automatic:

- **DJI camera audio silenced.** The Pocket's mic is across the room, so it lags
  and beats against the lavs (echo). `srcEnable="video"` is *ignored by some FCP
  versions* — so `build_applied` also forces `adjust-volume=-96dB` on every spine
  clip. The lavs are the only audio.
- **Drift correction.** Each recorder's clock runs ~0.5 ms/min off the camera;
  `Audio.slope` re-derives every audio segment's in-point from the full sync
  mapping so audio tracks the picture (no creeping lip-sync). Note: lav-to-camera
  sync is ultimately ~1 frame — the limit of independent recorders.
- **Mic gate** (`sync.compute_mic_gate`). Two open lavs of the same room comb →
  echo, so never play both. The trainer's lav plays by default; the client's lav
  plays only when *they* are the louder speaker (their own close mic). No echo,
  and the client's voice is present. ~128 switches/session land on pauses; add
  crossfades only if clicks are audible.

- **Sanitize EVERY audio source, not just the trainer's mic** (hard lesson).
  The mute spans were originally built only from the *trainer's* transcript — but
  the gate then plays the *client's* lav during their replies, exposing their
  off-topic talk (e.g. personal/family news, off-topic banter). Conversations are
  two-sided and **timed differently on each mic**, so the client's off-topic
  won't line up with the trainer-based mutes. Therefore: **transcribe BOTH lavs**,
  and for the client lav scan the cues that are *gated (they play) AND not already
  muted AND not cut* for off-topic/sensitive content, and mute those spans too.
  Mutes silence whichever mic plays in the span, so adding the client's off-topic
  spans covers it. Re-check after every gate change.
- **Cross-dissolves** at the cuts (0.5s), using the exact built-in Cross Dissolve
  format (verified against a real FCP export).

`fcpxml.validate()` checks DTD **and** that every referenced media file resolves
(catches the double-encoding bug class). File URLs must be single-encoded
(`pathname2url` only — never `quote()` on top).

## Gotchas baked in from experience

- DJI clips carry **drop-frame embedded timecodes**; the FCPXML must set each
  spine clip's `start`/`tcFormat` from them or FCP rejects the edit ("no
  respective media"). `fcpxml.py` handles this via `parse_timecode`.
- Whisper hallucinates repeated tokens over quiet/music ("Thank you.", "tat tat
  tat") — harmless, lands as KEEP.
- HuggingFace's Xet CDN stalls here; download whisper weights with
  `HF_HUB_DISABLE_XET=1`.
- Conservative bias: when a segment is ambiguous, KEEP. It is worse to drop
  coaching (or footage of the client training) than to leave in filler.
