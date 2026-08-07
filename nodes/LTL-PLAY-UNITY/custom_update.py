'''Update add-on for the "App Launcher" node (SLSA Play node server).

Nodel auto-executes every non-'_'-prefixed *.py file in a node folder into the same
namespace, so this file sits *alongside* the stock museumsvictoria App Launcher 'script.py'
and adds CircleCI-update capability without forking the recipe. It reuses the recipe's
'Power' action and 'Running' signal to bounce the server around an update.

It does NOT reimplement the download/unzip/yarn-install dance in Jython - it shells out to
the bundled 'update-server.ps1' (CircleCI artifact discovery -> download -> wipe + extract
C:\\Content\\server -> copy config\\.env -> yarn install; no server start - this node owns
that). Actions added:

  * "Check For Update"  - query CircleCI, refresh the version signals (no deploy)
  * "Deploy Update"     - stop the server if running, run update-server.ps1, restart if it was running

Stays a no-op on its own: deploying an update only happens when you invoke "Deploy Update".
'''

import os

# <parameters ---------------------------------------------------------------

param_DeployDir = Parameter({'title': 'Update - deploy directory', 'group': 'Update', 'order': next_seq(),
                             'schema': {'type': 'string', 'hint': 'C:\\Content'},
                             'desc': 'The SLSA Play deploy directory (holds server\\, config\\, .circle-token, .current-version). Blank => the default below.'})

param_UpdateScript = Parameter({'title': 'Update - helper script path', 'group': 'Update', 'order': next_seq(),
                                'schema': {'type': 'string', 'hint': '(blank = "update-server.ps1" next to this node)'},
                                'desc': 'PowerShell helper that does the actual update. Blank => "update-server.ps1" in this node folder.'})

param_UpdateBranch = Parameter({'title': 'Update - branch', 'group': 'Update', 'order': next_seq(),
                                'schema': {'type': 'string', 'hint': 'main'},
                                'desc': 'CircleCI branch to pull the node-server build from. Blank => "main".'})

param_AutoCheckHours = Parameter({'title': 'Update - auto-check interval (hours)', 'group': 'Update', 'order': next_seq(),
                                  'schema': {'type': 'integer', 'hint': '0 = off'},
                                  'desc': 'If > 0, automatically run "Check For Update" this often (never auto-deploys).'})

DEFAULT_DEPLOY_DIR = 'C:\\Content'
UPDATE_TIMEOUT_SECONDS = 20 * 60

# --->

# <signals ------------------------------------------------------------------

local_event_DeployedVersion        = LocalEvent({'title': 'Deployed version',        'group': 'Update', 'order': next_seq(), 'schema': {'type': 'string'},  'desc': 'Contents of <deploy>\\.current-version'})
local_event_LatestAvailableVersion = LocalEvent({'title': 'Latest available version', 'group': 'Update', 'order': next_seq(), 'schema': {'type': 'string'},  'desc': 'Latest successful CircleCI build (from the last check)'})
local_event_UpdateAvailable        = LocalEvent({'title': 'Update available',         'group': 'Update', 'order': next_seq(), 'schema': {'type': 'boolean'}, 'desc': 'True if the latest CircleCI build differs from the deployed version'})
local_event_UpdateInProgress       = LocalEvent({'title': 'Update in progress',       'group': 'Update', 'order': next_seq(), 'schema': {'type': 'boolean'}})
local_event_LastUpdateCheck        = LocalEvent({'title': 'Last update check',         'group': 'Update', 'order': next_seq(), 'schema': {'type': 'string'}})
local_event_LastUpdateApplied      = LocalEvent({'title': 'Last update applied',       'group': 'Update', 'order': next_seq(), 'schema': {'type': 'string'}})

# --->

# <helpers ------------------------------------------------------------------

def _deployDir():
  return param_DeployDir if not is_blank(param_DeployDir) else DEFAULT_DEPLOY_DIR

def _nodeFolder():
  # nodeConfig.json sets the App Launcher recipe's 'App. Working Dir.' to this node's own
  # folder, so param_AppWorkingDir is a reliable handle on it; fall back to the cwd.
  d = param_AppWorkingDir
  if not is_blank(d) and os.path.isdir(d):
    return d
  return os.getcwd()

def _updateScriptPath():
  if not is_blank(param_UpdateScript):
    return param_UpdateScript
  return os.path.join(_nodeFolder(), 'update-server.ps1')

def _refreshDeployedVersion():
  vf = os.path.join(_deployDir(), '.current-version')
  try:
    if os.path.isfile(vf):
      f = open(vf, 'rb')
      try:
        content = f.read()
      finally:
        f.close()
      if content.startswith('\xef\xbb\xbf'):  # update-server.ps1 (PS5 -Encoding utf8) writes it UTF-8-with-BOM
        content = content[3:]
      local_event_DeployedVersion.emit(content.strip())
    else:
      local_event_DeployedVersion.emit('')
  except Exception, e:
    console.warn('Could not read .current-version: %s' % e)

def _parseKeyValues(text):
  info = {}
  for line in (text or '').splitlines():
    if '=' in line:
      k, _, v = line.partition('=')
      info[k.strip()] = v.strip()
  return info

def _applyVersionInfo(info):
  if 'DEPLOYED_VERSION' in info: local_event_DeployedVersion.emit(info['DEPLOYED_VERSION'])
  if 'LATEST_VERSION' in info:   local_event_LatestAvailableVersion.emit(info['LATEST_VERSION'])
  if 'UPDATE_AVAILABLE' in info: local_event_UpdateAvailable.emit(info['UPDATE_AVAILABLE'].strip().lower() == 'true')

# --->

# <the update runner --------------------------------------------------------

def _runUpdateScript(checkOnly):
  script = _updateScriptPath()
  if not os.path.isfile(script):
    console.error('Update helper not found: [%s]' % script)
    return

  deployDir = _deployDir()
  cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, '-DeployDir', deployDir]
  if not is_blank(param_UpdateBranch):
    cmd.extend(['-Branch', param_UpdateBranch])
  if checkOnly:
    cmd.append('-CheckOnly')

  wasRunning = (local_event_Running.getArg() == 'On')  # local_event_Running is from the App Launcher recipe

  if not checkOnly:
    if local_event_UpdateInProgress.getArg() == True:
      console.warn('An update is already in progress; ignoring.')
      return
    local_event_UpdateInProgress.emit(True)
    if wasRunning:
      console.info('Stopping the server before updating...')
      lookup_local_action('Power').call('Off')  # the App Launcher recipe kills the server process here

  console.info('%s: %s' % ('Checking for update' if checkOnly else 'Deploying update', ' '.join(cmd)))

  def finished(res):
    out = res.stdout or ''
    err = res.stderr or ''
    for line in out.splitlines():
      if line.strip(): console.info('update> %s' % line)
    for line in err.splitlines():
      if line.strip(): console.warn('update! %s' % line)

    info = _parseKeyValues(out)
    _applyVersionInfo(info)
    local_event_LastUpdateCheck.emit(str(date_now()))

    if checkOnly:
      if res.code not in (0, 10):
        console.error('Check-for-update failed (exit %s).' % res.code)
      elif local_event_UpdateAvailable.getArg() == True:
        console.info('Update available: %s -> %s. Use "Deploy Update" to apply.'
                     % (local_event_DeployedVersion.getArg(), local_event_LatestAvailableVersion.getArg()))
      else:
        console.info('Up to date (%s).' % local_event_DeployedVersion.getArg())
      return

    # --- deploy path ---
    local_event_UpdateInProgress.emit(False)
    _refreshDeployedVersion()
    if res.code == 0:
      if 'UPDATE_COMPLETE' in info:
        local_event_LastUpdateApplied.emit(str(date_now()))
        console.info('Update applied: now on %s.' % info['UPDATE_COMPLETE'])
      else:
        console.info('Update step finished OK (nothing to apply).')
      if wasRunning:
        console.info('Restarting the server...')
        lookup_local_action('Power').call('On')
    else:
      console.error('Update FAILED (exit %s) - server left stopped. See the "update>" lines above.' % res.code)

  quick_process(cmd, finished=finished, timeoutInSeconds=UPDATE_TIMEOUT_SECONDS, working=deployDir)

# --->

# <actions ------------------------------------------------------------------

@local_action({'title': 'Check For Update', 'group': 'Update', 'order': next_seq(),
               'desc': 'Query CircleCI for the latest node-server build; refreshes the version signals. Does not deploy anything.'})
def CheckForUpdate(arg=None):
  _runUpdateScript(checkOnly=True)

@local_action({'title': 'Deploy Update', 'group': 'Update', 'order': next_seq(),
               'desc': 'Stop the server if running, pull + install the latest CircleCI build (via update-server.ps1), then restart it if it had been running. Replaces <deploy>\\server - use deliberately.'})
def DeployUpdate(arg=None):
  _runUpdateScript(checkOnly=False)

# --->

# <init ---------------------------------------------------------------------

@after_main
def _initUpdateAddon():
  local_event_UpdateInProgress.emit(False)
  _refreshDeployedVersion()
  hrs = param_AutoCheckHours
  if hrs and hrs > 0:
    Timer(lambda: _runUpdateScript(checkOnly=True), hrs * 3600.0, firstDelayInSeconds=60.0)
    console.info('Auto update-check enabled: every %s h.' % hrs)

# --->
