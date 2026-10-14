#!/bin/sh
# usage: tools/fcp/export_project.sh <final_cut.fcpxml> "<Project name>" [<out dir>]
#
# Import the FCPXML into the PT library (FCP_LIBRARY, default ~/Movies/PT.fcpbundle;
# make it once with File > New > Library), open the project, and export it as HEVC 8-bit
# 3840x2160 MP4 with "Save only". Drives Final Cut through accessibility in the
# background; the Command-E step brings Final Cut to the front for about ten
# seconds (the Share menu is unreliable through accessibility).
# The project name in the FCPXML must be unique in the library: rewrite it first,
#   sed 's/<project name="[^"]*"/<project name="RMU PT 30 Sep 26"/' final_cut_dissolves.fcpxml > "RMU PT 30 Sep 26.fcpxml"
# The out dir defaults to ~/Movies (upload from the local disk, not the NAS).
set -e
S=$(cd "$(dirname "$0")" && pwd); XML="$1"; PR="$2"; OUT="${3:-$HOME/Movies}"; LIB="${FCP_LIBRARY:-$HOME/Movies/PT.fcpbundle}"
pgrep -x "Final Cut Pro" >/dev/null || { open -g -a "Final Cut Pro" "$LIB"; sleep 25; }
open -g -a "Final Cut Pro" "$XML"; sleep 12
# library chooser (appears when the file is opened from outside Final Cut). Its list only
# shows libraries Final Cut remembers; a new library is not in it, so "Locate…" with a typed path is the sure route.
osascript - "$LIB" <<'AS'
on run argv
  with timeout of 120 seconds
  tell application "System Events"
    set prev to name of first process whose frontmost is true
    tell process "Final Cut Pro"
      if not (exists window "Open Library") then return "no chooser"
      set frontmost to true
      delay 1
      click button "Locate…" of window "Open Library"
      delay 3
      keystroke "g" using {command down, shift down}
      delay 2.5
      keystroke (item 1 of argv)
      delay 1.5
      keystroke return
      delay 2.5
      keystroke return
      delay 4
    end tell
    if prev is not "Final Cut Pro" then tell process prev to set frontmost to true
    return "library located"
  end tell
  end timeout
end run
AS
sleep 15
# open the project: press the browser tile with the project's name. A bounded recursive search;
# `entire contents` of the whole window times out on a large browser.
osascript - "$PR" <<'AS'
using terms from application "System Events"
  on findPress(e, dep, want)
    if dep > 16 then return false
    try
      if role of e is "AXGroup" and description of e is want then
        perform action "AXPress" of e
        return true
      end if
    end try
    try
      repeat with c in UI elements of e
        if my findPress(c, dep + 1, want) then return true
      end repeat
    end try
    return false
  end findPress
end using terms from
on run argv
  with timeout of 300 seconds
  tell application "System Events" to tell process "Final Cut Pro"
    if not (my findPress(window "Final Cut Pro", 0, item 1 of argv)) then error "project tile not found: " & (item 1 of argv)
  end tell
  end timeout
  return "opened " & (item 1 of argv)
end run
AS
for i in $(seq 1 24); do sleep 5; d=$(python3 "$S/ax.py" "Final Cut Pro" 8 2>/dev/null | grep "ProjectInfo" | sed 's/.*value=\[\([^]]*\)\].*/\1/'); case "$d" in ""|00:00) ;; *) echo "timeline=$d"; break;; esac; done
"$S/share_cmde.sh"
osascript <<'AS'
with timeout of 180 seconds
tell application "System Events" to tell process "Final Cut Pro"
  set w to window "Export File"
  click radio button "Settings" of tab group 1 of w
  delay 1.5
  set tg to tab group 1 of w
  repeat with pair in {{"Format:", "Computer"}, {"Video Codec:", "HEVC 8-bit"}, {"Resolution:", "3840 x 2160"}, {"Action:", "Save only"}}
    set pb to pop up button (item 1 of pair) of tg
    if value of pb is not (item 2 of pair) then
      click pb
      delay 1
      click menu item (item 2 of pair) of menu 1 of pb
      delay 1.5
    end if
  end repeat
  click button "Next…" of w
end tell
end timeout
AS
sleep 4
# save sheet: the name field already holds the project name; set the folder via Command-Shift-G (Final Cut in front for a moment)
osascript - "$OUT" "$PR" <<'AS'
on run argv
  with timeout of 120 seconds
  tell application "System Events"
    set prev to name of first process whose frontmost is true
    tell process "Final Cut Pro"
      set frontmost to true
      delay 1.5
      -- type the folder into the Go To box: `set value` does not register, keystrokes do
      keystroke "g" using {command down, shift down}
      delay 2.5
      keystroke (item 1 of argv)
      delay 1.5
      keystroke return
      delay 3
    end tell
    if prev is not "Final Cut Pro" then tell process prev to set frontmost to true
  end tell
  end timeout
end run
AS
# read the sheet back with the AX dump (entire contents of the sheet is empty while Final Cut is in the background)
WH=$(python3 "$S/ax.py" "Export File" 12 2>/dev/null | grep "name=\[Where:\]" | sed 's/.*value=\[\([^]]*\)\].*/\1/')
NM=$(python3 "$S/ax.py" "Export File" 12 2>/dev/null | grep "AXTextField name=\[Save As:\]" | sed 's/.*value=\[\([^]]*\)\].*/\1/')
echo "where=[$WH] name=[$NM]"
[ "$WH" = "$(basename "$OUT")" ] || { echo "folder is not $(basename "$OUT"); not saving"; exit 1; }
[ "$NM" = "$PR.mp4" ] || [ "$NM" = "$PR" ] || echo "warning: name is [$NM]"
"$S/clickname.sh" "Export File" "Save"
echo "export started: $OUT/$PR.mp4 (watch Window > Background Tasks > Sharing)"
