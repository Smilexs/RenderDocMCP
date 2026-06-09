#define AppName "RenderDoc MCP"
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\build\windows\stage"
#endif

[Setup]
AppId={{B7B44B2C-7377-4985-A68A-F18B13F3F511}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=RenderDocMCP
DefaultDirName={localappdata}\RenderDocMCP
DefaultGroupName=RenderDoc MCP
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist\windows
OutputBaseFilename=RenderDocMCP-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\renderdoc-mcp.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\renderdoc_extension\*"; DestDir: "{app}\renderdoc_extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\install_renderdoc_extension.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\RenderDoc MCP Server"; Filename: "{app}\renderdoc-mcp.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Install RenderDoc MCP Extension"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\install_renderdoc_extension.ps1"" install -ExtensionSource ""{app}\renderdoc_extension"""; WorkingDir: "{app}"
Name: "{autodesktop}\RenderDoc MCP Server"; Filename: "{app}\renderdoc-mcp.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\install_renderdoc_extension.ps1"" install -ExtensionSource ""{app}\renderdoc_extension"""; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\install_renderdoc_extension.ps1"" uninstall"; Flags: runhidden waituntilterminated
