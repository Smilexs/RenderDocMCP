@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM RenderDoc MCP Bridge - 启动 MCP Server
REM
REM 正常情况下 MCP Server 由 AI 客户端（Claude Desktop / Claude Code）
REM 通过 stdio 自动拉起，这里提供一个手动启动入口，便于：
REM   - 验证 renderdoc-mcp 是否安装成功
REM   - 调试 Server 启动错误
REM   - 查看依赖加载日志
REM
REM 注意: MCP Server 使用 stdio 协议，手动启动后窗口会等待 stdin 输入。
REM       这并非卡死，关闭窗口即可。
REM ============================================================

cd /d "%~dp0"

echo.
echo === 启动 RenderDoc MCP Server ===
echo.

where renderdoc-mcp >nul 2>nul
if errorlevel 1 (
    echo [警告] PATH 中未找到 renderdoc-mcp 命令
    echo 尝试通过 uv tool run 启动...
    echo.
    where uv >nul 2>nul
    if errorlevel 1 (
        echo [错误] 既找不到 renderdoc-mcp，也找不到 uv。
        echo 请先运行 "2.安装MCP服务器.bat" 完成安装。
        pause
        exit /b 1
    )
    uv tool run renderdoc-mcp
    goto :end
)

echo 提示：
echo   - MCP Server 通过 stdio 与客户端通信，窗口等待输入是正常的
echo   - 请确保 RenderDoc 已启动并启用了 "RenderDoc MCP Bridge" 扩展
echo   - 关闭此窗口即可停止 Server
echo.

renderdoc-mcp

:end
echo.
echo MCP Server 已停止。
pause
endlocal
