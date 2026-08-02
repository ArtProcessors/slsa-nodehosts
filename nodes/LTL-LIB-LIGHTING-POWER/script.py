'''
**Lighting Power adapter**

Presents a standard Nodel power interface for the Dynalite lighting so it can be a
member of the **LTL-LIB-GALLERY** group.

The Group recipe requires every `Action & Signal` member to provide:

  * a `Power` **action** taking `'On'` / `'Off'`
  * a `Power` **event** emitting `'On'` / `'Off'`
  * a `Status` **event** (`{level, message}`) while _Provides Status?_ is ticked

The lighting gateway offers none of those directly: its scene recalls are
argument-less custom messages, and its feedback is a per-area preset label. This
node translates between the two, and does nothing else.

Bind the remote actions and events below to the gateway node (`xxdynet`):

  | this node       | gateway                              |
  |-----------------|--------------------------------------|
  | `Scene On`      | `GalleryOn`                          |
  | `Scene Off`     | `AllOff`                             |
  | `Scene State`   | `Area2Preset` (per-area preset feedback) |
  | `Gateway Status`| `Status`                             |

`Scene State` carries the gateway's preset *label*, so the two parameters below map
those labels onto `On` / `Off`. Relabelling a preset on the gateway means updating
the matching parameter here.
'''

DEFAULT_ON_STATES = 'Gallery On, Cleaning On'
DEFAULT_OFF_STATES = 'All Off'

POWER_SCHEMA = {'type': 'string', 'enum': ['On', 'Off']}

STATUS_SCHEMA = {'type': 'object', 'properties': {
    'level': {'type': 'integer', 'title': 'Level', 'order': 1},
    'message': {'type': 'string', 'title': 'Message', 'order': 2}}}


### Parameters

param_onStates = Parameter({'title': 'Gateway values meaning "On"', 'order': 1,
                            'desc': 'Comma-separated gateway preset labels that mean the lights are on.',
                            'schema': {'type': 'string', 'hint': DEFAULT_ON_STATES}})

param_offStates = Parameter({'title': 'Gateway values meaning "Off"', 'order': 2,
                             'desc': 'Comma-separated gateway preset labels that mean the lights are off.',
                             'schema': {'type': 'string', 'hint': DEFAULT_OFF_STATES}})

param_assumeState = Parameter({'title': 'Assume state on command', 'order': 3,
                               'desc': 'Emit the requested state as soon as the command is sent, instead of waiting for the '
                                       'gateway to report it. Only enable this if the gateway does not echo preset changes onto '
                                       'the bus -- the reported state then reflects what was asked for, not what the lights '
                                       'are actually doing, and a scene recalled from a wall panel will not show up here.',
                               'schema': {'type': 'boolean'}})

# the parsed forms of the two lists above
onStates = list()
offStates = list()


### Local signals

local_event_Power = LocalEvent({'title': 'Power', 'group': 'Power', 'order': next_seq(), 'schema': POWER_SCHEMA})

local_event_Status = LocalEvent({'title': 'Status', 'group': 'Status', 'order': next_seq(), 'schema': STATUS_SCHEMA})


### Scene recalls on the gateway

remote_action_SceneOn = RemoteAction()

remote_action_SceneOff = RemoteAction()


### Main

def main():
  del onStates[:]
  del offStates[:]

  onStates.extend(splitLabels(param_onStates or DEFAULT_ON_STATES))
  offStates.extend(splitLabels(param_offStates or DEFAULT_OFF_STATES))

  console.info('Started. "On" is %s, "Off" is %s%s'
               % (onStates, offStates, '; assuming state on command' if param_assumeState else ''))

def splitLabels(raw):
  return [label.strip() for label in str(raw).split(',') if label.strip() != '']


### Power

@local_action({'title': 'Power', 'group': 'Power', 'order': next_seq(), 'schema': POWER_SCHEMA})
def Power(arg=None):
  # the Group recipe hands a plain string to an ordinary member and a
  # {'state': ..., 'noPropagate': ...} map to a group member; tolerate either
  state = arg.get('state') if hasattr(arg, 'get') else arg
  state = str(state).strip() if state is not None else ''

  if state == 'On':
    remote_action_SceneOn.call()

  elif state == 'Off':
    remote_action_SceneOff.call()

  else:
    return console.warn('Power: ignoring unexpected argument %s' % repr(arg))

  if param_assumeState:
    local_event_Power.emit(state)


### Feedback from the gateway

def remote_event_SceneState(arg=None):
  label = str(arg).strip() if arg is not None else ''

  if label in onStates:
    local_event_Power.emit('On')

  elif label in offStates:
    local_event_Power.emit('Off')

  else:
    # an unmapped scene tells us nothing reliable, so leave Power showing its last known value
    console.warn('Scene State: "%s" is in neither the "On" nor the "Off" list; Power left unchanged' % label)

def remote_event_GatewayStatus(arg=None):
  # the lights are only controllable while the gateway is reachable, so the group
  # should see the gateway's own status
  local_event_Status.emit(arg)
