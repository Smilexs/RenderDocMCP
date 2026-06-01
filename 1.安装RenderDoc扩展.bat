@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM RenderDoc MCP Bridge - 安装 RenderDoc 扩展
REM
REM 默认安装到 %APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge
REM 自定义路径请编辑 .renderdocmcp.json 或在命令行传 --extension-dir
REM ============================================================

cd /d "%~dp0"

echo.
echo === [1/1] 安装 RenderDoc MCP Bridge 扩展 ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

python scripts\install_extension.py %*
if errorlevel 1 (
    echo.
    echo [错误] 扩展安装失败，请检查上方日志
    pause
    exit /b 1
)

echo.
echo === 完成 ===
echo.
echo 后续步骤：
echo   1. 启动 RenderDoc
echo   2. Tools ^> Manage Extensions
echo   3. 启用 "RenderDoc MCP Bridge"
echo.
pause
endlocal
