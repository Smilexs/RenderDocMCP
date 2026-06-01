@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM RenderDoc MCP Bridge - 安装 MCP 服务器
REM
REM 使用 uv 把 renderdoc-mcp 安装为全局 CLI 工具，并提示如何
REM 配置 Claude Desktop / Claude Code 客户端。
REM ============================================================

cd /d "%~dp0"

echo.
echo === [1/3] 检查 uv ===
echo.

where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv。
    echo 请先安装 uv: https://docs.astral.sh/uv/getting-started/installation/
    echo Windows 一键安装命令:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    pause
    exit /b 1
)

echo.
echo === [2/3] 安装 renderdoc-mcp (可编辑模式) ===
echo.

uv tool install --editable . --force
if errorlevel 1 (
    echo.
    echo [错误] uv tool install 失败，请检查上方日志
    pause
    exit /b 1
)

echo.
echo === [3/3] 把 uv 工具目录加入 PATH ===
echo.
uv tool update-shell

echo.
echo === 完成 ===
echo.
echo 已注册命令: renderdoc-mcp
echo 如果当前终端找不到该命令，请重启终端 / IDE / Claude Desktop。
echo.
echo --- Claude Desktop 配置 ---
echo 路径: %%APPDATA%%\Claude\claude_desktop_config.json
echo 内容:
echo {
echo   "mcpServers": {
echo     "renderdoc": { "command": "renderdoc-mcp" }
echo   }
echo }
echo.
echo --- Claude Code 配置 ---
echo 在项目根添加 .mcp.json（同上格式）
echo.
pause
endlocal
