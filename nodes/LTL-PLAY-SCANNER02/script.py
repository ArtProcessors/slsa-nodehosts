'''
**ScanSnap scanner power / restart adapter**

The ScanSnap scanners have no network control of their own. They hang off a switched
NETIO outlet on a table PDU, and their front-panel power button is pressed by a
SwitchBot. This node presents that pair as a single device, so the dashboard can bind
one `Power` / `Status` / `Restart` set.

Restoring the outlet does *not* bring the scanner back up by itself -- its front panel
button still has to be pressed once it has had time to boot. So:

  * `Power` **Off** simply switches the outlet off.
  * `Power` **On** switches the outlet on, waits the _Boot duration_, then presses.
  * `Restart` switches the outlet off, waits the _Off duration_, then does exactly
    what `Power On` does.

Bind the remote actions and events below:

  | this node           | target                                     |
  |---------------------|--------------------------------------------|
  | `Outlet Power`      | table PDU `Output <n>` (action)            |
  | `Outlet State`      | table PDU `Output <n>` (event)             |
  | `PDU Status`        | table PDU `Status` (event)                 |
  | `SwitchBot Press`   | the SwitchBot node's press action          |
  | `Scanner PC Status` | the scanner PC node's `Status` (event)     |
  | `ScanSnap Running`  | the ScanSnap launcher's `Running` (event)  |

**The press is gated.** There is no point pressing the scanner's power button while the
PC it feeds is still booting or the ScanSnap software is not up, so the press waits for
both to be ready, up to the _Ready timeout_, and is abandoned rather than fired blindly
if they never are. The outlet is *not* gated -- switching it on is harmless, and the
scanner needs power before it can respond to anything.

Readiness deliberately requires the reporting binding to be **wired right now**, not
merely to hold a value that says "ready". The PC nodes run *on* the scanner PC, so when
that PC is off they disappear and Nodel keeps serving their last known value -- a
`Running` of `true` published before the PC went down would otherwise read as ready the
next morning, and the press would land mid-boot.

While a sequence is running `Busy` is true and `Status` reports that rather than the
PDU's own status, so the tile does not flap into an alarm state over an outage we
caused deliberately.

`Power On` and `Restart` are both refused while a sequence is already running -- the
SwitchBot press is a *toggle*, so letting two sequences overlap would press twice and
switch the scanner back off. `Power Off` is never refused: it cancels whatever is
pending and kills the outlet, so there is always a way to stop things.
'''

from org.nodel.core import BindingState

DEFAULT_OFF_DURATION = 10
DEFAULT_BOOT_DURATION = 30
DEFAULT_READY_TIMEOUT = 180

# how often readiness is re-checked, both while waiting for it and at rest
READY_POLL_INTERVAL = 5

# status 'level' reserved for "we are deliberately interfering with this thing"
BUSY_LEVEL = 5

POWER_SCHEMA = {'type': 'string', 'enum': ['On', 'Off']}

STATUS_SCHEMA = {'type': 'object', 'properties': {
    'level': {'type': 'integer', 'title': 'Level', 'order': 1},
    'message': {'type': 'string', 'title': 'Message', 'order': 2}}}

NEVER_SEEN_STATUS = {'level': 99, 'message': 'PDU has never been seen'}

# the remote events that must all be ready before the SwitchBot is pressed,
# paired with how each is described to the operator
READY_INPUTS = [('ScannerPCStatus', 'the scanner PC'),
                ('ScanSnapRunning', 'the ScanSnap software')]


### Parameters

param_offDuration = Parameter({'title': 'Off duration (sec)', 'order': 1,
                               'desc': 'How long "Restart" leaves the outlet off before switching it back on.',
                               'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_OFF_DURATION}})

param_bootDuration = Parameter({'title': 'Boot duration (sec)', 'order': 2,
                                'desc': 'How long to wait after the outlet is switched on before considering the '
                                        'SwitchBot press. Too short and the press lands on a scanner that is not '
                                        'ready to accept it.',
                                'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_BOOT_DURATION}})

param_readyTimeout = Parameter({'title': 'Ready timeout (sec)', 'order': 3,
                                'desc': 'How long to keep waiting for the scanner PC and the ScanSnap software after '
                                        'the boot duration has elapsed. If they are not both ready by then the press '
                                        'is abandoned and the status says so. Allow for a cold boot plus the software '
                                        'launching.',
                                'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_READY_TIMEOUT}})

param_pressWithoutReady = Parameter({'title': 'Press without waiting for readiness', 'order': 4,
                                     'desc': 'Commissioning escape hatch. Presses as soon as the boot duration has '
                                             'elapsed, without requiring the scanner PC or ScanSnap software to '
                                             'report ready. Leave off in normal operation.',
                                     'schema': {'type': 'boolean'}})


### Local signals

local_event_Power = LocalEvent({'title': 'Power', 'group': 'Power', 'order': next_seq(), 'schema': POWER_SCHEMA})

local_event_Status = LocalEvent({'title': 'Status', 'group': 'Status', 'order': next_seq(), 'schema': STATUS_SCHEMA})

local_event_Busy = LocalEvent({'title': 'Busy', 'group': 'Power', 'order': next_seq(),
                               'desc': 'True while a power-on or restart sequence is running.',
                               'schema': {'type': 'boolean'}})

local_event_Ready = LocalEvent({'title': 'Ready', 'group': 'Readiness', 'order': next_seq(),
                                'desc': 'True while the scanner PC and the ScanSnap software are both reporting ready.',
                                'schema': {'type': 'boolean'}})

local_event_ReadyDetail = LocalEvent({'title': 'Ready detail', 'group': 'Readiness', 'order': next_seq(),
                                      'desc': 'What readiness is waiting on, when it is not ready.',
                                      'schema': {'type': 'string'}})


### Remote bindings

remote_action_OutletPower = RemoteAction({'title': 'Outlet Power', 'group': 'Power'})

remote_action_SwitchBotPress = RemoteAction({'title': 'SwitchBot Press', 'group': 'Power'})


### State

sequence = None   # 'On' or 'Restart' while one is running, otherwise None
generation = 0    # bumped by every new command; a scheduled step whose generation is
                  # stale has been superseded and quietly abandons itself
pduStatus = None  # last status reported by the PDU
waitingFor = None # what the running sequence is waiting on, if anything
blocked = None    # why the last sequence gave up, until something supersedes it


### Main

def main():
  console.info('Started. "Power On" is: outlet On, %ss, wait for the scanner PC and ScanSnap software '
               '(up to %ss), SwitchBot press. "Restart" prefixes that with outlet Off, %ss.'
               % (bootDuration(), readyTimeout(), offDuration()))

  if pressWithoutReady():
    console.warn('"Press without waiting for readiness" is on -- the press will not wait for the scanner PC '
                 'or the ScanSnap software. This is a commissioning setting.')

@after_main
def initialise():
  local_event_Busy.emit(False)

  # seed from the persisted value so a node restart does not raise a spurious alarm in the
  # window before the PDU next reports (remote events do not replay when a binding is made)
  previous = local_event_Status.getArg()
  if hasattr(previous, 'get') and previous.get('level') != BUSY_LEVEL:
    globals()['pduStatus'] = previous

  refreshReadiness()
  refreshStatus()

# keeps 'Ready' meaningful at rest, so an operator can see why a press would not happen
timer_readiness = Timer(lambda: refreshReadiness(), READY_POLL_INTERVAL, READY_POLL_INTERVAL)


### Feedback from the PDU

def remote_event_OutletState(arg=None):
  state = asOnOff(arg)
  if state == None:
    return console.warn('Outlet State: ignoring unexpected value %s' % repr(arg))

  local_event_Power.emit(state)

def remote_event_PDUStatus(arg=None):
  globals()['pduStatus'] = arg
  refreshStatus()

def refreshStatus():
  if sequence != None:
    # a deliberate outage; do not let the PDU's view of it reach the dashboard
    local_event_Status.emit(busyStatus())

  elif blocked != None:
    local_event_Status.emit({'level': 2, 'message': blocked})

  elif pduStatus == None:
    local_event_Status.emit(NEVER_SEEN_STATUS)

  else:
    local_event_Status.emit(pduStatus)

def busyStatus():
  action = 'Scanner restarting.' if sequence == 'Restart' else 'Scanner starting up.'

  if waitingFor != None:
    return {'level': BUSY_LEVEL, 'message': '%s Waiting for %s.' % (action, waitingFor)}

  return {'level': BUSY_LEVEL, 'message': '%s Please wait.' % action}


### Readiness

# these exist only to be bound; the readiness check reads their current value and
# binding state directly rather than acting on each emission
def remote_event_ScannerPCStatus(arg=None):
  refreshReadiness()

def remote_event_ScanSnapRunning(arg=None):
  refreshReadiness()

def readiness():
  '''Returns (ready, reason). 'reason' names the first input that is not ready.'''
  if pressWithoutReady():
    return True, None

  for name, description in READY_INPUTS:
    binding = lookup_remote_event(name)

    if binding == None:
      return False, '%s (no "%s" binding exists)' % (description, name)

    # a value from a node that has since gone away is stale, not evidence of readiness.
    # the PC nodes live on the scanner PC, so they vanish with it and Nodel keeps
    # serving whatever they last said
    if binding.getStatus() != BindingState.Wired:
      return False, '%s (not currently reporting)' % description

    if not isReadyValue(binding.getArg()):
      return False, description

  return True, None

def refreshReadiness():
  ready, reason = readiness()

  local_event_Ready.emit(ready)
  local_event_ReadyDetail.emit('Ready' if ready else 'Waiting for %s' % reason)

  return ready, reason

def isReadyValue(value):
  if value == None:
    return False

  # a {'level': ..., 'message': ...} status counts as ready only at level 0
  if hasattr(value, 'get'):
    return value.get('level') == 0

  if value == True or value == 1:
    return True

  return str(value).strip().lower() in ('on', 'true', 'running', 'ok')


### Power

@local_action({'title': 'Power', 'group': 'Power', 'order': next_seq(), 'schema': POWER_SCHEMA})
def Power(arg=None):
  state = asOnOff(arg)
  if state == None:
    return console.warn('Power: ignoring unexpected argument %s' % repr(arg))

  if state == 'Off':
    return switchOff()

  if sequence != None:
    return console.warn('Power On: ignored, a "%s" sequence is already in progress' % sequence)

  beginSequence('On')
  switchOnThenPress()

def switchOff():
  # never refused -- this is the way out of a sequence that is misbehaving
  if sequence != None:
    console.info('Power Off: cancelling the "%s" sequence in progress' % sequence)

  endSequence()

  try:
    remote_action_OutletPower.call('Off')
  except Exception, e:
    console.error('Power Off: could not switch the outlet off: %s' % e)

# 'Power' is not emitted by these -- the PDU echoes the outlet state back through
# 'Outlet State', so what is reported is what the outlet actually did


### Restart

@local_action({'title': 'Restart', 'group': 'Power', 'order': next_seq(),
               'desc': 'Switch the outlet off, wait, then do what "Power On" does.'})
def Restart(arg=None):
  if sequence != None:
    return console.warn('Restart: ignored, a "%s" sequence is already in progress' % sequence)

  beginSequence('Restart')
  console.info('Restart: outlet Off for %ss, then On.' % offDuration())

  try:
    remote_action_OutletPower.call('Off')
  except Exception, e:
    endSequence()
    return console.error('Restart: could not switch the outlet off, sequence abandoned: %s' % e)

  schedule(switchOnThenPress, offDuration())


### The shared "switch on, wait for readiness, press" half

def switchOnThenPress():
  try:
    remote_action_OutletPower.call('On')
  except Exception, e:
    endSequence()
    return console.error('Power On: could not switch the outlet on, sequence abandoned: %s' % e)

  console.info('Outlet on; considering the SwitchBot press in %ss.' % bootDuration())
  schedule(lambda: awaitReady(readyAttempts()), bootDuration())

def awaitReady(attemptsLeft):
  ready, reason = refreshReadiness()

  if ready:
    setWaitingFor(None)
    return pressPowerButton()

  if attemptsLeft <= 0:
    endSequence()
    setBlocked('Waited %ss for %s. The SwitchBot was not pressed, so the scanner is powered but probably off.'
               % (readyTimeout(), reason))
    return console.warn('Gave up waiting for %s; the SwitchBot was not pressed.' % reason)

  if reason != waitingFor:
    console.info('Holding the SwitchBot press; waiting for %s.' % reason)

  setWaitingFor(reason)
  schedule(lambda: awaitReady(attemptsLeft - 1), READY_POLL_INTERVAL)

def pressPowerButton():
  # power is already back on at this point, so a failed press is reported but not rolled back
  try:
    remote_action_SwitchBotPress.call()
    console.info('SwitchBot pressed; sequence complete.')
  except Exception, e:
    console.error('The SwitchBot press failed; the scanner has power but may still be off: %s' % e)

  endSequence()

def readyAttempts():
  return max(1, readyTimeout() / READY_POLL_INTERVAL)


### Sequencing

def beginSequence(name):
  globals()['generation'] = generation + 1
  globals()['sequence'] = name
  globals()['waitingFor'] = None
  globals()['blocked'] = None
  local_event_Busy.emit(True)
  refreshStatus()

def endSequence():
  globals()['generation'] = generation + 1
  globals()['sequence'] = None
  globals()['waitingFor'] = None
  local_event_Busy.emit(False)
  refreshStatus()

def setWaitingFor(reason):
  globals()['waitingFor'] = reason
  refreshStatus()

def setBlocked(message):
  globals()['blocked'] = message
  refreshStatus()

def schedule(func, delay):
  '''Runs func after delay, unless a later command has superseded it in the meantime.'''
  expected = generation

  def step():
    if generation != expected:
      return console.log('(a later command superseded this step; abandoning it)')

    func()

  call_safe(step, delay)


### Convenience

def asOnOff(value):
  '''Returns 'On' / 'Off', or None if the value means neither. Action arguments arrive
     as whatever the caller sent -- string, boolean or number -- so tolerate all three.'''
  # the Group recipe hands a plain string to an ordinary member but a
  # {'state': ..., 'noPropagate': ...} map to a group member; tolerate either
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

def asSeconds(value, default):
  if value == None:
    return default

  try:
    seconds = int(value)
  except Exception:
    console.warn('"%s" is not a number of seconds; using %s instead' % (value, default))
    return default

  if seconds < 0:
    console.warn('%s is not a valid number of seconds; using %s instead' % (seconds, default))
    return default

  return seconds

def offDuration():
  return asSeconds(param_offDuration, DEFAULT_OFF_DURATION)

def bootDuration():
  return asSeconds(param_bootDuration, DEFAULT_BOOT_DURATION)

def readyTimeout():
  return asSeconds(param_readyTimeout, DEFAULT_READY_TIMEOUT)

def pressWithoutReady():
  return param_pressWithoutReady == True
