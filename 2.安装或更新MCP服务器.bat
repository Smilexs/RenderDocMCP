@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem ============================================================
rem RenderDoc MCP Bridge - install/update MCP server
rem Keep this batch file ASCII-only. Some Windows cmd.exe setups parse
rem UTF-8 batch files with Chinese text incorrectly before chcp takes effect.
rem ============================================================

rem When launched by double-click, Windows normally runs the batch through
rem "cmd /c" and closes the window after exit. Re-launch under "cmd /k" so
rem the logs remain visible even if the script exits early.
if /i "%~1"=="--inner" (
    shift /1
) else (
    echo %CMDCMDLINE% | findstr /i /c:"/c" >nul 2>nul
    if not errorlevel 1 (
        start "RenderDoc MCP Installer" "%ComSpec%" /k ""%~f0" --inner %*"
        exit /b
    )
)

cd /d "%~dp0"
set "PROJECT_DIR=%CD%"
set "TOOL_NAME=renderdoc-mcp"

echo.
echo ============================================================
echo RenderDoc MCP Bridge - Install / Update MCP Server
echo Project: %PROJECT_DIR%
echo ============================================================
echo.

echo === [1/6] Check uv ===
where uv.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv.exe was not found.
    echo Install uv first:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    set "EXITCODE=1"
    goto :finish
)

for /f "delims=" %%D in ('uv tool dir 2^>nul') do set "UV_TOOLS_DIR=%%D"
for /f "delims=" %%D in ('uv tool dir --bin 2^>nul') do set "UV_BIN_DIR=%%D"

if not defined UV_TOOLS_DIR (
    echo [ERROR] Could not get uv tool directory.
    set "EXITCODE=1"
    goto :finish
)

if not defined UV_BIN_DIR (
    echo [ERROR] Could not get uv tool executable directory.
    set "EXITCODE=1"
    goto :finish
)

set "TOOL_ENV_DIR=%UV_TOOLS_DIR%\%TOOL_NAME%"
set "TOOL_EXE=%UV_BIN_DIR%\%TOOL_NAME%.exe"
set "PATH=%UV_BIN_DIR%;%PATH%"

echo [OK] uv:
uv --version
echo [OK] uv tool dir: %UV_TOOLS_DIR%
echo [OK] uv bin dir : %UV_BIN_DIR%
echo.

echo === [2/6] Check running MCP server processes ===
call :print_running_mcp
if "%RUNNING_MCP%"=="1" (
    echo.
    echo [INFO] Running %TOOL_NAME% related processes were found.
    echo        Windows locks old .pyd files while those processes are alive.
    choice /C YN /N /M "Stop these processes and continue? [Y/N] "
    if errorlevel 2 (
        echo [CANCELLED] Close Claude / Codex / other MCP clients and run this again.
        set "EXITCODE=1"
        goto :finish
    )

    call :stop_running_mcp
    if errorlevel 1 (
        echo [ERROR] Some %TOOL_NAME% related processes are still running.
        set "EXITCODE=1"
        goto :finish
    )
) else (
    echo [OK] No running %TOOL_NAME% processes found.
)
echo.

echo === [3/6] Clean old or broken uv tool environment ===
uv tool uninstall %TOOL_NAME% >nul 2>nul

if exist "%TOOL_ENV_DIR%\*" (
    echo [CLEAN] Removing stale directory:
    echo   %TOOL_ENV_DIR%
    rmdir /s /q "%TOOL_ENV_DIR%" 2>nul
)

if exist "%TOOL_ENV_DIR%\*" (
    echo [ERROR] Could not remove old tool environment:
    echo   %TOOL_ENV_DIR%
    echo Make sure no process is using it, then retry.
    set "EXITCODE=1"
    goto :finish
)

if exist "%TOOL_EXE%" (
    echo [CLEAN] Removing old command:
    echo   %TOOL_EXE%
    del /f /q "%TOOL_EXE%" 2>nul
)

if exist "%TOOL_EXE%" (
    echo [ERROR] Could not remove old command:
    echo   %TOOL_EXE%
    set "EXITCODE=1"
    goto :finish
)

echo [OK] Cleanup completed.
echo.

echo === [4/6] Install or update %TOOL_NAME% ===
echo [RUN] uv tool install --editable "%PROJECT_DIR%" --reinstall --force
uv tool install --editable "%PROJECT_DIR%" --reinstall --force
if errorlevel 1 (
    echo.
    echo [ERROR] uv tool install failed. Check the log above.
    set "EXITCODE=1"
    goto :finish
)
echo.

echo === [5/6] Update PATH and verify command ===
uv tool update-shell
if errorlevel 1 (
    echo [WARN] uv tool update-shell failed. This window still has PATH set to:
    echo   %UV_BIN_DIR%
)

where %TOOL_NAME%.exe
if errorlevel 1 (
    echo [ERROR] Could not find %TOOL_NAME%.exe. Check PATH or uv bin dir.
    set "EXITCODE=1"
    goto :finish
)

uv tool list 2>&1 | findstr /i /c:"%TOOL_NAME% v" >nul
if errorlevel 1 (
    echo [ERROR] uv tool list does not show a valid %TOOL_NAME% install.
    echo Current uv tool list:
    uv tool list
    set "EXITCODE=1"
    goto :finish
)

if exist "%TOOL_ENV_DIR%\Scripts\python.exe" (
    "%TOOL_ENV_DIR%\Scripts\python.exe" -W ignore -c "import importlib.metadata as m; import mcp_server.server; print('import ok:', m.version('renderdoc-mcp'))"
    if errorlevel 1 (
        echo [ERROR] Command was installed, but Python package import failed.
        set "EXITCODE=1"
        goto :finish
    )
)

echo [OK] %TOOL_NAME% is installed and importable.
echo.

echo === [6/6] MCP client configuration hints ===
if exist "%PROJECT_DIR%\.mcp.json" (
    echo [OK] Project .mcp.json exists:
    echo   %PROJECT_DIR%\.mcp.json
) else (
    echo [WARN] Project .mcp.json was not found.
)

set "CLAUDE_CONFIG=%APPDATA%\Claude\claude_desktop_config.json"
if exist "%CLAUDE_CONFIG%" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:CLAUDE_CONFIG; try { $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json } catch { exit 2 }; if ($null -ne $j.mcpServers -and $null -ne $j.mcpServers.renderdoc -and $j.mcpServers.renderdoc.command -eq 'renderdoc-mcp') { exit 0 } else { exit 1 }"
    if errorlevel 2 (
        echo [WARN] Claude Desktop config exists but could not be parsed:
        echo   %CLAUDE_CONFIG%
    ) else (
        if errorlevel 1 (
            echo [INFO] Claude Desktop config exists, but renderdoc MCP is not configured:
            echo   %CLAUDE_CONFIG%
            echo Add this config and restart Claude Desktop if you use Claude Desktop:
            echo {
            echo   "mcpServers": {
            echo     "renderdoc": { "command": "renderdoc-mcp" }
            echo   }
            echo }
        ) else (
            echo [OK] Claude Desktop has renderdoc MCP configured.
        )
    )
) else (
    echo [INFO] Claude Desktop config was not found:
    echo   %CLAUDE_CONFIG%
)

echo.
echo ============================================================
echo Done
echo ============================================================
echo Command: %TOOL_NAME%
echo Path   : %TOOL_EXE%
echo.
echo If an MCP client was already open, restart it so it reloads PATH/config.

set "EXITCODE=0"
goto :finish

:print_running_mcp
set "RUNNING_MCP=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$toolRoot=Join-Path $env:APPDATA 'uv\tools\renderdoc-mcp'; $bin=Join-Path $env:USERPROFILE '.local\bin\renderdoc-mcp.exe'; $items=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.Name -eq 'renderdoc-mcp.exe' -or ($_.ExecutablePath -like ($toolRoot + '\*')) -or ($_.CommandLine -like ('*' + $bin + '*'))) }; if ($items) { $items | Select-Object ProcessId,Name,ExecutablePath,CommandLine | Format-Table -AutoSize; exit 1 } else { exit 0 }"
if errorlevel 1 set "RUNNING_MCP=1"
exit /b 0

:stop_running_mcp
powershell -NoProfile -ExecutionPolicy Bypass -Command "$toolRoot=Join-Path $env:APPDATA 'uv\tools\renderdoc-mcp'; $bin=Join-Path $env:USERPROFILE '.local\bin\renderdoc-mcp.exe'; $query={ $_.ProcessId -ne $PID -and ($_.Name -eq 'renderdoc-mcp.exe' -or ($_.ExecutablePath -like ($toolRoot + '\*')) -or ($_.CommandLine -like ('*' + $bin + '*'))) }; $items=Get-CimInstance Win32_Process | Where-Object $query; $items | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500; $left=Get-CimInstance Win32_Process | Where-Object $query; if ($left) { $left | Select-Object ProcessId,Name,ExecutablePath,CommandLine | Format-Table -AutoSize; exit 1 } else { exit 0 }"
exit /b %ERRORLEVEL%

:finish
if not defined EXITCODE set "EXITCODE=0"
echo.
echo Exit code: %EXITCODE%
echo This window will stay here. Press any key to continue...
pause >nul
endlocal & exit /b %EXITCODE%
