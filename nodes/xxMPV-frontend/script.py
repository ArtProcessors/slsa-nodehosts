'''mpv Playlist - tab-ready Frontend node (Frontend/Mk2 idiom).

Presents the UI (content/) as a single self-contained page/tab and relays to the
mpv *engine* node via Nodel remote bindings. Like a stock Frontend/Mk2 node, the
engine is chosen with a node-picker parameter and the bindings are confirmed in
nodeConfig.json -> remoteBindingValues (pointing at the engine node), so this works
cross-host over Nodel's protocol with no HTTP/CORS.

Unlike stock Mk2 (which auto-binds every action=/event= declared in index.xml), the
mpv playlist editor is JS-driven with object-arg schemas that don't map cleanly onto
Mk2's schemas.json auto-binding, so the event/action surface is mirrored explicitly
below: for every action the UI calls it exposes a local action of the same name that
relays to the engine; for every event the UI reads it exposes a local event of the
same name that mirrors the engine's. The content is scoped to a single .mpv-root tab
(content/index.xml + custom.js) so it can be lifted into a larger gallery dashboard.

The engine node = the mpv driver (this recipe minus content/). Set the "Engine
node" parameter to its name (e.g. xxMPV-dev).
'''

# Node-picker like Mk2's "Suggested Node". Kept named 'engineNode' because
# nodeConfig.json -> paramValues + remoteBindingValues reference that name.
param_engineNode = Parameter({'title': 'Engine node', 'order': 1,
    'desc': 'Name of the mpv engine node this frontend relays to (e.g. xxMPV-dev)',
    'schema': {'type': 'string', 'format': 'node',
               'hint': 'name of the mpv engine node (e.g. xxMPV-dev)'}})

# --- schemas (match the engine so args serialise through the relay) ---
_FILE_ITEM = {'type': 'object', 'properties': {
    'name':     {'type': 'string',  'order': 1},
    'included': {'type': 'boolean', 'order': 2},
    'position': {'type': 'integer', 'order': 3}}}
_PRESET_ITEM = {'type': 'object', 'properties': {
    'name':  {'type': 'string', 'order': 1},
    'files': {'type': 'array',  'order': 2, 'items': {'type': 'string'}}}}
_NOWPLAYING = {'type': 'object', 'properties': {
    'kind':  {'type': 'string', 'order': 1},
    'name':  {'type': 'string', 'order': 2},
    'files': {'type': 'array',  'order': 3, 'items': {'type': 'string'}}}}
_LASTIPC = {'type': 'object', 'properties': {
    'command': {'type': 'string',  'order': 1},
    'code':    {'type': 'integer', 'order': 2},
    'reply':   {'type': 'string',  'order': 3}}}
_SEL_OBJ = {'type': 'object', 'properties': {
    'file':     {'type': 'string',  'order': 1},
    'included': {'type': 'boolean', 'order': 2}}}
_POS_OBJ = {'type': 'object', 'properties': {
    'file':     {'type': 'string',  'order': 1},
    'position': {'type': 'integer', 'order': 2}}}

# events to mirror (engine -> local), name: schema
EVENTS = [
    ('Files',         {'type': 'array', 'items': _FILE_ITEM}),
    ('Playlist',      {'type': 'array', 'items': {'type': 'string'}}),
    ('NowPlaying',    _NOWPLAYING),
    ('Paused',        {'type': 'boolean'}),
    ('MPVRunning',    {'type': 'boolean'}),
    ('Loop',          {'type': 'boolean'}),
    ('Presets',       {'type': 'array', 'items': _PRESET_ITEM}),
    ('EditingPreset', {'type': 'string'}),
    ('PlayReady',     {'type': 'boolean'}),
    ('LastIPC',       _LASTIPC),
]

# actions to mirror (local -> engine), name: schema (None = no argument)
ACTIONS = [
    ('Play', None), ('PauseToggle', None), ('Previous', None), ('Next', None),
    ('Stop', None), ('LoopToggle', None), ('LoopSet', {'type': 'boolean'}),
    ('PlayFile', {'type': 'string'}),
    ('SetSelection', _SEL_OBJ), ('ToggleSelection', {'type': 'string'}),
    ('SetPosition', _POS_OBJ),
    ('AutoNumber', None), ('ClearSelection', None), ('ClearOrdering', None),
    ('Rescan', None),
    ('SavePreset', {'type': 'string'}), ('DeletePreset', {'type': 'string'}),
    ('RecallPreset', {'type': 'string'}), ('PlayPreset', {'type': 'string'}),
    ('UpdatePreset', {'type': 'string'}), ('NewPlaylist', None),
]

def _mirrorEvent(name, schema):
    evt = create_local_event(name, {'group': 'Status', 'title': name, 'schema': schema})
    create_remote_event(name, lambda arg=None, e=evt: e.emit(arg),
                        suggestedNode=param_engineNode, suggestedEvent=name)

def _mirrorAction(name, schema):
    meta = {'group': 'Playback', 'title': name}
    if schema is not None:
        meta['schema'] = schema
    remote = create_remote_action(name, suggestedNode=param_engineNode, suggestedAction=name)
    create_local_action(name, lambda arg=None, r=remote: r.call(arg), meta)

def main():
    if not param_engineNode:
        console.warn('Set the "Engine node" parameter to the mpv engine node name')
        return
    for name, schema in EVENTS:
        _mirrorEvent(name, schema)
    for name, schema in ACTIONS:
        _mirrorAction(name, schema)
    console.info('mpv frontend: mirrored %d events + %d actions to engine "%s"'
                 % (len(EVENTS), len(ACTIONS), param_engineNode))
