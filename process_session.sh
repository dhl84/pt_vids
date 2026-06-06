#!/bin/bash
#
# Process one PT session end to end: transcribe the trainer mic, sync it to the
# camera timeline, classify keep/cut, and emit a review-ready cut list plus an
# FCPXML you import into Final Cut Pro to verify cuts before committing.
#
# Usage:
#   ./process_session.sh <trainer_mic.(wav|m4a|...)> <folder-or-video-refs...>
#
# Examples:
#   # mic + a folder containing the camera clips (anchor auto-detected):
#   ./process_session.sh ~/Downloads/pt/28-may-26/REC00003.wav ~/Downloads/pt/28-may-26
#
#   # mic + explicit camera clips:
#   ./process_session.sh mic.wav clipA.MP4 clipB.MP4 clipC.MP4
#
# Outputs (written next to the mic file):
#   <mic>.srt          full transcript, timeline-aligned
#   review.md          table of CUT ranges (start | end | duration | reason)
#   cuts.txt           plain in/out ranges
#   cut_review.fcpxml  the synced timeline with a marker at every proposed cut
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Prefer the locally-downloaded whisper weights (no network needed).
if [ -d "$HERE/models/whisper-large-v3-turbo" ]; then
  export WHISPER_MODEL="${WHISPER_MODEL:-$HERE/models/whisper-large-v3-turbo}"
fi

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <trainer_mic> <folder-or-video-refs...>" >&2
  exit 2
fi

MIC="$1"; shift
exec uv run --project "$HERE" main.py "$MIC" --sync-ref "$@"
