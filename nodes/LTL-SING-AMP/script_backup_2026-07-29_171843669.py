'''
**Blaze** PowerZone Connect 1002 and similar amplifiers

`rev 5.2603`

**Resources:** [Blaze Open API for Installer PDF](https://images.salsify.com/image/upload/s--C3GIZKp0--/toqrdtrvqdfxciglxvjm.pdf)

Work-in-progress: feel free to update recipe with extra registers of interest

_revision history_

* _r5 added Zones C & D; added ZONE-{ZID}.PRIMARY_SRC (read) and ZONE-{ZID}.PRIORITY_SRC (set) for all 4 zones; added friendly SetSource actions with named-source enum_
* _r4 JP bugfix, and updated resource link_
* _r3 JP included Zone 2_
* _r2 JP Drops connection on subscription silence_
* _r1 JP created_

'''

param_ipAddress = Parameter({ "title": "IP address", "schema": { "type": "string", "hint": "(overrides bindings)" }})

DEFAULT_PORT = 7621
param_port = Parameter({ "schema": { "type": "integer", "hint": "(default %s)" % DEFAULT_PORT }})

local_event_IPAddress = LocalEvent({ "schema": { "type": "string" }})

def remote_event_IPAddress(arg):
  if is_blank(param_ipAddress):
    prev = local_event_IPAddress.getArg()
    if prev != arg:
      console.info("IP address updated to %s (previously %s)" % (arg, prev))
      local_event_IPAddress.emit(arg)
      dest = "%s:%s" % (arg, param_port or DEFAULT_PORT)
      console.info("Will connect to %s..." % dest)
      _tcp.setDest(dest)
      _tcp.drop()

# -->

# Source value mapping (per Blaze Open API)
# These are the standard {SID} source integer values sent to ZONE-{ZID}.PRIORITY_SRC
# and ROUT-{RID}.SRC registers.
#
#   0    = Off (no source)
#   100  = Analogue Input 1
#   101  = Analogue Input 2
#   500  = Mix channel 0
#   501  = Mix channel 1
#   502  = Mix channel 2
#   503  = Mix channel 3
#
# Adjust SOURCE_NAMES to match the inputs configured in your amplifier.
SOURCE_NAMES = {
  0:   "Off",
  100: "Analogue-1",
  101: "Analogue-2",
  500: "Mix-0",
  501: "Mix-1",
  502: "Mix-2",
  503: "Mix-3",
}

def main():
  if is_blank(param_ipAddress):
    # try last dynamic address
    ipAddr = local_event_IPAddress.getArg()
    console.info("Last dynamic IP address used: %s" % ipAddr)
    
  else:
    ipAddr = param_ipAddress
    console.info("Fixed config IP address target: %s" % ipAddr)
  
  if not is_blank(ipAddr):
    local_event_IPAddress.emitIfDifferent(ipAddr)
    port = param_port or DEFAULT_PORT
    console.info("Will connect to port %s" % port)
    _tcp.setDest("%s:%s" % (ipAddr, port))
    
  tryInitStringRegister("SYSTEM.DEVICE.VENDOR_NAME", "SYSTEM.DEVICE")
  tryInitStringRegister("SYSTEM.DEVICE.MODEL_NAME", "SYSTEM.DEVICE")
  tryInitStringRegister("SYSTEM.DEVICE.SERIAL", "SYSTEM.DEVICE")
  tryInitStringRegister("SYSTEM.DEVICE.FIRMWARE_DATE", "SYSTEM.DEVICE")
  tryInitStringRegister("SYSTEM.DEVICE.FIRMWARE", "SYSTEM.DEVICE")

  # --- Zone registers ---
  # ZONE-{ZID}.PRIMARY_SRC  : integer, read-only  -- reflects the currently playing source
  #                            (may differ from PRIORITY_SRC if ducking/priority logic is active)
  # ZONE-{ZID}.PRIORITY_SRC : integer, read/write -- sets the priority (override) source for the zone
  #
  # Source integer values: 0=Off, 100=Analogue-1, 101=Analogue-2, 200=Mix-0, 201=Mix-1
  # A friendly SetSource action (enum dropdown) is also created for each zone -- see initZoneSourceControl()

  for zone in ["A", "B", "C", "D"]:
    group = "ZONE-%s" % zone
    tryInitFloatRegister("ZONE-%s.GAIN" % zone, group, withSetter=True)
    tryInitBoolRegister("ZONE-%s.MUTE" % zone, group, withSetter=True)
    tryInitFloatRegister("ZONE-%s.DYN.SIGNAL" % zone, group)
    tryInitIntegerRegister("ZONE-%s.PRIMARY_SRC" % zone, group, withSetter=True)
    tryInitIntegerRegister("ZONE-%s.PRIORITY_SRC" % zone, group, withSetter=False)
    initZoneSourceControl(zone, group)

  # SPDIF
  tryInitFloatRegister("ROUT-200.GAIN", "ROUT-20X -- SPDIF", withSetter=True) 
  tryInitFloatRegister("ROUT-200.DYN.SIGNAL", "ROUT-20X -- SPDIF")
  tryInitFloatRegister("ROUT-201.GAIN", "ROUT-20X -- SPDIF", withSetter=True)
  tryInitFloatRegister("ROUT-201.DYN.SIGNAL", "ROUT-20X -- SPDIF")
  tryInitIntegerRegister("ROUT-200.SRC", "ROUT-20X -- SPDIF", withSetter=True)
  tryInitIntegerRegister("ROUT-201.SRC", "ROUT-20X -- SPDIF", withSetter=True)
  
  # SOURCE OFF:                SOURCE ANALOGUE-1:
  # SET ROUT-200.SRC 0         SET ROUT-200.SRC 100


def initZoneSourceControl(zone, group):
  '''
  Creates a friendly "ZONE-{zone}.SetSource" action with an enum (dropdown) schema
  so Nodel presents named source options rather than raw integers.

  Selecting a source sends:  SET ZONE-{zone}.PRIMARY_SRC <value>

  Extend SOURCE_NAMES at the top of this recipe to add/rename sources.
  '''
  action_name = "ZONE-%s.SetSource" % zone

  a = lookup_local_action(action_name)
  if a is not None:
    return

  # Build an enum list from SOURCE_NAMES: [{"title": "Off", "const": 0}, ...]
  enum_list = [ {"title": label, "const": val} for val, label in sorted(SOURCE_NAMES.items()) ]

  def set_source(value, _zone=zone):
    _tcp.send("SET ZONE-%s.PRIMARY_SRC %s" % (_zone, int(value)))

  create_local_action(
    action_name,
    set_source,
    {
      "title": "Set Source",
      "group": group,
      "order": next_seq(),
      "schema": {
        "type": "integer",
        "enum": [item["const"] for item in enum_list],
        "enumTitles": [item["title"] for item in enum_list]
      }
    }
  )

    
_handlers_byPrefix = { } # e.g. { "ZONE-A.GAIN": fn,     # *SET ZONE-A.GAIN -11.2   from setting / getting
                         #        "VC-3.VALUE": fn,      # +VC-3.VALUE 100.0        from getters / subscriptions
  
_initial_getters = [] # strings to send on TCP connect

def tryInitBoolRegister(name, group, withSetter=False): # e.g. ZONE-A.MUTE 0
  e = lookup_local_event(name)
  if e is not None:
    return
  
  e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "boolean" }})
  
  _initial_getters.append("GET %s" % name) # this needs to be sent on the first connection
  
  def value_handler(arg):
    e.emit(arg == "1")
    
  _handlers_byPrefix[name] = value_handler
  
  if withSetter:
    def setter(value):
      _tcp.send("SET %s %s" % (name, "1" if value else "0")) # SET ZONE-A.GAIN -11.2

    a = create_local_action(name, setter, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "boolean" }})
    
def tryInitIntegerRegister(name, group, withSetter=False): # e.g. 
  e = lookup_local_event(name)
  if e is not None:
    return
  
  e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "integer" }})
  
  _initial_getters.append("GET %s" % name) # this needs to be sent on the first connection
  
  def value_handler(arg):
    e.emit(int(arg))
    
  _handlers_byPrefix[name] = value_handler
  
  if withSetter:
    def setter(value):
      _tcp.send("SET %s %s" % (name, value)) # SET ZONE-A.GAIN -11.2

    a = create_local_action(name, setter, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "integer" }})    
    
def tryInitFloatRegister(name, group, withSetter=False): # name could be "ZONE-A.GAIN"
  e = lookup_local_event(name)
  if e is not None:
    return
  
  e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "number" }})
  
  _initial_getters.append("GET %s" % name) # this needs to be sent on the first connection
  
  def value_handler(arg):
    e.emit(float(arg))
    
  _handlers_byPrefix[name] = value_handler
  
  if withSetter:
    def setter(value):
      _tcp.send("SET %s %s" % (name, value)) # SET ZONE-A.GAIN -11.2

    a = create_local_action(name, setter, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "number" }})
    
def tryInitStringRegister(name, group, withSetter=False): # name could be "ZONE-A.GAIN"
  e = lookup_local_event(name)
  
  if e is not None:
    return
  
  e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "string" }})
  
  _initial_getters.append("GET %s" % name) # this needs to be sent on the first connection
  
  def value_handler(arg):
    e.emit(arg)
    
  _handlers_byPrefix[name] = value_handler
  
  if withSetter:
    def setter(value):
      _tcp.send("SET %s %s" % (name, value)) # SET ZONE-A.GAIN -11.2

    a = create_local_action(name, setter, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "string" }})    
    
# <!-- protocol

def parse_line(rawLine):
  global _lastReceive
  
  # e.g. From subscriptions:
  #      +VC-3.VALUE 100.0   or
  #      +IN-100.DYN.SIGNAL -12.71
  #      +IN-100.DYN.CLIP 0
  #      +VC-3.VALUE 100.0
  #      +SYSTEM.DEVICE.FIRMWARE_DATE "Nov  7 2024 11:17:58"
  #
  #      From settings
  #      > SET ZONE-A.GAIN -11.2
  #      < *SET ZONE-A.GAIN -11.2
  if rawLine.startswith("+"):
    # subscriptions /  getters
    line = rawLine
    parts = line.split(" ") # e.g. ["+VC-3.VALUE", "100.0"]
    name = parts[0][1:].strip() # drop "+", will be "VC-3.VALUE"    
    
  elif rawLine.startswith("*SET "):
    # from a set command, e.g. *SET ROUT-200.GAIN -7
    line = rawLine[5:] # drop the *SET
    parts = line.split(" ") # e.g. [ "ROUT-200.GAIN", "-7"]
    name = parts[0]
    
  else:
    return
  
  if line:
    # check if enclosed in quotes
    if line.endswith('"'):
      firstQuotePos = line.find('"')
      value = line[firstQuotePos+1:-1].strip() # strip first and last quotes
    else:
      value = parts[1].strip()
    
    handler = _handlers_byPrefix.get(name)
    if handler is not None:
      _lastReceive = system_clock()
      
      handler(value)
      return
    
    # uncomment this dynamically create any subscription data which comes through
    # ideally this is properly opted-into with value conversions, etc. but for
    # debugging purposes this could be useful although WARNING it can generate a LOT OF ACTIVITY
    #
    # e = lookup_local_event(name)
    # if e is None:
    #   parts = name.split(".") # e.g. [ "VC-3", "VALUE" ]
    #   group = parts[0] if len(parts) > 0 else None
    #   e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "string" }})
    #   
    # e.emit(value)

# -->


def tcp_connected():
  console.info("TCP connected")
  
  for line in _initial_getters:
    _tcp.send(line)
  
  _tcp.send("SUBSCRIBE * 2")

def tcp_disconnected():
  console.warn("TCP connected")

def tcp_timeout():
  log(0, 'tcp_timeout - will drop if connected')
  _tcp.drop()

def tcp_sent(data):
  log(1, "tcp_sent [%s]" % data)

def tcp_received(data):
  log(2, "tcp_received [%s]" % data)
  parse_line(data)

_tcp = TCP(connected=tcp_connected, 
          disconnected=tcp_disconnected, 
          sent=tcp_sent,
          received=tcp_received,
          timeout=tcp_timeout, 
          sendDelimiters='\n', 
          receiveDelimiters='\n')

# <!-- logging

local_event_LogLevel = LocalEvent({ "group": "Debug", "order": 10000+next_seq(), "desc": "Use this to ramp up the logging (with indentation)", "schema": { "type": "integer" }})

def warn(level, msg):
  if (local_event_LogLevel.getArg() or 0) >= level:
    console.warn(('  ' * level) + msg)

def log(level, msg):
  if (local_event_LogLevel.getArg() or 0) >= level:
    console.log(('  ' * level) + msg)

# --!>

# <status and error reporting ---

# for comms drop-out
_lastReceive = system_clock()

# roughly, the last contact  
local_event_LastContactDetect = LocalEvent({ "group": "Status", "order": 99999+next_seq(), "schema": { "type": "string" }})

# node status
local_event_Status = LocalEvent({ "group": "Status", "order": 99999+next_seq(), "schema": { "type": "object", "properties": {
        "level": { "type": "integer", "order": 1 },
        "message": { 'type': "string", "order": 2 }}}})
  
def statusCheck():
  diff = (system_clock() - _lastReceive)/1000.0 # (in secs)
  now = date_now()
  
  if diff > (status_check_interval*2):
    previousContactValue = local_event_LastContactDetect.getArg()
    
    if previousContactValue == None:
      message = 'Always been missing'
      
    else:
      previousContact = date_parse(previousContactValue)
      message = 'Missing %s' % formatPeriod(previousContact)
      
    local_event_Status.emit({'level': 2, 'message': message})
    
  else:
    # update contact info
    local_event_LastContactDetect.emit(str(now))
    local_event_Status.emit({'level': 0, 'message': 'OK'})
    
status_check_interval = 75
status_timer = Timer(statusCheck, status_check_interval)

def formatPeriod(dateObj):
  if dateObj == None:       return 'for unknown period'
  
  now = date_now()
  diff = (now.getMillis() - dateObj.getMillis()) / 1000 / 60 # in mins
  
  if diff == 0:             return 'for <1 min'
  elif diff < 60:           return 'for <%s mins' % diff
  elif diff < 60*24:        return 'since %s' % dateObj.toString('h:mm:ss a')
  else:                     return 'since %s' % dateObj.toString('E d-MMM h:mm a')

# --->
