'''
**Scheduled media playback adapter**

Lets `LTL-LIB-SCHEDULER` drive the mpv playlists ("presets") that operators build in
the **Media Player** tab of `xxDashboard`.

The scheduler only *emits named local events* - it declares no remote actions, so it
cannot call another node itself. This node is the missing half: it consumes those
events and calls the matching action on the mpv engine, passing the playlist name
straight through from the schedule's **Event argument** field.

Bind as follows:

  | this node                   | bind to                              |
  |-----------------------------|--------------------------------------|
  | `PlayPlaylist` (event)      | `LTL-LIB-SCHEDULER` . `PlayPlaylist` |
  | `StopPlayback` (event)      | `LTL-LIB-SCHEDULER` . `StopPlayback` |
  | `Presets` (event)           | `xxMPV-eng` . `Presets`              |
  | `PlayPreset` (action)       | `xxMPV-eng` . `PlayPreset`           |
  | `Stop` (action)             | `xxMPV-eng` . `Stop`                 |

Adding a playlist needs no change here: save it in the Media Player tab, then type
its name into the schedule's *Event argument*. **The name is the whole contract** -
rename a playlist without updating the schedule and the schedule fires into nothing
(the engine logs `no such preset` and carries on playing whatever it was). To make
that visible rather than silent, this node keeps the engine's playlist list and
reports a mismatch on `Last error`. It still forwards the request - the engine, not
this node, is the authority on what exists, and a bound event value can be stale.
'''

### Calls onto the mpv engine

remote_action_PlayPreset = RemoteAction({'title': 'Play preset', 'group': 'Media engine',
                                         'order': next_seq(), 'schema': {'type': 'string'}})

remote_action_Stop = RemoteAction({'title': 'Stop', 'group': 'Media engine', 'order': next_seq()})


### Status

local_event_KnownPlaylists = LocalEvent({'title': 'Known playlists', 'group': 'Status', 'order': next_seq(),
                                         'desc': 'What the engine last reported it has saved.',
                                         'schema': {'type': 'array', 'items': {'type': 'string'}}})

local_event_LastRequest = LocalEvent({'title': 'Last request', 'group': 'Status', 'order': next_seq(),
                                      'schema': {'type': 'string'}})

local_event_LastError = LocalEvent({'title': 'Last error', 'group': 'Status', 'order': next_seq(),
                                    'schema': {'type': 'string'}})


# names the engine says it has; empty until its Presets event arrives
knownPlaylists = list()


def fail(message):
  console.warn(message)
  local_event_LastError.emit(message)
  local_event_LastError.persistNow()


def doPlay(arg):
  name = str(arg).strip() if arg is not None else ''

  if len(name) == 0:
    fail('Play playlist was triggered with no playlist name - put the name in the '
         'schedule\'s "Event argument" field.')
    return

  # advisory only: never block on a list that may predate the engine's last save
  if len(knownPlaylists) > 0 and name not in knownPlaylists:
    fail('Playlist "%s" is not one the engine reported (%s) - was it renamed? Sending anyway.'
         % (name, ', '.join(knownPlaylists)))

  console.info('Play playlist "%s"' % name)
  local_event_LastRequest.emit('Play "%s"' % name)
  remote_action_PlayPreset.call(name)


def doStop():
  console.info('Stop playback')
  local_event_LastRequest.emit('Stop')
  remote_action_Stop.call()


### Schedule triggers

def remote_event_PlayPlaylist(arg):
  '''{"title": "Play playlist (scheduled)", "group": "Schedule triggers", "desc": "Argument is the playlist name."}'''
  doPlay(arg)


def remote_event_StopPlayback(arg):
  '''{"title": "Stop playback (scheduled)", "group": "Schedule triggers"}'''
  doStop()


### Engine feedback

def remote_event_Presets(arg):
  '''{"title": "Saved playlists (from the engine)", "group": "Media engine"}'''
  del knownPlaylists[:]

  if arg is not None:
    for preset in arg:
      name = preset.get('name') if hasattr(preset, 'get') else preset
      if name:
        knownPlaylists.append(str(name))

  local_event_KnownPlaylists.emit(list(knownPlaylists))


### Manual test controls (same path the schedules take)

@local_action({'title': 'Play playlist now', 'group': 'Manual test', 'order': next_seq(),
               'desc': 'Runs the scheduled-play path immediately, for testing.',
               'schema': {'type': 'string', 'hint': 'playlist name'}})
def PlayPlaylistNow(arg=None):
  doPlay(arg)


@local_action({'title': 'Stop playback now', 'group': 'Manual test', 'order': next_seq()})
def StopPlaybackNow(arg=None):
  doStop()


def main():
  console.info('Scheduled media playback adapter starting.')


@after_main
def setup():
  local_event_LastRequest.emitIfDifferent(local_event_LastRequest.getArg() or 'Nothing requested yet.')
  local_event_LastError.emitIfDifferent(local_event_LastError.getArg() or 'No errors recorded.')
