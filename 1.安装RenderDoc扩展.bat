@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem ============================================================
rem RenderDoc MCP Bridge - install/update RenderDoc extension
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
        start "RenderDoc Extension Installer" "%ComSpec%" /k ""%~f0" --inner %*"
        exit /b
    )
)

set "INSTALL_ARGS="
:collect_args
if "%~1"=="" goto :args_done
call set "INSTALL_ARGS=%%INSTALL_ARGS%% "%%~1""
shift /1
goto :collect_args
:args_done

cd /d "%~dp0"
set "PROJECT_DIR=%CD%"
set "INSTALLER=%PROJECT_DIR%\scripts\install_extension.py"
set "CONFIG_FILE=%PROJECT_DIR%\.renderdocmcp.json"

echo.
echo ============================================================
echo RenderDoc MCP Bridge - Install / Update RenderDoc Extension
echo Project: %PROJECT_DIR%
echo ============================================================
echo.

echo === [1/4] Check Python ===
set "PYTHON_CMD="
where python.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
) else (
    where py.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python was not found.
    echo Install Python 3.10+ and add it to PATH, then run this again.
    set "EXITCODE=1"
    goto :finish
)

echo [OK] Python:
%PYTHON_CMD% --version
if errorlevel 1 (
    echo [ERROR] Python exists, but it could not be executed.
    set "EXITCODE=1"
    goto :finish
)
echo.

echo === [2/4] Check installer files ===
if not exist "%INSTALLER%" (
    echo [ERROR] Installer not found:
    echo   %INSTALLER%
    set "EXITCODE=1"
    goto :finish
)

if not exist "%PROJECT_DIR%\renderdoc_extension\extension.json" (
    echo [ERROR] RenderDoc extension source was not found:
    echo   %PROJECT_DIR%\renderdoc_extension
    set "EXITCODE=1"
    goto :finish
)

if exist "%CONFIG_FILE%" (
    echo [OK] Config file:
    echo   %CONFIG_FILE%
) else (
    echo [INFO] No .renderdocmcp.json found. The built-in default target will be used.
)
echo.

echo === [3/4] Install extension and configure Always Load ===
echo [RUN] %PYTHON_CMD% "%INSTALLER%" %INSTALL_ARGS%
%PYTHON_CMD% "%INSTALLER%" %INSTALL_ARGS%
if errorlevel 1 (
    echo.
    echo [ERROR] Extension installation failed. Check the log above.
    set "EXITCODE=1"
    goto :finish
)
echo.

echo === [4/4] Verify installed targets and settings ===
%PYTHON_CMD% -c "import importlib.util, pathlib, sys; script=pathlib.Path(r'%INSTALLER%'); spec=importlib.util.spec_from_file_location('install_extension', script); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); args=mod.build_parser().parse_args(sys.argv[1:]); root=pathlib.Path(r'%PROJECT_DIR%'); sys.exit(0 if mod.verify_targets(args, root) else 1)" %INSTALL_ARGS%
if errorlevel 1 (
    echo [WARN] Command completed, but one or more target directories or settings did not match the expected state.
    echo        Check .renderdocmcp.json and the installer log above.
) else (
    echo [OK] Target directories and settings match the requested operation.
)

echo.
echo ============================================================
echo Done
echo ============================================================
echo Next steps:
echo   1. Restart RenderDoc.
echo   2. renderdoc_mcp_bridge will be loaded automatically.
echo   3. If RenderDoc was open while installing, close it before starting it again.

set "EXITCODE=0"
goto :finish

:finish
if not defined EXITCODE set "EXITCODE=0"
echo.
echo Exit code: %EXITCODE%
echo This window will stay here. Press any key to continue...
pause >nul
endlocal & exit /b %EXITCODE%
