"""PT session transcript -> Final Cut Pro cut list.

Transcribes an hour-long personal-training session and produces a review-ready
list of sections to remove (rest chatter, setup fumbling, off-topic talk).

Pipeline: extract audio -> transcribe (SRT) -> parse -> classify KEEP/CUT
via local Ollama -> merge into ranges -> cut silent camera-repositioning gaps
the audio can't see (flag_camera_motion) -> write review.md + cuts.txt.

Conservative by design: when in doubt, KEEP. It is worse to drop coaching
than to leave in filler — and the camera-motion pass only cuts a silent gap
when the *camera itself* is clearly moving, never a static gap (quiet reps).

Usage:
    uv run main.py path/to/session.mp4
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# --- config ---------------------------------------------------------------

# HF repo id by default; override with WHISPER_MODEL to point at a local dir
# (containing config.json + weights.safetensors), e.g. on a flaky network.
WHISPER_MODEL = os.environ.get(
    "WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.6:35b-a3b-coding-mxfp8")
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10            # segments per classification call
SILENCE_GAP_MS = 5000      # gaps larger than this between cues -> KEEP (silent reps)
# A silent gap is *usually* the client doing quiet reps (keep it). But it can
# also be dead footage — the camera being picked up, carried, and repositioned
# between sets (also silent, but useless). Audio can't tell them apart; the
# video can: a mounted camera barely changes frame-to-frame, a carried one
# changes wholesale. We probe long silent gaps and cut the ones in motion.
CAMERA_MOTION_MIN_GAP_S = 8.0     # only inspect silent KEEP gaps at least this long
# We detect *global* camera translation (the whole frame shifting together) via
# FFT phase correlation between successive downscaled frames — NOT raw pixel
# change, which a subject moving in a fixed frame also produces (explosive reps
# read as high pixel-change but near-zero global translation). A frame-pair
# whose global shift is >= CAMERA_PAN_PX counts as "the camera moved"; if more
# than CAMERA_MOVING_FRAC of a silent gap's pairs moved, it's repositioning, cut.
CAMERA_PAN_PX = 2.0               # global shift (px, at the 96x54 proxy) = "moved"
# Conservative by design (prime directive: never drop reps). In testing explosive
# reps peaked at ~0.24 and real repositioning ran 0.26–0.52, so 0.35 cuts clear
# repositioning without touching reps. Rig/scene vary — tune per the [motion] log
# line (prints the measured fraction for every gap) via PT_CAMERA_MOVING_FRAC.
CAMERA_MOVING_FRAC = float(os.environ.get("PT_CAMERA_MOVING_FRAC", "0.35"))
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".flac", ".aac"}


@dataclass
class Segment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    label: str = "KEEP"        # KEEP | CUT
    reason: str = ""


@dataclass
class Range:
    start_ms: int
    end_ms: int
    label: str
    reason: str = ""


# --- timecodes ------------------------------------------------------------

def ms_to_tc(ms: int) -> str:
    """Milliseconds -> HH:MM:SS,mmm (SRT / FCP-readable)."""
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def tc_to_ms(tc: str) -> int:
    """HH:MM:SS,mmm -> milliseconds."""
    hms, _, millis = tc.strip().replace(".", ",").partition(",")
    h, m, s = hms.split(":")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(millis or 0)


# --- step 1: audio extraction + transcription -----------------------------

def extract_audio(src: Path) -> Path:
    """Extract 16kHz mono wav from a video file via ffmpeg."""
    out = src.with_name(f"{src.stem}.16k.wav")
    print(f"[audio] extracting 16kHz mono wav -> {out.name}")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-vn", str(out)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def transcribe(audio: Path, srt_path: Path) -> None:
    """Transcribe audio to SRT with mlx-whisper. Skips if a non-empty SRT already
    exists (re-runs after tuning the model/cuts shouldn't redo the ~5-min pass)."""
    import mlx_whisper

    if srt_path.exists() and srt_path.stat().st_size > 0:
        print(f"[whisper] {srt_path.name} exists — skipping transcription "
              f"(delete it to force a re-transcribe)")
        return
    print(f"[whisper] transcribing with {WHISPER_MODEL} (this takes a while)...")
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=WHISPER_MODEL,
        verbose=False,
    )
    write_srt(result["segments"], srt_path)
    print(f"[whisper] wrote {srt_path.name} ({len(result['segments'])} cues)")


def write_srt(segments: list[dict], srt_path: Path) -> None:
    """Write cues, skipping whisper's empty / zero-duration artifacts so the
    SRT holds only real cues (and parse counts match)."""
    lines = []
    i = 0
    for seg in segments:
        text = seg["text"].strip()
        start_ms = int(seg["start"] * 1000)
        end_ms = int(seg["end"] * 1000)
        if not text or end_ms <= start_ms:
            continue
        i += 1
        lines.append(f"{i}\n{ms_to_tc(start_ms)} --> {ms_to_tc(end_ms)}\n{text}\n")
    srt_path.write_text("\n".join(lines), encoding="utf-8")


# --- step 2: parse SRT ----------------------------------------------------

TC_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})")


def parse_srt(srt_path: Path) -> list[Segment]:
    """Block-based SRT parse: one cue per blank-line-separated block. Robust to
    empty text and odd spacing; reads every cue in the file."""
    raw = srt_path.read_text(encoding="utf-8")
    segs = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.splitlines()
        if len(lines) < 2 or not lines[0].strip().isdigit():
            continue
        tc = TC_RE.search(lines[1])
        if not tc:
            continue
        start, end = tc.groups()
        text = " ".join(" ".join(lines[2:]).split())
        segs.append(Segment(
            index=int(lines[0].strip()),
            start_ms=tc_to_ms(start),
            end_ms=tc_to_ms(end),
            text=text,
        ))
    return segs


# --- step 3: classify via Ollama ------------------------------------------

SYSTEM_PROMPT = """\
You are editing the transcript of a personal-training (PT) session to help an \
editor cut it down. For each numbered segment decide KEEP or CUT.

KEEP = coaching: instruction, tips, technique cues, feedback, motivation, \
counting reps, or any talk while performing an exercise.
CUT  = rest chatter, equipment fiddling, setup fumbling, off-topic / personal \
talk, long filler with no training content.

Rules:
- Default to KEEP when ambiguous. NEVER CUT a segment you are unsure about.
- It is far worse to cut coaching than to leave in filler.

Return ONLY a JSON array, no prose, no markdown fences. One object per input \
segment, in order:
[{"index": <int>, "label": "KEEP"|"CUT", "reason": "<short>"}]"""


# Enforce the array shape via Ollama structured output. Plain `format: "json"`
# lets some models (e.g. gemma4) emit a single object instead of the per-segment
# array, which then parses as "no labels" -> everything defaults to KEEP.
CLASSIFY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "label": {"type": "string", "enum": ["KEEP", "CUT"]},
            "reason": {"type": "string"},
        },
        "required": ["index", "label", "reason"],
    },
}


# Per-batch stall monitoring. A healthy classify call streams tokens
# continuously and finishes in a few seconds (qwen ~3s warm). Two failure modes
# seen in practice: a hung request (no tokens at all) and a model looping on junk
# tokens (gemma4). STALL_S bounds the first (read timeout = max gap between
# tokens; generous enough to cover a cold model load before the first token).
# BATCH_BUDGET_S bounds the second (total wall-clock for one batch).
STALL_S = 60.0
BATCH_BUDGET_S = 150.0


def _consume_stream(lines, budget_s: float, now=time.monotonic) -> str:
    """Join an Ollama streaming response, aborting if the batch overruns
    `budget_s` (a model stalling/looping). Raises requests.Timeout on overrun so
    the caller's normal request-failure handling (retry, then default-KEEP)
    catches it. `now` is injectable for testing."""
    start = now()
    parts = []
    for line in lines:
        if not line:
            continue
        obj = json.loads(line)
        parts.append(obj.get("response", ""))
        if obj.get("done"):
            break
        if now() - start > budget_s:
            raise requests.exceptions.Timeout(
                f"classify batch exceeded {budget_s:.0f}s — model stalling, aborting")
    return "".join(parts)


def _ollama(prompt: str, n: int) -> str:
    # Bound the array to exactly `n` items: an unbounded array schema lets some
    # models (gemma4) never emit the closing `]` and generate thousands of junk
    # objects until timeout. Fixing the count forces the grammar to terminate.
    schema = {**CLASSIFY_SCHEMA, "minItems": n, "maxItems": n}
    # Stream so a stall is caught mid-flight: the (connect, read) timeout aborts
    # if no token arrives for STALL_S, and _consume_stream caps total wall-clock.
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": True,
            "format": schema,
            # No chain-of-thought: this is a direct labelling task, and on a
            # hybrid model (qwen3.6) reasoning tokens eat the whole token budget
            # and leave the response empty. Off = direct JSON, and faster.
            "think": False,
            # ~64 tokens/segment is ample for index+label+short reason; also a
            # hard backstop against a single runaway "reason" string.
            "options": {"temperature": 0, "num_predict": 64 * n + 128},
        },
        stream=True,
        timeout=(10, STALL_S),
    )
    resp.raise_for_status()
    with resp:
        return _consume_stream(resp.iter_lines(), BATCH_BUDGET_S)


def _extract_json_array(text: str) -> list | None:
    """Pull the first JSON array out of a model response, defensively."""
    text = text.strip()
    # `format: json` may wrap the array in an object, e.g. {"results": [...]}
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass
    # Fallback: grab the outermost [...] span.
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def classify_batch(batch: list[Segment]) -> None:
    """Classify one batch in place. Defaults to KEEP on any failure."""
    numbered = "\n".join(f"{s.index}: {s.text}" for s in batch)
    prompt = f"Classify these {len(batch)} segments:\n\n{numbered}"

    parsed = None
    for attempt in range(2):  # one retry on bad JSON
        try:
            parsed = _extract_json_array(_ollama(prompt, len(batch)))
        except requests.RequestException as e:
            print(f"  [warn] ollama request failed: {e}")
            parsed = None
        if parsed is not None:
            break

    if parsed is None:
        print(f"  [warn] batch {batch[0].index}-{batch[-1].index}: "
              f"unparseable output, defaulting all to KEEP")
        return

    by_index = {s.index: s for s in batch}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        seg = by_index.get(item.get("index"))
        if seg is None:
            continue
        label = str(item.get("label", "KEEP")).upper().strip()
        if label == "CUT":          # only flip on an explicit, valid CUT
            seg.label = "CUT"
            seg.reason = str(item.get("reason", "")).strip()


def classify(segments: list[Segment]) -> None:
    total = len(segments)
    for i in range(0, total, BATCH_SIZE):
        batch = segments[i:i + BATCH_SIZE]
        print(f"[classify] {i + len(batch)}/{total} segments", end="\r")
        classify_batch(batch)
    print(f"[classify] {total}/{total} segments — done")


# --- step 4: merge into ranges --------------------------------------------

def merge_ranges(segments: list[Segment]) -> list[Range]:
    """Collapse adjacent same-label segments into contiguous ranges.

    Gaps > SILENCE_GAP_MS between cues are treated as KEEP (silent reps).
    The result tiles the whole timeline with no gaps or overlaps.
    """
    if not segments:
        return []

    ranges: list[Range] = []

    def push(start_ms: int, end_ms: int, label: str, reason: str) -> None:
        if end_ms <= start_ms:
            return
        if ranges and ranges[-1].label == label:
            ranges[-1].end_ms = end_ms  # extend same-label neighbour
            if label == "CUT" and reason and reason not in ranges[-1].reason:
                ranges[-1].reason = f"{ranges[-1].reason}; {reason}".strip("; ")
        else:
            ranges.append(Range(start_ms, end_ms, label, reason))

    cursor = segments[0].start_ms
    for seg in segments:
        if seg.start_ms - cursor > SILENCE_GAP_MS:
            push(cursor, seg.start_ms, "KEEP", "")  # silent gap -> likely reps
        push(max(seg.start_ms, cursor), seg.end_ms,
             seg.label, seg.reason if seg.label == "CUT" else "")
        cursor = max(cursor, seg.end_ms)

    return ranges


def _camera_pan_px(clip: Path, off_s: float, dur_s: float,
                   fps: int = 4) -> list[float]:
    """Per-frame-pair global translation magnitude (px) over [off_s, +dur_s] of
    `clip`, via FFT phase correlation on 96x54 gray frames. A panning/carried
    camera shifts the whole frame (clear correlation peak off-centre); a subject
    moving in a fixed frame does not. Returns [] if fewer than 2 frames decode."""
    import numpy as np
    W, H = 96, 54
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{off_s:.3f}", "-t", f"{dur_s:.3f}",
         "-i", str(clip), "-vf", f"fps={fps},scale={W}:{H},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True).stdout
    n = len(raw) // (W * H)
    if n < 2:
        return []
    f = np.frombuffer(raw[:n * W * H], np.uint8).reshape(n, H, W).astype(np.float32)
    mags = []
    for a, b in zip(f[:-1], f[1:]):
        A = np.fft.rfft2(a - a.mean())
        B = np.fft.rfft2(b - b.mean())
        R = A * np.conj(B)
        R /= np.abs(R) + 1e-8
        c = np.fft.irfft2(R, s=a.shape)
        py, px = np.unravel_index(int(np.argmax(c)), c.shape)
        dy = py - (H if py > H // 2 else 0)      # wrap negative shifts
        dx = px - (W if px > W // 2 else 0)
        mags.append(float((dx * dx + dy * dy) ** 0.5))
    return mags


def flag_camera_motion(ranges: list[Range], model, videos: list[Path],
                       pan_fn=_camera_pan_px) -> list[Range]:
    """Flip long *silent* KEEP gaps to CUT when the camera is being carried /
    repositioned between sets — dead footage that audio classification can't see
    (no dialogue) and that the 'silent gap = quiet reps' default wrongly keeps.
    Static-camera silent gaps (real quiet reps) stay KEEP. Needs the sync model
    to map timeline time -> clip + in-clip offset.
    """
    by = {Path(p).name: p for p in videos}
    clips = model.clips                              # [(name, timeline_start_s)]
    starts = [s for _, s in clips] + [model.timeline_end]
    durs = [starts[i + 1] - starts[i] for i in range(len(clips))]

    flipped = 0
    for r in ranges:
        # only untouched silent gaps (KEEP with no classifier reason)
        if r.label != "KEEP" or r.reason:
            continue
        a, b = r.start_ms / 1000.0, r.end_ms / 1000.0
        if b - a < CAMERA_MOTION_MIN_GAP_S:
            continue
        # a gap can straddle a clip boundary (DJI auto-splits mid-recording) —
        # collect pan magnitudes across every clip slice it covers.
        mags = []
        for (name, start), dur in zip(clips, durs):
            lo, hi = max(a, start), min(b, start + dur)
            if hi - lo >= 1.0 and name in by:
                mags += pan_fn(by[name], lo - start, hi - lo)
        if not mags:
            continue
        frac = sum(1 for m in mags if m >= CAMERA_PAN_PX) / len(mags)
        moving = frac >= CAMERA_MOVING_FRAC
        print(f"[motion] silent gap {ms_to_tc(r.start_ms)}–{ms_to_tc(r.end_ms)} "
              f"moving={frac:.2f} (thresh {CAMERA_MOVING_FRAC:.2f}) -> "
              f"{'CUT camera repositioning' if moving else 'KEEP (reps)'}")
        if moving:
            r.label = "CUT"
            r.reason = "Camera repositioning / dead footage (no dialogue, camera in motion)"
            flipped += 1
    if flipped:
        print(f"[motion] cut {flipped} silent camera-repositioning gap(s)")
    return ranges


# --- step 5: output -------------------------------------------------------

def write_outputs(ranges: list[Range], src: Path,
                  timeline_ms: int | None = None) -> None:
    cuts = [r for r in ranges if r.label == "CUT"]
    if timeline_ms is None:
        timeline_ms = ranges[-1].end_ms - ranges[0].start_ms if ranges else 0
    cut_ms = sum(r.end_ms - r.start_ms for r in cuts)
    pct = (cut_ms / timeline_ms * 100) if timeline_ms else 0

    review = src.with_name("review.md")
    lines = [
        f"# Cut review — {src.name}",
        "",
        f"{len(cuts)} CUT ranges · {fmt_dur(cut_ms)} of "
        f"{fmt_dur(timeline_ms)} ({pct:.1f}%)",
        "",
        "| start | end | duration | reason |",
        "| --- | --- | --- | --- |",
    ]
    for r in cuts:
        lines.append(
            f"| {ms_to_tc(r.start_ms)} | {ms_to_tc(r.end_ms)} "
            f"| {fmt_dur(r.end_ms - r.start_ms)} | {r.reason or '—'} |"
        )
    review.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cuts_txt = src.with_name("cuts.txt")
    cuts_txt.write_text(
        "\n".join(f"{ms_to_tc(r.start_ms)} --> {ms_to_tc(r.end_ms)}"
                  for r in cuts) + "\n",
        encoding="utf-8",
    )

    print(f"\n[output] {review.name}: {len(cuts)} cut ranges")
    print(f"[output] {cuts_txt.name}: in/out list")
    print(f"\nTotal CUT: {fmt_dur(cut_ms)} of {fmt_dur(timeline_ms)} "
          f"({pct:.1f}% of session)")


def fmt_dur(ms: int) -> str:
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


# --- sync: map recorder time onto the FCP timeline ------------------------

def gather_videos(refs: list[str]) -> list[Path]:
    """Expand --sync-ref args (files or dirs) into a list of video files."""
    out: list[Path] = []
    for r in refs:
        p = Path(r).expanduser()
        if p.is_dir():
            out += [q for q in sorted(p.iterdir())
                    if q.suffix.lower() in VIDEO_EXTS]
        elif p.suffix.lower() in VIDEO_EXTS:
            out.append(p)
    return out


def apply_sync(segments: list[Segment], model) -> tuple[list[Segment], int]:
    """Map every segment's timecodes onto the timeline and clip to its bounds.

    Returns (kept_segments, timeline_end_ms). Segments fully outside the
    timeline (recorder rolling before/after the session) are dropped; those
    straddling an edge are clamped.
    """
    end_ms = int(round(model.timeline_end * 1000))
    kept: list[Segment] = []
    for s in segments:
        ns = int(round(model.rec_ms_to_timeline_ms(s.start_ms)))
        ne = int(round(model.rec_ms_to_timeline_ms(s.end_ms)))
        ns, ne = max(0, ns), min(end_ms, ne)
        if ne <= ns:
            continue                      # entirely outside the timeline
        s.start_ms, s.end_ms = ns, ne
        kept.append(s)
    kept.sort(key=lambda s: s.start_ms)
    return kept, end_ms


def _probe_video(path: Path) -> dict:
    """Frame count, fps, dimensions and embedded start timecode for a video."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-show_entries", "stream_tags=timecode:format_tags=timecode",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=0", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    num, den = (int(x) for x in d["r_frame_rate"].split("/"))
    frames = int(d["nb_frames"]) if d.get("nb_frames", "N/A").isdigit() \
        else round(float(d["duration"]) * num / den)
    return {"width": int(d["width"]), "height": int(d["height"]),
            "fps_num": num, "fps_den": den, "frames": frames,
            "timecode": d.get("TAG:timecode", d.get("timecode", ""))}


def emit_fcpxml(src: Path, model, ranges: list[Range],
                extra_audios: list | None = None,
                style: str = "bladed") -> None:
    """Write a verify-before-cutting FCPXML of the synced timeline.

    style="markers": a labelled marker at each CUT, nothing removed.
    style="bladed":  timeline pre-split at each cut, CUT segments named and
                     disabled so you scrub the edit then ripple-delete them.
    """
    import fcpxml

    # video clips in timeline order (per the sync model), with real frame counts
    by_name = {p.name: p for p in gather_videos([str(model.anchor.parent)])}
    videos, fmt = [], None
    for name, _tl0 in model.clips:
        p = by_name.get(name)
        if p is None:
            continue
        meta = _probe_video(p)
        fmt = fmt or meta
        videos.append(fcpxml.VideoClip(p, meta["frames"], name=p.stem,
                                       timecode=meta["timecode"]))

    audios = [fcpxml.Audio(src, 48000, sync_audio_duration(src), model.intercept,
                           name=f"{src.stem} (primary mic)", lane=-1,
                           slope=model.slope)]
    audios += extra_audios or []

    tl = fcpxml.Timeline(
        width=fmt["width"], height=fmt["height"],
        fps_num=fmt["fps_num"], fps_den=fmt["fps_den"],
        videos=videos, audios=audios,
        cuts=[fcpxml.Cut(r.start_ms / 1000, r.end_ms / 1000, r.reason)
              for r in ranges if r.label == "CUT"],
        project_name=f"{src.stem} — cut review",
    )
    builder = fcpxml.build_bladed if style == "bladed" else fcpxml.build
    out = builder(tl, src.with_name("cut_review.fcpxml"))
    ok, msg = fcpxml.validate(out)
    print(f"[fcpxml] {out.name}: {len(tl.cuts)} cuts ({style}), "
          f"{len(audios)} mic track(s)  "
          f"(DTD {'valid' if ok else 'INVALID: ' + msg})")


def sync_audio_duration(path: Path) -> float:
    import sync
    return sync.ffprobe_duration(path)


def build_final_cut(folder, trainer_mic, user_mic=None, mute_spans=None,
                    dissolve_s=0.5, cuts_from="review.md"):
    """Produce the finished cut (cuts applied) for a session, with the full
    audio chain handled automatically:

      • cuts read from review.md (the human-reviewed list)
      • off-topic `mute_spans` silenced (footage kept)
      • DJI camera audio silenced (lavs are the audio)
      • trainer-mic clock drift corrected (per-segment re-sync)
      • mic gate: the client's lav plays when *they* speak, trainer's otherwise
        (never both → no echo), auto-detected from the two lavs' energy
      • cross-dissolves at the cuts

    Writes <folder>/final_cut_dissolves.fcpxml and final_cut.fcpxml.
    """
    import re
    import fcpxml
    import sync

    D = Path(folder)
    mic = D / trainer_mic
    videos_on_disk = sorted(p for p in D.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    model = sync.build_model(mic, videos_on_disk)

    cuts = []
    for line in (D / cuts_from).read_text().splitlines():
        c = [x.strip() for x in line.split("|")[1:-1]]
        if len(c) == 4 and re.match(r"\d\d:\d\d:\d\d,", c[0]):
            cuts.append(fcpxml.Cut(tc_to_ms(c[0]) / 1000, tc_to_ms(c[1]) / 1000, c[3]))

    by = {p.name: p for p in videos_on_disk}
    vids, fmt = [], None
    for name, _ in model.clips:
        meta = _probe_video(by[name])
        fmt = fmt or meta
        vids.append(fcpxml.VideoClip(by[name], meta["frames"], name=Path(name).stem,
                                     timecode=meta["timecode"]))

    audios = [fcpxml.Audio(mic, 48000, sync_audio_duration(mic), model.intercept,
                           name=f"{mic.stem} (trainer mic)", lane=-1, slope=model.slope)]
    gate = []
    if user_mic:
        um = D / user_mic
        al = sync.align_to_reference(um, mic)
        user_off = model.rec_ms_to_timeline_ms(al.intercept * 1000) / 1000.0
        user_slope = model.slope * al.slope
        audios.append(fcpxml.Audio(um, 48000, sync_audio_duration(um), user_off,
                                   name=f"{um.stem} (client mic)", lane=-2,
                                   slope=user_slope))
        gate = sync.compute_mic_gate(um, mic, user_off, user_slope,
                                     model.intercept, model.slope, model.timeline_end)
        print(f"[gate] client speaks in {len(gate)} spans "
              f"({fmt_dur(int(sum(b - a for a, b in gate) * 1000))})")

    tl = fcpxml.Timeline(width=fmt["width"], height=fmt["height"],
                         fps_num=fmt["fps_num"], fps_den=fmt["fps_den"],
                         videos=vids, audios=audios, cuts=cuts,
                         project_name=f"{D.name} — FINAL CUT")
    for name, dis in [("final_cut_dissolves.fcpxml", dissolve_s),
                      ("final_cut.fcpxml", 0.0)]:
        out = fcpxml.build_applied(tl, D / name, mute_spans=mute_spans,
                                   dissolve_s=dis, gate_user_spans=gate)
        ok, msg = fcpxml.validate(out)
        print(f"[final] {name}: {'valid' if ok else 'INVALID: ' + msg}")
    return D / "final_cut_dissolves.fcpxml"


# --- resource preflight ---------------------------------------------------
#
# The classification model is large (~tens of GB). If it doesn't fit in free
# RAM it gets paged to swap, and the whole machine — plus anything else open,
# like a Final Cut Pro export — grinds to a crawl for hours. We learned this the
# hard way, so refuse to start (or to enter the classification phase) when memory
# is already exhausted, and tell the user to free some up and re-run.
# Override with PT_SKIP_RESOURCE_CHECK=1 to proceed anyway (accepts swapping).

def _macos_mem_stats() -> dict | None:
    """Memory stats in bytes on macOS, or None if not measurable here."""
    if sys.platform != "darwin":
        return None
    try:
        def _sysctl(key: str) -> int:
            return int(subprocess.run(["sysctl", "-n", key],
                                      capture_output=True, text=True).stdout.strip())
        page = _sysctl("hw.pagesize")
        total = _sysctl("hw.memsize")
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        def pages(label: str) -> int:
            m = re.search(rf"{re.escape(label)}:\s+(\d+)\.", vm)
            return int(m.group(1)) * page if m else 0
        # memory reclaimable without swapping out active anonymous pages
        available = (pages("Pages free") + pages("Pages inactive")
                     + pages("Pages speculative") + pages("Pages purgeable"))
        swap = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                              capture_output=True, text=True).stdout
        sm = re.search(r"used\s*=\s*([\d.]+)M.*free\s*=\s*([\d.]+)M", swap)
        swap_used = float(sm.group(1)) * 1024**2 if sm else 0.0
        swap_free = float(sm.group(2)) * 1024**2 if sm else 0.0
        return {"total": total, "available": available,
                "swap_used": swap_used, "swap_free": swap_free}
    except Exception:
        return None


def _ollama_model_bytes() -> tuple[int | None, bool]:
    """(model size in bytes or None, already-loaded?) for OLLAMA_MODEL."""
    base = OLLAMA_URL.rsplit("/api/", 1)[0]
    size, loaded = None, False
    try:
        tags = requests.get(f"{base}/api/tags", timeout=5).json()
        for m in tags.get("models", []):
            if OLLAMA_MODEL in (m.get("name"), m.get("model")):
                size = int(m.get("size") or 0) or None
                break
    except Exception:
        pass
    try:
        ps = requests.get(f"{base}/api/ps", timeout=5).json()
        loaded = any(OLLAMA_MODEL in (m.get("name"), m.get("model"))
                     for m in ps.get("models", []))
    except Exception:
        pass
    return size, loaded


def preflight_resources(stage: str = "start") -> bool:
    """True if there's enough free memory to load the classification model
    without heavy swapping; otherwise print guidance and return False."""
    if os.environ.get("PT_SKIP_RESOURCE_CHECK"):
        return True
    mem = _macos_mem_stats()
    if not mem:
        return True   # can't measure (non-macOS / probe failed) — don't block
    size, loaded = _ollama_model_bytes()

    GB = 1024 ** 3
    # If the model is already resident, its RAM is committed — let it run.
    need = 0 if loaded else (size or 0)
    headroom = need * 1.10            # KV cache + runtime overhead
    avail = mem["available"]

    reasons = []
    if need and avail < headroom:
        reasons.append(
            f"'{OLLAMA_MODEL}' needs ~{headroom/GB:.0f} GB free, "
            f"but only {avail/GB:.1f} GB is available")
    if mem["swap_used"] > 6 * GB and mem["swap_free"] < 2 * GB:
        reasons.append(
            f"the system is already swapping heavily "
            f"({mem['swap_used']/GB:.1f} GB used, {mem['swap_free']/GB:.1f} GB free)")
    if not reasons:
        return True

    where = ("not starting" if stage == "start"
             else "pausing before classification")
    print()
    print("=" * 72)
    print(f"INSUFFICIENT MEMORY — {where}")
    for r in reasons:
        print(f"  - {r}")
    print(f"\n  total RAM {mem['total']/GB:.0f} GB | available {avail/GB:.1f} GB "
          f"| swap used {mem['swap_used']/GB:.1f} GB")
    print("\n  Running now would force the model into swap and grind the whole")
    print("  machine (and any open app like Final Cut Pro) to a crawl for hours.")
    print("\n  Free up memory, then re-run the SAME command:")
    print("    - quit memory-heavy apps (Final Cut Pro, browsers, Docker, VMs)")
    print("    - or wait for a running export/render to finish")
    print("    - to proceed anyway and accept swapping: "
          "set PT_SKIP_RESOURCE_CHECK=1")
    print("=" * 72)
    return False


# --- driver ---------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="PT session transcript -> Final Cut Pro cut list")
    ap.add_argument("audio", help="audio/video file to transcribe (the mic)")
    ap.add_argument("--sync-ref", nargs="+", default=[], metavar="PATH",
                    help="anchor video file(s) or a folder; cuts are aligned "
                         "to the timeline by audio cross-correlation")
    ap.add_argument("--sync-mic", nargs="+", default=[], metavar="PATH",
                    help="extra mic recording(s) to add to the FCPXML, aligned "
                         "to the primary mic via speech-band correlation "
                         "(use for a second lav that shares only faint speech)")
    ap.add_argument("--cut-style", choices=["markers", "bladed"], default="markers",
                    help="markers (default): a labelled marker at each cut on the "
                         "primary storyline (shows in Timeline Index ▸ Tags); "
                         "bladed: timeline pre-split with cuts disabled (note: FCP "
                         "greys disabled clips subtly and hides their tags)")
    ap.add_argument("--keep", nargs="+", default=[], metavar="TC",
                    help="force-KEEP: timecode(s) HH:MM:SS[,mmm] of cuts to "
                         "restore (e.g. after a visual review). Any CUT range "
                         "covering one of these is turned back into KEEP")
    args = ap.parse_args()

    # Heads-up if there isn't enough free RAM for the classification model.
    # Transcription + sync are cheap and never thrash, so we still run them and
    # cache the SRT; the hard gate is re-checked before classification (which
    # self-skips and tells you to re-run when memory frees). This keeps the
    # cheap work from being wasted when RAM is tight at launch.
    preflight_resources("start")

    src = Path(args.audio).expanduser()
    if not src.exists():
        print(f"error: file not found: {src}")
        return 1

    ext = src.suffix.lower()
    if ext in VIDEO_EXTS:
        audio = extract_audio(src)
    elif ext in AUDIO_EXTS:
        audio = src
    else:
        print(f"error: unsupported extension {ext!r}")
        return 1

    srt_path = src.with_suffix(".srt")
    transcribe(audio, srt_path)

    segments = parse_srt(srt_path)
    print(f"[parse] {len(segments)} segments from SRT")
    if not segments:
        print("error: no segments parsed from SRT")
        return 1

    # spot-check first 3 cues (recorder time)
    print("[parse] first cues:")
    for s in segments[:3]:
        print(f"  {ms_to_tc(s.start_ms)} {s.text[:70]}")

    timeline_ms = None
    sync_model = None
    if args.sync_ref:
        import sync
        videos = gather_videos(args.sync_ref)
        if not videos:
            print("error: --sync-ref matched no video files")
            return 1
        print(f"[sync] aligning to timeline via {len(videos)} ref clip(s)...")
        model = sync_model = sync.build_model(src, videos)
        off = model.intercept
        print(f"[sync] clip order on timeline:")
        for name, tl0 in model.clips:
            print(f"         {ms_to_tc(int(tl0 * 1000))}  {name}")
        print(f"[sync] anchor (timeline 0): {model.anchor.name}")
        print(f"[sync] offset: {off:+.3f}s (recorder t=0 -> timeline {off:+.3f}s)")
        print(f"[sync] drift:  {model.drift_ms_per_min:+.1f} ms/min  "
              f"confidence={model.confidence:.2f}  "
              f"fit residual={model.residual_ms:.0f} ms")
        if model.confidence < 0.40 or model.residual_ms > 500:
            print("[sync] WARNING: weak/inconsistent alignment — "
                  "verify the first cues against FCP before trusting cuts")
        before = len(segments)
        segments, timeline_ms = apply_sync(segments, model)
        print(f"[sync] mapped {before} cues -> {len(segments)} on timeline "
              f"(0 .. {ms_to_tc(timeline_ms)})")
        print("[sync] first cues on timeline (check against FCP):")
        for s in segments[:3]:
            print(f"  {ms_to_tc(s.start_ms)} {s.text[:70]}")

    # Re-check right before the memory-heavy classification step: transcription
    # + sync took a few minutes, during which another app may have eaten the RAM.
    if not preflight_resources("classify"):
        print("[classify] skipped — SRT is saved; re-run when memory frees up.")
        return 2
    classify(segments)
    ranges = merge_ranges(segments)
    if sync_model is not None:
        ranges = flag_camera_motion(ranges, sync_model, videos)
    if args.keep:
        ranges = apply_force_keep(ranges, args.keep)
    write_outputs(ranges, src, timeline_ms)
    if sync_model is not None:
        extra_audios = align_extra_mics(args.sync_mic, src, sync_model)
        emit_fcpxml(src, sync_model, ranges, extra_audios, style=args.cut_style)
    return 0


def apply_force_keep(ranges: list[Range], keeps: list[str]) -> list[Range]:
    """Turn any CUT range covering a force-keep timecode back into KEEP, then
    re-merge adjacent same-label ranges. For carrying visual-review corrections
    forward to a re-run."""
    pts = [tc_to_ms(k if "," in k else k + ",000") for k in keeps]
    n = 0
    for r in ranges:
        if r.label == "CUT" and any(r.start_ms <= p < r.end_ms for p in pts):
            r.label, r.reason, n = "KEEP", "", n + 1
    print(f"[keep] restored {n} cut range(s) to KEEP")
    merged: list[Range] = []
    for r in ranges:
        if merged and merged[-1].label == r.label:
            merged[-1].end_ms = r.end_ms
        else:
            merged.append(r)
    return merged


def align_extra_mics(mic_args: list[str], primary: Path, model) -> list:
    """Align each extra mic to the primary mic (speech-band) and return a list
    of fcpxml.Audio positioned on the timeline. Skips any that won't lock."""
    import fcpxml
    import sync

    out = []
    lane = -2
    for raw in mic_args:
        mic = Path(raw).expanduser()
        if not mic.exists():
            print(f"[mic] skip (not found): {mic}")
            continue
        try:
            al = sync.align_to_reference(mic, primary)
        except RuntimeError as e:
            print(f"[mic] {mic.name}: {e} — skipped")
            continue
        # mic t=0 -> primary t=intercept -> timeline
        tl_off = model.rec_ms_to_timeline_ms(al.intercept * 1000) / 1000.0
        print(f"[mic] {mic.name}: timeline offset {tl_off:+.3f}s  "
              f"drift={(al.slope - 1) * 60000:+.2f}ms/min  "
              f"conf={al.confidence:.2f} resid={al.residual_ms:.0f}ms "
              f"({al.n_windows} windows)")
        out.append(fcpxml.Audio(mic, 48000, sync.ffprobe_duration(mic),
                                tl_off, name=f"{mic.stem} (mic)", lane=lane,
                                slope=model.slope * al.slope))
        lane -= 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
