"""Render a captioned compilation from an edit list (JSON) with ffmpeg + Pillow.

usage: uv run tools/compile_journey.py EDL.json OUT.mp4 [--only N[,N...]] [--preview]

The edit list is a JSON array. Two item types:
  {"type": "card", "lines": ["BIG LINE", "small line", ...], "dur": 3}
  {"type": "clip", "src": "file.mp4", "in": 12.0, "out": 20.5, "speed": 4,
   "audio": "src" | "none" | {"file": "lav.wav", "ss": 87.2},
   "caption": "Head forward, fast", "tag": "Kip swing", "head": "21 May 2026",
   "sub": "Session 1 of 12", "kind": "cue|mistake|breakthrough|drill_intro",
   "progress": 0.08, "crop": [x, y, w, h], "badge": "REPLAY"}
Audio plays only at speed 1. Each item becomes one 1920x1080 30 fps segment in
<OUT>.parts/, and the segments are joined without a second encode. A segment
is rendered again only when its item changed.

ffmpeg here has no drawtext or subtitles filter, so Pillow draws each caption
layer as a transparent PNG and ffmpeg overlays it.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
TTC = "/System/Library/Fonts/Avenir Next Condensed.ttc"
KIND = {"mistake": ("FIX", (226, 68, 52)), "breakthrough": ("WIN", (38, 166, 91)),
        "cue": ("CUE", (240, 180, 0)), "drill_intro": ("DRILL", (60, 130, 220))}
ENC = ["-c:v", "h264_videotoolbox", "-b:v", "12M", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "30000"]


def font(size: int, style: str = "Heavy") -> ImageFont.FreeTypeFont:
    for i in range(12):
        f = ImageFont.truetype(TTC, size, index=i)
        if f.getname()[1] == style:
            return f
    raise SystemExit(f"font style not found: {style}")


def box(d: ImageDraw.ImageDraw, xy, text, f, fill=(255, 255, 255), bg=(0, 0, 0, 170), pad=(22, 10)):
    """Text on a rounded dark plate; xy is the plate's top-left. Returns the plate's right x and bottom y."""
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    x, y = xy
    x1, y1 = x + (r - l) + 2 * pad[0], y + (b - t) + 2 * pad[1]
    d.rounded_rectangle([x, y, x1, y1], radius=12, fill=bg)
    d.text((x + pad[0] - l, y + pad[1] - t), text, font=f, fill=fill)
    return x1, y1


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        if d.textlength((cur + " " + w).strip(), font=f) <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur); cur = w
    return lines + [cur] if cur else lines


def overlay_png(it: dict, path: Path) -> None:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if it.get("head"):                                   # top-left: date + session counter
        _, y1 = box(d, (48, 40), it["head"], font(44))
        if it.get("sub"):
            box(d, (48, y1 + 8), it["sub"], font(30, "Demi Bold"), pad=(18, 8))
    x = W - 48                                           # top-right: speed / badge, then drill name
    tags = []
    if it.get("badge"):
        tags.append((it["badge"], (255, 255, 255), (226, 68, 52, 230)))
    sp = it.get("speed", 1)
    if sp != 1:
        tags.append((f"{sp:g}x", (0, 0, 0), (255, 221, 51, 235)))
    if it.get("tag"):
        tags.append((it["tag"].upper(), (255, 255, 255), (0, 0, 0, 170)))
    y = 40
    for text, fg, bg in tags:
        f = font(40)
        tw = d.textlength(text, font=f) + 44
        box(d, (x - tw, y), text, f, fill=fg, bg=bg)
        y += 74
    if it.get("caption"):                                # lower third: kind label + caption
        f = font(62)
        lines = wrap(d, it["caption"], f, W - 420)
        lh = 78
        y0 = H - 110 - lh * len(lines)
        label, col = KIND.get(it.get("kind", ""), (None, None))
        for i, line in enumerate(lines):
            tw = d.textlength(line, font=f)
            x0 = (W - tw) / 2 - 26
            box(d, (x0, y0 + i * lh), line, f, pad=(26, 8), bg=(0, 0, 0, 190))
            if label and i == 0:
                lf = font(34)
                lw = d.textlength(label, font=lf) + 32
                box(d, (x0 - lw - 10, y0 + 12), label, lf, bg=col + (240,), pad=(16, 8))
    if "progress" in it:                                 # journey bar along the bottom edge
        d.rectangle([0, H - 12, W, H], fill=(0, 0, 0, 150))
        d.rectangle([0, H - 12, int(W * it["progress"]), H], fill=(255, 221, 51, 255))
    im.save(path)


def card_png(it: dict, path: Path) -> None:
    im = Image.new("RGB", (W, H), (14, 14, 16))
    d = ImageDraw.Draw(im)
    sizes = [132 if i == 0 else 54 for i in range(len(it["lines"]))]
    if it.get("list"):
        sizes = [96] + [58] * (len(it["lines"]) - 1)
    total = sum(int(s * 1.35) for s in sizes)
    y = (H - total) / 2
    for line, s in zip(it["lines"], sizes):
        f = font(s, "Heavy" if s > 60 else "Demi Bold")
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, y), line, font=f, fill=(255, 221, 51) if s > 60 else (235, 235, 235))
        y += int(s * 1.35)
    im.save(path)


def render(it: dict, out: Path, work: Path) -> None:
    png = work / (out.stem + ".png")
    if it["type"] == "card":
        card_png(it, png)
        cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", str(it.get("dur", 3)), "-i", str(png),
               "-f", "lavfi", "-t", str(it.get("dur", 3)), "-i", "anullsrc=r=48000:cl=stereo",
               "-vf", f"fade=in:0:8,fade=out:st={it.get('dur', 3) - 0.3}:d=0.3,format=yuv420p", *ENC, str(out)]
        subprocess.run(cmd, check=True)
        return
    overlay_png(it, png)
    dur, sp = it["out"] - it["in"], it.get("speed", 1)
    odur = dur / sp
    vf = ""
    if it.get("crop"):
        x, y, w, h = it["crop"]
        vf = f"crop={w}:{h}:{x}:{y},"
    vf += (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
           f"setpts=(PTS-STARTPTS)/{sp},fps={FPS},format=yuv420p")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{it['in']:.3f}", "-t", f"{dur:.3f}", "-i", it["src"]]
    au = it.get("audio", "none") if sp == 1 else "none"
    if isinstance(au, dict):
        cmd += ["-ss", f"{au['ss']:.3f}", "-t", f"{dur:.3f}", "-i", au["file"]]
        amap = "[1:a]"
    elif au == "src":
        amap = "[0:a]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{odur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "[1:a]"
    n = cmd.count("-i")
    cmd += ["-loop", "1", "-i", str(png), "-filter_complex",
            f"[0:v]{vf}[v];[v][{n}:v]overlay=shortest=1[vo];"
            f"{amap}aresample=48000,afade=in:d=0.08,afade=out:st={max(0, odur - 0.12):.3f}:d=0.12,"
            f"apad=whole_dur={odur:.3f}[ao]",
            "-map", "[vo]", "-map", "[ao]", "-t", f"{odur:.3f}", *ENC, str(out)]
    subprocess.run(cmd, check=True)


def main() -> int:
    edl_path, out = Path(sys.argv[1]), Path(sys.argv[2])
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    edl = json.loads(edl_path.read_text())
    work = out.with_suffix(".parts")
    work.mkdir(exist_ok=True)
    parts = []
    for i, it in enumerate(edl):
        if only and i not in only:
            continue
        key = hashlib.sha1(json.dumps(it, sort_keys=True).encode()).hexdigest()[:10]
        part = work / f"{i:03d}_{key}.mp4"
        if not part.exists():
            for old in work.glob(f"{i:03d}_*"):
                old.unlink()
            render(it, part, work)
            print(f"[{i:03d}] {it['type']} {it.get('caption') or it.get('lines', [''])[0]}", flush=True)
        parts.append(part)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    print(f"wrote {out} ({float(d) / 60:.1f} min, {len(parts)} segments)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
