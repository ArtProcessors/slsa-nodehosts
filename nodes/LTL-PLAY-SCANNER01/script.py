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

  | this node           | target                                        |
  |---------------------|-----------------------------------------------|
  | `Outlet Power`      | table PDU `Output <n>` (action)               |
  | `Outlet State`      | table PDU `Output <n>` (event)                |
  | `PDU Status`        | table PDU `Status` (event)                    |
  | `SwitchBot Press`   | the SwitchBot node's press action             |
  | `Scanner PC Status` | the scanner PC node's `Status` (event)        |
  | `ScanSnap Running`  | `LTL-PLAY-SCANMON0x` `ScanSnap Running` (event) |
  | `Scanner Power`     | `LTL-PLAY-SCANMON0x` `Scanner Power` (event)  |
  | `Scanner USB`       | `LTL-PLAY-SCANMON0x` `Scanner USB` (event)    |
  | `ScanSnap Busy`     | `LTL-PLAY-SCANMON0x` `ScanSnap Busy` (event)  |
  | `Scanner Poll`      | `LTL-PLAY-SCANMON0x` `Fast Poll` (action)     |

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

**The SwitchBot presses the SV600's _Scan_ button, not a power toggle** (confirmed on site
2026-09-14): from off, a press powers the scanner on; while it is on, a press starts a scan.
So a press that turns out to be unnecessary costs an unwanted scan, whereas a press that is
wrongly skipped leaves the scanner off and unusable -- when in doubt, press.

`Power On` and `Restart` are both refused while a sequence is already running, so two
sequences cannot press on top of each other. `Power Off` is never refused: it cancels
whatever is pending and kills the outlet, so there is always a way to stop things.

**Confirming the press.** With `Scanner Power` bound to a `LTL-PLAY-SCANMON0x` monitor
there is feedback on whether the press actually worked -- so _Confirm the press_ makes the
sequence watch for the scanner to come up and say so if it does not. A sequence that
switched the outlet on, and every `Restart`, always presses -- the scanner cannot already be
on. A `Power On` while the outlet was *already* on (All On or the Play switch pressed again
during opening hours) presses only if the monitor freshly reports the scanner **not on
USB**; otherwise it skips, because the scanner may be mid-session and a press would start a
scan. Caveat: whether an SV600 that has gone to sleep stays on USB is unverified -- if it
does, that `Power On` will not wake it, the tile goes red after the grace, and `Restart`
forces a press.

**Monitor values are only trusted once they are fresh.** When a scanner PC restarts, the
bindings to its monitor reconnect still holding the value from *before* the PC went down
(Nodel does not replay on reconnect), and the monitor may not publish again for 150s. The
gallery close shuts the PC down in the same second the outlet is cut, so that stale value is
typically "On". Until the monitor delivers something after reconnecting, its values read
as `Unknown`, the press waits for a fresh reading, and this node asks the monitor to poll.

The monitor reports three states, and the difference between them matters here. `Unknown`
means it cannot see the scanner, not that the scanner is off -- ScanSnap Home refuses to
answer mid-scan. So `Unknown` is never a fault and never a reason to skip a press.

**Confirmation has two stages when `Scanner USB` is bound.** After the press the scanner
should appear on USB within seconds (~12s measured) -- if it does not within the _USB
timeout_, the press did not land, the scanner has no mains, or the cable is out. Once it
is on USB, ScanSnap Home takes up to ~2.5 minutes to pick it up, which is the
_ScanSnap Home timeout_. `Status` stays busy (blue) through both, and a failure at either
stage names that stage. If the scanner is definitely not on USB after the first press,
it is pressed once more before that is called a failure. There is never a second press
while it is on USB: it is already powered, so a press would only start a scan.

**`Status` also reports the scanner at rest, not just the outlet.** With the outlet on:

  * not on USB -- a fault straight away. The monitor already requires two readings in
    a row, so this is ~5-15s after the scanner drops, not a flap;
  * on USB (or USB not reported) but ScanSnap Home says no scanner -- a fault only after
    the _Not-seen grace_, because ScanSnap Home briefly loses a scanner that never left
    USB whenever it restarts;
  * the monitor cannot tell (`Unknown`: mid-scan, or the PC is not reporting) -- never a
    fault. The scanner PC's own tile covers a PC that has gone away.

A failure left by a sequence clears itself as soon as the scanner reports `On`, or when the
outlet is switched off -- it describes the scanner, not the last command.
'''

from org.nodel.core import BindingState

DEFAULT_OFF_DURATION = 10
DEFAULT_BOOT_DURATION = 30
DEFAULT_READY_TIMEOUT = 180

# how often readiness is re-checked, both while waiting for it and at rest
READY_POLL_INTERVAL = 5

# how often the scanner is re-checked while waiting for a press to be confirmed
CONFIRM_POLL_INTERVAL = 5

# from the scanner appearing on USB to ScanSnap Home seeing it (~2 min 20s measured), or from
# the press when USB is not reported
DEFAULT_CONFIRM_TIMEOUT = 180

# from the press to the scanner appearing on USB (~12s measured, including the BLE press)
DEFAULT_USB_TIMEOUT = 60

# how long ScanSnap Home may report no scanner, while it is on USB, before that is a fault
DEFAULT_UNSEEN_GRACE = 180

NOT_ON_USB = 'Scanner not on USB: switched off, no power, or cable disconnected'

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

# the remote events published by the ScanSnap monitor on the scanner PC. Their bindings
# reconnect holding pre-shutdown values, so each counts only once it has delivered afresh
MONITOR_EVENTS = ('ScannerPower', 'ScannerUSB', 'ScanSnapRunning', 'ScanSnapBusy')

# how long the monitor is asked to fast-poll when a fresh reading is needed, and how often
# that request may be repeated while it goes unanswered
FRESH_POLL_SECONDS = 10
FRESH_REQUEST_INTERVAL = 30


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

param_confirmPress = Parameter({'title': 'Confirm the press', 'order': 4,
                                'desc': 'Watch the ScanSnap monitor after the button is pressed and report whether '
                                        'the scanner actually came on, pressing once more if it never reached USB. '
                                        'When the outlet was already on, presses only if the scanner is freshly '
                                        'reported not on USB -- the button is Scan, so otherwise a press could start '
                                        'a scan mid-session ("Restart" always presses). Needs the "Scanner Power" '
                                        'binding.',
                                'schema': {'type': 'boolean'}})

param_usbTimeout = Parameter({'title': 'USB timeout (sec)', 'order': 5,
                              'desc': 'With "Scanner USB" bound: how long after the press the scanner has to appear '
                                      'on USB. If it does not, the press did not land or the scanner has no power.',
                              'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_USB_TIMEOUT}})

param_confirmTimeout = Parameter({'title': 'ScanSnap Home timeout (sec)', 'order': 6,
                                  'desc': 'How long ScanSnap Home is given to see the scanner once it is on USB '
                                          '(or, without "Scanner USB", after the press) before giving up on it.',
                                  'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_CONFIRM_TIMEOUT}})

param_unseenGrace = Parameter({'title': 'Not-seen grace (sec)', 'order': 7,
                               'desc': 'At rest, how long ScanSnap Home may report no scanner while the scanner is on '
                                       'USB before the status becomes a fault. Covers ScanSnap Home restarting.',
                               'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_UNSEEN_GRACE}})

param_repressOnFailure = Parameter({'title': 'Press again if it did not come on', 'order': 8,
                                    'desc': 'Only matters without "Scanner USB" (with it, a press that did not reach '
                                            'USB is always retried once). If the scanner still definitely reports Off '
                                            'once the ScanSnap Home timeout has elapsed, press once more. The button is '
                                            'Scan, so a wrong press starts a scan rather than switching anything off. '
                                            'Never fires while the scanner is on USB.',
                                    'schema': {'type': 'boolean'}})

param_pressWithoutReady = Parameter({'title': 'Press without waiting for readiness', 'order': 9,
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

local_event_ScannerPower = LocalEvent({'title': 'Scanner Power', 'group': 'Scanner', 'order': next_seq(),
                                       'desc': 'What the ScanSnap monitor makes of the scanner itself, as opposed '
                                               'to its outlet. "Unknown" means it cannot see it -- not that it is off.',
                                       'schema': {'type': 'string', 'enum': ['On', 'Off', 'Unknown']}})

local_event_ScannerUSB = LocalEvent({'title': 'Scanner USB', 'group': 'Scanner', 'order': next_seq(),
                                     'desc': 'Whether the scanner PC can see the scanner on USB. "Unknown" when the '
                                             'monitor is not reporting it.',
                                     'schema': {'type': 'string', 'enum': ['Connected', 'Not connected', 'Unknown']}})

local_event_PressConfirmed = LocalEvent({'title': 'Press confirmed', 'group': 'Scanner', 'order': next_seq(),
                                         'desc': 'True once the scanner has been seen to come on after a press.',
                                         'schema': {'type': 'boolean'}})


### Remote bindings

remote_action_OutletPower = RemoteAction({'title': 'Outlet Power', 'group': 'Power'})

remote_action_SwitchBotPress = RemoteAction({'title': 'SwitchBot Press', 'group': 'Power'})

remote_action_ScannerPoll = RemoteAction({'title': 'Scanner Poll', 'group': 'Scanner',
                                          'desc': 'The ScanSnap monitor\'s "Fast Poll", so a press is confirmed in '
                                                  'seconds rather than at the monitor\'s next scheduled poll.'})


### State

sequence = None   # 'On' or 'Restart' while one is running, otherwise None
generation = 0    # bumped by every new command; a scheduled step whose generation is
                  # stale has been superseded and quietly abandons itself
pduStatus = None  # last status reported by the PDU
waitingFor = None # what the running sequence is waiting on, if anything
blocked = None    # {level, message}: why the last sequence gave up, until something supersedes it
unseenSince = None  # system_clock() since ScanSnap Home has reported no scanner while it is on USB
lastPressAt = 0     # system_clock() of the last press
lastStatus = None   # what 'Status' last emitted, so the 5s refresh only emits changes
graceChecked = False  # whether the last status refresh evaluated the not-seen grace
outletWasOn = False   # whether the outlet was already on when the running sequence began
freshEvents = set()   # monitor events that have delivered since their binding last connected
lastFreshRequestAt = 0  # system_clock() the monitor was last asked for a fresh reading
freshRequestLogged = False  # the current wait for a fresh reading has been logged


### Main

def main():
  console.info('Started. "Power On" is: outlet On, %ss, wait for the scanner PC and ScanSnap software '
               '(up to %ss), SwitchBot press. "Restart" prefixes that with outlet Off, %ss.'
               % (bootDuration(), readyTimeout(), offDuration()))

  if pressWithoutReady():
    console.warn('"Press without waiting for readiness" is on -- the press will not wait for the scanner PC '
                 'or the ScanSnap software. This is a commissioning setting.')

  if confirmPress():
    console.info('The press will be confirmed: on USB within %ss (if "Scanner USB" is bound), then seen by '
                 'ScanSnap Home within %ss.' % (usbTimeout(), confirmTimeout()))

  if repressOnFailure() and not confirmPress():
    console.warn('"Press again if it did not come on" does nothing while "Confirm the press" is off.')

  elif repressOnFailure():
    console.info('"Press again if it did not come on" is on (only used when "Scanner USB" is not bound).')

@after_main
def initialise():
  local_event_Busy.emit(False)

  # seed from the persisted value so a node restart does not raise a spurious alarm in the
  # window before the PDU next reports (remote events do not replay when a binding is made).
  # Only a healthy one: 'Status' now also carries scanner faults, and seeding one of those as
  # the PDU's own status would hide the scanner checks until the PDU next reported
  previous = local_event_Status.getArg()
  if hasattr(previous, 'get') and previous.get('level') == 0:
    globals()['pduStatus'] = {'level': 0, 'message': 'OK'}

  local_event_PressConfirmed.emit(False)

  refreshAtRest()
  refreshStatus()

# keeps 'Ready' and 'Scanner Power' meaningful at rest, so an operator can see why a press
# would not happen, and so the scanner reads 'Unknown' once the monitor stops reporting
timer_readiness = Timer(lambda: refreshAtRest(), READY_POLL_INTERVAL, READY_POLL_INTERVAL)

def refreshAtRest():
  checkMonitorBindings()
  refreshReadiness()
  local_event_ScannerPower.emitIfDifferent(scannerPowerNow())
  local_event_ScannerUSB.emitIfDifferent(scannerUsbNow())

  # the grace period and a monitor that stops reporting both need re-evaluating over time,
  # not just when something is emitted
  clearBlockedIfScannerOn()
  refreshStatus()


### Feedback from the PDU

def remote_event_OutletState(arg=None):
  state = asOnOff(arg)
  if state == None:
    return console.warn('Outlet State: ignoring unexpected value %s' % repr(arg))

  local_event_Power.emit(state)

  if state == 'Off' and sequence == None and blocked != None:
    # a failure describes a scanner that was meant to be on; with the outlet off it is moot
    globals()['blocked'] = None

  refreshStatus()

def remote_event_PDUStatus(arg=None):
  globals()['pduStatus'] = arg
  refreshStatus()

def refreshStatus():
  globals()['graceChecked'] = False
  status = currentStatus()

  if not graceChecked:
    # the grace clock only means something while it is being checked continuously; a stale
    # start time from before the outlet went off must not make a later check fail at once
    globals()['unseenSince'] = None

  # Java maps from the PDU binding do not compare equal to dicts, so normalise first
  status = {'level': status.get('level'), 'message': status.get('message')}

  if status != lastStatus:
    globals()['lastStatus'] = status
    local_event_Status.emit(status)

def currentStatus():
  if sequence != None:
    # a deliberate outage; do not let the PDU's view of it reach the dashboard
    return busyStatus()

  if blocked != None and blocked['level'] >= 2:
    return blocked

  if pduStatus == None:
    return NEVER_SEEN_STATUS

  if not hasattr(pduStatus, 'get') or pduStatus.get('level') != 0:
    return pduStatus

  outletOn = local_event_Power.getArg() == 'On'

  if outletOn:
    fault = connectionFault()
    if fault != None:
      return fault

  if blocked != None:
    return blocked

  if outletOn and scannerPowerNow() == 'On':
    return {'level': 0, 'message': 'Scanner connected'}

  if outletOn and scannerPowerNow() == 'Unknown':
    # not a fault (mid-scan, or the monitor has not reported since reconnecting), but do not
    # let the PDU's "OK" read as "the scanner is fine"
    return {'level': 0, 'message': 'Scanner state not known yet'}

  if local_event_Power.getArg() == 'Off':
    # the PDU's "OK" beside an Off switch reads as "the scanner is OK"
    return {'level': 0, 'message': 'Off'}

  return pduStatus

def connectionFault():
  '''With the outlet on and nothing running: why the scanner cannot be used, or None.'''
  globals()['graceChecked'] = True
  usb = scannerUsbNow()
  power = scannerPowerNow()
  now = system_clock()

  if power != 'Off' or usb == 'Not connected':
    globals()['unseenSince'] = None

  if power == 'On':
    return None

  if usb == 'Not connected':
    if now - lastPressAt < usbTimeout() * 1000:
      return None  # just pressed; it takes a few seconds to reach USB

    return {'level': 2, 'message': NOT_ON_USB}

  if power != 'Off':
    return None  # 'Unknown': mid-scan, or the monitor is not reporting -- never a fault

  # ScanSnap Home says no scanner, but it is on USB (or USB is not reported)
  if unseenSince == None:
    globals()['unseenSince'] = now

  if now - unseenSince < unseenGrace() * 1000:
    return None

  if usb == 'Connected':
    return {'level': 2, 'message': 'Scanner is on USB, but ScanSnap Home does not see it'}

  return {'level': 2, 'message': 'Scanner not connected to the scanner PC'}

def clearBlockedIfScannerOn():
  if blocked != None and sequence == None and scannerPowerNow() == 'On':
    console.info('The scanner is reporting On; clearing "%s"' % blocked['message'])
    globals()['blocked'] = None

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
  noteDelivery('ScanSnapRunning')
  refreshReadiness()


### Freshness of the monitor's values

def noteDelivery(name):
  '''A remote event handler only runs for a real emission, and only while wired -- that is
     what makes the value current. Ignore anything arriving while the binding is not wired.'''
  binding = lookup_remote_event(name)

  if binding != None and binding.getStatus() == BindingState.Wired:
    freshEvents.add(name)

def isFresh(name):
  return name in freshEvents

def checkMonitorBindings():
  '''Forgets freshness for any monitor binding that is not wired right now, and asks the
     monitor to poll when one is wired but has not delivered since it (re)connected.'''
  waiting = False

  for name in MONITOR_EVENTS:
    binding = lookup_remote_event(name)

    if binding == None or binding.getStatus() != BindingState.Wired:
      freshEvents.discard(name)

    elif name not in freshEvents:
      waiting = True

  if waiting:
    requestFreshReading()
  else:
    globals()['freshRequestLogged'] = False

def requestFreshReading():
  now = system_clock()

  if now - lastFreshRequestAt < FRESH_REQUEST_INTERVAL * 1000:
    return

  globals()['lastFreshRequestAt'] = now

  if not freshRequestLogged:
    # once per reconnection, not every 30s while the monitor stays silent
    globals()['freshRequestLogged'] = True
    console.info('The ScanSnap monitor has (re)connected; asking it for a fresh reading.')

  try:
    remote_action_ScannerPoll.call(FRESH_POLL_SECONDS)
  except Exception, e:
    console.warn('Could not ask the ScanSnap monitor to poll: %s' % e)


### The scanner itself, as reported by the ScanSnap monitor

def remote_event_ScannerPower(arg=None):
  noteDelivery('ScannerPower')
  local_event_ScannerPower.emitIfDifferent(scannerPowerNow())
  clearBlockedIfScannerOn()
  refreshStatus()

def remote_event_ScannerUSB(arg=None):
  noteDelivery('ScannerUSB')
  local_event_ScannerUSB.emitIfDifferent(scannerUsbNow())
  refreshStatus()

def remote_event_ScanSnapBusy(arg=None):
  noteDelivery('ScanSnapBusy')  # otherwise read directly when a confirmation gives up

def scannerUsbNow():
  '''Returns 'Connected', 'Not connected' or 'Unknown'. A value that is not fresh -- from a
     monitor that has gone away, or not heard from since its binding reconnected -- reads as
     'Unknown', as for "Scanner Power".'''
  binding = lookup_remote_event('ScannerUSB')

  if binding == None or binding.getStatus() != BindingState.Wired or not isFresh('ScannerUSB'):
    return 'Unknown'

  value = binding.getArg()

  if value in ('Connected', 'Not connected'):
    return value

  return 'Unknown'

def canSeeUsb():
  return scannerUsbNow() != 'Unknown'

def scanSnapBusy():
  binding = lookup_remote_event('ScanSnapBusy')
  return (binding != None and binding.getStatus() == BindingState.Wired and isFresh('ScanSnapBusy')
          and binding.getArg() == True)

def scannerPowerNow():
  '''Returns 'On', 'Off' or 'Unknown'. Anything we cannot currently see reads as
     'Unknown' and never as 'Off' -- the monitor runs on the scanner PC, so it goes away
     with it, and Nodel keeps serving whatever it last said. Being wired is not enough: a
     binding that reconnects still holds its pre-shutdown value until the monitor emits.'''
  binding = lookup_remote_event('ScannerPower')

  if binding == None or binding.getStatus() != BindingState.Wired or not isFresh('ScannerPower'):
    return 'Unknown'

  value = binding.getArg()

  if value == None:
    return 'Unknown'

  if hasattr(value, 'get'):
    value = value.get('state')

  text = str(value).strip().lower()

  if text in ('on', 'true', '1'):
    return 'On'

  if text in ('off', 'false', '0'):
    return 'Off'

  return 'Unknown'

def canConfirm():
  binding = lookup_remote_event('ScannerPower')
  return binding != None and binding.getStatus() == BindingState.Wired

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

    if name in MONITOR_EVENTS and not isFresh(name):
      return False, '%s (waiting for a fresh reading)' % description

    if not isReadyValue(binding.getArg()):
      return False, description

  # the press decision (skip or press, and which confirmation to run) reads the monitor, so it
  # must not be made on values left over from before the scanner PC restarted
  if confirmPress() and canConfirm() and not isFresh('ScannerPower'):
    return False, 'a fresh reading from the ScanSnap monitor'

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

  wasOn = local_event_Power.getArg() == 'On'
  beginSequence('On')
  globals()['outletWasOn'] = wasOn
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
    setBlocked(2, 'Waited %ss for %s. The SwitchBot was not pressed, so the scanner is powered but probably off.'
                  % (readyTimeout(), reason))
    return console.warn('Gave up waiting for %s; the SwitchBot was not pressed.' % reason)

  if reason != waitingFor:
    console.info('Holding the SwitchBot press; waiting for %s.' % reason)

  setWaitingFor(reason)
  schedule(lambda: awaitReady(attemptsLeft - 1), READY_POLL_INTERVAL)

def pressPowerButton():
  # If this sequence switched the outlet on (or is a Restart) the scanner cannot already be
  # on, so always press. If the outlet was ALREADY on, the scanner may be in use: press only
  # when the monitor freshly says it is not on USB, i.e. definitely off. Anything else --
  # on USB, or cannot tell (ScanSnap Home busy for a long scan) -- risks starting a scan in
  # the middle of someone's session. Restart is the way to force a press
  if confirmPress() and outletWasOn:
    usb = scannerUsbNow()

    if usb != 'Not connected':
      console.info('The outlet was already on and the scanner is not definitely off (USB: %s); skipping '
                   'the press, which could only start a scan. Use "Restart" to force one.' % usb)
      local_event_PressConfirmed.emit(scannerPowerNow() == 'On')
      return endSequence()

  if not doPress():
    return endSequence()

  if not confirmPress():
    console.info('Sequence complete.')
    return endSequence()

  if not canConfirm():
    console.warn('"Confirm the press" is on, but nothing is reporting "Scanner Power", so the press '
                 'cannot be confirmed. Bind it to a ScanSnap monitor.')
    return endSequence()

  local_event_PressConfirmed.emit(False)

  if canSeeUsb():
    setWaitingFor('the scanner to appear on USB')
    return schedule(lambda: awaitUsb(usbAttempts(), False), CONFIRM_POLL_INTERVAL)

  awaitScanSnap(False)

def awaitUsb(attemptsLeft, pressedAgain):
  '''Stage one: the scanner should reach USB within seconds of a press that landed.'''
  if scannerPowerNow() == 'On':
    return pressConfirmed()

  usb = scannerUsbNow()

  if usb == 'Connected':
    console.info('The scanner is on USB; giving ScanSnap Home up to %ss to see it.' % confirmTimeout())
    return awaitScanSnap(pressedAgain)

  if attemptsLeft > 0:
    return schedule(lambda: awaitUsb(attemptsLeft - 1, pressedAgain), CONFIRM_POLL_INTERVAL)

  # definitely not on USB means not powered, so one more press can only help: the button is
  # Scan, and the worst a needless press does is start a scan
  if usb == 'Not connected' and not pressedAgain:
    console.warn('The scanner is still not on USB %ss after the press; pressing once more.' % usbTimeout())
    setWaitingFor('the scanner to appear on USB after a second press')

    if doPress():
      return schedule(lambda: awaitUsb(usbAttempts(), True), CONFIRM_POLL_INTERVAL)

  endSequence()

  if usb == 'Not connected':
    presses = 'either of two presses' if pressedAgain else 'its button was pressed'
    setBlocked(2, 'Scanner did not power on: not on USB %ss after %s. Check the SwitchBot, '
                  'the scanner\'s power and its USB cable.' % (usbTimeout(), presses))
  else:
    setBlocked(1, 'Could not confirm the scanner came on: the scanner PC stopped reporting USB. Its button was '
                  'pressed.')

  console.warn('The scanner did not appear on USB; it reports %s.' % usb)

def awaitScanSnap(pressedAgain):
  '''Stage two, or the only stage when USB is not reported: ScanSnap Home sees the scanner.'''
  setWaitingFor('ScanSnap Home to see the scanner')
  requestScannerPoll()

  schedule(lambda: awaitScannerOn(confirmAttempts(), pressedAgain), CONFIRM_POLL_INTERVAL)

def pressConfirmed():
  local_event_PressConfirmed.emit(True)
  console.info('The scanner is reporting On; sequence complete.')
  endSequence()

def doPress():
  # power is already back on at this point, so a failed press is reported but not rolled back
  try:
    remote_action_SwitchBotPress.call()
    globals()['lastPressAt'] = system_clock()
    console.info('SwitchBot pressed.')
    return True
  except Exception, e:
    console.error('The SwitchBot press failed; the scanner has power but may still be off: %s' % e)
    return False

def requestScannerPoll():
  '''Asks the monitor to poll quickly for a while, so confirmation takes seconds rather
     than waiting for its slow scheduled poll.'''
  try:
    remote_action_ScannerPoll.call(confirmTimeout() + CONFIRM_POLL_INTERVAL)
  except Exception, e:
    console.warn('Could not ask the ScanSnap monitor to poll; confirmation will be as slow as its '
                 'own polling: %s' % e)

def awaitScannerOn(attemptsLeft, pressedAgain):
  state = scannerPowerNow()

  if state == 'On':
    return pressConfirmed()

  if attemptsLeft > 0:
    return schedule(lambda: awaitScannerOn(attemptsLeft - 1, pressedAgain), CONFIRM_POLL_INTERVAL)

  usb = scannerUsbNow()

  # only ever on a definite 'Off', and never while it is on USB: it is powered, so a press
  # would only start a scan (the button is Scan)
  if state == 'Off' and usb != 'Connected' and repressOnFailure() and not pressedAgain:
    console.warn('The scanner still reports Off after %ss; pressing once more.' % confirmTimeout())

    if doPress():
      if canSeeUsb():
        setWaitingFor('the scanner to appear on USB')
        return schedule(lambda: awaitUsb(usbAttempts(), True), CONFIRM_POLL_INTERVAL)

      return awaitScanSnap(True)

  endSequence()

  if usb == 'Not connected':
    setBlocked(2, '%s. It dropped off USB while ScanSnap Home was picking it up.' % NOT_ON_USB)

  elif usb == 'Connected' and scanSnapBusy():
    # ScanSnap Home refuses to answer while its window is open or a scan is running, and the
    # monitor holds its last answer meanwhile -- so 'Off' here is not evidence of a fault
    setBlocked(1, 'Scanner is on USB, but ScanSnap Home is busy, so it could not be confirmed')

  elif usb == 'Connected' and state == 'Off':
    setBlocked(2, 'Scanner is on USB, but ScanSnap Home had not seen it %ss later. Try restarting ScanSnap Home.'
                  % confirmTimeout())

  elif state == 'Off':
    setBlocked(2, 'The scanner still reported Off %ss after its power button was pressed. It has power '
                  'but is probably still off.' % confirmTimeout())

  else:
    setBlocked(1, 'Could not confirm the scanner came on: nothing is reporting its state. It has power, '
                  'and the button was pressed.')

  console.warn('Could not confirm the scanner came on; it reports %s, USB %s.' % (state, usb))

def readyAttempts():
  return max(1, readyTimeout() / READY_POLL_INTERVAL)

def confirmAttempts():
  return max(1, confirmTimeout() / CONFIRM_POLL_INTERVAL)

def usbAttempts():
  return max(1, usbTimeout() / CONFIRM_POLL_INTERVAL)


### Sequencing

def beginSequence(name):
  globals()['generation'] = generation + 1
  globals()['sequence'] = name
  globals()['outletWasOn'] = False  # "Power On" sets it after this; a Restart must always press
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

def setBlocked(level, message):
  globals()['blocked'] = {'level': level, 'message': message}
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

def confirmTimeout():
  return asSeconds(param_confirmTimeout, DEFAULT_CONFIRM_TIMEOUT)

def usbTimeout():
  return asSeconds(param_usbTimeout, DEFAULT_USB_TIMEOUT)

def unseenGrace():
  return asSeconds(param_unseenGrace, DEFAULT_UNSEEN_GRACE)

def pressWithoutReady():
  return param_pressWithoutReady == True

def confirmPress():
  return param_confirmPress == True

def repressOnFailure():
  return param_repressOnFailure == True
