@echo off
REM ============================================================================
REM  Launched by the Nodel node "SLSA PLay Node Server".
REM  Runs the *already-deployed* SLSA Play node server build.
REM
REM  This wrapper deliberately does NOT update the server (no CircleCI pull, no
REM  yarn install). Updates are a separate concern - see custom_update.py /
REM  update-server.ps1 alongside this file (actions "Check For Update" and
REM  "Deploy Update" on the node).
REM
REM  NODE_ENV=production is REQUIRED here: the repo's `yarn start` sets it via
REM  cross-env, but we run node directly. Without it the server thinks it's in
REM  dev mode and proxies /ui to a (non-existent) Vite dev server instead of
REM  serving the built UI from public\ui (see src\index.ts).
REM
REM  Output: node's stdout/stderr flow up through cmd to this process, so the
REM  App Launcher recipe (_process stdout/stderr handlers) pipes it to the Nodel
REM  node console. Don't redirect stdout/stderr here.
REM
REM  Node version: pinned to v22.12.0 to match the repo's .nvmrc (shipped in
REM  the build zip as server\.nvmrc). If that changes, update the --using
REM  value below.
REM ============================================================================
set "NODE_ENV=production"
cd /d "C:\Content\server" || exit /b 1
"%LOCALAPPDATA%\Microsoft\WinGet\Links\fnm.exe" exec --using v22.12.0 -- node .\dist\index.js
