'''
**Blaze** PowerZone Connect 1002 and similar amplifiers

`rev 7`

**Resources:** Blaze/Sonance "Open API for Installers", edition 2026.24.1 (June 2026)

Work-in-progress: feel free to update recipe with extra registers of interest

_revision history_

* _r7 added OUT-{1..4} registers and a second synthesised MASTER OUTPUT group (drives OUT-{OID}.GAIN / .MUTE, i.e. the power-amp outputs) -- the two master groups now share one implementation; corrected the zone master's default gain ceiling to 0 dB per API 5.177_
* _r6 added synthesised MASTER group -- Master Volume / Master Mute fan out one absolute value to every zone named in the "Master zones" parameter_
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

# <!-- master volume

# The amplifier has no master register of its own -- these groups are synthesised by the
# recipe. A master move writes the SAME ABSOLUTE gain to every member named in its
# parameter, so per-member trim differences are NOT preserved. If the members need to keep
# a relative balance, drive them individually instead.
#
# Two masters are provided because they sit at different points in the signal chain:
#
#   source --> ZONE-{ZID}.GAIN --> OUT-{OID}.GAIN --> speaker terminals
#
#   MASTER         drives ZONE-{A..D}.GAIN, i.e. the zone (source-side) level
#                  API range -80..0 dB (API 2026.24.1 5.177)
#   MASTER OUTPUT  drives OUT-{1..4}.GAIN, i.e. the power-amp outputs
#                  API range -30..+15 dB (API 2026.24.1 5.41)
#
# Attenuating at the zone pulls the level down ahead of the zone's own processing (ducking,
# priority, compressor); attenuating at the output pulls down everything feeding that
# speaker line regardless of which zone or route got it there. Use MASTER OUTPUT when you
# want a true "turn the room down" that the source-side logic cannot work around.
#
# Feedback comes from the amplifier's own echo (*SET ... / +...), not from the action, so a
# fader only moves once the device has actually accepted the change.

GAIN_EPSILON = 0.05 # dB; members within this of each other count as "in sync"

class MasterControl:
  '''
  One synthesised master fader + mute over a set of registers sharing a naming pattern,
  e.g. "ZONE-%s.GAIN" over [ "A", "B", "C", "D" ] or "OUT-%s.GAIN" over [ "1" .. "4" ].

  The member events must already exist (see main()) before init() is called -- a member
  naming a register this amplifier does not have is dropped with a warning.

  Its own feedback events are held BY NAME and resolved in init(), not passed in as
  objects: at the time these instances are constructed the module-level local_event_*
  globals are still the declarations, and the toolkit only swaps in the live event objects
  once the script has finished loading.
  '''

  def __init__(self, name, memberNoun, paramTitle, gainPattern, mutePattern,
               volumeEventName, muteEventName, inSyncEventName, defaultMin, defaultMax):
    self.name = name               # console prefix, e.g. "Master Volume"
    self.memberNoun = memberNoun   # "zone" / "output", used in console messages
    self.paramTitle = paramTitle   # the parameter naming the members, e.g. "Master zones"
    self.gainPattern = gainPattern
    self.mutePattern = mutePattern
    self.volumeEventName = volumeEventName
    self.muteEventName = muteEventName
    self.inSyncEventName = inSyncEventName
    self.volumeEvent = None        # resolved in init()
    self.muteEvent = None
    self.inSyncEvent = None
    self.defaultMin = defaultMin
    self.defaultMax = defaultMax
    self.minGain = defaultMin
    self.maxGain = defaultMax
    self.members = []              # resolved in init(), e.g. [ "A", "B", "C", "D" ]

  def setVolume(self, arg):
    if arg == None or arg == "":
      console.warn("%s: no value given, ignoring" % self.name)
      return

    if len(self.members) == 0:
      console.warn("%s: no %ss are being managed, ignoring (see '%s' parameter)" % (self.name, self.memberNoun, self.paramTitle))
      return

    value = float(arg)
    clamped = min(max(value, self.minGain), self.maxGain)

    if clamped != value:
      console.warn("%s: %s dB is outside %s..%s dB, using %s dB" % (self.name, value, self.minGain, self.maxGain, clamped))

    log(1, "%s %s dB -> %s(s) %s" % (self.name, clamped, self.memberNoun, ", ".join(self.members)))

    for member in self.members:
      _tcp.send("SET %s %s" % (self.gainPattern % member, clamped))

  def setMute(self, arg):
    if len(self.members) == 0:
      console.warn("%s: no %ss are being managed, ignoring (see '%s' parameter)" % (self.name, self.memberNoun, self.paramTitle))
      return

    # tolerate the string forms a dashboard switch or scheduler might send
    on = arg in (True, 1, "1", "true", "True", "on", "On", "Mute", "Muted")

    log(1, "%s %s -> %s(s) %s" % (self.name, on, self.memberNoun, ", ".join(self.members)))

    for member in self.members:
      _tcp.send("SET %s %s" % (self.mutePattern % member, "1" if on else "0"))

  def recompute(self, ignoredArg=None):
    'Derives the master feedback from whatever the managed members last reported.'

    gains = []
    mutes = []

    for member in self.members:
      gainEvent = lookup_local_event(self.gainPattern % member)
      if gainEvent != None and gainEvent.getArg() != None:
        gains.append(float(gainEvent.getArg()))

      muteEvent = lookup_local_event(self.mutePattern % member)
      if muteEvent != None and muteEvent.getArg() != None:
        mutes.append(muteEvent.getArg() == True)

    # a member that has never reported is UNKNOWN, not agreeing -- claiming "in sync" or
    # "muted" on partial information could leave a channel audibly live while the dashboard
    # says otherwise, so those two only publish once every member has been heard from
    allGainsKnown = len(gains) == len(self.members)
    allMutesKnown = len(mutes) == len(self.members)

    if len(gains) > 0:
      # report the loudest member so the fader still shows a meaningful (and safe-side)
      # position while the initial GETs are still trickling in
      self.volumeEvent.emitIfDifferent(max(gains))

    if allGainsKnown:
      self.inSyncEvent.emitIfDifferent((max(gains) - min(gains)) <= GAIN_EPSILON)

    if allMutesKnown:
      self.muteEvent.emitIfDifferent(not (False in mutes))

  def init(self, spec, minGain, maxGain):
    self.volumeEvent = lookup_local_event(self.volumeEventName)
    self.muteEvent = lookup_local_event(self.muteEventName)
    self.inSyncEvent = lookup_local_event(self.inSyncEventName)

    members = []

    for part in spec.split(","):
      member = part.strip().upper()

      if member == "":
        continue

      if lookup_local_event(self.gainPattern % member) == None:
        console.warn("%s: '%s' is not a %s on this amplifier, ignoring" % (self.paramTitle, member, self.memberNoun))
        continue

      members.append(member)

    self.members = members

    if minGain != None:
      self.minGain = float(minGain)

    if maxGain != None:
      self.maxGain = float(maxGain)

    if self.minGain > self.maxGain:
      console.warn("%s: gain limits are inverted (%s > %s), reverting to defaults" % (self.name, self.minGain, self.maxGain))
      self.minGain = self.defaultMin
      self.maxGain = self.defaultMax

    if len(self.members) == 0:
      console.warn("%s manages no %ss -- check the '%s' parameter" % (self.name, self.memberNoun, self.paramTitle))
      return

    console.info("%s drives %s(s) %s, limited to %s..%s dB" % (self.name, self.memberNoun, ", ".join(self.members), self.minGain, self.maxGain))

    # a plain function, not the bound method -- Jython will not coerce a bound method into
    # the Handler interface addEmitHandler expects
    def onMemberChanged(arg):
      self.recompute(arg)

    for member in self.members:
      lookup_local_event(self.gainPattern % member).addEmitHandler(onMemberChanged)
      lookup_local_event(self.mutePattern % member).addEmitHandler(onMemberChanged)

    self.recompute()

# <!-- master over the zones (source side)

MASTER_GROUP = "MASTER"

DEFAULT_MASTER_ZONES = "A, B, C, D"

param_masterZones = Parameter({ "title": "Master zones", "order": next_seq(),
                                "schema": { "type": "string", "hint": "(comma separated, default '%s')" % DEFAULT_MASTER_ZONES }})

# Guard rails for the fader. ZONE-{ZID}.GAIN accepts -80..0 dB (API 2026.24.1 5.177), and
# the per-zone ZONE-{ZID}.GAIN_MIN / GAIN_MAX registers may narrow that further on the
# device, so the defaults here are the widest the amplifier will ever accept.
DEFAULT_MASTER_MIN_GAIN = -80.0
DEFAULT_MASTER_MAX_GAIN = 0.0

param_masterMinGain = Parameter({ "title": "Master min. gain (dB)", "order": next_seq(),
                                  "schema": { "type": "number", "hint": "(default %s)" % DEFAULT_MASTER_MIN_GAIN }})

param_masterMaxGain = Parameter({ "title": "Master max. gain (dB)", "order": next_seq(),
                                  "schema": { "type": "number", "hint": "(default %s)" % DEFAULT_MASTER_MAX_GAIN }})

local_event_MasterVolume = LocalEvent({ "title": "Master Volume", "group": MASTER_GROUP, "order": next_seq(),
                                        "desc": "Master fader position (dB). When zones disagree this reports the loudest one.",
                                        "schema": { "type": "number" }})

local_event_MasterMute = LocalEvent({ "title": "Master Mute", "group": MASTER_GROUP, "order": next_seq(),
                                      "desc": "True only when every managed zone is muted",
                                      "schema": { "type": "boolean" }})

local_event_MasterZonesInSync = LocalEvent({ "title": "Master Zones In Sync", "group": MASTER_GROUP, "order": next_seq(),
                                             "desc": "False when the managed zones are not all at the same gain, e.g. after a zone was trimmed individually",
                                             "schema": { "type": "boolean" }})

_zoneMaster = MasterControl("Master Volume", "zone", "Master zones",
                            "ZONE-%s.GAIN", "ZONE-%s.MUTE",
                            "MasterVolume", "MasterMute", "MasterZonesInSync",
                            DEFAULT_MASTER_MIN_GAIN, DEFAULT_MASTER_MAX_GAIN)

_masterVolumeAction = create_local_action("Master Volume", lambda arg: _zoneMaster.setVolume(arg),
                                          { "title": "Master Volume", "group": MASTER_GROUP, "order": next_seq(),
                                            "desc": "Sets every managed zone to this absolute gain (dB)",
                                            "schema": { "type": "number" }})

_masterMuteAction = create_local_action("Master Mute", lambda arg: _zoneMaster.setMute(arg),
                                        { "title": "Master Mute", "group": MASTER_GROUP, "order": next_seq(),
                                          "desc": "Mutes / unmutes every managed zone",
                                          "schema": { "type": "boolean" }})

# -->

# <!-- master over the amplifier outputs

MASTER_OUTPUT_GROUP = "MASTER OUTPUT"

DEFAULT_MASTER_OUTPUTS = "1, 2, 3, 4"

param_masterOutputs = Parameter({ "title": "Master outputs", "order": next_seq(),
                                  "schema": { "type": "string", "hint": "(comma separated, default '%s'; 3 and 4 are 4-channel models only)" % DEFAULT_MASTER_OUTPUTS }})

# OUT-{OID}.GAIN accepts -30..+15 dB (API 2026.24.1 5.41). Note the positive ceiling: this
# stage can add gain, unlike the zone stage, so raising the maximum here makes the amplifier
# genuinely louder rather than just less attenuated.
DEFAULT_MASTER_OUTPUT_MIN_GAIN = -30.0
DEFAULT_MASTER_OUTPUT_MAX_GAIN = 15.0

param_masterOutputMinGain = Parameter({ "title": "Master output min. gain (dB)", "order": next_seq(),
                                        "schema": { "type": "number", "hint": "(default %s)" % DEFAULT_MASTER_OUTPUT_MIN_GAIN }})

param_masterOutputMaxGain = Parameter({ "title": "Master output max. gain (dB)", "order": next_seq(),
                                        "schema": { "type": "number", "hint": "(default %s)" % DEFAULT_MASTER_OUTPUT_MAX_GAIN }})

local_event_MasterOutputVolume = LocalEvent({ "title": "Master Output Volume", "group": MASTER_OUTPUT_GROUP, "order": next_seq(),
                                              "desc": "Master output fader position (dB). When outputs disagree this reports the loudest one.",
                                              "schema": { "type": "number" }})

local_event_MasterOutputMute = LocalEvent({ "title": "Master Output Mute", "group": MASTER_OUTPUT_GROUP, "order": next_seq(),
                                            "desc": "True only when every managed output is muted",
                                            "schema": { "type": "boolean" }})

local_event_MasterOutputsInSync = LocalEvent({ "title": "Master Outputs In Sync", "group": MASTER_OUTPUT_GROUP, "order": next_seq(),
                                               "desc": "False when the managed outputs are not all at the same gain, e.g. after an output was trimmed individually",
                                               "schema": { "type": "boolean" }})

_outputMaster = MasterControl("Master Output Volume", "output", "Master outputs",
                              "OUT-%s.GAIN", "OUT-%s.MUTE",
                              "MasterOutputVolume", "MasterOutputMute", "MasterOutputsInSync",
                              DEFAULT_MASTER_OUTPUT_MIN_GAIN, DEFAULT_MASTER_OUTPUT_MAX_GAIN)

_masterOutputVolumeAction = create_local_action("Master Output Volume", lambda arg: _outputMaster.setVolume(arg),
                                                { "title": "Master Output Volume", "group": MASTER_OUTPUT_GROUP, "order": next_seq(),
                                                  "desc": "Sets every managed amplifier output to this absolute gain (dB)",
                                                  "schema": { "type": "number" }})

_masterOutputMuteAction = create_local_action("Master Output Mute", lambda arg: _outputMaster.setMute(arg),
                                              { "title": "Master Output Mute", "group": MASTER_OUTPUT_GROUP, "order": next_seq(),
                                                "desc": "Mutes / unmutes every managed amplifier output",
                                                "schema": { "type": "boolean" }})

# -->

def initMasters():
  'Must run after the ZONE and OUT registers exist -- it binds onto their events.'

  _zoneMaster.init(DEFAULT_MASTER_ZONES if is_blank(param_masterZones) else param_masterZones,
                   param_masterMinGain, param_masterMaxGain)

  _outputMaster.init(DEFAULT_MASTER_OUTPUTS if is_blank(param_masterOutputs) else param_masterOutputs,
                     param_masterOutputMinGain, param_masterOutputMaxGain)

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

  # --- Output registers ---
  # OUT-{OID} is the power-amp output stage, downstream of the zone processing:
  #   source -> ZONE-{ZID}.GAIN -> OUT-{OID}.GAIN -> speaker terminals
  # {OID} is 1..4, with 3 and 4 present on 4-channel models only. GAIN spans -30..+15 dB
  # (API 2026.24.1 5.41) -- a wider and differently-centred range than the zone gain.
  #
  # OUT-{OID}.DYN.SIGNAL is subscription-only (API 2026.24.1 5.31): it arrives via the
  # SUBSCRIBE issued on connect and must NOT be GET, which the amplifier rejects.

  for out in ["1", "2", "3", "4"]:
    group = "OUT-%s" % out
    tryInitFloatRegister("OUT-%s.GAIN" % out, group, withSetter=True)
    tryInitBoolRegister("OUT-%s.MUTE" % out, group, withSetter=True)
    tryInitFloatRegister("OUT-%s.DYN.SIGNAL" % out, group, withGetter=False)

  # SPDIF
  tryInitFloatRegister("ROUT-200.GAIN", "ROUT-20X -- SPDIF", withSetter=True) 
  tryInitFloatRegister("ROUT-200.DYN.SIGNAL", "ROUT-20X -- SPDIF")
  tryInitFloatRegister("ROUT-201.GAIN", "ROUT-20X -- SPDIF", withSetter=True)
  tryInitFloatRegister("ROUT-201.DYN.SIGNAL", "ROUT-20X -- SPDIF")
  tryInitIntegerRegister("ROUT-200.SRC", "ROUT-20X -- SPDIF", withSetter=True)
  tryInitIntegerRegister("ROUT-201.SRC", "ROUT-20X -- SPDIF", withSetter=True)

  # SOURCE OFF:                SOURCE ANALOGUE-1:
  # SET ROUT-200.SRC 0         SET ROUT-200.SRC 100

  # must run last -- it binds onto the zone and output events created above
  initMasters()


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
    
def tryInitFloatRegister(name, group, withSetter=False, withGetter=True): # name could be "ZONE-A.GAIN"
  e = lookup_local_event(name)
  if e is not None:
    return

  e = create_local_event(name, { "title": name, "group": group, "order": next_seq(), "schema": { "type": "number" }})

  if withGetter: # subscription-only registers (e.g. OUT-1.DYN.SIGNAL) reject a GET
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
