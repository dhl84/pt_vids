#!/bin/sh
# usage: tools/fcp/export_project.sh <final_cut.fcpxml> "<Project name>" [<out dir>]
#
# Import the FCPXML into the open Final Cut Pro library (the PT library,
# ~/Movies/Untitled.fcpbundle), open the project, and export it as HEVC 8-bit
# 3840x2160 MP4 with "Save only". Drives Final Cut through accessibility in the
# background; the Command-E step brings Final Cut to the front for about ten
# seconds (the Share menu is unreliable through accessibility).
# The project name in the FCPXML must be unique in the library: rewrite it first,
#   sed 's/<project name="[^"]*"/<project name="RMU PT 30 Sep 26"/' final_cut_dissolves.fcpxml > "RMU PT 30 Sep 26.fcpxml"
# The out dir defaults to ~/Movies (upload from the local disk, not the NAS).
set -e
S=$(cd "$(dirname "$0")" && pwd); XML="$1"; PR="$2"; OUT="${3:-$HOME/Movies}"; LIB="${FCP_LIBRARY:-$HOME/Movies/Untitled.fcpbundle}"
pgrep -x "Final Cut Pro" >/dev/null || { open -g -a "Final Cut Pro" "$LIB"; sleep 25; }
open -g -a "Final Cut Pro" "$XML"; sleep 12
# library chooser (appears when the file is opened from outside Final Cut)
osascript - "$(basename "$LIB")" <<'AS'
on run argv
  with timeout of 120 seconds
  tell application "System Events" to tell process "Final Cut Pro"
    if not (exists window "Open Library") then return "no chooser"
    set w to window "Open Library"
    repeat with e in entire contents of w
      try
        if role of e is "AXRow" then
          set t to ""
          repeat with c in UI elements of e
            try
              repeat with cc in UI elements of c
                try
                  set t to t & (value of cc as text) & "/"
                end try
              end repeat
            end try
          end repeat
          if t contains (item 1 of argv) then
            set selected of e to true
            exit repeat
          end if
        end if
      end try
    end repeat
    delay 1
    click button "Choose" of w
    return "library chosen"
  end tell
  end timeout
end run
AS
sleep 15
# open the project (press the browser tile with the project's name)
osascript - "$PR" <<'AS'
on run argv
  with timeout of 300 seconds
  tell application "System Events" to tell process "Final Cut Pro"
    repeat with g in entire contents of window "Final Cut Pro"
      try
        if role of g is "AXGroup" and description of g is (item 1 of argv) then
          perform action "AXPress" of g
          return "opened " & (item 1 of argv)
        end if
      end try
    end repeat
    error "project tile not found: " & (item 1 of argv)
  end tell
  end timeout
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
# save sheet: folder via Command-Shift-G (Final Cut in front for a moment), then the name
osascript - "$OUT" "$PR" <<'AS'
on run argv
  with timeout of 120 seconds
  tell application "System Events"
    set prev to name of first process whose frontmost is true
    tell process "Final Cut Pro"
      set frontmost to true
      delay 1.5
      keystroke "g" using {command down, shift down}
      delay 2.5
      set sv to sheet 1 of window "Export File"
      if (count of sheets of sv) > 0 then
        repeat with e in entire contents of (sheet 1 of sv)
          try
            if role of e is "AXTextField" or role of e is "AXComboBox" then
              set value of e to (item 1 of argv)
              exit repeat
            end if
          end try
        end repeat
        delay 1
        keystroke return
        delay 3
      end if
      set nm to ""
      set wh to ""
      repeat with e in entire contents of window "Export File"
        try
          if role of e is "AXTextField" and name of e is "Save As:" then
            set value of e to (item 2 of argv)
            set nm to value of e
          end if
          if role of e is "AXPopUpButton" and name of e is "Where:" then set wh to value of e
        end try
      end repeat
    end tell
    if prev is not "Final Cut Pro" then tell process prev to set frontmost to true
    return "name=[" & nm & "] where=[" & wh & "]"
  end tell
  end timeout
end run
AS
"$S/clickname.sh" "Export File" "Save"
echo "export started: $OUT/$PR.mp4 (watch Window > Background Tasks > Sharing)"
