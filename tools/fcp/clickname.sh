#!/bin/sh
# usage: clickname.sh <window index or name> <button name>
osascript - "$1" "$2" <<'AS'
on run argv
  set w to item 1 of argv
  set nm to item 2 of argv
  tell application "System Events" to tell process "Final Cut Pro"
    if w = "1" or w = "2" or w = "3" then
      set win to window (w as integer)
    else
      set win to window w
    end if
    set els to entire contents of win
    repeat with e in els
      try
        if name of e is nm then
          click e
          return "clicked " & nm
        end if
      end try
    end repeat
    return "not found: " & nm
  end tell
end run
AS
