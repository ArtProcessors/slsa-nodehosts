'''
AVPro Edge AC-MX-42 -- 4x2 HDMI Matrix Switcher (network / Telnet control)

Transport : raw TCP, port 23, ASCII commands terminated with a carriage return (\\r).
Protocol  : AC-MX-42 unified command set (see manual, "RS-232 and TCP/IP Commands").

Core commands used here:
    SET OUTx VS INy      route output x [0=ALL,1,2] to input y [1-4]
    GET OUTx VS          query route  (x=0 -> all outputs)
    SET OUTx STREAM ON/OFF   output on/off  (x [0=ALL,1,2])
    GET OUTx STREAM
    SET OUTx HA MUTE ON/OFF  HDMI audio mute
    SET OUT1 VIDEOy      output-1 scaler  (1=BYPASS, 2=4K->2K)
    SET HDx AUTO EN/DIS  auto-switch  (x [0=both,1,2])

NOTE ON FEEDBACK: the manual documents the commands but not the exact echo/response
strings. This node parses any line containing "OUTn VS INn" / "OUTn STREAM ON|OFF"
(case/space tolerant). Watch the "Raw Received" event on the real device and, if the
echo differs, tweak the regexes in the "Response parsing" section below.
'''

import re

DEFAULT_PORT = 23
OUTPUTS = [1, 2]
INPUTS = [1, 2, 3, 4]

POLL_INTERVAL = 10      # seconds between routing polls
STATUS_INTERVAL = 5     # seconds between status re-evaluations
OFFLINE_AFTER = 30      # seconds without any RX before we flag a warning

# --- ordering helper (keeps the UI in a sensible order) --------------------
_order = [0]
def _seq():
    _order[0] += 1
    return _order[0]

# --- Parameters (node configuration) ---------------------------------------

param_ipAddress = Parameter({'title': 'IP address', 'order': _seq(),
                             'schema': {'type': 'string', 'hint': '192.168.1.239'}})

param_port = Parameter({'title': 'TCP port', 'order': _seq(),
                        'schema': {'type': 'integer', 'hint': str(DEFAULT_PORT)}})

# --- State -----------------------------------------------------------------

_connected = [False]
_secsSinceRx = [OFFLINE_AFTER + 1]

# --- Events (feedback / signals) -------------------------------------------

ev_outInput = {}    # output -> LocalEvent : which input the output is showing
ev_outStream = {}   # output -> LocalEvent : output stream (power) on/off
for _out in OUTPUTS:
    ev_outInput[_out] = create_local_event(
        'Output %s Input' % _out,
        {'title': 'Input', 'group': 'Output %s' % _out, 'order': _seq(),
         'schema': {'type': 'integer'}})
    ev_outStream[_out] = create_local_event(
        'Output %s Stream' % _out,
        {'title': 'Stream', 'group': 'Output %s' % _out, 'order': _seq(),
         'schema': {'type': 'boolean'}})

# Extracted audio (de-embedded analog 2CH + Toslink share one bus)
ev_exaFollow = create_local_event('Extracted Audio Follow',
    {'title': 'Follows output', 'group': 'Audio', 'order': _seq(),
     'schema': {'type': 'integer'}})
ev_exaEnabled = create_local_event('Extracted Audio Enabled',
    {'title': 'Enabled', 'group': 'Audio', 'order': _seq(),
     'schema': {'type': 'boolean'}})

ev_connected = create_local_event('Connected',
    {'title': 'Connected', 'group': 'Comms', 'order': _seq(),
     'schema': {'type': 'boolean'}})

ev_rawReceived = create_local_event('Raw Received',
    {'title': 'Raw received', 'group': 'Comms', 'order': _seq(),
     'schema': {'type': 'string'}})

ev_status = create_local_event('Status',
    {'title': 'Status', 'group': 'Status', 'order': 9999,
     'schema': {'type': 'object', 'title': 'Status', 'properties': {
         'level': {'type': 'integer', 'title': 'Level', 'order': 1},
         'message': {'type': 'string', 'title': 'Message', 'order': 2}}}})

def _status(level, message):
    ev_status.emitIfDifferent({'level': level, 'message': message})

# --- Comms (TCP) -----------------------------------------------------------

def tcp_connected():
    _connected[0] = True
    _secsSinceRx[0] = 0
    ev_connected.emit(True)
    console.info('Connected')
    _status(0, 'Connected')
    poll()  # immediate resync of feedback

def tcp_disconnected():
    _connected[0] = False
    ev_connected.emit(False)
    console.warn('Disconnected')
    _status(2, 'Disconnected')

def tcp_received(data):
    _secsSinceRx[0] = 0
    line = (data or '').strip()
    if line == '':
        return
    console.info('RX: %s' % line)
    ev_rawReceived.emit(line)
    parse(line)

def tcp_sent(data):
    pass  # uncomment for verbose TX logging: console.info('TX: %s' % data)

tcp = TCP(connected=tcp_connected,
          disconnected=tcp_disconnected,
          received=tcp_received,
          sent=tcp_sent,
          sendDelimiters='\r',        # device wants a CR after each command
          receiveDelimiters='\r\n')   # split replies on CR or LF

def send(cmd):
    console.info('TX: %s' % cmd)
    tcp.send(cmd)

# --- Response parsing ------------------------------------------------------
# Tolerant of spacing / case. Adjust here if the real echo format differs.

_reRoute = re.compile(r'OUT\s*([0-9])\s*VS\s*IN\s*([0-9])', re.IGNORECASE)
_reStream = re.compile(r'OUT\s*([0-9])\s*STREAM\s*(ON|OFF)', re.IGNORECASE)
_reExaBtv = re.compile(r'EXA\s*BTV\s*OUT\s*([0-9])', re.IGNORECASE)
_reExaEn = re.compile(r'EXA\s*(ENABLE|DISABLE|EN|DIS|ON|OFF)', re.IGNORECASE)

def parse(line):
    m = _reRoute.search(line)
    if m:
        out, inp = int(m.group(1)), int(m.group(2))
        ev = ev_outInput.get(out)
        if ev is not None:
            ev.emitIfDifferent(inp)

    m = _reStream.search(line)
    if m:
        out = int(m.group(1))
        on = m.group(2).upper() == 'ON'
        ev = ev_outStream.get(out)
        if ev is not None:
            ev.emitIfDifferent(on)

    # Extracted audio: which output it follows ("EXA BTV OUTn") ...
    m = _reExaBtv.search(line)
    if m:
        ev_exaFollow.emitIfDifferent(int(m.group(1)))

    # ... and enabled/muted state. Guard against the "BTV" line, which has no
    # EN/DIS token, matching here by accident.
    if _reExaBtv.search(line) is None:
        m = _reExaEn.search(line)
        if m:
            ev_exaEnabled.emitIfDifferent(m.group(1).upper() in ('ENABLE', 'EN', 'ON'))

# --- Helpers ---------------------------------------------------------------

def _truthy(arg):
    if isinstance(arg, bool):
        return arg
    return str(arg).strip().lower() in ('1', 'on', 'true', 'yes', 'enable', 'enabled')

# --- Actions: routing ------------------------------------------------------

def _route(out, inp):
    send('SET OUT%s VS IN%s' % (out, inp))

# Discrete "Output x Input y" buttons (handy for direct binding / crosspoints)
def _make_crosspoint(out, inp):
    def handler(arg=None):
        _route(out, inp)
    create_local_action('Output %s Input %s' % (out, inp), handler,
        {'title': 'Input %s' % inp, 'group': 'Output %s' % out, 'order': _seq(),
         'caption': 'Route output %s to input %s' % (out, inp)})

for _out in OUTPUTS:
    for _inp in INPUTS:
        _make_crosspoint(_out, _inp)

# Parametric selector per output (choose input 1-4 via the action argument)
def _make_route_selector(out):
    def handler(arg):
        _route(out, int(arg))
    create_local_action('Route Output %s' % out, handler,
        {'title': 'Route', 'group': 'Output %s' % out, 'order': _seq(),
         'schema': {'type': 'integer', 'title': 'Input (1-4)'}})

for _out in OUTPUTS:
    _make_route_selector(_out)

# Route BOTH outputs to one input (OUT0 = ALL)
def route_both(arg):
    send('SET OUT0 VS IN%s' % int(arg))
create_local_action('Route Both Outputs', route_both,
    {'title': 'Route both', 'group': 'System', 'order': _seq(),
     'schema': {'type': 'integer', 'title': 'Input (1-4)'}})

# --- Actions: stream (power) & audio ---------------------------------------

def _make_stream_action(out):
    def handler(arg):
        send('SET OUT%s STREAM %s' % (out, 'ON' if _truthy(arg) else 'OFF'))
    create_local_action('Output %s Stream' % out, handler,
        {'title': 'Stream on/off', 'group': 'Output %s' % out, 'order': _seq(),
         'schema': {'type': 'boolean'}})

def _make_mute_action(out):
    def handler(arg):
        send('SET OUT%s HA MUTE %s' % (out, 'ON' if _truthy(arg) else 'OFF'))
    create_local_action('Output %s Audio Mute' % out, handler,
        {'title': 'Audio mute', 'group': 'Output %s' % out, 'order': _seq(),
         'schema': {'type': 'boolean'}})

for _out in OUTPUTS:
    _make_stream_action(_out)
    _make_mute_action(_out)

# Output-1 scaler (only output 1 has a scaler on this model)
def scaler(arg):
    s = str(arg).strip().lower()
    if s in ('2', '4k->2k', '4k-2k', 'downscale', 'scale'):
        send('SET OUT1 VIDEO2')   # 4K -> 2K
    else:
        send('SET OUT1 VIDEO1')   # bypass
create_local_action('Output 1 Scaler', scaler,
    {'title': 'Scaler', 'group': 'Output 1', 'order': _seq(),
     'schema': {'type': 'string', 'enum': ['BYPASS', '4K->2K']}})

# --- Actions: extracted audio (de-embedded analog 2CH + Toslink) -----------
# The analog 2CH jack and the Toslink port share ONE extracted-audio bus that
# follows a single HDMI output at a time. "Follow" picks which output's audio
# it carries; the audio then tracks whatever input is routed to that output.

def exa_follow(arg):
    out = int(arg)
    if out not in OUTPUTS:
        console.warn('Extracted audio follow: output must be 1 or 2 (got %r)' % (arg,))
        return
    send('SET EXA BTV OUT%s' % out)   # bind extracted audio to output x
create_local_action('Extracted Audio Follow', exa_follow,
    {'title': 'Follow output', 'group': 'Audio', 'order': _seq(),
     'schema': {'type': 'integer', 'title': 'Output (1 or 2)', 'enum': [1, 2]}})

def exa_enable(arg):
    send('SET OUT0 EXA %s' % ('EN' if _truthy(arg) else 'DIS'))  # 0 = ALL
create_local_action('Extracted Audio Enable', exa_enable,
    {'title': 'Enable (unmute)', 'group': 'Audio', 'order': _seq(),
     'schema': {'type': 'boolean'}})

# --- Actions: system -------------------------------------------------------

def auto_switching(arg):
    send('SET HD0 AUTO %s' % ('EN' if _truthy(arg) else 'DIS'))  # HD0 = both outputs
create_local_action('Auto Switching', auto_switching,
    {'title': 'Auto switching (both outputs)', 'group': 'System', 'order': _seq(),
     'schema': {'type': 'boolean'}})

def action_poll(arg=None):
    poll()
create_local_action('Poll', action_poll,
    {'title': 'Poll now', 'group': 'System', 'order': _seq()})

# Raw command passthrough (EDID, address, network config, etc.)
def action_send(arg):
    send(str(arg))
create_local_action('Send', action_send,
    {'title': 'Send raw command', 'group': 'Comms', 'order': _seq(),
     'schema': {'type': 'string', 'hint': 'e.g. GET STA'}})

# --- Polling & health ------------------------------------------------------

def poll():
    if not _connected[0]:
        return
    send('GET OUT0 VS')       # all routes
    send('GET OUT0 STREAM')   # all stream states
    send('GET EXA BTV OUT')   # which output the extracted audio follows
    send('GET OUT0 EXA')      # extracted audio enabled/muted

def status_check():
    if not _connected[0]:
        _status(2, 'Not connected')
        return
    _secsSinceRx[0] += STATUS_INTERVAL
    if _secsSinceRx[0] > OFFLINE_AFTER:
        _status(1, 'No response for %ss' % _secsSinceRx[0])
    else:
        _status(0, 'OK')

timer_poll = Timer(poll, POLL_INTERVAL, 5)
timer_status = Timer(status_check, STATUS_INTERVAL, STATUS_INTERVAL)

# --- Startup ---------------------------------------------------------------

def main():
    ip = (param_ipAddress or '').strip()
    if ip == '':
        console.warn('No IP address configured -- set the "IP address" parameter.')
        _status(2, 'No IP address configured')
        return
    port = param_port or DEFAULT_PORT
    console.info('Connecting to %s:%s' % (ip, port))
    _status(1, 'Connecting to %s:%s' % (ip, port))
    tcp.setDest('%s:%s' % (ip, port))
