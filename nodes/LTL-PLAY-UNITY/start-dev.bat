@echo off
REM ============================================================================
REM  Launched by the Nodel "SLSA PLay Node Server" node when its "Run mode"
REM  param = Development (see custom_mode.py).
REM
REM  Runs the slsa-play-node source checkout's dev pair (via `yarn exec
REM  concurrently --kill-others-on-fail` - so if either side dies, concurrently
REM  exits non-zero and Nodel restarts the lot rather than leaving a half-dead
REM  pair):
REM      [server] yarn dev:server = ts-node src\index.ts  -> the node server on :9000
REM      [ui]     yarn dev:ui     = vite                  -> the Vite UI dev server on :5174
REM
REM  Consistency with Production: with NODE_ENV != production the node server
REM  itself proxies /ui/* to the Vite dev server (see src\index.ts) - so the UI
REM  is at the SAME url as in Production: http://<host>:9000/ui/  (in Production
REM  the server serves the built public\ui there instead). Vite binds all
REM  interfaces (ui\vite.config.ts: host true, allowedHosts true) and its HMR
REM  websocket connects straight to :5174, so live-reload also works from a
REM  remote browser.
REM
REM  Toolchain: resolve the pinned Node v22.12.0 install dir (matching .nvmrc)
REM  via `fnm exec ... node -p` and prepend it to PATH, then `corepack enable`
REM  so a standalone `yarn` is on PATH - the dev scripts spawn bare `yarn ...`
REM  children. fnm exec CAN spawn node.exe (a real executable) but NOT `.cmd`
REM  shims like corepack/yarn ("program not found"), hence the PATH approach.
REM  Deliberately no `for /f` on %FNM%: profile paths can contain spaces
REM  (C:\Users\Art Pro\...) and for /f's cmd /c layer mangles the
REM  quoting - a plain quoted invocation redirected to a temp file is robust.
REM
REM  Console output: stdout here is a pipe (not a terminal). NO_COLOR /
REM  FORCE_COLOR / CI tell the JS tooling to emit plain text (no ANSI colour,
REM  spinners, screen-clears) so Nodel's _process stdout/stderr handlers show it
REM  cleanly on this node's console. concurrently prefixes lines [ui]/[server].
REM  Plus a couple of `[start-dev] ...` diag echoes.
REM
REM  Prerequisites (one-time, see this node's README.md):
REM    - a git checkout of slsa-play-node at SRCDIR below
REM    - SRCDIR\.env (copy .env.example and configure for the box)
REM    - deps: this script runs `yarn install --immutable` on first Dev launch
REM      if node_modules is absent (slow, one-off) - it covers the ui workspace
REM      too (Yarn workspaces install from the root).
REM ============================================================================

REM --- adjust these if the checkout or fnm live elsewhere ---------------------
set "SRCDIR=C:\Content\slsa-play-node"
set "FNM=%LOCALAPPDATA%\Microsoft\WinGet\Links\fnm.exe"
if not exist "%FNM%" set "FNM=fnm"

set "NO_COLOR=1"
set "FORCE_COLOR=0"
set "CI=1"
set "NODE_ENV=development"
set "COREPACK_ENABLE_DOWNLOAD_PROMPT=0"

REM resolve the pinned Node install dir via fnm and prepend it to PATH
REM (avoids hardcoding the user-profile install path)
set "NODEDIR="
"%FNM%" exec --using v22.12.0 -- node -p "require('path').dirname(process.execPath)" > "%~dp0nodedir.tmp" 2>nul
set /p NODEDIR=<"%~dp0nodedir.tmp"
del "%~dp0nodedir.tmp" 2>nul
if not defined NODEDIR ( echo [start-dev] could not resolve Node v22.12.0 via fnm - is it installed? [fnm install v22.12.0] & exit /b 1 )
set "PATH=%NODEDIR%;%PATH%"
call corepack enable
echo [start-dev] node -^> & where node
echo [start-dev] yarn -^> & where yarn

cd /d "%SRCDIR%" || ( echo [start-dev] cd to %SRCDIR% FAILED & exit /b 1 )
echo [start-dev] cwd=%CD%  NODE_ENV=%NODE_ENV%
if not exist ".env" echo [start-dev] WARNING: no .env in %SRCDIR% - copy .env.example and configure it
if not exist "node_modules" ( echo [start-dev] installing deps via yarn install... & call yarn install --immutable || exit /b 1 )
echo [start-dev] starting dev - node server :9000 [proxies /ui -^> Vite], Vite :5174 ...
call yarn exec concurrently --names "ui,server" --kill-others-on-fail "yarn dev:ui" "yarn dev:server"
