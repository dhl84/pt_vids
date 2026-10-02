"""Digest a PT session transcript into the cues that describe the workout.

Whisper transcribes the whole session; most cues are coaching chatter. This
prints only the cues that carry exercise facts (sets, reps, kg, bands, holds,
movement names, pain), with timestamps, so a reviewer (a person or Claude in
session) can write the AtomaWOD record from a few hundred lines instead of
2,500. The record itself is authored, not parsed: see tools/ATOMAWOD.md.

Usage:
  uv run tools/workout_scan.py digest trainer.srt [--second client.srt] [--offset S]
      --offset shifts timestamps (seconds) e.g. the sync intercept, so the
      digest lines up with the camera timeline. --second merges a second lav
      (tagged B) for the cross-check of numbers the trainer mic missed.
  uv run tools/workout_scan.py template > pt_record.json
      Prints the two-part JSON skeleton (sessions.json entry + actuals entry).
"""
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402  (parse_srt)

# ponytail: one regex of workout words; extend when a session uses a new term.
WORDS = r"""
 rep|reps|set|sets|round|rounds|kg|kilo|kilos|pound|lb|band|banded|red|black|blue|green|purple|
 hold|hang|negative|negatives|transition|muscle.?up|pull.?up|chin.?up|dip|dips|row|rows|push.?down|
 pull.?down|pulldown|press|squat|deadlift|clean|snatch|kip|swing|swings|hollow|arch|false.?grip|
 support|ring|rings|bar|rope|cable|plate|dumbbell|kettlebell|sandbag|box|
 second|seconds|minute|minutes|amrap|emom|max|heavier|lighter|weight|
 pain|hurt|hurts|sore|shoulder|elbow|wrist|back|knee|
 stretch|mobility|warm.?up|rest|go again|one more|last set|next set|
 one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|hundred|half
"""
PAT = re.compile(r"\b(?:" + "|".join(w.strip() for w in WORDS.split("|") if w.strip()) + r")\b|\b\d+\b", re.I)
JUNK = re.compile(r"^((?:\w+[,.]? ?){1,2})\1{3,}", re.I)   # whisper loops ("two, two, two...")


def tc(s: float) -> str:
    s = int(s); return f"{s // 60:02d}:{s % 60:02d}"


def digest(primary: Path, second: Path | None, offset: float) -> list[str]:
    rows = []
    for path, tag in ((primary, "A"), (second, "B")):
        if not path:
            continue
        for seg in main.parse_srt(path):
            text = seg.text.strip()
            if JUNK.match(text) or not PAT.search(text):
                continue
            rows.append((seg.start_ms / 1000 + offset, tag, seg.index, text))
    rows.sort()
    return [f"{tc(t)} {tag}{i} {text}" for t, tag, i, text in rows]


TEMPLATE = {
    "session": {  # one sessions.json entry (pt-sessions/README.md in AtomaWOD)
        "date": "YYYY-MM-DD", "start_time": "HH:MM",
        "photo": "video: <file or YouTube title>",
        "name": "PT — <focus>",
        "sections": [{
            "label": "A — <block>", "section_type": "skill", "scoring_type": "sets_reps",
            "rep_pattern": [5, 5, 5, 5], "set_count": 4, "notes": "<whiteboard-style prescription>",
            "components": [{"movement": "Muscle-Up (Ring)", "reps": 5, "variation": "Strict Transition Drill"}],
        }],
    },
    "actuals": {  # one actuals.json entry, same date + start_time
        "date": "YYYY-MM-DD", "start_time": "HH:MM",
        "source": "video: <title> (trainer lav transcript + frame review)",
        "notes": None,
        "sections": [{
            "label": "A — <block>", "is_rx": False, "rpe": None, "notes": None,
            "components": [{
                "movement": "Muscle-Up (Ring)", "variation": "Strict Transition Drill",
                "sets": [{"set": 1, "reps": 5, "weight_kg": None, "duration_sec": None, "notes": None}],
            }],
        }],
    },
}


def demo() -> None:
    assert PAT.search("Four sets of five reps of some transition drills")
    assert PAT.search("make that a little bit heavier")
    assert not PAT.search("Yeah, exactly.")
    assert JUNK.match("two, two, two, two, two, two")
    assert not JUNK.match("Four sets of three reps")
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest"); d.add_argument("srt", type=Path)
    d.add_argument("--second", type=Path); d.add_argument("--offset", type=float, default=0.0)
    sub.add_parser("template"); sub.add_parser("demo")
    a = ap.parse_args()
    if a.cmd == "digest":
        print("\n".join(digest(a.srt, a.second, a.offset)))
    elif a.cmd == "template":
        print(json.dumps(TEMPLATE, indent=1, ensure_ascii=False))
    else:
        demo()
