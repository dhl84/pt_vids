#!/usr/bin/env python3
"""Dump the AX tree of a Final Cut Pro window. usage: ax.py [window-index|name] [maxdepth] [filter]"""
import subprocess, sys
win = sys.argv[1] if len(sys.argv) > 1 else "1"
depth = sys.argv[2] if len(sys.argv) > 2 else "6"
flt = sys.argv[3] if len(sys.argv) > 3 else ""
maxy = sys.argv[4] if len(sys.argv) > 4 else "100000"
wref = f"window {win}" if win.isdigit() else f'window "{win}"'
script = '''
using terms from application "System Events"
on walk(e, dep, maxd)
  set acc to ""
  try
    set rol to role of e
  on error
    return ""
  end try
  set namz to ""
  try
    set namz to name of e as text
  end try
  set valz to ""
  try
    set valz to value of e as text
  end try
  set descr to ""
  try
    set descr to description of e as text
  end try
  set subr to ""
  try
    set subr to subrole of e as text
  end try
  set dis to ""
  try
    if enabled of e is false then set dis to " DISABLED"
  end try
  set posz to ""
  try
    set p to position of e
    set sizz to size of e
    set posz to " @" & (item 1 of p) & "," & (item 2 of p) & " " & (item 1 of sizz) & "x" & (item 2 of sizz)
    if (item 2 of p) > MAXY then return ""
  end try
  set txt to ""
  repeat dep times
    set txt to txt & "  "
  end repeat
  set txt to txt & rol
  if subr is not "" and subr is not "missing value" then set txt to txt & "(" & subr & ")"
  if namz is not "" and namz is not "missing value" then set txt to txt & " name=[" & namz & "]"
  if descr is not "" and descr is not "missing value" and descr is not namz then set txt to txt & " desc=[" & descr & "]"
  if valz is not "" and valz is not "missing value" then set txt to txt & " value=[" & valz & "]"
  set acc to acc & txt & dis & posz & linefeed
  if dep < maxd then
    try
      repeat with c in UI elements of e
        set acc to acc & walk(c, dep + 1, maxd)
      end repeat
    end try
  end if
  return acc
end walk
end using terms from
tell application "System Events"
  tell process "Final Cut Pro"
    return my walk(WREF, 0, DEPTH)
  end tell
end tell
'''.replace("WREF", wref).replace("DEPTH", depth).replace("MAXY", maxy)
r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True)
txt = r.stdout if r.returncode == 0 else r.stderr
for ln in txt.splitlines():
    if not flt or flt.lower() in ln.lower():
        print(ln)
