#!/bin/sh
# usage: share_cmde.sh -- open the Export File sheet for the timeline project with Command-E (FCP active for ~10 s), up to 3 tries
for n in 1 2 3; do
  r=$(osascript <<'AS'
with timeout of 120 seconds
tell application "System Events"
  set prev to name of first process whose frontmost is true
  tell process "Final Cut Pro"
    set frontmost to true
    delay 2
    try
      if exists window "Background Tasks" then click (first button of window "Background Tasks" whose subrole is "AXCloseButton")
    end try
    delay 1
    click menu item "Timeline" of menu 1 of menu item "Go To" of menu "Window" of menu bar 1
    delay 1.5
    try
      click menu item "Clear Selected Ranges" of menu "Mark" of menu bar 1
    end try
    delay 0.8
    try
      click menu item "Deselect All" of menu "Edit" of menu bar 1
    end try
    delay 0.8
    keystroke "e" using command down
    delay 6
    set ex to exists window "Export File"
  end tell
  if prev is not "Final Cut Pro" then tell process prev to set frontmost to true
  return "sheet=" & ex
end tell
end timeout
AS
)
  echo "$r"; case "$r" in *"sheet=true"*) exit 0;; esac; sleep 3
done
exit 1
