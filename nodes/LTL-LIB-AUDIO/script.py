'''
**Little Library audio routing** - one operator button switches Zone A source on *both* amplifiers,
and the current mode is reported back from what the amplifiers actually say.

Replaces an earlier `Flows` configuration. The remote *action* stub names are unchanged
("Play Amp Zone A Source" / "Sing Amp Zone A Source") so existing bindings survive; two remote
*events* were added to read `ZONE-A.PRIMARY_SRC` back from each amp.

Each mode in the `Modes` parameter creates:

  - a local **action** (press it -> both amps are told to change source), and
  - a local **boolean event**, true only while *both* amps report that mode's source pair.

Exactly one mode event is true at a time; a source pair that matches no mode leaves them all
false (and `Audio Mode` reads "Unknown"). Nothing is emitted optimistically - the state comes
from the amps' own echoes, so a change made at the amplifier front panel, by the scheduler or
from another browser is reflected just the same.

Bind a dashboard button's action *and* event halves to the same mode name to get a latching
(radio-button) control.

**Power-on follow.** Bind the `Gallery Power` remote event to `LTL-LIB-GALLERY`'s `Desired Power`
and the mode named in `Recall on power-on` is recalled whenever the gallery is switched on - so
"All On" lands in a known routing state. Only `'On'` acts; there is no "off" routing state, so an
`'Off'` is deliberately ignored. Leave the parameter blank to disable the follow. This is a
one-way follow, not group membership: it does not affect any group's aggregate Power.
'''

DEFAULT_MODES = [
  {'name': 'Split Room',          'playSource': 500, 'singSource': 100},
  {'name': 'Play Audio Takeover', 'playSource': 500, 'singSource': 500},
  {'name': 'Sing Audio Takeover', 'playSource': 600, 'singSource': 100}
]

param_modes = Parameter({'title': 'Modes', 'order': 1, 'schema': {'type': 'array', 'items': {'type': 'object', 'properties': {
  'name':       {'title': 'Mode name (matches the dashboard signal)', 'type': 'string',  'order': 1},
  'playSource': {'title': 'Play amp ZONE-A.PRIMARY_SRC',              'type': 'integer', 'order': 2},
  'singSource': {'title': 'Sing amp ZONE-A.PRIMARY_SRC',              'type': 'integer', 'order': 3}
}}}})

param_recallOnPowerOn = Parameter({'title': 'Recall on power-on', 'order': 2,
  'desc': 'Mode recalled when "Gallery Power" reports On. Blank disables the follow.',
  'schema': {'type': 'string'}})

PLAY_SOURCE_ACTION = 'Play Amp Zone A Source'
SING_SOURCE_ACTION = 'Sing Amp Zone A Source'

GALLERY_POWER_EVENT = 'Gallery Power'

DEFAULT_RECALL = 'Split Room'  # used when the parameter has never been set

# last source reported by each amp; None means "has not told us yet"
_reported = {'play': None, 'sing': None}

_modes = []  # the resolved mode table


def main():
  global _modes
  _modes = param_modes or DEFAULT_MODES

  order = 0
  for mode in _modes:
    order = order + 1
    initMode(mode, order)

  create_local_event('Audio Mode', {'title': 'Audio Mode', 'group': 'Status', 'order': 100,
                                    'schema': {'type': 'string'}})

  create_remote_action(PLAY_SOURCE_ACTION, {'title': PLAY_SOURCE_ACTION, 'group': 'Amplifiers',
                                            'order': 110, 'schema': {'type': 'integer'}},
                       suggestedNode='LTL-PLAY-AMP')

  create_remote_action(SING_SOURCE_ACTION, {'title': SING_SOURCE_ACTION, 'group': 'Amplifiers',
                                            'order': 111, 'schema': {'type': 'integer'}},
                       suggestedNode='LTL-SING-AMP')

  create_remote_event('Play Amp Zone A Source Feedback', lambda arg: onSourceReported('play', arg),
                      {'title': 'Play Amp Zone A Source Feedback', 'group': 'Amplifiers',
                       'order': 120, 'schema': {'type': 'integer'}},
                      suggestedNode='LTL-PLAY-AMP')

  create_remote_event('Sing Amp Zone A Source Feedback', lambda arg: onSourceReported('sing', arg),
                      {'title': 'Sing Amp Zone A Source Feedback', 'group': 'Amplifiers',
                       'order': 121, 'schema': {'type': 'integer'}},
                      suggestedNode='LTL-SING-AMP')

  # must come after the mode loop above - it recalls one of those actions by name
  create_remote_event(GALLERY_POWER_EVENT, onGalleryPower,
                      {'title': GALLERY_POWER_EVENT, 'group': 'Gallery',
                       'order': 130, 'schema': {'type': 'string'}},
                      suggestedNode='LTL-LIB-GALLERY')

  evaluate()  # publish "nothing known yet" rather than leaving the dashboard blank

  console.info('Started with %s mode(s): %s' % (len(_modes), ', '.join([m['name'] for m in _modes])))

  recall = recallMode()
  if not recall:
    console.info('Power-on follow is disabled')
  else:
    console.info('Will recall "%s" when the gallery powers on' % recall)


def recallMode():
  '''The mode to recall on power-on. Never set -> the default; blank -> follow disabled.'''
  if param_recallOnPowerOn is None:
    return DEFAULT_RECALL

  return param_recallOnPowerOn.strip()


def onGalleryPower(arg=None):
  '''The gallery announced a power intent. Only "On" means anything here - there is no
     "off" routing state, so 'Off' (and any 'Partially ...' form) is deliberately ignored.'''
  state = asState(arg)
  if state != 'On':
    return

  recall = recallMode()
  if not recall:
    return

  mode = lookup_local_action(recall)
  if mode is None:
    return console.warn('Power-on: no mode named "%s" exists; check the "Recall on power-on" '
                        'parameter against the Modes table' % recall)

  console.info('Gallery powered on - recalling "%s"' % recall)
  mode.call(None)


def asState(value):
  '''Group members are called with a plain 'On'/'Off', but a group's "Extended" stub
     sends {'state': 'On', ...} instead - accept either, and booleans for good measure.'''
  if value is None:
    return None

  if hasattr(value, 'get'):  # dict/map-like i.e. the extended form
    value = value.get('state')

  if value is True:
    return 'On'

  if value is False:
    return 'Off'

  return str(value).strip() if value is not None else None


def initMode(mode, order):
  '''One action to recall the mode, one boolean event that is true while it is active.'''
  name = mode['name']

  def handler(arg=None, _mode=mode):  # _mode bound now, not when the lambda finally runs
    console.info('Recalling "%s" (play=%s, sing=%s)' % (_mode['name'], _mode['playSource'], _mode['singSource']))
    lookup_remote_action(PLAY_SOURCE_ACTION).call(_mode['playSource'])
    lookup_remote_action(SING_SOURCE_ACTION).call(_mode['singSource'])

  create_local_action(name, handler, {'title': name, 'group': 'Modes', 'order': order})
  create_local_event(name, {'title': name, 'group': 'Modes', 'order': order,
                            'schema': {'type': 'boolean'}})


def onSourceReported(which, arg):
  '''An amp told us what its Zone A source is (or a set was echoed back).'''
  _reported[which] = asInt(arg)
  evaluate()


def evaluate():
  '''Derive the active mode from the two reported sources and publish it.'''
  play = _reported['play']
  sing = _reported['sing']

  active = None
  for mode in _modes:
    if play == mode['playSource'] and sing == mode['singSource']:
      active = mode['name']
      break

  for mode in _modes:
    lookup_local_event(mode['name']).emitIfDifferent(mode['name'] == active)

  lookup_local_event('Audio Mode').emitIfDifferent(active or 'Unknown')


def asInt(value):
  '''Action and event arguments arrive as str, float or None depending on the sender.'''
  if value is None:
    return None
  try:
    return int(float(str(value).strip()))
  except:
    console.warn('Could not read "%s" as a source number' % value)
    return None
