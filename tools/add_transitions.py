"""Build one file per PT session with a dissolve at every real break.

Reuses the grouping and person-detection from stitch_sessions, then joins the
kept segments with xfade instead of a plain concatenation. A dissolve only
goes where footage was actually removed — a break of BREAK_GAP or more, either
a gap between two clips or a stretch this cut for having nobody in it. Two
clips that run straight on from each other keep a hard cut, so a dissolve
never blurs the middle of a set.

This re-encodes: a crossfade has to decode both sides. Everything else in the
toolkit copies the stream untouched, so keep the stitch_sessions output as the
master and treat this as the watchable version.

    uv run tools/add_transitions.py <source-dir> <out-dir> [--days ...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stitch_sessions import (  # noqa: E402
    KST, group_by_day, keep_spans, keyframe_times, detect_people,
    split_sessions, probe,
)

FFMPEG = str(Path(__file__).parent / "bin" / "ffmpeg")   # native arm64 build
XFADE = 0.75          # dissolve length, seconds
BREAK_GAP = 10.0      # a removed stretch this long earns a dissolve
BITRATE = "60M"       # matches the GoPro originals at 4K60


def build(segments: list[tuple[Path, float, float, bool]], dst: Path) -> None:
    """segments: (clip, start, end, dissolve_before). One encode pass."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    for clip, a, b, _ in segments:
        cmd += ["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(clip)]

    fc = []
    for i, (_, a, b, _) in enumerate(segments):
        fc.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")

    vcur, acur = "v0", "a0"
    run = segments[0][2] - segments[0][1]
    for i in range(1, len(segments)):
        dur = segments[i][2] - segments[i][1]
        if segments[i][3] and run > XFADE and dur > XFADE:
            off = run - XFADE
            fc.append(f"[{vcur}][v{i}]xfade=transition=fade:"
                      f"duration={XFADE}:offset={off:.3f}[vx{i}]")
            fc.append(f"[{acur}][a{i}]acrossfade=d={XFADE}[ax{i}]")
            run = run + dur - XFADE
        else:
            fc.append(f"[{vcur}][v{i}]concat=n=2:v=1:a=0[vx{i}]")
            fc.append(f"[{acur}][a{i}]concat=n=2:v=0:a=1[ax{i}]")
            run = run + dur
        vcur, acur = f"vx{i}", f"ax{i}"

    # xfade promotes the frames to a format with alpha, which the hardware
    # encoder will not take. Convert after the chain, not before it.
    fc.append(f"[{vcur}]format=yuv420p[vout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]",
            "-map", f"[{acur}]",
            "-c:v", "hevc_videotoolbox", "-b:v", BITRATE, "-tag:v", "hvc1",
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, capture_output=True, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--days", default="")
    ap.add_argument("--keep-whole", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keep_whole = {x for x in args.keep_whole.split(",") if x}
    only = {x for x in args.days.split(",") if x}
    days = group_by_day(args.source)
    args.outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="xfade-"))

    for day in sorted(days):
        if only and day not in only:
            continue
        for n, entries in enumerate(split_sessions(sorted(days[day])), start=1):
            tags = sorted({e[1].parent.name for e in entries} - {args.source.name})
            sfx = f"_s{n}" if len(split_sessions(sorted(days[day]))) > 1 else ""
            name = f"{day}{sfx}_{'-'.join(tags) or 'session'}"

            segments: list[tuple[Path, float, float, bool]] = []
            prev_end_wall = None
            for start, path, dur in entries:
                if path.name in keep_whole:
                    spans = [(0.0, dur)]
                else:
                    spans = keep_spans(detect_people(path, work),
                                       keyframe_times(path), dur)
                for j, (a, b) in enumerate(spans):
                    if not segments:
                        gap = 0.0
                    elif j:
                        gap = a - spans[j - 1][1]          # cut inside the clip
                    else:
                        gap = (start - prev_end_wall).total_seconds()
                    segments.append((path, a, b, gap >= BREAK_GAP))
                prev_end_wall = start + timedelta(seconds=dur)

            if not segments:
                continue
            fades = sum(1 for s in segments if s[3])
            total = sum(b - a for _, a, b, _ in segments) - fades * XFADE
            print(f"{name}: {len(segments)} segments, {fades} dissolves, "
                  f"{total/60:.1f}min")
            if args.dry_run:
                continue
            out = args.outdir / f"{name}.mp4"
            build(segments, out)
            print(f"  wrote {out}  ({out.stat().st_size/1e9:.1f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
