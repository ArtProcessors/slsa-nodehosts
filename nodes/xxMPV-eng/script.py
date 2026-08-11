'''
mpv Playlist Controller
=======================

Monitors a folder and lets you build an mpv playlist from a chosen subset of the
media files it finds.

  * The folder is re-scanned on a timer, so dropping a new file into the folder
    makes it appear automatically.
  * Each file gets two controls:
      - "Include: <file>"  (Selection group) -- tick to add it to the playlist.
      - "Position: <file>" (Ordering group)  -- a number, 1 = first, 2 = next...
  * The playlist is the selected files only, in position order. Selected files
    left at position 0 follow the numbered ones, sorted by name, so a pick is
    never silently dropped. Unselected files are ignored.
  * Workflow: tick the files you want, then either type positions or use
    "Auto-number selection" to number them 1..N in name order.

mpv is launched once in idle mode with a JSON IPC socket (--input-ipc-server).
Playback actions (Play / Pause / Next / Previous / Stop / Loop) are delivered to
that socket via a short-lived helper process (POSIX: `nc -U`; Windows: a
PowerShell named-pipe client), so they drive the running window instead of
relaunching it. mpv runs with the managed process's startOnce() (not start()),
so there is NO keep-alive/relaunch. It is launched --idle=yes, so Stop (and a
non-looping playlist ending) leave mpv open on a blank window, ready for the
next Play; mpv only exits if its process is killed or the window is closed.

Two different "off"s, deliberately kept apart:
  * Stop      -- playback control. Blanks mpv to its idle window, process stays
                 up, next Play is instant.
  * Quit mpv  -- machine control (Power group). Exits the process so the PC's
                 display can be used for something else. Power On / Play brings
                 it back. The Power / Status events exist so a dashboard tile
                 can bind to this the same way it binds to a PC or projector.

Coming up with the gallery: a gallery "All On" (or "Sing On") wakes this PC via
LTL-SING-PC-WOL and must leave mpv up and idle. Remote calls sent at that moment
are lost -- this host is not on the network yet -- so the cold path hangs off
this node's OWN start (the "Start mpv idle when this node starts" parameter),
which only happens when the PC boots. The warm path, an On arriving while the PC
is already awake, comes in on the "Sing Power" remote event. Both call StartMPV,
which is a no-op when mpv is already up.
'''

import os
import base64
import tempfile

# OS detection: Jython's os.name reports 'java', so ask the JVM instead.
try:
    from java.lang import System as _JSystem
    IS_WINDOWS = 'win' in (_JSystem.getProperty('os.name') or '').lower()
except:
    IS_WINDOWS = (os.sep == '\\')

# --- Parameters -------------------------------------------------------------

param_folder = Parameter({'title': 'Media folder', 'order': 1,
                          'schema': {'type': 'string', 'hint': '/data/media'}})

param_mpvPath = Parameter({'title': 'mpv binary', 'order': 2,
                           'schema': {'type': 'string', 'hint': 'mpv'}})

param_mpvArgs = Parameter({'title': 'Extra mpv arguments', 'order': 3,
                           'schema': {'type': 'string',
                                      'hint': '--fullscreen --loop-playlist=inf'}})

param_extensions = Parameter({'title': 'File extensions', 'order': 4,
                              'schema': {'type': 'string',
                                         'hint': 'mp4,mov,mkv,avi,m4v,webm'}})

param_pollSeconds = Parameter({'title': 'Folder poll (seconds)', 'order': 5,
                               'schema': {'type': 'integer', 'hint': '5'}})

param_ipcSocket = Parameter({'title': 'IPC socket / pipe path', 'order': 6,
                             'schema': {'type': 'string',
                                        'hint': 'blank = auto (POSIX: temp socket; Windows: \\\\.\\pipe\\nodel_mpv)'}})

param_ipcSender = Parameter({'title': 'IPC send command (POSIX only)', 'order': 7,
                             'schema': {'type': 'string',
                                        'hint': 'nc -U {socket}   (or: socat - UNIX-CONNECT:{socket})'}})

# This node only starts when its host starts, i.e. when this PC boots -- and the
# PC is only ever booted by a gallery "All On" / "Sing On" waking it (WOL, from
# LTL-SING-PC-WOL). So bringing mpv up here is what turns "the PC booted" into
# "mpv is up and idle, ready to Play". Left unset it is treated as ON, since a
# media PC with no mpv on it is not a useful resting state.
param_autoStart = Parameter({'title': 'Start mpv idle when this node starts', 'order': 8,
                             'schema': {'type': 'boolean', 'hint': 'unset = yes'}})

param_startDelaySeconds = Parameter({'title': 'Auto-start delay (seconds)', 'order': 9,
                                     'schema': {'type': 'integer', 'hint': '10'}})

# --- Runtime state ----------------------------------------------------------

included = {}          # filename -> bool, is it selected for the playlist
orders = {}            # filename -> position within the playlist (int, 0 = unset)
mpvRunning = [False]   # True while an mpv instance we launched is alive

# --- Status events ----------------------------------------------------------

local_event_Files = LocalEvent({
    'group': 'Status', 'order': 1,
    'title': 'Files in folder',
    'schema': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'name':     {'type': 'string',  'order': 1},
        'included': {'type': 'boolean', 'order': 2},
        'position': {'type': 'integer', 'order': 3}}}}})

local_event_Playlist = LocalEvent({
    'group': 'Status', 'order': 2,
    'title': 'Playlist (selected files in play order)',
    'schema': {'type': 'array', 'items': {'type': 'string'}}})

local_event_MPVRunning = LocalEvent({
    'group': 'Status', 'order': 3,
    'title': 'mpv running',
    'schema': {'type': 'boolean'}})

# --- Power signals ----------------------------------------------------------
# These exist purely so a dashboard power tile can bind to this node the same
# way it binds to a PC or projector. They are derived from "mpv running", never
# set independently -- see derivePower().

# String mirror of "mpv running", for <partialswitch join='...'/>, which expects
# 'On'/'Off' rather than a boolean.
local_event_Power = LocalEvent({
    'group': 'Power', 'order': 1,
    'title': 'Power',
    'schema': {'type': 'string', 'enum': ['On', 'Off']}})

# What the operator last ASKED for, persisted. Used only to tell a deliberate
# quit ("the PC has been handed back", not a fault) from mpv dying on its own.
local_event_DesiredPower = LocalEvent({
    'group': 'Power', 'order': 2,
    'title': 'Desired power',
    'schema': {'type': 'string', 'enum': ['On', 'Off']}})

# Status-tile shape, matching the {level, message} that <status event='...'>
# renders elsewhere on the dashboard.
local_event_Status = LocalEvent({
    'group': 'Status', 'order': -100,
    'title': 'Status',
    'schema': {'type': 'object', 'properties': {
        'level':   {'type': 'integer', 'order': 1},
        'message': {'type': 'string', 'order': 2}}}})

local_event_Loop = LocalEvent({
    'group': 'Status', 'order': 4,
    'title': 'Loop playlist',
    'schema': {'type': 'boolean'}})

local_event_LastIPC = LocalEvent({
    'group': 'Status', 'order': 5,
    'title': 'Last IPC send',
    'schema': {'type': 'object', 'properties': {
        'command': {'type': 'string', 'order': 1},
        'code':    {'type': 'integer', 'order': 2},
        'reply':   {'type': 'string', 'order': 3}}}})

# What is actually playing right now (vs. what is merely selected).
# kind: 'playlist' | 'video' | '' (nothing). name: playlist or file name.
local_event_NowPlaying = LocalEvent({
    'group': 'Status', 'order': 6,
    'title': 'Now playing',
    'schema': {'type': 'object', 'properties': {
        'kind':  {'type': 'string', 'order': 1},
        'name':  {'type': 'string', 'order': 2},
        'files': {'type': 'array', 'order': 3, 'items': {'type': 'string'}}}}})

# True while playback is paused (drives the Pause/Resume button label).
local_event_Paused = LocalEvent({
    'group': 'Status', 'order': 7,
    'title': 'Paused',
    'schema': {'type': 'boolean'}})

# Saved playlists (persisted). Each = {name, files:[filenames in play order]}.
local_event_Presets = LocalEvent({
    'group': 'Presets', 'order': 1,
    'title': 'Saved playlists',
    'schema': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'name':  {'type': 'string', 'order': 1},
        'files': {'type': 'array', 'order': 2, 'items': {'type': 'string'}}}}}})

# Name of the playlist currently loaded for editing (drives "Update playlist").
local_event_EditingPreset = LocalEvent({
    'group': 'Presets', 'order': 2,
    'title': 'Editing playlist',
    'schema': {'type': 'string'}})

# True when the current selection exactly matches a saved playlist -- Play only
# works in this state, so only saved playlists can be played (not ad-hoc ticks).
local_event_PlayReady = LocalEvent({
    'group': 'Presets', 'order': 3,
    'title': 'Play ready (selection is a saved playlist)',
    'schema': {'type': 'boolean'}})

# --- Helpers ----------------------------------------------------------------

def parseExtensions():
    raw = param_extensions or 'mp4,mov,mkv,avi,m4v,mpg,mpeg,wmv,flv,webm'
    return set(e.strip().lower().lstrip('.') for e in raw.split(',') if e.strip())

def playlistFilePath():
    return os.path.join(tempfile.gettempdir(), 'nodel_mpvdev_playlist.m3u8')

# Selection and ordering are driven by a FIXED set of actions (below) plus the
# live "Files in folder" event -- NOT by per-file dynamic actions. Dynamically
# injected actions don't refresh in the built-in admin UI without a node
# rebind, whereas event VALUES stream live, so the custom frontend (content/)
# renders the file list from the Files event and edits it through these fixed
# actions. See SetSelection / SetPosition / ToggleSelection.

def currentFolderNames():
    '''Media files currently in the folder (sorted by name).'''
    folder = param_folder
    if not folder or not os.path.isdir(folder):
        return []
    exts = parseExtensions()
    names = []
    for n in sorted(os.listdir(folder)):
        full = os.path.join(folder, n)
        if not os.path.isfile(full):
            continue
        ext = n.rsplit('.', 1)[-1].lower() if '.' in n else ''
        if ext in exts:
            names.append(n)
    return names

def resolvedPlaylistNames(names):
    '''The selected files in play order: positioned first (by number then name),
    then any selected-but-unpositioned files by name, so a pick is never dropped.'''
    selected = [n for n in names if included.get(n, False)]
    positioned = sorted([n for n in selected if orders.get(n, 0) > 0],
                        key=lambda n: (orders[n], n.lower()))
    unpositioned = sorted([n for n in selected if orders.get(n, 0) <= 0],
                          key=lambda n: n.lower())
    return positioned + unpositioned

def scanFolder():
    '''Re-read the folder, refresh the file list and rebuild the play order.'''
    folder = param_folder
    if not folder or not os.path.isdir(folder):
        console.warn('Media folder not found: %s' % folder)
        local_event_Files.emit([])
        local_event_Playlist.emit([])
        return []

    names = currentFolderNames()

    # Files-in-folder listing with each file's selection + position
    local_event_Files.emit([{'name': n,
                             'included': included.get(n, False),
                             'position': orders.get(n, 0)} for n in names])

    playlist = resolvedPlaylistNames(names)
    local_event_Playlist.emit(playlist)
    local_event_PlayReady.emit(isPlayReady())
    broadcastState()
    return [os.path.join(folder, n) for n in playlist]

def broadcastState():
    '''Re-emit "sticky" events (ones that otherwise only fire on change) so a
    frontend node that connects/reconnects gets current values. Cheap -- the
    frontend dedups unchanged values, so re-emitting the same value is a no-op
    for the UI. Harmless on a standalone node too.'''
    try:
        local_event_Presets.emit(getPresets())
        local_event_EditingPreset.emit(editingPreset())
        local_event_Loop.emit(isLoopEnabled())
        local_event_Paused.emit(isPaused())
        local_event_MPVRunning.emit(bool(mpvRunning[0]))
        derivePower()                  # Power + Status ride along with it
        np = local_event_NowPlaying.getArg()
        local_event_NowPlaying.emit(np if np else {'kind': '', 'name': '', 'files': []})
    except:
        pass

def writePlaylistFile(paths):
    f = open(playlistFilePath(), 'w')
    try:
        f.write('#EXTM3U\n')
        for p in paths:
            f.write(p + '\n')
    finally:
        f.close()

def ipcServerPath():
    '''Value passed to mpv --input-ipc-server.
    POSIX: a unix-socket file path. Windows: a named pipe path.'''
    if param_ipcSocket:
        return param_ipcSocket
    if IS_WINDOWS:
        return '\\\\.\\pipe\\nodel_mpvdev'
    return os.path.join(tempfile.gettempdir(), 'nodel_mpvdev.sock')

def winPipeName():
    '''Bare pipe name (no \\\\.\\pipe\\ prefix) for the PowerShell client.'''
    return ipcServerPath().rsplit('\\', 1)[-1]

def mpvCommandJson(command):
    '''Format one mpv IPC command list as a JSON string, e.g. ['cycle','pause'].
    No trailing newline -- each transport adds its own.'''
    parts = []
    for a in command:
        if a is True:
            parts.append('true')
        elif a is False:
            parts.append('false')
        elif isinstance(a, (int, long, float)):
            parts.append(str(a))
        else:
            s = str(a).replace('\\', '\\\\').replace('"', '\\"')
            parts.append('"' + s + '"')
    return '{"command": [' + ', '.join(parts) + ']}'

def _posixSender(js):
    '''A `printf ... | nc -U <socket>` shell pipeline (self-contained, so we
    don't depend on the host pushing stdin to the process).'''
    payload = (js + '\n').replace("'", "'\\''")     # safe inside single quotes
    quotedSock = "'" + ipcServerPath() + "'"
    sender = param_ipcSender or 'nc -U {socket}'
    if '{socket}' in sender:
        senderCmd = sender.replace('{socket}', quotedSock)
    else:
        senderCmd = sender + ' ' + quotedSock       # e.g. bare 'nc -U'
    return ['sh', '-c', "printf '%s' '" + payload + "' | " + senderCmd]

def _windowsSender(js):
    '''PowerShell that writes the command to mpv's named pipe. Passed as a
    Base64 -EncodedCommand so Windows/Java command-line quoting can't mangle
    the (quote-heavy) script.'''
    psJson = js.replace("'", "''")                  # PS single-quote escape
    ps = ("$ErrorActionPreference='Stop';"
          "$p=New-Object System.IO.Pipes.NamedPipeClientStream('.','" + winPipeName() +
          "',[System.IO.Pipes.PipeDirection]::InOut);"
          "$p.Connect(2000);"
          "$w=New-Object System.IO.StreamWriter($p);"
          "$w.AutoFlush=$true;"
          "$w.WriteLine('" + psJson + "');"
          "$w.Flush();$w.Dispose();$p.Dispose()")
    try:
        from java.lang import String as _JString
        from java.util import Base64 as _Base64
        # -EncodedCommand expects base64 of the UTF-16LE script text.
        enc = _Base64.getEncoder().encodeToString(_JString(ps).getBytes('UTF-16LE'))
        return ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', enc]
    except:
        return ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps]

def sendMpv(command):
    '''Send one mpv IPC command to the running mpv via --input-ipc-server.
    POSIX uses a unix socket (nc/socat); Windows uses a named pipe (PowerShell).
    Either way the command is self-contained in a short-lived process, so we
    don't rely on the host pushing stdin (which doesn't deliver reliably here).'''
    if not mpvRunning[0]:
        return

    js = mpvCommandJson(command)
    cmd = _windowsSender(js) if IS_WINDOWS else _posixSender(js)

    def finished(arg):
        try:
            code = getattr(arg, 'code', None)
            reply = (getattr(arg, 'stdout', '') or '').strip()
            err = (getattr(arg, 'stderr', '') or '').strip()
            local_event_LastIPC.emit({'command': ' '.join([str(c) for c in command]),
                                      'code': code if code is not None else -1,
                                      'reply': reply or err})
            if code not in (0, None):
                console.warn('mpv IPC send failed (exit %s): %s' % (code, err))
        except:
            pass

    try:
        quick_process(cmd, finished=finished, timeoutInSeconds=3)
    except:
        console.warn('Could not send command to mpv: %s' % command)

def isLoopEnabled():
    try:
        return bool(local_event_Loop.getArg())   # persisted across restarts
    except:
        return False

# --- Forcing the mpv window to the front (Windows) --------------------------
# mpv is launched by the Nodel host, which is not the foreground process, so
# Windows' focus-stealing prevention leaves the mpv window BEHIND the taskbar --
# even fullscreen, and even with --ontop --focus-on=all in the mpv arguments.
# Measured on site 2026-08-06: 24s after a node-driven launch the taskbar was
# still drawn over mpv.
#
# WScript.Shell's AppActivate does not fix it either. It returns True and
# changes nothing (also measured) -- SetForegroundWindow is simply ignored for a
# process with no foreground rights. Attaching to the CURRENT foreground
# window's input queue first is what makes the change legal, so that is what
# this does, then pins the window topmost so the taskbar cannot come back over
# it. Add-Type is used because Jython cannot P/Invoke user32 directly.
#
# The script waits for mpv's window itself rather than being called on a delay:
# the window does not exist the instant the process does, and how long it takes
# varies with what the GPU is doing.
#
# -EncodedCommand (base64 of UTF-16LE) is deliberate and not decoration. Passing
# PowerShell source as an ordinary argument gets its quotes eaten when Windows
# re-joins the argument vector into a command line -- the first attempt at this
# died with "The term 'activated=$r' is not recognized".

FOCUS_PS = r'''
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Fg {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
}
'@
$deadline = (Get-Date).AddSeconds(20)
do {
  $p = Get-Process mpv -EA SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if ($p) { break }
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if (-not $p) { Write-Output 'no mpv window'; return }
$h = $p.MainWindowHandle
$fg = [Fg]::GetForegroundWindow()
$op = 0
$tFg = [Fg]::GetWindowThreadProcessId($fg, [ref]$op)
$tMe = [Fg]::GetCurrentThreadId()
[void][Fg]::AttachThreadInput($tFg, $tMe, $true)
[void][Fg]::ShowWindow($h, 9)
[void][Fg]::BringWindowToTop($h)
$r = [Fg]::SetForegroundWindow($h)
[void][Fg]::AttachThreadInput($tFg, $tMe, $false)
[void][Fg]::SetWindowPos($h, [IntPtr]-1, 0, 0, 0, 0, 0x0003)   # HWND_TOPMOST, no move/resize
Write-Output ('setfg=' + $r + ' hwnd=' + $h)
'''

def focusMpvWindow():
    '''Bring the mpv window to the front. No-op off Windows, and harmless if the
       window never appears -- the PowerShell gives up on its own deadline.'''
    if not IS_WINDOWS:
        return
    try:
        encoded = base64.b64encode(unicode(FOCUS_PS).encode('utf-16-le'))
        quick_process(['powershell', '-NoProfile', '-EncodedCommand', encoded],
                      timeoutInSeconds=40,
                      finished=lambda r: console.info('focus: %s' % (r.stdout or '').strip()))
    except:
        console.warn('Could not force the mpv window to the front')

def onMpvStarted():
    mpvRunning[0] = True
    local_event_MPVRunning.emit(True)
    console.info('mpv started')
    focusMpvWindow()

def setPaused(state):
    local_event_Paused.emit(bool(state))

def isPaused():
    try:
        return bool(local_event_Paused.getArg())
    except:
        return False

def teardownPlayback():
    '''Mark playback stopped and clear the now-playing telemetry. Idempotent, so
    both Stop and the mpv-exit callback can call it.'''
    mpvRunning[0] = False
    local_event_MPVRunning.emit(False)
    setNowPlaying('', '', [])          # nothing is playing any more
    setPaused(False)                   # not paused when stopped
    removeStaleSocket()

def onMpvStopped(code):
    # Fires when mpv exits for ANY reason -- Stop, playlist end, or the user
    # closing the window. startOnce() means there is no keep-alive, so it stays
    # stopped (no relaunch).
    teardownPlayback()
    console.info('mpv stopped (exit %s)' % code)

def setNowPlaying(kind, name, files):
    local_event_NowPlaying.emit({'kind': kind or '', 'name': name or '',
                                 'files': [str(f) for f in (files or [])]})

def removeStaleSocket():
    # Only POSIX uses a real socket file; a Windows named pipe is not a file.
    if IS_WINDOWS:
        return
    try:
        if os.path.exists(ipcServerPath()):
            os.remove(ipcServerPath())
    except:
        pass

def onMpvStdout(line):
    pass                                   # drained but not logged (mpv runs --really-quiet)

def onMpvStderr(line):
    console.warn('mpv: %s' % line)         # surface genuine errors only

# One persistent managed process, reused for every Play (see VLC recipe pattern).
mpvProc = Process([], started=onMpvStarted, stopped=onMpvStopped,
                  stdout=onMpvStdout, stderr=onMpvStderr)
# A managed Process auto-launches when idle; with an empty command that spams
# "No launch arguments were provided" every 15s. Park it stopped until Play.
try:
    mpvProc.stop()
except:
    pass

def startMpv(loopOverride=None):
    '''loopOverride: None = use the global Loop setting; True/False = force it
    (per-file on-demand play forces no-loop).'''
    # Record the intent here, at the single choke point every launch path goes
    # through (Play, Play file, Play preset, Power On). Otherwise a quit-then-
    # Play sequence would leave Desired power on 'Off' and a later crash would
    # be reported as a deliberate quit.
    local_event_DesiredPower.emit('On')
    loop = isLoopEnabled() if loopOverride is None else bool(loopOverride)
    binary = param_mpvPath or 'mpv'
    removeStaleSocket()                          # POSIX: clear a leftover socket
    # --idle=yes: after Stop (or a non-looping playlist ends) mpv stays open on a
    # blank window instead of quitting, ready for the next Play.
    args = [binary, '--idle=yes', '--force-window=yes', '--really-quiet',
            '--input-ipc-server=%s' % ipcServerPath(),
            '--loop-playlist=%s' % ('inf' if loop else 'no'),
            '--playlist=%s' % playlistFilePath()]
    if param_mpvArgs:
        args += param_mpvArgs.split()

    mpvProc.setCommand(args)
    mpvProc.startOnce()    # run WITHOUT keep-alive: closing the window won't relaunch mpv

# --- Playback actions -------------------------------------------------------

def playPaths(paths, loopOverride=None):
    '''Play a list of absolute paths. loopOverride None = global Loop setting.'''
    if not paths:
        console.warn('No media files to play')
        return
    setPaused(False)                    # playback starts unpaused
    writePlaylistFile(paths)
    if not mpvRunning[0]:
        startMpv(loopOverride)                        # launches with --playlist
    else:
        loop = isLoopEnabled() if loopOverride is None else bool(loopOverride)
        sendMpv(['set_property', 'loop-playlist', 'inf' if loop else 'no'])
        sendMpv(['loadlist', playlistFilePath(), 'replace'])
        sendMpv(['set_property', 'pause', False])

@local_action({'group': 'Playback', 'order': 1, 'title': 'Play'})
def Play(arg=None):
    '''Play the selected saved playlist. Only works when the current selection
    is a saved playlist (recalled or just saved) -- unsaved ad-hoc ticks are
    not played. Use "Play preset" to play a named playlist directly.'''
    if not isPlayReady():
        console.warn('Play: select or save a playlist first (unsaved changes are not played)')
        return
    names = resolvedPlaylistNames(currentFolderNames())
    setNowPlaying('playlist', editingPreset(), names)
    playPaths(scanFolder(), None)

@local_action({'group': 'Playback', 'order': 20, 'title': 'Play file',
               'schema': {'type': 'string', 'hint': 'filename -- plays once, no loop'}})
def PlayFile(arg=None):
    '''On-demand: play a single file from the folder once, without looping.
    (The frontend puts a Play button on each row so the operator never types.)'''
    if arg is None:
        return
    name = str(arg)
    folder = param_folder
    if not folder:
        return
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        console.warn('Play file: no such file "%s"' % name)
        return
    setNowPlaying('video', name, [name])
    playPaths([path], loopOverride=False)

@local_action({'group': 'Playback', 'order': 2, 'title': 'Pause / Resume'})
def PauseToggle(arg=None):
    if not mpvRunning[0]:
        return                          # nothing to pause
    newPaused = not isPaused()
    setPaused(newPaused)                # track state so the button label is right
    sendMpv(['set_property', 'pause', newPaused])

@local_action({'group': 'Playback', 'order': 6, 'title': 'Loop playlist (toggle)'})
def LoopToggle(arg=None):
    enabled = not isLoopEnabled()
    local_event_Loop.emit(enabled)                       # persist new state
    sendMpv(['set_property', 'loop-playlist', 'inf' if enabled else 'no'])
    console.info('Loop playlist %s' % ('ON' if enabled else 'OFF'))

@local_action({'group': 'Playback', 'order': 7, 'title': 'Loop playlist (set)',
               'schema': {'type': 'boolean'}})
def LoopSet(arg=None):
    '''Explicit on/off, e.g. for scheduling. Pass true or false.'''
    enabled = bool(arg)
    local_event_Loop.emit(enabled)
    sendMpv(['set_property', 'loop-playlist', 'inf' if enabled else 'no'])

@local_action({'group': 'Playback', 'order': 3, 'title': 'Next'})
def Next(arg=None):
    sendMpv(['playlist-next', 'weak'])

@local_action({'group': 'Playback', 'order': 4, 'title': 'Previous'})
def Previous(arg=None):
    sendMpv(['playlist-prev', 'weak'])

@local_action({'group': 'Playback', 'order': 5, 'title': 'Stop'})
def Stop(arg=None):
    '''Stop the video but leave mpv open on a blank window (idle), ready to play
    again -- it does NOT quit mpv.'''
    if mpvRunning[0]:
        sendMpv(['stop'])         # stop playback + clear file; mpv stays idle
    setNowPlaying('', '', [])     # nothing is playing now
    setPaused(False)              # ...and not paused

# --- Power (quit mpv / bring it back) ---------------------------------------
# Stop above is the *playback* control: it blanks mpv to its idle window and
# leaves the process up, ready for the next Play. Quit below is the *machine*
# control, driven from the dashboard's Multipurpose tab: it exits mpv entirely
# so the PC's display can be used for something else.

@local_action({'group': 'Power', 'order': 10, 'title': 'Quit mpv'})
def QuitMPV(arg=None):
    '''Exit mpv, releasing the display. Unlike Stop, the process does not
    survive this. Play (or Power On) brings it back.'''
    local_event_DesiredPower.emit('Off')
    # Unconditional: mpvProc.stop() is idempotent, and if mpvRunning[0] has gone
    # stale-False while a window is still up, guarding on it would strand mpv.
    mpvProc.stop()      # -> onMpvStopped -> teardownPlayback() clears the state
    derivePower()       # in case no process was up, so nothing else will fire

@local_action({'group': 'Power', 'order': 11, 'title': 'Start mpv (idle)'})
def StartMPV(arg=None):
    '''Bring mpv back up on its blank idle window, ready to Play. Deliberately
    does NOT start playback -- that stays Play's job.'''
    local_event_DesiredPower.emit('On')
    if mpvRunning[0]:
        derivePower()
        return
    # startMpv() ALWAYS passes --playlist=<file>, so a playlist left on disk by
    # an earlier Play would start playing right here -- the opposite of idle,
    # and on a boot that means the exhibit starts itself. Blank it first, every
    # time: Play rewrites this file anyway, so nothing is lost.
    writePlaylistFile([])
    setNowPlaying('', '', [])
    startMpv()

@local_action({'group': 'Power', 'order': 12, 'title': 'Power',
               'schema': {'type': 'string', 'enum': ['On', 'Off']}})
def Power(arg=None):
    '''On -> mpv idle and ready; Off -> mpv gone. This is what the dashboard's
    <partialswitch> calls, so it must accept both halves.'''
    # Args reach a Nodel action as whatever the caller sent -- a bare string
    # from a switch, but {'state': 'On'} from the Group recipe. Duck-type the
    # unwrap: a JSON object arrives here as a *java.util.Map*, not a Python
    # dict, so isinstance(arg, dict) silently misses it.
    if not isinstance(arg, basestring) and hasattr(arg, 'get'):
        try:
            arg = arg.get('state') or arg.get('value')
        except:
            pass
    state = str(arg).strip().lower() if arg is not None else ''

    if state in ('on', 'true', '1'):
        StartMPV.call()
    elif state in ('off', 'false', '0'):
        QuitMPV.call()
    else:
        console.warn('Power: expected "On" or "Off", got %s' % repr(arg))

def derivePower():
    '''Recompute the two dashboard-facing signals from actual running state.'''
    running = bool(mpvRunning[0])
    local_event_Power.emit('On' if running else 'Off')

    if running:
        local_event_Status.emit({'level': 0, 'message': 'OK'})
    elif local_event_DesiredPower.getArg() == 'Off':
        # Quit was deliberate -- the PC has been handed back on purpose, so this
        # is a normal resting state, not a fault.
        local_event_Status.emit({'level': 0, 'message': 'mpv quit by operator'})
    else:
        local_event_Status.emit({'level': 2, 'message': 'mpv is not running'})

@after_main
def bindPowerSignals():
    # Derive from the existing running flag rather than emitting from both
    # onMpvStarted() and teardownPlayback(), so there is one source of truth.
    # A module-level function, not a bound method -- addEmitHandler rejects those.
    local_event_MPVRunning.addEmitHandler(lambda arg: derivePower())
    derivePower()
    # The emit handler alone is not enough: if mpv fails to LAUNCH, MPVRunning
    # never changes, so the tile would sit on its previous value indefinitely.
    # Re-derive on a timer so "asked for On, still not running" surfaces.
    Timer(derivePower, 30, 5)

# --- Coming up with the gallery ---------------------------------------------
# "All On" (or "Sing On") has to leave mpv up and idle on this PC. That happens
# two ways, because the PC may be off OR already awake when the On arrives:
#
#   cold  -- the PC is asleep. LTL-SING-PC-WOL wakes it; every remote call aimed
#            at this node in that moment is dropped, because this host is not on
#            the network yet. The node's own start is the only reliable hook, so
#            autoStartMpv() below does the work. See main().
#   warm  -- the PC is already up and mpv was quit from the dashboard's
#            Multipurpose tile. Nothing reboots, so there is no node start to
#            hook: the "Sing Power" remote event catches it instead.
#
# Both land on StartMPV, which is a no-op when mpv is already running, so the
# two paths overlapping is harmless.

def autoStartEnabled():
    '''Unset means yes. Parameters do not always arrive as native Python types,
       so accept the string forms a hand-edited config can produce too.'''
    if param_autoStart is None:
        return True
    if isinstance(param_autoStart, basestring):
        return param_autoStart.strip().lower() not in ('', 'false', 'no', 'off', '0')
    return bool(param_autoStart)

def autoStartDelay():
    try:
        return max(0, int(param_startDelaySeconds))
    except:
        return 10                    # unset, blank or nonsense -> the default

def autoStartMpv():
    if mpvRunning[0]:
        return                       # already up (someone hit Play in the meantime)
    console.info('Auto-start: bringing mpv up idle')
    StartMPV.call()

def onSingPower(arg=None):
    '''The Sing group announced a power intent. Only "On" means anything here --
       "Off" powers the whole PC down anyway, so quitting mpv first would add a
       race and change nothing on screen.'''
    # Arrives as a plain string, but be tolerant of the Group recipe's
    # {'state': 'On'} shape, the same way the Power action above is.
    if not isinstance(arg, basestring) and hasattr(arg, 'get'):
        try:
            arg = arg.get('state') or arg.get('value')
        except:
            pass

    if str(arg).strip().lower() != 'on':
        return

    console.info('Sing power On - ensuring mpv is up')
    StartMPV.call()

# --- Presets (saved playlists) ----------------------------------------------
# A preset = {name, files:[filenames in play order]}, stored in the persisted
# Presets event. PlayPreset/RecallPreset take the preset NAME (stable, short,
# operator-chosen) as their argument -- that is what you bind to another node
# or schedule, e.g. schedule "Play preset" with arg "Morning" at 09:00.

def getPresets():
    try:
        v = local_event_Presets.getArg()
        return [dict(p) for p in v] if v else []
    except:
        return []

def findPreset(name):
    if name is None:
        return None
    name = str(name)
    for p in getPresets():
        if p.get('name') == name:
            return p
    return None

def applyPreset(p):
    '''Load a preset's files as the current selection, in the preset's order.'''
    files = p.get('files') or []
    for n in list(included.keys()):
        included[n] = False
    for n in list(orders.keys()):
        orders[n] = 0
    i = 1
    for n in files:
        included[str(n)] = True
        orders[str(n)] = i
        i += 1

def editingPreset():
    try:
        v = local_event_EditingPreset.getArg()
        return str(v) if v else ''
    except:
        return ''

def setEditingPreset(name):
    local_event_EditingPreset.emit(str(name) if name else '')

def isPlayReady():
    '''True only when the current selection matches the saved definition of the
    active playlist -- i.e. it was recalled/saved and not edited since. Play is
    gated on this so unsaved ad-hoc selections cannot be played.'''
    p = findPreset(editingPreset())
    if p is None:
        return False
    present = set(currentFolderNames())
    savedPresent = [str(f) for f in (p.get('files') or []) if str(f) in present]
    return resolvedPlaylistNames(currentFolderNames()) == savedPresent

def writePreset(name):
    '''Save the current selection + order under 'name' and make it the edit
    target. Returns the file count.'''
    files = resolvedPlaylistNames(currentFolderNames())    # selected, in play order
    presets = [p for p in getPresets() if p.get('name') != name]
    presets.append({'name': name, 'files': files})
    presets.sort(key=lambda p: (p.get('name') or '').lower())
    local_event_Presets.emit(presets)
    setEditingPreset(name)
    return len(files)

@local_action({'group': 'Presets', 'order': 10, 'title': 'Save preset',
               'schema': {'type': 'string', 'hint': 'playlist name (overwrites if it exists)'}})
def SavePreset(arg=None):
    '''Save the current selection + order as a named playlist.'''
    if arg is None:
        return
    name = str(arg).strip()
    if not name:
        return
    console.info('Saved preset "%s" (%d files)' % (name, writePreset(name)))
    scanFolder()          # selection now matches the saved playlist -> Play ready

@local_action({'group': 'Presets', 'order': 14, 'title': 'Update playlist',
               'schema': {'type': 'string',
                          'hint': 'blank = the currently-recalled playlist'}})
def UpdatePreset(arg=None):
    '''Re-save the current selection into an existing playlist -- e.g. after
    recalling one and ticking a few more files. With no arg it updates the
    playlist that was last recalled/saved (the "Editing playlist").'''
    name = (str(arg).strip() if arg else '') or editingPreset()
    if not name:
        console.warn('Update playlist: nothing is loaded -- use Save preset with a name')
        return
    console.info('Updated preset "%s" (%d files)' % (name, writePreset(name)))
    scanFolder()          # selection now matches the saved playlist -> Play ready

@local_action({'group': 'Presets', 'order': 15, 'title': 'New playlist (clear editing)'})
def NewPlaylist(arg=None):
    '''Clear the selection and the edit target so the next Save starts fresh.'''
    for n in list(included.keys()):
        included[n] = False
    for n in list(orders.keys()):
        orders[n] = 0
    setEditingPreset('')
    scanFolder()

@local_action({'group': 'Presets', 'order': 11, 'title': 'Delete preset',
               'schema': {'type': 'string', 'hint': 'playlist name'}})
def DeletePreset(arg=None):
    if arg is None:
        return
    name = str(arg)
    local_event_Presets.emit([p for p in getPresets() if p.get('name') != name])
    if editingPreset() == name:
        setEditingPreset('')
    scanFolder()          # recompute Play-ready state

@local_action({'group': 'Presets', 'order': 12, 'title': 'Recall preset',
               'schema': {'type': 'string', 'hint': 'playlist name -- loads selection, no playback'}})
def RecallPreset(arg=None):
    '''Load a preset into the current selection without playing, and make it the
    edit target so "Update playlist" saves changes back to it.'''
    p = findPreset(arg)
    if p is None:
        console.warn('Recall preset: no such preset "%s"' % arg)
        return
    applyPreset(p)
    setEditingPreset(p.get('name'))
    scanFolder()

@local_action({'group': 'Presets', 'order': 13, 'title': 'Play preset',
               'schema': {'type': 'string', 'hint': 'playlist name -- loads it and plays'}})
def PlayPreset(arg=None):
    '''Load a preset and play it (honours the global Loop setting).
    This is the action to bind/schedule, e.g. arg "Morning".'''
    p = findPreset(arg)
    if p is None:
        console.warn('Play preset: no such preset "%s"' % arg)
        return
    applyPreset(p)
    setEditingPreset(p.get('name'))
    setNowPlaying('playlist', p.get('name'), resolvedPlaylistNames(currentFolderNames()))
    playPaths(scanFolder(), None)

# --- Selection / ordering actions (fixed; driven by the frontend) -----------

@local_action({'group': 'Selection', 'order': 10, 'title': 'Set selection',
               'schema': {'type': 'object', 'title': 'file + included', 'properties': {
                   'file':     {'type': 'string',  'order': 1},
                   'included': {'type': 'boolean', 'order': 2}}}})
def SetSelection(arg=None):
    '''Include/exclude one file. arg = {file, included}.'''
    name = _argFile(arg)
    if name is None:
        return
    included[name] = bool(arg.get('included'))
    scanFolder()

@local_action({'group': 'Selection', 'order': 11, 'title': 'Toggle selection',
               'schema': {'type': 'string', 'hint': 'filename'}})
def ToggleSelection(arg=None):
    '''Flip one file's selection. arg = filename string.'''
    if arg is None:
        return
    name = str(arg)
    included[name] = not included.get(name, False)
    scanFolder()

@local_action({'group': 'Ordering', 'order': 10, 'title': 'Set position',
               'schema': {'type': 'object', 'title': 'file + position', 'properties': {
                   'file':     {'type': 'string',  'order': 1},
                   'position': {'type': 'integer', 'order': 2}}}})
def SetPosition(arg=None):
    '''Set one file's play order. arg = {file, position}. 1 = first, 0 = unset.'''
    name = _argFile(arg)
    if name is None:
        return
    try:
        pos = int(arg.get('position'))
    except:
        pos = 0
    orders[name] = max(0, pos)
    scanFolder()

def _argFile(arg):
    '''Pull a non-empty 'file' string out of an object action argument.'''
    try:
        name = arg.get('file')
    except:
        return None
    if not name:
        return None
    return str(name)

# --- Management actions ------------------------------------------------------

@local_action({'group': 'Selection', 'order': 80, 'title': 'Auto-number selection'})
def AutoNumber(arg=None):
    '''Assign positions 1..N to the currently-selected files (name order),
    so you can select first, then one click to order them.'''
    selected = sorted([n for n in included.keys() if included.get(n, False)],
                      key=lambda n: n.lower())
    i = 1
    for name in selected:
        orders[name] = i
        i += 1
    scanFolder()

@local_action({'group': 'Selection', 'order': 81, 'title': 'Clear selection'})
def ClearSelection(arg=None):
    for name in list(included.keys()):
        included[name] = False
    scanFolder()

@local_action({'group': 'Selection', 'order': 79, 'title': 'Rescan folder now'})
def Rescan(arg=None):
    scanFolder()

@local_action({'group': 'Ordering', 'order': 91, 'title': 'Clear all positions'})
def ClearOrdering(arg=None):
    for name in list(orders.keys()):
        orders[name] = 0
    scanFolder()

# --- Main -------------------------------------------------------------------

def restoreSelectionState():
    '''The Files event persists its last value across restarts; use it to
    recover each file's selection + position.'''
    try:
        prev = local_event_Files.getArg()
    except:
        prev = None
    if not prev:
        return
    for item in prev:
        try:
            nm = item.get('name')
            if nm:
                included[nm] = bool(item.get('included'))
                orders[nm] = int(item.get('position') or 0)
        except:
            pass

def main():
    console.info('mpv Playlist Controller starting')
    restoreSelectionState()
    interval = max(2, param_pollSeconds or 5)
    Timer(scanFolder, interval, 1)   # poll the folder; first run after 1s

    # The warm path: an On that arrives while this PC is already awake.
    create_remote_event('Sing Power', onSingPower,
                        {'title': 'Sing Power', 'group': 'Power', 'order': 20,
                         'schema': {'type': 'string'}},
                        suggestedNode='LTL-LIB-SING', suggestedEvent='Desired Power')

    # The cold path: this node starting IS the PC having booted.
    if not autoStartEnabled():
        console.info('Auto-start is disabled - mpv will not be launched on node start')
        return

    delay = autoStartDelay()
    console.info('Auto-start: mpv will be brought up idle in %ss' % delay)
    call_safe(autoStartMpv, delay)
