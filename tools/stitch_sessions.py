"""Stitch PT session clips into one video per day, dropping shots with no people.

Samples each clip at its keyframes (GoPro = exactly 1/sec, ~30x faster than
decoding every frame), asks Apple's Vision framework whether a person is in
each sampled frame, then keeps only the stretches where somebody is present.

Conservative: a gap counts as dead only when nobody is seen for MIN_DEAD
seconds, and every kept run is padded by PAD seconds on each side. A missed
detection costs a second of empty gym; an over-eager cut loses a working set.

    uv run tools/stitch_sessions.py <source-dir> <out-dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DETECT = Path(__file__).parent / "persondetect"

MIN_DEAD = 5.0        # seconds with nobody visible before a stretch is cut
PAD = 1.0             # seconds kept either side of a run of people
MIN_KEEP = 2.0        # drop kept runs shorter than this (detector flicker)
SAMPLE_WIDTH = 640    # downscale for detection only
OUT_HEIGHT = 1080
# ponytail: h264 not hevc — the installed ffmpeg is an x86_64 build under Rosetta
# and cannot open the HEVC hardware encoder. Switch to hevc_videotoolbox if a
# native arm64 ffmpeg gets installed.
GAP_SPLIT = 3 * 3600  # not used for grouping; reported only


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def probe(path: Path) -> tuple[float, str]:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:format_tags=creation_time",
               "-of", "json", str(path)])
    fmt = json.loads(out)["format"]
    return float(fmt["duration"]), fmt.get("tags", {}).get("creation_time", "")


def keyframe_times(path: Path) -> list[float]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
               "-of", "csv=p=0", str(path)])
    vals = [x.strip().rstrip(",") for x in out.split()]
    return [float(x) for x in vals if x and x != "N/A"]


def detect_people(path: Path, work: Path) -> list[bool]:
    """True/False per keyframe: is a person visible?"""
    frames = work / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey",
         "-hwaccel", "videotoolbox", "-i", str(path), "-vsync", "0",
         "-vf", f"scale={SAMPLE_WIDTH}:-2", "-q:v", "6",
         str(frames / "%06d.jpg")],
        capture_output=True, check=True)
    out = run([str(DETECT), str(frames)])
    present = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            present.append(int(parts[1]) > 0)
    shutil.rmtree(frames)
    return present


def keep_spans(present: list[bool], times: list[float], dur: float) -> list[tuple[float, float]]:
    """Turn per-sample presence into padded keep intervals."""
    n = min(len(present), len(times))
    if n == 0:
        return [(0.0, dur)]          # could not sample -> keep everything
    present, times = present[:n], times[:n]

    # a run of absent samples is only 'dead' if it lasts MIN_DEAD seconds
    filled = list(present)
    i = 0
    while i < n:
        if filled[i]:
            i += 1
            continue
        j = i
        while j < n and not filled[j]:
            j += 1
        start = times[i]
        end = times[j] if j < n else dur
        if end - start < MIN_DEAD:
            for k in range(i, j):
                filled[k] = True      # short blip: assume the detector missed them
        i = j

    spans = []
    i = 0
    while i < n:
        if not filled[i]:
            i += 1
            continue
        j = i
        while j < n and filled[j]:
            j += 1
        start = max(0.0, times[i] - PAD)
        end = min(dur, (times[j] if j < n else dur) + PAD)
        if end - start >= MIN_KEEP:
            spans.append((start, end))
        i = j

    merged: list[tuple[float, float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def encode(src: Path, spans: list[tuple[float, float]], dst: Path) -> None:
    expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in spans)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-hwaccel", "videotoolbox", "-i", str(src),
         "-vf", f"select='{expr}',setpts=N/FRAME_RATE/TB,scale=-2:{OUT_HEIGHT}",
         "-af", f"aselect='{expr}',asetpts=N/SR/TB",
         "-c:v", "h264_videotoolbox", "-b:v", "10M",
         "-r", "60", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
         "-map_metadata", "-1", str(dst)],
        capture_output=True, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the cuts without encoding")
    args = ap.parse_args()

    clips = sorted(p for p in args.source.rglob("*")
                   if p.suffix.lower() in {".mp4", ".mov"})
    if not clips:
        print(f"no clips under {args.source}", file=sys.stderr)
        return 1

    # group by Korea-local calendar date
    days: dict[str, list] = defaultdict(list)
    for p in clips:
        dur, created = probe(p)
        if not created:
            print(f"skip (no creation_time): {p}", file=sys.stderr)
            continue
        local = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(KST)
        days[local.strftime("%Y-%m-%d")].append((local, p, dur))

    args.outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stitch-"))
    grand_in = grand_out = 0.0

    for day in sorted(days):
        entries = sorted(days[day])
        tags = sorted({e[1].parent.name for e in entries} - {args.source.name})
        name = f"{day}_{'-'.join(tags) or 'session'}"
        parts, day_in, day_out = [], 0.0, 0.0
        print(f"\n=== {day}  ({len(entries)} clips)")

        for idx, (_, path, dur) in enumerate(entries):
            times = keyframe_times(path)
            present = detect_people(path, work)
            spans = keep_spans(present, times, dur)
            kept = sum(e - s for s, e in spans)
            day_in += dur
            day_out += kept
            pct = 100 * kept / dur if dur else 0
            print(f"  {path.parent.name}/{path.name}  "
                  f"{dur:7.1f}s -> {kept:7.1f}s ({pct:5.1f}%)  {len(spans)} span(s)")
            if not spans or args.dry_run:
                continue
            part = work / f"{name}_{idx:03d}.mp4"
            encode(path, spans, part)
            parts.append(part)

        grand_in += day_in
        grand_out += day_out
        print(f"  --- day total: {day_in/60:.1f}min -> {day_out/60:.1f}min")
        if args.dry_run or not parts:
            continue

        listing = work / f"{name}.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        final = args.outdir / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", str(listing), "-c", "copy", str(final)],
            capture_output=True, check=True)
        for p in parts:
            p.unlink()
        listing.unlink()
        print(f"  wrote {final}")

    shutil.rmtree(work, ignore_errors=True)
    print(f"\nTOTAL: {grand_in/60:.1f}min -> {grand_out/60:.1f}min "
          f"({100*grand_out/grand_in if grand_in else 0:.1f}% kept)")
    return 0


def demo() -> None:
    """Self-check for the span logic — the only non-obvious part."""
    t = [float(i) for i in range(20)]
    # people for 0-4, nobody 5-14 (10s dead), people 15-19
    p = [True]*5 + [False]*10 + [True]*5
    spans = keep_spans(p, t, 20.0)
    assert len(spans) == 2, spans
    assert spans[0] == (0.0, 6.0), spans          # 5 padded by 1
    assert spans[1] == (14.0, 20.0), spans        # 15 padded by 1, clipped to dur

    # a 3s blip of non-detection is shorter than MIN_DEAD -> not a cut
    p = [True]*5 + [False]*3 + [True]*12
    assert keep_spans(p, t, 20.0) == [(0.0, 20.0)], keep_spans(p, t, 20.0)

    # nobody at all -> nothing kept
    assert keep_spans([False]*20, t, 20.0) == []

    # a single stray detection in an empty clip is shorter than MIN_KEEP... but
    # padding makes it 2s, so it survives. That is the conservative direction.
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        raise SystemExit(main())
