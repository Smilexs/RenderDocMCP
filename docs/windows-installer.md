# Windows Installer

This project can be released as a per-user Windows installer:

```powershell
.\packaging\windows\build.ps1
```

The build emits:

```text
dist\windows\RenderDocMCP-Setup-<version>.exe
```

## Prerequisites

- Windows 10/11
- Python 3.10+
- uv
- Inno Setup 6 (`ISCC.exe` on PATH, or pass `-InnoSetupCompiler`)

PyInstaller is pulled by the build script with `uv run --with pyinstaller`, so it does not need to be installed globally.

## What the Installer Does

- Installs `renderdoc-mcp.exe` to `%LOCALAPPDATA%\RenderDocMCP`.
- Copies the RenderDoc extension payload into the app install directory.
- Runs `install_renderdoc_extension.ps1` to install the extension into `%APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge`.
- Updates `%APPDATA%\qrenderdoc\UI.config` so `renderdoc_mcp_bridge` is listed in `AlwaysLoad_Extensions`.
- Creates Start Menu shortcuts for launching the MCP server manually and reinstalling the extension.

The MCP server uses stdio, so normal MCP clients should launch `renderdoc-mcp.exe` themselves. The Start Menu shortcut is mainly for diagnostics.

## MCP Client Configuration

After installation, configure MCP clients with the installed executable path:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "%LOCALAPPDATA%\\RenderDocMCP\\renderdoc-mcp.exe"
    }
  }
}
```

If the client does not expand environment variables in `command`, use the absolute expanded path.

## Updating

To update, download and run the new `RenderDocMCP-Setup-<version>.exe`.

- If only the MCP server changed, restart the MCP client.
- If `renderdoc_extension` changed, restart RenderDoc.
- If files are locked, close Claude/Codex/other MCP clients and RenderDoc, then rerun the installer.
