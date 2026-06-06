"""Generate an FCPXML timeline with the proposed cuts as review markers.

This is the "verify before cutting" deliverable (Option B1): it reconstructs the
synced session timeline — the camera clips on the spine with the trainer mic as a
connected audio clip, positioned by the measured sync offset — and drops a
labelled marker at every proposed CUT. Nothing is removed. Import it into Final
Cut Pro, review the markers in the Timeline Index, and accept/adjust each cut
yourself before committing.

All times are snapped to the sequence frame grid. Validated against Apple's
FCPXML DTD with xmllint.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote
from urllib.request import pathname2url

FCPXML_VERSION = "1.9"


@dataclass
class VideoClip:
    path: Path
    frames: int          # exact video frame count
    name: str = ""
    timecode: str = ""   # embedded start timecode, e.g. "10:22:52;44" (DF if ';')


@dataclass
class Audio:
    path: Path
    sample_rate: int
    duration_s: float
    offset_s: float      # timeline position where the recorder's t=0 sits
    name: str = ""
    lane: int = -1       # connected-clip lane (-1 just below the spine)
    slope: float = 1.0   # timeline_s per recorder_s — corrects clock drift
                         # (timeline_s = slope*rec_s + offset_s)


@dataclass
class Cut:
    start_s: float       # timeline seconds
    end_s: float
    reason: str


@dataclass
class Timeline:
    width: int = 3840
    height: int = 2160
    fps_num: int = 60000
    fps_den: int = 1001
    videos: list[VideoClip] = field(default_factory=list)
    audios: list[Audio] = field(default_factory=list)   # markers go on audios[0]
    cuts: list[Cut] = field(default_factory=list)
    project_name: str = "PT session — cut review"


# --- time helpers (everything snapped to the frame grid) ------------------

def _file_url(p: Path) -> str:
    # pathname2url already percent-encodes (spaces -> %20); do NOT quote again
    # or characters double-encode (space -> %2520) and FCP can't find the file.
    url = "file://" + pathname2url(str(p.resolve()))
    # guard: the URL must decode back to the exact path (catches encoding bugs
    # like double-encoding before they ever reach FCP)
    if Path(unquote(url[len("file://"):])) != p.resolve():
        raise ValueError(f"file URL does not round-trip for {p}: {url}")
    return url


def parse_timecode(tc: str, fps_num: int, fps_den: int) -> tuple[int, bool]:
    """Embedded timecode string -> (absolute frame number, is_drop_frame).

    A ';' before the frames field marks drop-frame. Drop-frame skips
    `2*(fps/30)` frame numbers each minute except every tenth minute.
    """
    import re as _re
    drop = ";" in tc
    h, m, s, f = (int(x) for x in _re.split("[:;]", tc.strip()))
    fps_round = round(fps_num / fps_den)
    if drop:
        dropn = fps_round // 15            # 30fps->2, 60fps->4
        total_min = 60 * h + m
        fn = ((h * 3600 + m * 60 + s) * fps_round + f
              - dropn * (total_min - total_min // 10))
    else:
        fn = (h * 3600 + m * 60 + s) * fps_round + f
    return fn, drop


class _T:
    def __init__(self, fps_num: int, fps_den: int):
        self.n, self.d = fps_num, fps_den

    def frame_dur(self) -> str:
        return f"{self.d}/{self.n}s"

    def frames(self, count: int) -> str:
        v = count * self.d
        return f"{v}/{self.n}s" if v else "0s"

    def secs(self, sec: float) -> str:
        return self.frames(round(sec * self.n / self.d))

    def to_frames(self, sec: float) -> int:
        return round(sec * self.n / self.d)


# --- builder --------------------------------------------------------------

def build(tl: Timeline, out_path: Path) -> Path:
    T = _T(tl.fps_num, tl.fps_den)
    total_frames = sum(v.frames for v in tl.videos)

    fcpxml = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(fcpxml, "resources")

    fmt_id = "r1"
    ET.SubElement(resources, "format", id=fmt_id,
                  name=f"FFVideoFormat{tl.height}p{tl.fps_num // 1000}",
                  frameDuration=T.frame_dur(),
                  width=str(tl.width), height=str(tl.height),
                  colorSpace="1-1-1 (Rec. 709)")

    # video assets — honour the media's embedded start timecode, else FCP
    # rejects the edit ("no respective media")
    for i, v in enumerate(tl.videos, 1):
        v.ref = f"v{i}"                                   # type: ignore[attr-defined]
        start_fn, drop = (parse_timecode(v.timecode, tl.fps_num, tl.fps_den)
                          if v.timecode else (0, False))
        v.start = T.frames(start_fn)                      # type: ignore[attr-defined]
        v.tcfmt = "DF" if drop else "NDF"                 # type: ignore[attr-defined]
        asset = ET.SubElement(resources, "asset", id=v.ref,
                              name=v.name or v.path.stem,
                              start=v.start, duration=T.frames(v.frames),
                              hasVideo="1", hasAudio="1", format=fmt_id,
                              videoSources="1", audioSources="1",
                              audioChannels="2", audioRate="48000")
        ET.SubElement(asset, "media-rep", kind="original-media",
                      src=_file_url(v.path))

    # audio assets (one per mic)
    for j, a in enumerate(tl.audios, 1):
        a.ref = f"a{j}"                                   # type: ignore[attr-defined]
        a_samples = round(a.duration_s * a.sample_rate)
        asset = ET.SubElement(resources, "asset", id=a.ref,
                              name=a.name or a.path.stem, start="0s",
                              duration=f"{a_samples}/{a.sample_rate}s",
                              hasVideo="0", hasAudio="1", audioSources="1",
                              audioChannels="1", audioRate=str(a.sample_rate))
        ET.SubElement(asset, "media-rep", kind="original-media",
                      src=_file_url(a.path))

    # library / event / project / sequence
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="PT cut review")
    project = ET.SubElement(event, "project", name=tl.project_name)
    sequence = ET.SubElement(project, "sequence", format=fmt_id,
                             duration=T.frames(total_frames),
                             tcStart="0s", tcFormat="NDF",
                             audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")

    # spine: the camera clips back to back
    offset_frames = 0
    first_clip = None
    spine_clips = []   # (clip_el, tl_start_frames, frames, media_start_frames)
    for i, v in enumerate(tl.videos):
        clip = ET.SubElement(spine, "asset-clip", ref=v.ref,
                             offset=T.frames(offset_frames),
                             name=v.name or v.path.stem,
                             duration=T.frames(v.frames),
                             start=v.start, tcFormat=v.tcfmt,
                             srcEnable="video")   # drop DJI camera audio; use lavs
        sf = parse_timecode(v.timecode, tl.fps_num, tl.fps_den)[0] if v.timecode else 0
        spine_clips.append((clip, offset_frames, v.frames, sf))
        if first_clip is None:
            first_clip = clip
        offset_frames += v.frames
    parent_start_fn = spine_clips[0][3]

    # connected audio clips (one per mic), anchored to the first spine clip.
    for a in tl.audios:
        audio_tl_frames = T.to_frames(a.offset_s)
        ET.SubElement(
            first_clip, "asset-clip", ref=a.ref, lane=str(a.lane),
            offset=T.frames(audio_tl_frames + parent_start_fn),
            name=a.name or a.path.stem,
            duration=T.frames(min(T.to_frames(a.duration_s),
                                  total_frames - audio_tl_frames)),
            start="0s", tcFormat="NDF", audioRole="dialogue")

    # markers go on the ENABLED spine clips (primary storyline) so they always
    # appear in the Timeline Index ▸ Tags — markers on disabled clips are hidden.
    for c in tl.cuts:
        tf = T.to_frames(c.start_s)
        host = next((sc for sc in spine_clips
                     if sc[1] <= tf < sc[1] + sc[2]), None)
        if host is None:
            continue
        clip_el, tl0, _fr, msf = host
        dur_frames = max(1, T.to_frames(c.end_s) - tf)
        reason = (c.reason or "filler").split(";")[0].strip()[:80]
        ET.SubElement(clip_el, "marker", start=T.frames(msf + (tf - tl0)),
                      duration=T.frames(dur_frames), value=f"CUT: {reason}")

    _indent(fcpxml)
    body = ET.tostring(fcpxml, encoding="unicode")
    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n' + body + "\n", encoding="utf-8")
    return out_path


def build_bladed(tl: Timeline, out_path: Path) -> Path:
    """Like build(), but the timeline is pre-split at every cut boundary and the
    CUT segments are named "CUT: reason" and disabled (enabled="0").

    On import you scrub the *edited* result — cut spans play black & silent —
    then select the greyed/flagged segments and ripple-delete them in bulk.
    Restored false positives simply never become CUT segments.
    """
    T = _T(tl.fps_num, tl.fps_den)
    total = sum(v.frames for v in tl.videos)

    fcpxml = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(fcpxml, "resources")
    fmt_id = "r1"
    ET.SubElement(resources, "format", id=fmt_id,
                  name=f"FFVideoFormat{tl.height}p{tl.fps_num // 1000}",
                  frameDuration=T.frame_dur(), width=str(tl.width),
                  height=str(tl.height), colorSpace="1-1-1 (Rec. 709)")

    # video clip resources, with timeline span + media start (timecode) in frames
    clip_spans = []   # (clip, ref, tl_start_f, frames, media_start_f, tcfmt)
    off = 0
    for i, v in enumerate(tl.videos, 1):
        ref = f"v{i}"
        sf, drop = (parse_timecode(v.timecode, tl.fps_num, tl.fps_den)
                    if v.timecode else (0, False))
        asset = ET.SubElement(resources, "asset", id=ref, name=v.name or v.path.stem,
                              start=T.frames(sf), duration=T.frames(v.frames),
                              hasVideo="1", hasAudio="1", format=fmt_id,
                              videoSources="1", audioSources="1",
                              audioChannels="2", audioRate="48000")
        ET.SubElement(asset, "media-rep", kind="original-media", src=_file_url(v.path))
        clip_spans.append((v, ref, off, v.frames, sf, "DF" if drop else "NDF"))
        off += v.frames

    for j, a in enumerate(tl.audios, 1):
        a.ref = f"a{j}"                                   # type: ignore[attr-defined]
        ET.SubElement(ET.SubElement(resources, "asset", id=a.ref,
                      name=a.name or a.path.stem, start="0s",
                      duration=f"{round(a.duration_s * a.sample_rate)}/{a.sample_rate}s",
                      hasVideo="0", hasAudio="1", audioSources="1",
                      audioChannels="1", audioRate=str(a.sample_rate)),
                      "media-rep", kind="original-media", src=_file_url(a.path))

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="PT cut review")
    project = ET.SubElement(event, "project", name=tl.project_name)
    sequence = ET.SubElement(project, "sequence", format=fmt_id,
                             duration=T.frames(total), tcStart="0s", tcFormat="NDF",
                             audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")

    # cut spans in frames, and reason lookup
    cut_spans = []
    for c in tl.cuts:
        cs, ce = T.to_frames(c.start_s), T.to_frames(c.end_s)
        if ce > cs:
            cut_spans.append((cs, ce, (c.reason or "filler").split(";")[0].strip()[:70]))
    cut_spans.sort()

    def cut_reason(f0):
        for cs, ce, r in cut_spans:
            if cs <= f0 < ce:
                return r
        return None

    # mic timeline coverage, included as boundaries so each segment is fully
    # covered (or not) by each mic — lets us nest audio per video segment
    mics = [(a, T.to_frames(a.offset_s),
             min(total, T.to_frames(a.offset_s) + T.to_frames(a.duration_s)))
            for a in tl.audios]

    # boundaries: clip edges + cut edges + mic edges, clamped to [0, total]
    bounds = {0, total}
    for _, _, ts, fr, _, _ in clip_spans:
        bounds.add(ts); bounds.add(ts + fr)
    for cs, ce, _ in cut_spans:
        bounds.add(max(0, min(total, cs))); bounds.add(max(0, min(total, ce)))
    for _, ms, me in mics:
        bounds.add(max(0, min(total, ms))); bounds.add(max(0, min(total, me)))
    bounds = sorted(bounds)

    for b0, b1 in zip(bounds, bounds[1:]):
        if b1 <= b0:
            continue
        clip = next(c for c in clip_spans if c[2] <= b0 < c[2] + c[3])
        v, ref, ts, fr, msf, tcfmt = clip
        reason = cut_reason(b0)
        host_start = msf + (b0 - ts)            # this segment's media in-point
        attrs = dict(ref=ref, offset=T.frames(b0),
                     name=(f"CUT: {reason}" if reason else (v.name or v.path.stem)),
                     duration=T.frames(b1 - b0),
                     start=T.frames(host_start), tcFormat=tcfmt,
                     srcEnable="video")          # drop DJI camera audio; use lavs
        if reason:
            attrs["enabled"] = "0"
        seg = ET.SubElement(spine, "asset-clip", **attrs)

        # nest each covering mic's audio INSIDE this segment, so a ripple-delete
        # of the segment takes its audio with it and keeps everything in sync.
        for a, ms, me in mics:
            if ms <= b0 and b1 <= me:
                a_attrs = dict(ref=a.ref, lane=str(a.lane),
                               offset=T.frames(host_start),     # align to segment
                               name=a.name or a.path.stem,
                               duration=T.frames(b1 - b0),
                               start=T.frames(b0 - ms),         # mic-local in-point
                               tcFormat="NDF", audioRole="dialogue")
                if reason:
                    a_attrs["enabled"] = "0"
                ET.SubElement(seg, "asset-clip", **a_attrs)

        if reason:
            # marker (chevron + Timeline Index entry) AFTER anchored items, so
            # every cut is obvious even though FCP greys disabled clips subtly
            ET.SubElement(seg, "marker", start=T.frames(host_start),
                          duration=T.frames(b1 - b0), value=f"CUT: {reason}")

    _indent(fcpxml)
    out_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
                        + ET.tostring(fcpxml, encoding="unicode") + "\n", encoding="utf-8")
    return out_path


def build_applied(tl: Timeline, out_path: Path,
                  mute_spans: list | None = None,
                  dissolve_s: float = 0.0,
                  gate_user_spans: list | None = None) -> Path:
    """Final edit with the cuts MADE: only KEEP segments survive, concatenated
    back-to-back (cut spans removed, the timeline ripples shorter). Each KEEP
    segment carries its mic audio nested in sync. The result is a finished
    sequence, not a review timeline.

    mute_spans: list of (start_s, end_s) in ORIGINAL timeline seconds where the
    mic audio is disabled (footage kept, audio silenced) — for off-topic talk
    that plays while the client is exercising.
    """
    T = _T(tl.fps_num, tl.fps_den)
    total = sum(v.frames for v in tl.videos)
    mute_f = sorted((T.to_frames(a), T.to_frames(b)) for a, b in (mute_spans or []))
    gate_f = sorted((T.to_frames(a), T.to_frames(b)) for a, b in (gate_user_spans or []))

    def is_muted(f0):
        return any(ms <= f0 < me for ms, me in mute_f)

    def is_user_active(f0):     # mic gate: the client (2nd mic) is the speaker
        return any(gs <= f0 < ge for gs, ge in gate_f)

    fcpxml = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(fcpxml, "resources")
    fmt_id = "r1"
    ET.SubElement(resources, "format", id=fmt_id,
                  name=f"FFVideoFormat{tl.height}p{tl.fps_num // 1000}",
                  frameDuration=T.frame_dur(), width=str(tl.width),
                  height=str(tl.height), colorSpace="1-1-1 (Rec. 709)")

    clip_spans = []   # (ref, tl_start_f, frames, media_start_f, tcfmt, name)
    off = 0
    for i, v in enumerate(tl.videos, 1):
        ref = f"v{i}"
        sf, drop = (parse_timecode(v.timecode, tl.fps_num, tl.fps_den)
                    if v.timecode else (0, False))
        asset = ET.SubElement(resources, "asset", id=ref, name=v.name or v.path.stem,
                              start=T.frames(sf), duration=T.frames(v.frames),
                              hasVideo="1", hasAudio="1", format=fmt_id,
                              videoSources="1", audioSources="1",
                              audioChannels="2", audioRate="48000")
        ET.SubElement(asset, "media-rep", kind="original-media", src=_file_url(v.path))
        clip_spans.append((ref, off, v.frames, sf, "DF" if drop else "NDF",
                           v.name or v.path.stem))
        off += v.frames
    for j, a in enumerate(tl.audios, 1):
        a.ref = f"a{j}"                                   # type: ignore[attr-defined]
        ET.SubElement(ET.SubElement(resources, "asset", id=a.ref,
                      name=a.name or a.path.stem, start="0s",
                      duration=f"{round(a.duration_s * a.sample_rate)}/{a.sample_rate}s",
                      hasVideo="0", hasAudio="1", audioSources="1",
                      audioChannels="1", audioRate=str(a.sample_rate)),
                      "media-rep", kind="original-media", src=_file_url(a.path))

    if dissolve_s > 0:
        # built-in Cross Dissolve + Audio Crossfade (uids from a real FCP export)
        ET.SubElement(resources, "effect", id="rDis", name="Cross Dissolve",
                      uid="FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265")
        ET.SubElement(resources, "effect", id="rAud", name="Audio Crossfade",
                      uid="FFAudioTransition")
    D = T.to_frames(dissolve_s)        # dissolve length in frames

    # mic timeline coverage uses the drift slope: timeline = slope*rec + offset
    mics = [(a, T.to_frames(a.offset_s),
             min(total, T.to_frames(a.offset_s + a.slope * a.duration_s)))
            for a in tl.audios]

    cut_spans = sorted((T.to_frames(c.start_s), T.to_frames(c.end_s))
                       for c in tl.cuts if T.to_frames(c.end_s) > T.to_frames(c.start_s))

    def in_cut(f0):
        return any(cs <= f0 < ce for cs, ce in cut_spans)

    bounds = {0, total}
    for _, ts, fr, _, _, _ in clip_spans:
        bounds.add(ts); bounds.add(ts + fr)
    for cs, ce in cut_spans:
        bounds.add(max(0, min(total, cs))); bounds.add(max(0, min(total, ce)))
    for _, ms, me in mics:
        bounds.add(max(0, min(total, ms))); bounds.add(max(0, min(total, me)))
    for ms, me in mute_f:
        bounds.add(max(0, min(total, ms))); bounds.add(max(0, min(total, me)))
    for gs, ge in gate_f:
        bounds.add(max(0, min(total, gs))); bounds.add(max(0, min(total, ge)))
    bounds = sorted(bounds)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="PT final cut")
    project = ET.SubElement(event, "project", name=tl.project_name)
    sequence = ET.SubElement(project, "sequence", format=fmt_id,
                             tcStart="0s", tcFormat="NDF",
                             audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")

    def add_transition(boundary_f):
        """Cross Dissolve centred on a cut boundary; clips stay back-to-back and
        the dissolve plays over each side's handles (the removed footage)."""
        tr = ET.SubElement(spine, "transition", name="Cross Dissolve",
                           offset=T.frames(boundary_f - D // 2), duration=T.frames(D))
        fv = ET.SubElement(tr, "filter-video", ref="rDis", name="Cross Dissolve")
        ET.SubElement(fv, "param", name="Look", key="1", value="11 (Video)")
        ET.SubElement(fv, "param", name="Amount", key="2", value="100")
        ET.SubElement(fv, "param", name="Ease", key="50", value="2 (In & Out)")
        ET.SubElement(tr, "filter-audio", ref="rAud", name="Audio Crossfade")

    new_off = 0          # running position on the compacted timeline
    prev_orig_end = None  # original-timeline end of the last placed segment
    prev_new_dur = 0      # placed length of the last segment
    for b0, b1 in zip(bounds, bounds[1:]):
        if b1 <= b0 or in_cut(b0):
            continue                                   # drop cut spans entirely
        ref, ts, fr, msf, tcfmt, name = next(
            c for c in clip_spans if c[1] <= b0 < c[1] + c[2])
        host_start = msf + (b0 - ts)
        # a dissolve goes only where footage was actually removed (a gap in the
        # original timeline), and only if both clips can host the overlap
        if (D > 0 and prev_orig_end is not None and b0 > prev_orig_end + 1
                and prev_new_dur >= D and (b1 - b0) >= D and new_off - D // 2 > 0):
            add_transition(new_off)
        seg = ET.SubElement(spine, "asset-clip", ref=ref, offset=T.frames(new_off),
                            name=name, duration=T.frames(b1 - b0),
                            start=T.frames(host_start), tcFormat=tcfmt,
                            srcEnable="video")   # drop DJI camera audio (some FCP
                                                 # versions ignore this attribute)
        # belt-and-suspenders: force the camera audio to silence — adjust-volume
        # is always honored on import, unlike srcEnable. Must come before the
        # nested (anchored) lav clips per the DTD content model.
        ET.SubElement(seg, "adjust-volume", amount="-96dB")
        for i, (a, ms, me) in enumerate(mics):
            if ms <= b0 and b1 <= me:
                # drift-corrected mic in-point: rec_s = (timeline_s - offset)/slope
                rec_s = (b0 * T.d / T.n - a.offset_s) / a.slope
                ac = dict(ref=a.ref, lane=str(a.lane), offset=T.frames(host_start),
                          name=a.name or a.path.stem, duration=T.frames(b1 - b0),
                          start=T.secs(rec_s), tcFormat="NDF",
                          audioRole="dialogue")
                # mic gate: trainer (primary) plays, except when the client is
                # the active speaker -> their lav plays instead. Never both, so
                # no echo. Off-topic spans mute whichever would play.
                if i == 0:
                    disabled = is_muted(b0) or is_user_active(b0)
                elif i == 1:
                    disabled = is_muted(b0) or not is_user_active(b0)
                else:
                    disabled = True
                if disabled:
                    ac["enabled"] = "0"
                ET.SubElement(seg, "asset-clip", **ac)
        new_off += b1 - b0
        prev_orig_end, prev_new_dur = b1, b1 - b0

    sequence.set("duration", T.frames(new_off))
    _indent(fcpxml)
    out_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
                        + ET.tostring(fcpxml, encoding="unicode") + "\n", encoding="utf-8")
    return out_path


def _indent(elem, level=0):
    pad = "\n" + "    " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "    "
        for child in elem:
            _indent(child, level + 1)
            if not (child.tail or "").strip():
                child.tail = pad + "    "
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    elif level and not (elem.tail or "").strip():
        elem.tail = pad


def check_media(path: Path) -> list[str]:
    """Return the names of any media-rep files the FCPXML references that don't
    resolve on disk — catches bad URL encoding or moved/renamed media before the
    file ever reaches FCP (where it would show as a red 'missing media' clip)."""
    missing = []
    for mr in ET.parse(path).getroot().iter("media-rep"):
        src = mr.get("src", "")
        if src.startswith("file://"):
            fp = Path(unquote(src[len("file://"):]))
            if not fp.exists():
                missing.append(fp.name)
    return missing


def validate(path: Path, version: str = FCPXML_VERSION) -> tuple[bool, str]:
    """Validate against Apple's bundled FCPXML DTD AND confirm every referenced
    media file resolves on disk.

    The DTD lives under a path containing spaces, which trips up xmllint's
    in-place DTD resolution, so we copy it to a temp file first.
    """
    import shutil
    import tempfile

    missing = check_media(path)
    if missing:
        return False, "missing media (would show red in FCP): " + ", ".join(missing)

    dtd = Path("/Applications/Final Cut Pro.app/Contents/Frameworks/"
               "Interchange.framework/Versions/A/Resources/"
               f"FCPXMLv{version.replace('.', '_')}.dtd")
    if not dtd.exists():
        return True, f"(DTD not found at {dtd}; media OK; skipped DTD check)"
    with tempfile.NamedTemporaryFile(suffix=".dtd", delete=False) as tmp:
        shutil.copyfile(dtd, tmp.name)
        local_dtd = tmp.name
    try:
        r = subprocess.run(["xmllint", "--noout", "--dtdvalid", local_dtd,
                            str(path)], capture_output=True, text=True)
    finally:
        Path(local_dtd).unlink(missing_ok=True)
    return r.returncode == 0, (r.stderr.strip() or "valid (DTD + media)")
