"""Audio-based sync: map a recorder's own time onto the FCP timeline.

The trainer mic (REC*.wav) was recorded independently of the camera, so its
internal t=0 is not the timeline's 00:00:00. FCP's Synchronized Clip lines them
up by cross-correlating audio; this module reproduces that number so cuts land
on the timeline without any FCP export.

Approach: compute a coarse energy envelope (default 100 fps) for each signal and
cross-correlate. Two correlation windows (near the anchor's start and end) give
two (recorder-time -> timeline-time) points; the line through them corrects both
a constant offset and any clock drift between the two devices.

Timeline 0 is defined as the start of the anchor video clip.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FPS = 100              # envelope frames per second (10ms hop)
SR = 16000             # audio sample rate for decoding


# --- audio loading --------------------------------------------------------

def load_audio(path: Path, start: float | None = None,
               dur: float | None = None, sr: int = SR) -> np.ndarray:
    """Decode (a slice of) a media file's first audio stream to mono float32."""
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-map", "a:0", "-ac", "1", "-ar", str(sr),
            "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def envelope(x: np.ndarray, sr: int = SR, fps: int = FPS) -> np.ndarray:
    """RMS energy envelope at `fps` frames/sec."""
    hop = sr // fps
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    frames = x[:n * hop].astype(np.float64).reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


def media_envelope(path: Path, start: float | None = None,
                   dur: float | None = None, fps: int = FPS) -> np.ndarray:
    return envelope(load_audio(path, start, dur), fps=fps)


def speech_envelope(path: Path, fps: int = FPS, sr: int = 8000,
                    lo: int = 300, hi: int = 3400, win: int = 256) -> np.ndarray:
    """Log-compressed speech-band energy envelope.

    Band-limiting to the voice range and log-compressing the level lets a quiet
    *shared* voice (e.g. the trainer bleeding faintly into the client's lav)
    drive the correlation, instead of each mic's own loud close-up noise. This
    is what makes two very different-sounding mics sync on "what is said".
    """
    x = load_audio(path, sr=sr)
    hop = sr // fps
    n = (len(x) - win) // hop
    if n <= 0:
        return np.zeros(0)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(win), axis=1))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = (freqs >= lo) & (freqs <= hi)
    return np.log1p(spec[:, band].sum(axis=1))


# --- cross-correlation ----------------------------------------------------

def xcorr_lag(template: np.ndarray, signal: np.ndarray) -> tuple[int, float]:
    """Find the lag (in frames) where `template` best aligns inside `signal`.

    Returns (lag, score) such that signal[lag : lag+len(template)] ~ template.
    `lag` may be negative (template starts before signal). `score` is the
    normalised peak correlation in [0, 1]; higher = more confident.
    """
    t = template - template.mean()
    s = signal - signal.mean()
    n = len(s) + len(t) - 1
    nfft = 1 << int(np.ceil(np.log2(n)))
    cc = np.fft.irfft(np.fft.rfft(s, nfft) * np.conj(np.fft.rfft(t, nfft)), nfft)
    # full-correlation layout: index i -> lag = i for i <= len(s)-1,
    # and the negative lags wrap to the tail of the array.
    cc = np.concatenate([cc[-(len(t) - 1):], cc[:len(s)]])  # lags -(M-1)..(N-1)
    peak = int(np.argmax(cc))
    lag = peak - (len(t) - 1)
    # confidence: normalised cross-correlation against the LOCAL signal window
    # at the matched position (not the whole signal), so a clean match -> ~1.
    lo, hi = max(0, lag), min(len(s), lag + len(t))
    win = s[lo:hi]
    tt = t[lo - lag:lo - lag + len(win)]
    denom = np.sqrt((tt ** 2).sum()) * np.sqrt((win ** 2).sum()) + 1e-12
    score = float((tt * win).sum() / denom)
    return lag, score


# --- sync model -----------------------------------------------------------

@dataclass
class SyncModel:
    slope: float        # timeline_seconds per recorder_second (~1.0)
    intercept: float    # timeline seconds at recorder t=0
    anchor: Path        # clip whose start == timeline 00:00:00
    timeline_end: float          # seconds
    points: list[tuple[float, float]]   # (recorder_s, timeline_s) used for fit
    drift_ms_per_min: float
    confidence: float            # median peak correlation of fit windows
    residual_ms: float           # worst fit residual = alignment accuracy
    clips: list[tuple[str, float]]   # (name, timeline_start_s) in order

    def rec_ms_to_timeline_ms(self, ms: int) -> float:
        return (self.slope * (ms / 1000.0) + self.intercept) * 1000.0


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _locate(rec_env: np.ndarray, video: Path, at_s: float, win_s: float,
            fps: int) -> tuple[float, float]:
    """Correlate a window of `video` (starting at clip-time `at_s`) against the
    recorder envelope. Returns (recorder_time_s, score) for that window start.
    """
    tmpl = media_envelope(video, start=at_s, dur=win_s, fps=fps)
    lag, score = xcorr_lag(tmpl, rec_env)
    return lag / fps, score


def build_model(rec_path: Path, videos: list[Path],
                win_s: float = 120.0, fps: int = FPS) -> SyncModel:
    """Fit a recorder->timeline line by cross-correlating the recorder against
    each reference clip.

    The reference clips are sequential, contiguous segments of the timeline, so
    each clip's timeline start is the cumulative duration of earlier clips. We
    sample a few mid-clip windows per clip (avoiding edges), turn each into a
    (recorder_time, timeline_time) point, then robustly fit a single line
    spanning the whole session — correcting constant offset AND clock drift.
    """
    rec_env = media_envelope(rec_path, fps=fps)

    # 1. coarse-locate each clip's start to establish timeline order.
    # Use the best-scoring of several windows per clip: a single mid-clip window
    # can land on quiet/ambiguous audio (low score) and mislocate by minutes,
    # which would corrupt the timeline order and every downstream cut.
    clips = []
    for v in videos:
        dur = ffprobe_duration(v)
        cand = []
        for frac in (0.15, 0.5, 0.85):
            ws = max(0.0, min(dur - win_s, dur * frac))
            rec_s, sc = _locate(rec_env, v, ws, min(win_s, dur), fps)
            cand.append((sc, rec_s - ws))
        _, start_rec = max(cand)                 # most confident window wins
        clips.append({"path": v, "dur": dur, "start_rec": start_rec})
    clips.sort(key=lambda c: c["start_rec"])     # timeline order = order in rec

    # 2. assign each clip its timeline start (cumulative duration)
    tl = 0.0
    for c in clips:
        c["tl_start"] = tl
        tl += c["dur"]
    timeline_end = tl

    # 3. gather fit points: a few windows per clip, spanning the whole timeline
    pts: list[tuple[float, float, float]] = []   # (rec_s, timeline_s, score)
    for c in clips:
        for frac in (0.15, 0.5, 0.85):
            ws = max(0.0, min(c["dur"] - win_s, c["dur"] * frac))
            rec_s, sc = _locate(rec_env, c["path"], ws, min(win_s, c["dur"]), fps)
            pts.append((rec_s, c["tl_start"] + ws, sc))

    # 4. robust linear fit, keeping confident windows and rejecting outliers
    good = [(r, t) for (r, t, s) in pts if s >= 0.40]
    if len(good) < 2:
        good = [(r, t) for (r, t, s) in sorted(pts, key=lambda p: -p[2])[:4]]
    R = np.array([g[0] for g in good])
    T = np.array([g[1] for g in good])
    slope, intercept = np.polyfit(R, T, 1)
    resid = T - (slope * R + intercept)
    keep = np.abs(resid) <= max(0.5, 3.0 * np.std(resid))
    if keep.sum() >= 2:
        slope, intercept = np.polyfit(R[keep], T[keep], 1)
        resid = T[keep] - (slope * R[keep] + intercept)

    confidence = float(np.median([s for (_, _, s) in pts if s >= 0.40] or
                                 [s for (_, _, s) in pts]))
    return SyncModel(
        slope=float(slope),
        intercept=float(intercept),
        anchor=clips[0]["path"],
        timeline_end=timeline_end,
        points=[(float(r), float(t)) for r, t in good],
        drift_ms_per_min=(float(slope) - 1.0) * 60_000.0,
        confidence=confidence,
        residual_ms=float(np.max(np.abs(resid)) * 1000.0),
        clips=[(c["path"].name, c["tl_start"]) for c in clips],
    )


@dataclass
class MicAlignment:
    slope: float          # ref_seconds per mic_second (~1.0)
    intercept: float      # ref seconds at mic t=0
    confidence: float
    residual_ms: float
    n_windows: int

    def mic_s_to_ref_s(self, sec: float) -> float:
        return self.slope * sec + self.intercept


def align_to_reference(mic_path: Path, ref_path: Path, fps: int = FPS,
                       win_s: float = 60.0, step_s: float = 15.0,
                       min_conf: float = 0.45) -> MicAlignment:
    """Map a secondary mic's time onto a reference mic's time by sliding speech
    windows of the reference across the mic and taking the consensus offset.

    Robust to mics that share only faint speech: it locates many reference
    windows in the mic, keeps confident matches, clusters them on the dominant
    offset (rejecting spurious peaks), and fits a `mic -> ref` line.
    """
    er = speech_envelope(ref_path, fps=fps)
    em = speech_envelope(mic_path, fps=fps)
    W = int(win_s * fps)
    pts = []   # (mic_s, ref_s, score)
    for s in range(int(30 * fps), len(er) - W, int(step_s * fps)):
        lag, sc = xcorr_lag(er[s:s + W], em)   # ref window located in the mic
        if sc >= min_conf:
            pts.append((lag / fps, s / fps, sc))
    if len(pts) < 2:
        raise RuntimeError(f"could not align {mic_path.name}: too few matches")

    # keep the dominant offset cluster (mic_s - ref_s within 0.5s of the mode)
    deltas = np.array([m - r for m, r, _ in pts])
    best_center, best_n = deltas[0], 0
    for d in deltas:
        n = int(np.sum(np.abs(deltas - d) < 0.5))
        if n > best_n:
            best_center, best_n = d, n
    keep = [p for p, d in zip(pts, deltas) if abs(d - best_center) < 0.5]

    M = np.array([p[0] for p in keep])
    R = np.array([p[1] for p in keep])
    slope, intercept = np.polyfit(M, R, 1)
    resid = R - (slope * M + intercept)
    return MicAlignment(
        slope=float(slope), intercept=float(intercept),
        confidence=float(np.median([p[2] for p in keep])),
        residual_ms=float(np.max(np.abs(resid)) * 1000.0),
        n_windows=len(keep),
    )


def compute_mic_gate(user_path: Path, ref_path: Path,
                     user_off: float, user_slope: float,
                     ref_off: float, ref_slope: float, timeline_end: float,
                     threshold_db: float = 6.0, smooth_s: float = 0.3,
                     min_s: float = 0.8, merge_gap_s: float = 0.5,
                     fps: int = FPS) -> list[tuple[float, float]]:
    """Timeline spans where the *user* (2nd lav) is the dominant speaker.

    Mic gate for two close lavs of the same room: at each instant compare the
    user mic's energy to the trainer mic's. Where the user mic is clearly louder
    (they're talking into their own close mic), the user is speaking — play their
    lav there and the trainer's elsewhere, never both, so there's no echo. Both
    loud (or both quiet) stays on the trainer, so no coaching is dropped.

    The (off, slope) pairs map each recorder's time onto the timeline
    (timeline_s = slope*rec_s + off).
    """
    eu = media_envelope(user_path, fps=fps)
    er = media_envelope(ref_path, fps=fps)
    diff, ts = [], []
    for fi in range(int(ref_off * fps), int(timeline_end * fps)):
        t = fi / fps
        iu = int((t - user_off) / user_slope * fps)
        ir = int((t - ref_off) / ref_slope * fps)
        if 0 <= iu < len(eu) and 0 <= ir < len(er) and eu[iu] > 0 and er[ir] > 0:
            diff.append(20 * np.log10(eu[iu] / er[ir]))
            ts.append(t)
    if not diff:
        return []
    diff = np.array(diff)
    ts = np.array(ts)
    k = max(1, int(smooth_s * fps))
    active = np.convolve(diff, np.ones(k) / k, mode="same") > threshold_db

    spans, i = [], 0
    while i < len(active):
        if active[i]:
            j = i
            while j < len(active) and active[j]:
                j += 1
            spans.append([ts[i], ts[j - 1]])
            i = j
        else:
            i += 1
    merged = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < merge_gap_s:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(a, b) for a, b in merged if b - a > min_s]
