# Windows 安装包

本项目可以发布为 Windows 用户级安装包。构建脚本会先用 PyInstaller 打包
`renderdoc-mcp.exe`，再用 Inno Setup 生成安装程序：

```powershell
.\packaging\windows\build.ps1
```

构建成功后会输出：

```text
dist\windows\RenderDocMCP-Setup-<version>.exe
```

## 构建依赖

- Windows 10/11
- Python 3.10+
- uv
- Inno Setup 6

`ISCC.exe` 需要能被 PATH 找到；如果没有加入 PATH，也可以在构建时通过
`-InnoSetupCompiler` 显式传入路径：

```powershell
.\packaging\windows\build.ps1 -InnoSetupCompiler "C:\Users\<用户名>\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
```

PyInstaller 不需要全局安装。构建脚本会通过
`uv run --with pyinstaller` 临时拉取并运行 PyInstaller。

## 安装器会做什么

- 将 `renderdoc-mcp.exe` 安装到 `%LOCALAPPDATA%\RenderDocMCP`。
- 将 RenderDoc 扩展文件复制到安装目录内，作为后续安装扩展的源文件。
- 运行 `install_renderdoc_extension.ps1`，把扩展安装到
  `%APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge`。
- 更新 `%APPDATA%\qrenderdoc\UI.config`，把 `renderdoc_mcp_bridge` 加入
  `AlwaysLoad_Extensions`。
- 创建开始菜单快捷方式，用于手动启动 MCP Server 或重新安装 RenderDoc 扩展。

MCP Server 使用 stdio 通信。正常使用时，应由 Claude、Codex 等 MCP 客户端
自动启动 `renderdoc-mcp.exe`；开始菜单里的启动项主要用于诊断和手动测试。

## MCP 客户端配置

安装完成后，可以把 MCP 客户端配置为调用安装后的可执行文件：

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "%LOCALAPPDATA%\\RenderDocMCP\\renderdoc-mcp.exe"
    }
  }
}
```

如果客户端不会展开 `command` 里的环境变量，请改用展开后的绝对路径，例如：

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "C:\\Users\\<用户名>\\AppData\\Local\\RenderDocMCP\\renderdoc-mcp.exe"
    }
  }
}
```

## 更新

更新时下载并运行新的 `RenderDocMCP-Setup-<version>.exe` 即可。

- 如果只更新了 MCP Server，重启 MCP 客户端即可。
- 如果更新了 `renderdoc_extension`，需要重启 RenderDoc。
- 如果安装器提示文件被占用，先关闭 Claude、Codex、其他 MCP 客户端和 RenderDoc，
  然后重新运行安装器。
