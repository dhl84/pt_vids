"""Self-check for flag_camera_motion: silent gaps where the camera is moving get
cut, static ones (reps) stay, short gaps and classifier-cuts are left alone. No
ffmpeg — a fake pan_fn supplies per-frame translation magnitudes."""
from types import SimpleNamespace
from main import flag_camera_motion, Range, CAMERA_MOTION_MIN_GAP_S


def _run():
    # one 30s clip on the timeline; fake pan_fn keyed by in-clip offset window
    model = SimpleNamespace(clips=[("A.MP4", 0.0)], timeline_end=30.0)
    videos = [__import__("pathlib").Path("A.MP4")]

    def pan_fn(clip, off, dur):
        # the 10..20s stretch pans hard (mostly >=2px); elsewhere the camera is
        # static and only a moving subject jitters it (mostly 0, rare spike)
        if 10.0 <= off < 20.0:
            return [3.0] * 8 + [0.0] * 2          # 80% moving -> repositioning
        return [0.0] * 7 + [3.0] * 3              # 30% < 0.35 thresh -> reps

    ranges = [
        Range(0, 8_000, "KEEP", ""),                       # static silent reps
        Range(8_000, 9_000, "KEEP", ""),                   # too short to inspect
        Range(10_000, 20_000, "KEEP", ""),                 # camera repositioning
        Range(20_000, 28_000, "CUT", "off-topic chatter"), # classifier cut, leave
    ]
    return flag_camera_motion(ranges, model, videos, pan_fn=pan_fn)


def test_motion():
    r = _run()
    assert r[0].label == "KEEP", "static silent reps must stay KEEP"
    assert r[1].label == "KEEP", "sub-threshold-length gap must be ignored"
    assert r[2].label == "CUT", "camera-motion gap must flip to CUT"
    assert "repositioning" in r[2].reason.lower()
    assert r[3].reason == "off-topic chatter", "classifier CUT must be untouched"
    # the short gap is below the minimum inspection length
    assert (r[1].end_ms - r[1].start_ms) / 1000.0 < CAMERA_MOTION_MIN_GAP_S


if __name__ == "__main__":
    test_motion()
    print("ok")
