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

  | this node         | target                                    |
  |-------------------|-------------------------------------------|
  | `Outlet Power`    | table PDU `Output <n>` (action)           |
  | `Outlet State`    | table PDU `Output <n>` (event)            |
  | `PDU Status`      | table PDU `Status` (event)                |
  | `SwitchBot Press` | the SwitchBot node's press action         |

While a sequence is running `Busy` is true and `Status` reports that rather than the
PDU's own status, so the tile does not flap into an alarm state over an outage we
caused deliberately.

`Power On` and `Restart` are both refused while a sequence is already running -- the
SwitchBot press is a *toggle*, so letting two sequences overlap would press twice and
switch the scanner back off. `Power Off` is never refused: it cancels whatever is
pending and kills the outlet, so there is always a way to stop things.
'''

DEFAULT_OFF_DURATION = 10
DEFAULT_BOOT_DURATION = 30

# status 'level' reserved for "we are deliberately interfering with this thing"
BUSY_LEVEL = 5

POWER_SCHEMA = {'type': 'string', 'enum': ['On', 'Off']}

STATUS_SCHEMA = {'type': 'object', 'properties': {
    'level': {'type': 'integer', 'title': 'Level', 'order': 1},
    'message': {'type': 'string', 'title': 'Message', 'order': 2}}}

BUSY_STATUS = {'On': {'level': BUSY_LEVEL, 'message': 'Scanner starting up. Please wait.'},
               'Restart': {'level': BUSY_LEVEL, 'message': 'Scanner restarting. Please wait.'}}

NEVER_SEEN_STATUS = {'level': 99, 'message': 'PDU has never been seen'}


### Parameters

param_offDuration = Parameter({'title': 'Off duration (sec)', 'order': 1,
                               'desc': 'How long "Restart" leaves the outlet off before switching it back on.',
                               'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_OFF_DURATION}})

param_bootDuration = Parameter({'title': 'Boot duration (sec)', 'order': 2,
                                'desc': 'How long to wait after the outlet is switched on before the SwitchBot presses '
                                        'the scanner\'s power button. Too short and the press lands on a scanner that '
                                        'is not ready to accept it.',
                                'schema': {'type': 'integer', 'hint': '%s' % DEFAULT_BOOT_DURATION}})


### Local signals

local_event_Power = LocalEvent({'title': 'Power', 'group': 'Power', 'order': next_seq(), 'schema': POWER_SCHEMA})

local_event_Status = LocalEvent({'title': 'Status', 'group': 'Status', 'order': next_seq(), 'schema': STATUS_SCHEMA})

local_event_Busy = LocalEvent({'title': 'Busy', 'group': 'Power', 'order': next_seq(),
                               'desc': 'True while a power-on or restart sequence is running.',
                               'schema': {'type': 'boolean'}})


### Remote bindings

remote_action_OutletPower = RemoteAction({'title': 'Outlet Power', 'group': 'Power'})

remote_action_SwitchBotPress = RemoteAction({'title': 'SwitchBot Press', 'group': 'Power'})


### State

sequence = None   # 'On' or 'Restart' while one is running, otherwise None
generation = 0    # bumped by every new command; a scheduled step whose generation is
                  # stale has been superseded and quietly abandons itself
pduStatus = None  # last status reported by the PDU


### Main

def main():
  console.info('Started. "Power On" is: outlet On, %ss, SwitchBot press. "Restart" prefixes that with '
               'outlet Off, %ss.' % (bootDuration(), offDuration()))

@after_main
def initialise():
  local_event_Busy.emit(False)

  # seed from the persisted value so a node restart does not raise a spurious alarm in the
  # window before the PDU next reports (remote events do not replay when a binding is made)
  previous = local_event_Status.getArg()
  if hasattr(previous, 'get') and previous.get('level') != BUSY_LEVEL:
    globals()['pduStatus'] = previous

  refreshStatus()


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
    local_event_Status.emit(BUSY_STATUS[sequence])

  elif pduStatus == None:
    local_event_Status.emit(NEVER_SEEN_STATUS)

  else:
    local_event_Status.emit(pduStatus)


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
  console.info('Restart: outlet Off for %ss, then On, then a SwitchBot press %ss later.'
               % (offDuration(), bootDuration()))

  try:
    remote_action_OutletPower.call('Off')
  except Exception, e:
    endSequence()
    return console.error('Restart: could not switch the outlet off, sequence abandoned: %s' % e)

  schedule(switchOnThenPress, offDuration())


### The shared "switch on and press" half

def switchOnThenPress():
  try:
    remote_action_OutletPower.call('On')
  except Exception, e:
    endSequence()
    return console.error('Power On: could not switch the outlet on, sequence abandoned: %s' % e)

  console.info('Outlet on; the SwitchBot will press in %ss.' % bootDuration())
  schedule(pressPowerButton, bootDuration())

def pressPowerButton():
  # power is already back on at this point, so a failed press is reported but not rolled back
  try:
    remote_action_SwitchBotPress.call()
    console.info('SwitchBot pressed; sequence complete.')
  except Exception, e:
    console.error('The SwitchBot press failed; the scanner has power but may still be off: %s' % e)

  endSequence()


### Sequencing

def beginSequence(name):
  globals()['generation'] = generation + 1
  globals()['sequence'] = name
  local_event_Busy.emit(True)
  refreshStatus()

def endSequence():
  globals()['generation'] = generation + 1
  globals()['sequence'] = None
  local_event_Busy.emit(False)
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
