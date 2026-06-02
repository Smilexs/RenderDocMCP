@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

echo.
echo === RenderDoc MCP Server ===
echo.
echo This window runs the MCP server in stdio mode.
echo If it stays open and waits, that is normal.
echo Close this window to stop the server.
echo.

set "LOCAL_BIN=%USERPROFILE%\.local\bin"
if exist "%LOCAL_BIN%" set "PATH=%LOCAL_BIN%;%PATH%"

set "EXITCODE=0"

if exist "%LOCAL_BIN%\renderdoc-mcp.exe" (
    echo Starting: "%LOCAL_BIN%\renderdoc-mcp.exe"
    echo.
    call "%LOCAL_BIN%\renderdoc-mcp.exe"
    set "EXITCODE=%ERRORLEVEL%"
    goto :done
)

where renderdoc-mcp.exe >nul 2>nul
if not errorlevel 1 (
    echo Starting: renderdoc-mcp
    echo.
    call renderdoc-mcp
    set "EXITCODE=%ERRORLEVEL%"
    goto :done
)

if exist "%LOCAL_BIN%\uv.exe" (
    echo renderdoc-mcp was not found. Trying uv from:
    echo   "%LOCAL_BIN%\uv.exe"
    echo.
    call "%LOCAL_BIN%\uv.exe" run python -m mcp_server.server
    set "EXITCODE=%ERRORLEVEL%"
    goto :done
)

where uv.exe >nul 2>nul
if not errorlevel 1 (
    echo renderdoc-mcp was not found. Trying uv from PATH.
    echo.
    call uv run python -m mcp_server.server
    set "EXITCODE=%ERRORLEVEL%"
    goto :done
)

echo ERROR: Could not find renderdoc-mcp.exe or uv.exe.
echo Run "2.install MCP server" first, or add this directory to PATH:
echo   %LOCAL_BIN%
set "EXITCODE=1"

:done
echo.
echo MCP Server stopped. Exit code: %EXITCODE%
echo.
pause
exit /b %EXITCODE%
