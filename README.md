# pt_cutlist

Turn a recorded personal-training session into a review-ready list of sections
to remove in Final Cut Pro — fully local, no cloud, no manual transcription.

Given the trainer's mic recording and the camera clip(s), it:

1. **Transcribes** the mic with `mlx-whisper` (Whisper large-v3-turbo) → SRT.
2. **Syncs** the mic to the camera timeline by audio cross-correlation, so cut
   timecodes land on your FCP timeline with no manual export or offset fiddling.
3. **Classifies** every spoken segment KEEP or CUT with a local Ollama model
   (coaching/exercise talk = KEEP; rest chatter, setup fumbling, off-topic = CUT).
4. **Outputs** `review.md`, `cuts.txt`, and an **FCPXML** with a marker at every
   proposed cut that you import to verify before committing.

Conservative by design: when a segment is ambiguous it is **kept**. It is worse
to drop coaching than to leave in filler.

## Requirements

- macOS, Apple Silicon (uses MLX)
- `uv`, Python 3.12 — `uv sync` to install deps
- `ffmpeg` + `ffprobe` on PATH
- Ollama running locally with the classification model pulled
  (default `qwen3.6:35b-a3b-coding-mxfp8`)
- Whisper weights: a local copy lives in `models/whisper-large-v3-turbo/`
  (`config.json` + `weights.safetensors`). If missing, download once with:
  `HF_HUB_DISABLE_XET=1 uv run hf download mlx-community/whisper-large-v3-turbo \
   --local-dir models/whisper-large-v3-turbo`
  (the `HF_HUB_DISABLE_XET=1` avoids the flaky Xet CDN.)

## Usage — one command per session

```sh
./process_session.sh <trainer_mic> <folder-or-camera-clips...>
```

Examples:

```sh
# mic + a folder of camera clips (sync anchor auto-detected):
./process_session.sh ~/Downloads/pt/28-may-26/REC00003.wav ~/Downloads/pt/28-may-26

# mic + explicit clips:
./process_session.sh mic.wav clipA.MP4 clipB.MP4 clipC.MP4
```

Or call the script directly:

```sh
WHISPER_MODEL=models/whisper-large-v3-turbo \
  uv run main.py mic.wav --sync-ref /path/to/clips_folder
```

Without `--sync-ref` it just transcribes + classifies a single file, with
timecodes in the file's own time (no timeline alignment).

### A second mic (e.g. the client's lav)

A second lav often can't be synced against the camera — it mostly captures
close-up breathing/movement, sharing only faint speech. Add it with
`--sync-mic`; it's aligned to the **primary mic** via a speech-band, log-scaled
correlation (it locks on the trainer's voice bleeding faintly into both), then
placed on the timeline as a second audio track in the FCPXML:

```sh
uv run main.py REC00003.wav --sync-ref clips_folder --sync-mic REC00001.wav
```

If a mic won't lock (too little shared speech), it's reported and skipped rather
than placed wrong.

## Outputs (written next to the mic file)

| file | what |
| --- | --- |
| `<mic>.srt` | full transcript with timeline-aligned timecodes |
| `review.md` | table of CUT ranges: start \| end \| duration \| reason |
| `cuts.txt` | plain `HH:MM:SS,mmm --> …` ranges, one per line |
| `cut_review.fcpxml` | the synced timeline with a marker at every proposed cut |

It also prints the total CUT duration and % of the session.

## Reviewing cuts in Final Cut Pro (verify before cutting)

Import `cut_review.fcpxml` (File ▸ Import ▸ XML). It creates a **new project**
— it does not touch your media or existing project. The timeline is the camera
clips with the mic(s) synced underneath.

Two styles (`--cut-style`):

- **`markers`** (default): a labelled marker at every cut, placed on the enabled
  primary storyline. Open **Timeline Index ▸ Tags** for a clickable list of all
  cuts, jump to each, and delete the spans you accept. Reliable and always
  visible.
- **`bladed`**: the timeline is pre-split at every cut and the cut segments are
  named "CUT: reason" and **disabled**, so you can scrub the edited result.
  Caveat: FCP greys disabled clips only subtly and **hides their markers/tags**,
  so cuts can be hard to spot — prefer `markers` unless you specifically want the
  black/silent preview.

### Carrying a visual review forward

Audio/transcript classification can't see the picture, so it may flag a cut
where you're actually mid-rep (a coach often chats while you work) or miss an
unrelated member dominating the frame. After eyeballing the cuts, restore any
false positives on a re-run with `--keep`:

```sh
uv run main.py REC00003.wav --sync-ref clips --sync-mic REC00001.wav \
  --keep 00:04:25 00:04:51 00:25:45 00:40:54
```

Each CUT range covering one of those timecodes is turned back into KEEP.

> The cut decisions are AI-generated. Always review before committing. Spans
> where the classifier timed out are left as KEEP, so they won't appear as cut
> markers — they simply stay in.

## How sync works

The mic and camera run on independent clocks, so the mic's t=0 is not the
timeline's 00:00:00. The tool computes a 100 fps energy envelope for the mic and
each camera clip, cross-correlates to locate each clip within the mic recording,
and fits a single `recorder-time → timeline-time` line across the whole session.
That line corrects both the constant offset **and** any clock drift between
devices. The camera clips are treated as sequential, contiguous timeline
segments (clip *i* starts at the cumulative duration of earlier clips); the
anchor (timeline 00:00:00) is auto-detected as the earliest clip.

The run prints `confidence` (median correlation peak) and `fit residual` (worst
deviation of any window from the fitted line = your alignment accuracy). A
residual of a few ms is sub-frame. If confidence is low or residual large, it
warns you to verify the first cues against FCP.

## Config (env or top of `main.py`)

- `WHISPER_MODEL` — HF repo id, or a local model dir (default the bundled copy)
- `OLLAMA_MODEL` — classification model (default `qwen3.6:35b-a3b-coding-mxfp8`)
- `OLLAMA_URL` — default `http://localhost:11434/api/generate`
- `BATCH_SIZE` — segments per Ollama call (default 10)
- `SILENCE_GAP_MS` — gaps larger than this between cues count as KEEP
  (likely silent exercise reps); default 5000

## Files

- `main.py` — pipeline: transcribe → parse → sync → classify → output → fcpxml
- `sync.py` — audio cross-correlation + recorder→timeline fit
- `fcpxml.py` — FCPXML generation + DTD validation
- `process_session.sh` — one-command wrapper for a session
- `.claude/skills/plan-video-demo/` — Claude Code skill (`/plan-video-demo`) that
  grills you through the plan + decision tree, runs the pipeline, then drives the
  visual verification pass

## Notes / known caveats

- **All local** — no external API calls at runtime.
- **Classification is defensive**: malformed/timed-out model output retries
  once, then defaults that batch to KEEP (conservative). A segment only flips to
  CUT on an explicit, valid `CUT` label.
- **Sub-second cuts** can appear; tighten by dropping ranges under a minimum
  length if you want a cleaner list.
- **Whisper artifacts**: on music/noise Whisper can hallucinate repeated tokens;
  these land as KEEP and don't produce cuts.
- The FCPXML is validated against Apple's bundled DTD (`FCPXMLv1.9`). DTD-valid
  doesn't guarantee FCP accepts every semantic edge case — test-import once on a
  new machine/FCP version.
