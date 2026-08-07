'''
SwitchBot Bot -- dual-transport control: SwitchBot Cloud API (v1.1) OR BLE-direct.

Set "Default mode" to pick which transport Press / On / Off use. Both transports
are always available via the explicit "Press via Cloud" / "Press via BLE" actions
plus the "Last latency (ms)" event, so you can A/B the two head-to-head.

CLOUD  -- needs a SwitchBot Hub bridging the Bot. Get token+secret in the app:
          Profile > Preferences > tap "App Version" 10x > Developer Options.
BLE    -- needs Bluetooth on the Nodel host, python3, and the switchbot_ble.py
          helper (pip3 install bleak). Works for Bots with no BLE password.
          switchbot_radio.py sits beside it and resets a wedged Windows radio;
          "Reset Bluetooth Radio" calls it, and a press timeout does so once
          automatically.
'''

# NOTE: the stdlib 'json' module fails to import in this Jython host, so we use
# the toolkit's native json_decode/json_encode instead.
import time, hmac, hashlib, base64, uuid
# get_url raises a java.lang.RuntimeException on HTTP errors (e.g. 401); Python's
# `except Exception` does NOT catch Java throwables in Jython, so catch this too.
from java.lang import Throwable

# --- Parameters -------------------------------------------------------------
param_Mode = Parameter({'title': 'Default mode', 'order': 0,
                        'schema': {'type': 'string', 'enum': ['Cloud', 'BLE']}})

# Cloud
param_Token    = Parameter({'title': 'Cloud: API token',  'group': 'Cloud', 'order': 1, 'schema': {'type': 'string'}})
param_Secret   = Parameter({'title': 'Cloud: API secret', 'group': 'Cloud', 'order': 2, 'schema': {'type': 'string'}})
param_DeviceID = Parameter({'title': 'Cloud: Device ID',  'group': 'Cloud', 'order': 3, 'schema': {'type': 'string'}})
param_PollSecs = Parameter({'title': 'Cloud: status poll (s)', 'group': 'Cloud', 'order': 4,
                            'schema': {'type': 'integer', 'hint': '60'}})

# BLE
param_BleAddr   = Parameter({'title': 'BLE: address (MAC / macOS UUID)', 'group': 'BLE', 'order': 1, 'schema': {'type': 'string'}})
param_Python    = Parameter({'title': 'BLE: python3 path', 'group': 'BLE', 'order': 2, 'schema': {'type': 'string', 'hint': 'python3'}})
param_BleScript = Parameter({'title': 'BLE: helper script path', 'group': 'BLE', 'order': 3, 'schema': {'type': 'string', 'hint': '/opt/nodel/switchbot_ble.py'}})
param_AutoRecover = Parameter({'title': 'BLE: auto-reset the radio on timeout', 'group': 'BLE', 'order': 4,
                               'desc': 'On a press timeout, reset the Bluetooth radio and retry once. Default on.',
                               'schema': {'type': 'boolean'}})

BASE = 'https://api.switch-bot.com/v1.1'

# --- Status events ----------------------------------------------------------
# The Bot is a momentary actuator with no state feedback of its own, so Status
# reports the outcome of the LAST press attempt, not the position of anything.
# 'Unknown' (level 1) until something is tried -- never report an untried bot as
# healthy. Shape is Nodel's standard {level, message} so the dashboard's
# <status> tile renders and colours it like every other tile on the Play tab.
local_event_Status        = LocalEvent({'group': 'Status', 'order': 0, 'title': 'Status',
                                        'desc': 'Outcome of the last press attempt.',
                                        'schema': {'type': 'object', 'properties': {
                                          'level':   {'type': 'integer', 'title': 'Level',   'order': 1},
                                          'message': {'type': 'string',  'title': 'Message', 'order': 2}}}})
local_event_Battery       = LocalEvent({'group': 'Status', 'order': 1, 'schema': {'type': 'integer'}})
local_event_Power         = LocalEvent({'group': 'Status', 'order': 2, 'schema': {'type': 'string'}})
local_event_LastTransport = LocalEvent({'group': 'Status', 'order': 3, 'schema': {'type': 'string'}})
local_event_LastLatency   = LocalEvent({'group': 'Status', 'order': 4, 'schema': {'type': 'integer'}})
local_event_Error         = LocalEvent({'group': 'Status', 'order': 5, 'schema': {'type': 'string'}})

def _status(level, message):
  local_event_Status.emit({'level': level, 'message': message})

# --- Cloud transport --------------------------------------------------------
def _headers():
  token  = lookup_parameter('Token') or ''
  secret = lookup_parameter('Secret') or ''
  t = str(int(time.time() * 1000))
  nonce = str(uuid.uuid4())
  mac = hmac.new(str(secret), token + t + nonce, hashlib.sha256)
  sign = base64.b64encode(mac.digest())
  return {'Authorization': token, 't': t, 'nonce': nonce, 'sign': sign,
          'Content-Type': 'application/json; charset=utf8'}

def _cloud(cmd):
  start = time.time()
  try:
    url = '%s/devices/%s/commands' % (BASE, lookup_parameter('DeviceID'))
    body = '{"command":"%s","parameter":"default","commandType":"command"}' % cmd
    resp = get_url(url, post=body, contentType='application/json', headers=_headers())
    ms = int((time.time() - start) * 1000)
    console.info('Cloud %s ok in %sms -> %s' % (cmd, ms, resp))
    local_event_LastTransport.emit('Cloud'); local_event_LastLatency.emit(ms); local_event_Error.emit('')
    _status(0, 'Last %s OK via Cloud in %sms' % (cmd, ms))
  except (Exception, Throwable), e:
    console.warn('Cloud %s failed: %s' % (cmd, e)); local_event_Error.emit(str(e))
    _status(2, 'Last %s failed via Cloud: %s' % (cmd, e))

# --- BLE transport ----------------------------------------------------------
# Nodel sandboxes recipes and blocks raw java.lang.ProcessBuilder, so we launch
# the helper via the toolkit's quick_process (async: result arrives in finished).
BLE_TIMEOUT = 20
RADIO_TIMEOUT = 45  # the reset helper deliberately sleeps ~9s mid-toggle

def _radio_script():
  '''The radio helper sits beside the BLE helper, so it survives node renames.

  Deriving it beats a second path parameter: a rename moves the node directory
  and rewrites nothing, and one stale path parameter has already cost us a
  site visit.'''
  p = lookup_parameter('BleScript') or ''
  i = max(p.rfind('\\'), p.rfind('/'))
  return (p[:i + 1] if i >= 0 else '') + 'switchbot_radio.py'

def _auto_recover():
  # Unset means on -- the whole point is that it recovers without being armed.
  v = lookup_parameter('AutoRecover')
  if v is None or v == '':
    return True
  if isinstance(v, basestring):
    return v.strip().lower() not in ('false', 'no', 'off', '0')
  return bool(v)

def _reset_radio(then=None):
  '''Toggle the host's Bluetooth radio off and on. `then` gets True/False.'''
  script_path = _radio_script()
  start = time.time()

  def finished(result):
    ms = int((time.time() - start) * 1000)
    code = getattr(result, 'code', None)
    out = (getattr(result, 'stdout', '') or '').strip()
    err = (getattr(result, 'stderr', '') or '').strip()
    ok = (code == 0)
    if ok:
      console.info('Bluetooth radio reset ok in %sms: %s' % (ms, out.replace('\n', ' | ')))
    elif code is None:
      console.warn('Bluetooth radio reset did not complete (%s) -- helper %s' % (
        'timed out' if ms >= (RADIO_TIMEOUT - 1) * 1000 else 'never started', script_path))
    else:
      console.warn('Bluetooth radio reset failed (exit %s): %s' % (code, err or out))
    if then is not None:
      then(ok)

  try:
    quick_process([lookup_parameter('Python') or 'python3', script_path],
                  finished=finished, timeoutInSeconds=RADIO_TIMEOUT)
  except (Exception, Throwable), e:
    console.warn('Bluetooth radio reset failed to launch: %s' % e)
    if then is not None:
      then(False)

def _ble(cmd, retry=True):
  arg = {'press': 'press', 'turnOn': 'on', 'turnOff': 'off'}.get(cmd, 'press')
  script_path = lookup_parameter('BleScript'); addr = lookup_parameter('BleAddr')
  if not script_path or not addr:
    console.warn('BLE: set the helper script path and address parameters')
    _status(2, 'Not configured -- set the BLE helper script path and address'); return
  cmdlist = [lookup_parameter('Python') or 'python3', script_path, addr, arg]
  start = time.time()

  def finished(result):
    ms = int((time.time() - start) * 1000)
    code = getattr(result, 'code', None)
    out = (getattr(result, 'stdout', '') or '').strip()
    err = (getattr(result, 'stderr', '') or '').strip()
    # The toolkit leaves 'code' as None when the process never ran (bad exe path)
    # or timed out -- NOT only on success. Treating None as OK reported presses
    # that never happened, so it gets its own branch.
    #
    # Those two causes need very different fixes, and the elapsed time separates
    # them cleanly: a failed launch returns almost immediately, a timeout returns
    # at the deadline. Do not collapse them back into one message -- "check your
    # paths" sent the last person down the wrong track for an hour when the real
    # fault was a wedged Bluetooth radio.
    if code is None:
      if ms < (BLE_TIMEOUT - 1) * 1000:
        console.warn('BLE %s: helper never started -- check the BLE python3 path (%s) '
                     'and helper script path (%s)' % (arg, lookup_parameter('Python'), script_path))
        local_event_Error.emit(err or out or 'helper never started')
        _status(2, 'Last %s failed: helper never started -- check the BLE python3 / helper script paths' % arg)
        return
      console.warn('BLE %s: timed out after %ss. The helper ran but Bluetooth did not answer -- '
                   'usually a wedged radio, not a bad path.' % (arg, BLE_TIMEOUT))
      local_event_Error.emit(err or out or ('timed out after %ss' % BLE_TIMEOUT))
      if retry and _auto_recover():
        console.info('Resetting the Bluetooth radio and retrying the %s once...' % arg)
        _status(1, 'Press timed out -- resetting the Bluetooth radio and retrying')

        def after_reset(ok):
          if ok:
            _ble(cmd, retry=False)
          else:
            _status(2, 'Last %s timed out and the Bluetooth radio reset failed -- '
                       'reboot the host, or check the console' % arg)
        _reset_radio(then=after_reset)
        return
      _status(2, 'Last %s timed out after %ss -- Bluetooth did not answer; try Reset Bluetooth Radio'
                 % (arg, BLE_TIMEOUT))
      return
    if code != 0:
      console.warn('BLE %s failed (exit %s): %s' % (arg, code, err or out))
      local_event_Error.emit(err or out or ('exit %s' % code))
      _status(2, 'Last %s failed (exit %s): %s' % (arg, code, err or out)); return
    console.info('BLE %s ok in %sms %s' % (arg, ms, out))
    local_event_LastTransport.emit('BLE'); local_event_LastLatency.emit(ms); local_event_Error.emit('')
    _status(0, 'Last %s OK via BLE in %sms%s' % (arg, ms, '' if retry else ' (after a radio reset)'))

  try:
    quick_process(cmdlist, finished=finished, timeoutInSeconds=BLE_TIMEOUT)
  except (Exception, Throwable), e:
    console.warn('BLE launch failed: %s' % e); local_event_Error.emit(str(e))
    _status(2, 'Last %s failed to launch: %s' % (arg, e))

def _dispatch(cmd):
  (_ble if lookup_parameter('Mode') == 'BLE' else _cloud)(cmd)

# --- Actions (follow Default mode) ------------------------------------------
@local_action({'group': 'Control', 'order': 1})
def Press(arg=None):
  '''Momentary press'''
  _dispatch('press')

@local_action({'group': 'Control', 'order': 2})
def On(arg=None):
  _dispatch('turnOn')

@local_action({'group': 'Control', 'order': 3})
def Off(arg=None):
  _dispatch('turnOff')

# --- Actions (explicit, for A/B comparison) ---------------------------------
@local_action({'group': 'Compare', 'order': 1})
def PressViaCloud(arg=None):
  _cloud('press')

@local_action({'group': 'Compare', 'order': 2})
def PressViaBLE(arg=None):
  _ble('press')

# --- Recovery ---------------------------------------------------------------
@local_action({'group': 'Recover', 'order': 1, 'title': 'Reset Bluetooth Radio'})
def ResetBluetoothRadio(arg=None):
  '''Toggle the host Bluetooth radio off and on (Windows). Takes ~10s.

  For when presses time out but the paths are fine: the Intel stack reports
  healthy -- driver OK, bthserv Running, radio On -- while BLE discovery
  returns nothing at all.'''
  _reset_radio()

# --- Cloud status -----------------------------------------------------------
def _poll():
  device = lookup_parameter('DeviceID'); token = lookup_parameter('Token')
  if not device or not token:
    return
  try:
    url = '%s/devices/%s/status' % (BASE, device)
    data = json_decode(get_url(url, headers=_headers())).get('body', {})
    local_event_Battery.emit(data.get('battery')); local_event_Power.emit(data.get('power')); local_event_Error.emit('')
  except (Exception, Throwable), e:
    console.warn('poll failed: %s' % e); local_event_Error.emit(str(e))

@local_action({'group': 'Status', 'order': 6})
def PollStatus(arg=None):
  '''Fetch battery + power now (Cloud only)'''
  _poll()

@local_action({'group': 'Setup'})
def ListDevices(arg=None):
  '''Dump account devices to find the Cloud Device ID'''
  console.info(get_url('%s/devices' % BASE, headers=_headers()))

# --- Main -------------------------------------------------------------------
def main():
  console.info('SwitchBot Bot recipe started (default mode: %s)' % (lookup_parameter('Mode') or 'Cloud'))
  # A restart tells us nothing about the bot, and the previous outcome did not
  # survive it -- so go back to Unknown rather than implying health.
  _status(1, 'Unknown -- no press attempted since this node started')

Timer(lambda: _poll(), lookup_parameter('PollSecs') or 60, 10)
