'''Run-mode add-on for the "App Launcher" node (SLSA Play node server).

Loaded alongside the stock App Launcher 'script.py' (Nodel auto-executes every non-'_'-prefixed
*.py in a node folder into the same namespace), so it adds a "Run mode" dropdown without forking
the recipe:

  * Production  -> start-server.bat  -> the deployed CircleCI build (C:\\Content\\server, node dist\\index.js)
  * Development -> start-dev.bat      -> `yarn dev` out of the slsa-play-node source checkout (ts-node + Vite UI dev server on :5174)

The recipe's main()/finishMain() always sets the App Launcher's command from `param_AppArgs`
(`/d /s /c start-server.bat`, the Production default in nodeConfig.json). This add-on overrides it
*after* main() via `_process.setCommand(...)` when Mode=Development - deliberately NOT by reassigning
`param_AppArgs`, because that would make Nodel rewrite nodeConfig.json on saveConfig (and is racy on
back-to-back reloads). So the mode lives purely in nodeConfig.json's `Mode` value; `param_AppArgs`
stays constant.

Only one mode runs at a time (this node supervises a single process). Change "Run mode" via the
Nodel UI; the node reloads and `_applyRunMode` re-picks the command; whether the server then
(re)launches is governed by `PowerStateOnStart` (or use the "Restart" action here).
'''

# <parameters ---------------------------------------------------------------

param_Mode = Parameter({'title': 'Run mode', 'group': 'Mode', 'order': next_seq(),
                        'schema': {'type': 'string', 'enum': ['Production', 'Development']},
                        'desc': 'Production = the deployed CircleCI build (C:\\Content\\server). Development = `yarn dev` out of the slsa-play-node source checkout (hot-editable: ts-node + the Vite UI dev server). Changing this reloads the node; re-Power (or use Restart) to apply. Blank => Production.'})

# --->

# <signals ------------------------------------------------------------------

local_event_Mode = LocalEvent({'title': 'Run mode', 'group': 'Mode', 'order': next_seq(), 'schema': {'type': 'string'},
                               'desc': 'The run mode the App Launcher is currently configured for (Production / Development).'})

# --->

# <after main: override the command for Development; leave Production as the recipe set it ---

def _mode():
  return param_Mode if not is_blank(param_Mode) else 'Production'

@before_main
def _emitMode():
  local_event_Mode.emit(_mode())

@after_main
def _applyRunMode():
  mode = _mode()
  local_event_Mode.emit(mode)  # (again, in case _emitMode ran before the param settled)
  if mode == 'Development':
    console.warn('Run mode: DEVELOPMENT - serving `yarn dev` out of the source checkout (hot-editable). Set "Run mode" back to Production when done.')
    # finishMain() already set the command to start-server.bat; swap it to start-dev.bat.
    wasOn = (local_event_DesiredPower.getArg() == 'On')
    if wasOn:
      lookup_local_action('Power').call('Off')   # clean stop first (avoids a false "interrupted" flag)
    _process.setCommand([param_AppPath, '/d', '/s', '/c', 'start-dev.bat'])
    if wasOn:
      lookup_local_action('Power').call('On')
  else:
    console.info('Run mode: Production - serving the deployed build (C:\\Content\\server).')
    # nothing to do - finishMain() already set the command from param_AppArgs (= /d /s /c start-server.bat)

# --->

# <action: reload-and-restart in the current mode ---------------------------

@local_action({'title': 'Restart', 'group': 'Mode', 'order': next_seq(),
               'desc': 'Stop then start the server (Power Off -> Power On). Use after switching "Run mode" to bring the server back in the new mode.'})
def Restart(arg=None):
  console.info('Restart requested - stopping then starting...')
  lookup_local_action('Power').call('Off')
  call_safe(lambda: lookup_local_action('Power').call('On'), delay=3)

# --->
