@echo off
:: Spawn the PFactory MCP server for Claude Code via stdio (Windows companion
:: to start-pfactory-mcp.sh). Issue #10.
::
:: Resolves the repo root via %CLAUDE_PROJECT_DIR% (set by Claude Code) and
:: falls back to the script's parent directory. The venv at
:: apps\backend\.venv must exist - created by `npm run install:backend`.

setlocal

set "ROOT=%CLAUDE_PROJECT_DIR%"
if not defined ROOT (
    set "ROOT=%~dp0.."
)

set "PYTHON=%ROOT%\apps\backend\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo pfactory MCP server cannot start: Python venv missing at >&2
    echo     %PYTHON% >&2
    echo. >&2
    echo From the PFactory repo root run: >&2
    echo     npm run install:backend >&2
    exit /b 1
)

cd /d "%ROOT%\apps\backend"
"%PYTHON%" -m mcp_server.pfactory_server %*
