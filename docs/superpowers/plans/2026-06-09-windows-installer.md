# Windows Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable Windows release path that produces a per-user `RenderDocMCP-Setup-x.y.z.exe` installer.

**Architecture:** Package the MCP stdio server as a PyInstaller onedir executable and wrap it with an Inno Setup installer. Use a PowerShell helper during install/uninstall to copy the RenderDoc extension and update `UI.config` Always Load.

**Tech Stack:** Python 3.10+, PyInstaller, Inno Setup 6, PowerShell 5+, pytest.

---

### Task 1: RenderDoc Extension Install Helper

**Files:**
- Create: `packaging/windows/install_renderdoc_extension.ps1`
- Test: `tests/test_windows_extension_installer_ps1.py`

- [ ] **Step 1: Write the failing test**

Create a pytest that builds a temporary RenderDoc config tree, invokes `packaging/windows/install_renderdoc_extension.ps1 install`, and asserts:

```python
assert (extension_dir / "renderdoc_mcp_bridge" / "extension.json").is_file()
assert "renderdoc_mcp_bridge" in ui_config["AlwaysLoad_Extensions"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_windows_extension_installer_ps1.py -q`

Expected: FAIL because `install_renderdoc_extension.ps1` does not exist.

- [ ] **Step 3: Implement the PowerShell helper**

Add an install/uninstall script with parameters:

```powershell
param(
  [ValidateSet("install", "uninstall")] [string] $Command = "install",
  [string] $ExtensionSource,
  [string] $ExtensionDir = "$env:APPDATA\qrenderdoc\extensions",
  [switch] $NoAlwaysLoad
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_windows_extension_installer_ps1.py -q`

Expected: PASS.

### Task 2: Windows Build and Installer Assets

**Files:**
- Create: `packaging/windows/renderdoc_mcp_entry.py`
- Create: `packaging/windows/renderdoc-mcp.spec`
- Create: `packaging/windows/RenderDocMCP.iss`
- Create: `packaging/windows/build.ps1`
- Modify: `pyproject.toml`

- [ ] **Step 1: Make local package tests work under uv**

Update Hatch package inclusion so `renderdoc_extension` is importable when running `uv run pytest -q`.

- [ ] **Step 2: Add PyInstaller entry/spec**

Create a tiny entry script:

```python
from mcp_server.server import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add Inno Setup script**

Install to `{localappdata}\RenderDocMCP`, copy the PyInstaller app, copy `renderdoc_extension`, run the PowerShell helper after install, and run it again on uninstall.

- [ ] **Step 4: Add build orchestrator**

Add `packaging/windows/build.ps1` to clean `build/windows`, run PyInstaller through `uv run --with pyinstaller`, find `ISCC.exe`, and emit `dist/windows/RenderDocMCP-Setup-<version>.exe`.

### Task 3: Documentation and Verification

**Files:**
- Create: `docs/windows-installer.md`
- Modify: `README.md`

- [ ] **Step 1: Document prerequisites and commands**

Document:

```powershell
.\packaging\windows\build.ps1
```

- [ ] **Step 2: Run verification**

Run:

```powershell
python -m pytest -q
uv run pytest -q
```

Expected: all tests pass.
