'''
**ScanSnap scanner monitor (Windows agent)**

Reports whether the ScanSnap scanner attached to *this PC* is actually powered on, by
asking ScanSnap Home's own SDK. This is the only feedback path the scanners have: they
are USB devices with no network control, switched by a PDU outlet and a SwitchBot press.

**This node must run on the scanner PC's own Nodel host**, alongside `LTL-PLAY-BOT0x` and
`LTL-PLAY-SNAP0x`. It shells out to a small C# helper (`ScanSnapStatus.cs`, compiled to
`.exe` on first run) which reads the registry and drives `PfuSsMonSdk.exe`. On any
non-Windows host it loads, says so in `Status`, and does nothing else.

What it publishes, and what to bind it to:

  | this node               | consumer                                              |
  |-------------------------|-------------------------------------------------------|
  | `Scanner Power`         | `LTL-PLAY-SCANNER0x` remote event `ScannerPower`      |
  | `ScanSnap Running`      | `LTL-PLAY-SCANNER0x` remote event `ScanSnapRunning`   |
  | `Fast Poll`             | `LTL-PLAY-SCANNER0x` remote action `ScannerPoll`      |
  | `ScanSnap Power`        | dashboard "Software (ScanSnap Home)" switch (event)   |
  | `ScanSnap Status`       | dashboard "Software (ScanSnap Home)" tile (event)     |
  | `Power`                 | dashboard "Software (ScanSnap Home)" switch (action)  |
  | `Restart ScanSnap`      | dashboard "Software (ScanSnap Home)" restart button   |

**It also controls ScanSnap Home**, which is why it replaces the App Launcher node that
used to sit on that tile. Windows starts ScanSnap Home at login, so nothing here ever
owned that process -- an App Launcher's `Power Off` is a handle to its *own* child, so it
had nothing to stop, and its `Power On` would have started a second copy. The stop here is
a `taskkill` by image name and the start is detached through `cmd /c start`, so neither
depends on who launched it, and restarting this node does not take the tray app down.

**`Scanner Power` is three-state on purpose.** The SDK refuses to answer while a scan is
in progress or while an operator has the ScanSnap Home window open -- and that is exactly
when a visitor is using the thing. Treating that refusal as "no scanner" would drop the
tile to Off every time somebody scanned. So a poll that cannot answer *holds* the last
known state for the _Hold duration_, and only then falls back to `Unknown`. Never read
`Unknown` as `Off`: nothing downstream should press a power button on the strength of it.

`ScanSnap Running` is a straight process check on `PfuSsMon.exe`. It is more trustworthy
than an app launcher's idea of whether it is running, because it does not depend on this
host having been the thing that started it.
'''

import os

DEFAULT_POLL_INTERVAL = 150
DEFAULT_FAST_POLL_INTERVAL = 5
DEFAULT_FAST_POLL_DURATION = 60
DEFAULT_HOLD_DURATION = 600
DEFAULT_RESTART_WAIT = 10
DEFAULT_RELAUNCH_AFTER = 2

# how long ScanSnap Home is given to close politely before it is forced
GRACEFUL_KILL_WAIT = 8

PROCESS_NAME = 'PfuSsMon.exe'

# the helper is given this long before it is assumed wedged
PROCESS_TIMEOUT = 30

# first poll after the node starts (and after a successful compile)
STARTUP_DELAY = 10

SOURCE_NAME = 'ScanSnapStatus.cs'
BINARY_NAME = 'ScanSnapStatus.exe'

# the .NET Framework 4 C# compiler, 64-bit first
COMPILER_PATHS = [r'Microsoft.NET\Framework64\v4.0.30319\csc.exe',
                  r'Microsoft.NET\Framework\v4.0.30319\csc.exe']

POWER_SCHEMA = {'type': 'string', 'enum': ['On', 'Off', 'Unknown']}

STATUS_SCHEMA = {'type': 'object', 'properties': {
    'level': {'type': 'integer', 'title': 'Level', 'order': 1},
    'message': {'type': 'string', 'title': 'Message', 'order': 2}}}

# how each helper result is interpreted:
#   'scanner'  -- True: definitely on, False: definitely off, None: cannot tell, hold
#   'level'    -- the Status level it implies
#   'message'  -- what the operator is told
RESULTS = {
  'OK':                  {'scanner': True,  'level': 0, 'message': 'Scanner connected'},
  'NO_SCANNER':          {'scanner': False, 'level': 0, 'message': 'No scanner connected'},
  'BUSY':                {'scanner': None,  'level': 0, 'message': 'ScanSnap Home is busy (scanning, or its window is open)'},
  'NOT_RUNNING':         {'scanner': None,  'level': 1, 'message': 'ScanSnap Home is not running'},
  'NOT_INSTALLED':       {'scanner': None,  'level': 2, 'message': 'ScanSnap Home is not installed on this PC'},
  'SDK_NOT_FOUND':       {'scanner': None,  'level': 2, 'message': 'PfuSsMonSdk.exe is not registered on this PC'},
  'NO_INFO_FILE':        {'scanner': None,  'level': 2, 'message': 'The ScanSnap SDK answered but wrote no information'},
  'PARAM_ERROR':         {'scanner': None,  'level': 2, 'message': 'The ScanSnap SDK rejected the query'},
  'UNSUPPORTED_VERSION': {'scanner': None,  'level': 2, 'message': 'The ScanSnap SDK interface version is wrong for this ScanSnap Home build'}}

INFO_EVENTS = ['FirmVersion', 'SerialNo', 'ScannerName', 'AcquisitionDate', 'ManagerVersion']


### Parameters

param_pollInterval = Parameter({'title': 'Poll interval (sec)', 'order': 1,
                                'desc': 'How often the scanner is checked at rest.',
                                'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_POLL_INTERVAL}})

param_fastPollInterval = Parameter({'title': 'Fast poll interval (sec)', 'order': 2,
                                    'desc': 'How often the scanner is checked during a "Fast Poll" burst.',
                                    'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_FAST_POLL_INTERVAL}})

param_fastPollDuration = Parameter({'title': 'Fast poll duration (sec)', 'order': 3,
                                    'desc': 'How long a "Fast Poll" burst lasts. Long enough for the scanner to '
                                            'come up after its power button is pressed.',
                                    'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_FAST_POLL_DURATION}})

param_restartWait = Parameter({'title': 'Restart wait (sec)', 'order': 5,
                               'desc': 'How long to leave ScanSnap Home closed before starting it again.',
                               'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_RESTART_WAIT}})

param_appPath = Parameter({'title': 'ScanSnap Home path (override)', 'order': 6,
                           'desc': 'Only needed if the registry lookup fails. Normally this is left blank and the '
                                   'path is learnt from the first poll.',
                           'schema': {'type': 'string', 'hint': r'C:\Program Files\PFU\ScanSnap\Home\PfuSsMon.exe'}})

param_relaunch = Parameter({'title': 'Start ScanSnap Home if it is not running', 'order': 7,
                            'desc': 'Watchdog. Windows starts ScanSnap Home at login; this covers it dying '
                                    'afterwards, which otherwise leaves the scanners unusable until someone '
                                    'notices. Off by default because it launches software unattended.',
                            'schema': {'type': 'boolean'}})

param_relaunchAfter = Parameter({'title': 'Start it after (polls)', 'order': 8,
                                 'desc': 'How many consecutive polls must report it not running before the watchdog '
                                         'starts it. More than one, so a poll taken during a login or an update does '
                                         'not trigger a launch.',
                                 'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_RELAUNCH_AFTER}})

param_holdDuration = Parameter({'title': 'Hold duration (sec)', 'order': 4,
                                'desc': 'How long the last known scanner state is held when a poll cannot answer '
                                        '(a scan in progress, ScanSnap Home closed). After this it reports Unknown '
                                        'rather than going on claiming something it can no longer see.',
                                'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_HOLD_DURATION}})


### Local signals

local_event_ScannerPower = LocalEvent({'title': 'Scanner Power', 'group': 'Scanner', 'order': next_seq(),
                                       'desc': 'On / Off / Unknown. "Unknown" is not "Off".',
                                       'schema': POWER_SCHEMA})

local_event_ScannerConnected = LocalEvent({'title': 'Scanner Connected', 'group': 'Scanner', 'order': next_seq(),
                                           'desc': 'True only when the scanner is definitely connected and powered. '
                                                   'False covers "definitely not" and "cannot tell".',
                                           'schema': {'type': 'boolean'}})

local_event_ScannerCount = LocalEvent({'title': 'Scanner Count', 'group': 'Scanner', 'order': next_seq(),
                                       'schema': {'type': 'integer'}})

local_event_ScanSnapRunning = LocalEvent({'title': 'ScanSnap Running', 'group': 'ScanSnap Home', 'order': next_seq(),
                                          'desc': 'True while the PfuSsMon.exe process is present.',
                                          'schema': {'type': 'boolean'}})

local_event_ScanSnapInstalled = LocalEvent({'title': 'ScanSnap Installed', 'group': 'ScanSnap Home', 'order': next_seq(),
                                            'schema': {'type': 'boolean'}})

local_event_ScanSnapBusy = LocalEvent({'title': 'ScanSnap Busy', 'group': 'ScanSnap Home', 'order': next_seq(),
                                       'desc': 'True when the last poll was refused because a scan was in progress '
                                               'or the ScanSnap Home window was open.',
                                       'schema': {'type': 'boolean'}})

local_event_ScanSnapPower = LocalEvent({'title': 'ScanSnap Power', 'group': 'ScanSnap Home', 'order': next_seq(),
                                        'desc': 'On / Off, for a dashboard switch. This is the real process, not '
                                                'something this node believes it launched.',
                                        'schema': {'type': 'string', 'enum': ['On', 'Off']}})

local_event_ScanSnapStatus = LocalEvent({'title': 'ScanSnap Status', 'group': 'ScanSnap Home', 'order': next_seq(),
                                         'desc': 'Health of ScanSnap Home itself, for its dashboard tile. Not '
                                                 'running is a fault: nothing can scan.',
                                         'schema': STATUS_SCHEMA})

local_event_AppPath = LocalEvent({'title': 'App path', 'group': 'ScanSnap Home', 'order': next_seq(),
                                  'desc': 'Where PfuSsMon.exe was found, learnt from the registry via the helper.',
                                  'schema': {'type': 'string'}})

local_event_LastLaunch = LocalEvent({'title': 'Last launch', 'group': 'ScanSnap Home', 'order': next_seq(),
                                     'schema': {'type': 'string'}})

local_event_LastResult = LocalEvent({'title': 'Last Result', 'group': 'Status', 'order': next_seq(),
                                     'desc': 'The raw result code from the last poll.',
                                     'schema': {'type': 'string'}})

local_event_LastPoll = LocalEvent({'title': 'Last Poll', 'group': 'Status', 'order': next_seq(),
                                   'schema': {'type': 'string'}})

local_event_Status = LocalEvent({'title': 'Status', 'group': 'Status', 'order': next_seq(), 'schema': STATUS_SCHEMA})

for _name in INFO_EVENTS:
  # scanner asset info, straight through from the SDK
  globals()['local_event_%s' % _name] = LocalEvent({'title': _name, 'group': 'Scanner Info',
                                                    'order': next_seq(), 'schema': {'type': 'string'}})


### State

scannerState = 'Unknown'  # 'On', 'Off' or 'Unknown'
definitiveAt = None       # system_clock() of the last poll that actually knew, or None
polling = False           # a helper process is in flight
usable = False            # the helper exists and this is a host that can run it
fastUntil = 0             # system_clock() the current fast-poll burst ends at
fastGeneration = 0        # bumped by each new burst so an old chain abandons itself
status = {'level': 3, 'message': 'Starting up'}
notRunningPolls = 0       # consecutive polls that found ScanSnap Home down
afterPoll = []            # callbacks waiting on the result of the poll in flight
launching = False         # a start or restart is under way


### Main

def main():
  console.info('Started. Polling every %ss; "Fast Poll" polls every %ss for %ss.'
               % (pollInterval(), fastPollInterval(), fastPollDuration()))

  if relaunch():
    console.info('The watchdog will start ScanSnap Home after %s polls report it down.' % relaunchAfter())

@after_main
def initialise():
  local_event_ScannerPower.emit(scannerState)
  local_event_ScannerConnected.emit(False)
  local_event_ScanSnapBusy.emit(False)
  local_event_ScanSnapStatus.emit({'level': 3, 'message': 'Not checked yet'})
  setStatus(3, 'Starting up')

  prepareHelper()

timer_poll = Timer(lambda: poll('scheduled'), DEFAULT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, stopped=True)


### Polling

@local_action({'title': 'Poll Now', 'group': 'Scanner', 'order': next_seq(),
               'desc': 'Check the scanner immediately.'})
def PollNow(arg=None):
  poll('requested')

@local_action({'title': 'Fast Poll', 'group': 'Scanner', 'order': next_seq(),
               'desc': 'Poll rapidly for a while -- used after the scanner\'s power button is pressed, so the '
                       'result is known in seconds rather than at the next scheduled poll. Optionally takes a '
                       'number of seconds to override the configured duration.',
               'schema': {'type': 'integer'}})
def FastPoll(arg=None):
  seconds = asSeconds(arg, fastPollDuration())

  globals()['fastGeneration'] = fastGeneration + 1
  globals()['fastUntil'] = system_clock() + (seconds * 1000)

  console.info('Fast polling for %ss.' % seconds)
  fastStep(fastGeneration)

def fastStep(generation):
  if generation != fastGeneration:
    return  # a later burst has taken over

  if system_clock() >= fastUntil:
    return log(1, 'Fast poll burst finished')

  poll('fast')
  call_safe(lambda: fastStep(generation), fastPollInterval())

def poll(reason):
  if not usable:
    return log(2, 'Poll (%s) skipped: the helper is not available' % reason)

  if polling:
    # the previous helper has not come back; do not stack processes on top of it
    return log(1, 'Poll (%s) skipped: one is already in flight' % reason)

  globals()['polling'] = True
  log(2, 'Polling (%s)' % reason)

  try:
    quick_process([binaryPath()], working=nodeRoot(), timeoutInSeconds=PROCESS_TIMEOUT,
                  finished=pollFinished)
  except Exception, e:
    globals()['polling'] = False
    setStatus(2, 'Could not run %s: %s' % (BINARY_NAME, e))

def pollFinished(result):
  globals()['polling'] = False
  local_event_LastPoll.emit(str(date_now()))

  try:
    handlePollResult(result)
  finally:
    drainAfterPoll()

def pollThen(callback):
  '''Runs callback once a poll has been applied, so decisions are made on a fresh reading
     rather than one that could be a whole poll interval old.'''
  if not usable:
    return console.warn('Nothing can be polled on this host, so the request was ignored.')

  afterPoll.append(callback)
  poll('pre-check')

def drainAfterPoll():
  pending = afterPoll[:]
  del afterPoll[:]

  for callback in pending:
    try:
      callback()
    except Exception, e:
      console.error('A step waiting on the poll failed: %s' % e)

def handlePollResult(result):
  if result.code == None:
    return failedPoll('%s did not answer within %ss' % (BINARY_NAME, PROCESS_TIMEOUT))

  if result.code != 0:
    return failedPoll('%s exited with code %s' % (BINARY_NAME, result.code))

  values = parseOutput(result.stdout)

  code = values.get('Result')
  if code == None:
    return failedPoll('%s produced no result (%s)' % (BINARY_NAME, summarise(result.stdout)))

  applyResult(code, values)

def failedPoll(message):
  '''The helper itself did not work -- this says nothing about the scanner, so hold.'''
  local_event_LastResult.emit('HELPER_FAILED')
  applyScanner(None)
  setStatus(2, message)
  console.warn(message)

def parseOutput(stdout):
  '''Pulls the "SS.<key>=<value>" lines out of the helper's output.'''
  values = {}

  if stdout == None:
    return values

  for line in stdout.splitlines():
    line = line.strip()

    if not line.startswith('SS.'):
      if len(line) > 0:
        log(2, line)
      continue

    separator = line.find('=')
    if separator < 0:
      continue

    # split on the first '=' only; values may contain one
    values[line[3:separator]] = line[separator + 1:]

  return values


### Interpreting a result

def applyResult(code, values):
  local_event_LastResult.emit(code)

  installed = values.get('Installed') == '1'
  running = values.get('Running') == '1' and code != 'NOT_RUNNING'

  local_event_ScanSnapInstalled.emit(installed)
  local_event_ScanSnapRunning.emit(running)
  local_event_ScanSnapBusy.emit(code == 'BUSY')

  if 'AppPath' in values:
    local_event_AppPath.emit(values['AppPath'])

  applyScanSnapHome(installed, running)

  for name in INFO_EVENTS:
    if name in values:
      globals()['local_event_%s' % name].emit(values[name])

  known = RESULTS.get(code)

  if known == None:
    applyScanner(None)
    return setStatus(2, 'Unexpected result from %s: %s' % (BINARY_NAME, code))

  scanner = known['scanner']

  if scanner == True:
    # 'OK' means the SDK answered; the count is what says whether anything is there
    count = asCount(values.get('ScannerCount'))
    local_event_ScannerCount.emit(count)
    scanner = count > 0

    if scanner:
      setStatus(0, known['message'])
    else:
      setStatus(0, 'No scanner connected')

  else:
    if scanner == False:
      local_event_ScannerCount.emit(0)

    setStatus(known['level'], known['message'])

  applyScanner(scanner)

def applyScanSnapHome(installed, running):
  '''The ScanSnap Home half: its own tile, and the watchdog.'''
  local_event_ScanSnapPower.emit('On' if running else 'Off')

  if running:
    globals()['notRunningPolls'] = 0
    return local_event_ScanSnapStatus.emit({'level': 0, 'message': 'Running'})

  if not installed:
    globals()['notRunningPolls'] = 0
    return local_event_ScanSnapStatus.emit({'level': 2, 'message': 'ScanSnap Home is not installed on this PC'})

  globals()['notRunningPolls'] = notRunningPolls + 1

  # down is a fault in its own right, whatever anybody's desired state says: nothing can
  # scan until it is back
  local_event_ScanSnapStatus.emit({'level': 2, 'message': 'ScanSnap Home is not running'})

  considerRelaunch()

def considerRelaunch():
  if not relaunch() or launching:
    return

  if notRunningPolls < relaunchAfter():
    return log(1, 'Watchdog: ScanSnap Home has been down for %s of %s polls'
                  % (notRunningPolls, relaunchAfter()))

  console.warn('ScanSnap Home has been down for %s polls; starting it.' % notRunningPolls)
  globals()['notRunningPolls'] = 0
  startScanSnap('watchdog')


### Controlling ScanSnap Home
#
# Nothing here assumes this node started ScanSnap Home. Windows does, at login, and the
# stop is a taskkill by image name rather than a handle to a child process -- so it works
# regardless of who launched it. That is the whole point: a wrapper that can only manage
# its own child cannot manage this application at all.

@local_action({'title': 'Power', 'group': 'ScanSnap Home', 'order': next_seq(),
               'desc': 'Starts or stops ScanSnap Home itself. Stopping is refused while a scan is in progress.',
               'schema': {'type': 'string', 'enum': ['On', 'Off']}})
def Power(arg=None):
  state = asOnOff(arg)

  if state == None:
    return console.warn('Power: ignoring unexpected argument %s' % repr(arg))

  if state == 'On':
    return startScanSnap('requested')

  stopScanSnap('requested', None, False)

@local_action({'title': 'Restart ScanSnap', 'group': 'ScanSnap Home', 'order': next_seq(),
               'desc': 'Closes ScanSnap Home and starts it again. Refused while a scan is in progress.'})
def RestartScanSnap(arg=None):
  stopScanSnap('restart', restartStep, False)

@local_action({'title': 'Force Restart ScanSnap', 'group': 'ScanSnap Home', 'order': next_seq(),
               'desc': 'Restarts ScanSnap Home without checking whether it is busy. Use when its window is open '
                       'and the ordinary restart keeps refusing -- it will destroy a scan that is in progress.'})
def ForceRestartScanSnap(arg=None):
  stopScanSnap('forced restart', restartStep, True)

def restartStep():
  console.info('Starting ScanSnap Home again in %ss.' % restartWait())
  call_safe(lambda: startScanSnap('restart'), restartWait())

def stopScanSnap(reason, then, force):
  if launching:
    return console.warn('Ignored: ScanSnap Home is already being started or stopped.')

  if force:
    return killScanSnap(reason, then)

  # take a fresh reading first -- 'ScanSnap Busy' can be a whole poll interval old, and
  # killing ScanSnap Home mid-scan destroys whatever the visitor was scanning
  pollThen(lambda: stopIfIdle(reason, then))

def stopIfIdle(reason, then):
  if local_event_LastResult.getArg() == 'HELPER_FAILED':
    # proceed anyway -- refusing here would make the restart unusable exactly when
    # something is broken -- but do not let it look like idleness was confirmed
    console.warn('Could not check whether ScanSnap Home is busy; continuing with the %s anyway.' % reason)

  elif local_event_ScanSnapBusy.getArg() == True:
    return console.warn('Refused: ScanSnap Home is busy -- a scan is in progress, or its window is open. '
                        'Use "Force Restart ScanSnap" if you are sure.')

  if local_event_ScanSnapRunning.getArg() != True:
    console.info('ScanSnap Home is not running; nothing to close.')

    if then != None:
      return then()

    return

  killScanSnap(reason, then)

def killScanSnap(reason, then):
  globals()['launching'] = True
  console.info('Closing ScanSnap Home (%s).' % reason)

  def done():
    local_event_ScanSnapPower.emit('Off')

    if then != None:
      return then()  # the restart owns 'launching' from here

    globals()['launching'] = False
    FastPoll.call(fastPollDuration())

  def forced(result):
    log(1, 'taskkill /F returned %s' % result.code)
    done()

  def graceful(result):
    log(1, 'taskkill returned %s' % result.code)
    # let it close politely, then force whatever is left -- a tray app can ignore the
    # polite request, and 'not quite closed' would make the restart start a second copy
    call_safe(lambda: taskkill(True, forced), GRACEFUL_KILL_WAIT)

  taskkill(False, graceful)

def taskkill(force, finished):
  command = ['taskkill']

  if force:
    command.append('/F')

  command.extend(['/IM', PROCESS_NAME])

  try:
    quick_process(command, timeoutInSeconds=PROCESS_TIMEOUT, mergeErr=True, finished=finished)
  except Exception, e:
    globals()['launching'] = False
    setStatus(2, 'Could not close ScanSnap Home: %s' % e)

def startScanSnap(reason):
  path = appPath()

  if path == None:
    globals()['launching'] = False
    return console.error('Cannot start ScanSnap Home: its location is not known yet. Wait for a successful poll, '
                         'or fill in "ScanSnap Home path (override)".')

  globals()['launching'] = True
  console.info('Starting ScanSnap Home (%s): %s' % (reason, path))
  local_event_LastLaunch.emit(str(date_now()))

  def finished(result):
    globals()['launching'] = False

    if result.code != 0:
      return setStatus(2, 'Could not start ScanSnap Home (code %s): %s' % (result.code, summarise(result.stdout)))

    FastPoll.call(fastPollDuration())

  try:
    # launched detached through 'start', so ScanSnap Home is NOT a child of this host:
    # restarting this node must not take the tray app down with it. Owning the process is
    # precisely what stopped the App Launcher wrapper from being able to manage it.
    quick_process(['cmd', '/c', 'start', '', path], timeoutInSeconds=PROCESS_TIMEOUT,
                  mergeErr=True, finished=finished)
  except Exception, e:
    globals()['launching'] = False
    setStatus(2, 'Could not start ScanSnap Home: %s' % e)

def appPath():
  for candidate in [param_appPath, local_event_AppPath.getArg()]:
    if candidate != None and len(str(candidate).strip()) > 0:
      return str(candidate).strip()

  return None


def applyScanner(known):
  '''known is True (on), False (off) or None (this poll could not tell).'''
  if known != None:
    globals()['scannerState'] = 'On' if known else 'Off'
    globals()['definitiveAt'] = system_clock()

  elif definitiveAt == None:
    globals()['scannerState'] = 'Unknown'

  elif (system_clock() - definitiveAt) > (holdDuration() * 1000):
    # we have been unable to see the scanner for long enough that the last answer is
    # no longer evidence of anything
    if scannerState != 'Unknown':
      console.warn('No definitive scanner reading for over %ss; reporting Unknown.' % holdDuration())
    globals()['scannerState'] = 'Unknown'

  # else: hold whatever we last knew

  if scannerState == 'Unknown' and status['level'] < 1:
    # 'ScanSnap Home is busy' is a perfectly healthy thing to report, but not once it has
    # gone on long enough that we no longer know what the scanner is doing
    setStatus(1, '%s; the scanner state is unknown' % status['message'])

  local_event_ScannerPower.emit(scannerState)
  local_event_ScannerConnected.emit(scannerState == 'On')


### Status

def setStatus(level, message):
  globals()['status'] = {'level': level, 'message': message}
  local_event_Status.emit(status)


### The helper binary

def nodeRoot():
  root = _node.getRoot()
  return root.getAbsolutePath() if root != None else None

def binaryPath():
  return os.path.join(nodeRoot(), BINARY_NAME)

def sourcePath():
  return os.path.join(nodeRoot(), SOURCE_NAME)

def prepareHelper():
  windir = os.environ.get('WINDIR')

  if windir == None:
    # loading cleanly on a non-Windows host matters: this node is edited and staged on
    # the main host, and a node that dies on import loses all of its events
    console.warn('This is not a Windows host, so the ScanSnap helper cannot run here. '
                 'Deploy this node to the scanner PC.')
    return setStatus(2, 'Not a Windows host; nothing is being monitored')

  if nodeRoot() == None:
    return setStatus(2, 'This node has no file-backed root, so the helper cannot be compiled')

  if not needsCompile():
    return helperReady()

  compiler = findCompiler(windir)

  if compiler == None:
    return setStatus(2, 'No .NET Framework 4 C# compiler found; expected csc.exe under %s' % windir)

  console.info('Compiling %s using %s' % (SOURCE_NAME, compiler))
  setStatus(1, 'Compiling the ScanSnap helper')

  try:
    quick_process([compiler, '/nologo', '/out:%s' % binaryPath(), sourcePath()],
                  working=nodeRoot(), timeoutInSeconds=PROCESS_TIMEOUT, mergeErr=True,
                  finished=compileFinished)
  except Exception, e:
    setStatus(2, 'Could not start the compiler: %s' % e)

def needsCompile():
  if not os.path.exists(binaryPath()):
    return True

  try:
    # recompile when the source has been edited, otherwise an edit silently does nothing
    return os.path.getmtime(sourcePath()) > os.path.getmtime(binaryPath())
  except Exception, e:
    console.warn('Could not compare %s and %s (%s); assuming the binary is current.'
                 % (SOURCE_NAME, BINARY_NAME, e))
    return False

def findCompiler(windir):
  for candidate in COMPILER_PATHS:
    path = os.path.join(windir, candidate)
    if os.path.exists(path):
      return path

  return None

def compileFinished(result):
  if result.code != 0:
    console.error('Compilation failed (code %s)' % result.code)
    console.error(summarise(result.stdout))
    return setStatus(2, 'The ScanSnap helper failed to compile')

  console.info('Compiled %s' % BINARY_NAME)
  helperReady()

def helperReady():
  globals()['usable'] = True
  setStatus(1, 'Waiting for the first poll')

  timer_poll.setDelayAndInterval(STARTUP_DELAY, pollInterval())
  timer_poll.start()


### Convenience

def summarise(text):
  if text == None:
    return '(no output)'

  text = text.strip()

  if len(text) <= 500:
    return text

  return text[:500] + '...'

def asOnOff(value):
  '''Returns 'On' / 'Off', or None. Action arguments arrive as whatever the caller sent --
     string, boolean or number -- and a group member is handed a map, so tolerate all of it.'''
  if hasattr(value, 'get'):
    value = value.get('state')

  if value == True or value == 1:
    return 'On'

  if value == False or value == 0:
    return 'Off'

  if value == None:
    return None

  text = str(value).strip().lower()

  if text == 'on':
    return 'On'

  if text == 'off':
    return 'Off'

  return None

def asCount(value):
  try:
    return int(value)
  except Exception:
    return 0

def asSeconds(value, default):
  '''Action arguments and parameters arrive as whatever was sent -- string, float or
     None -- so coerce rather than trusting the schema.'''
  if value == None or value == '':
    return default

  try:
    seconds = int(value)
  except Exception:
    console.warn('"%s" is not a number of seconds; using %s instead' % (value, default))
    return default

  if seconds <= 0:
    console.warn('%s is not a valid number of seconds; using %s instead' % (seconds, default))
    return default

  return seconds

def pollInterval():
  return asSeconds(param_pollInterval, DEFAULT_POLL_INTERVAL)

def fastPollInterval():
  return asSeconds(param_fastPollInterval, DEFAULT_FAST_POLL_INTERVAL)

def fastPollDuration():
  return asSeconds(param_fastPollDuration, DEFAULT_FAST_POLL_DURATION)

def holdDuration():
  return asSeconds(param_holdDuration, DEFAULT_HOLD_DURATION)

def restartWait():
  return asSeconds(param_restartWait, DEFAULT_RESTART_WAIT)

def relaunch():
  return param_relaunch == True

def relaunchAfter():
  return max(1, asSeconds(param_relaunchAfter, DEFAULT_RELAUNCH_AFTER))


### Logging

local_event_LogLevel = LocalEvent({'title': 'Log level', 'group': 'Debug', 'order': 10000 + next_seq(),
                                   'desc': 'Raise this to see the polling detail.',
                                   'schema': {'type': 'integer'}})

def log(level, message):
  if local_event_LogLevel.getArg() >= level:
    console.log(('.' * level) + ' ' + message)
