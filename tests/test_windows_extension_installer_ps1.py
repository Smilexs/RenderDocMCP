import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "windows" / "install_renderdoc_extension.ps1"


def run_installer(*args):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell, "PowerShell is required for this test"
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *map(str, args),
        ],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def make_extension_source(root: Path, marker: str = "v1") -> Path:
    source = root / "renderdoc_extension"
    source.mkdir(parents=True)
    (source / "extension.json").write_text(
        json.dumps({"name": "RenderDoc MCP Bridge", "version": marker}),
        encoding="utf-8",
    )
    (source / "__init__.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "stale.pyc").write_bytes(b"ignored")
    return source


def read_ui_config(extension_dir: Path) -> dict:
    return json.loads((extension_dir.parent / "UI.config").read_text(encoding="utf-8-sig"))


def test_powershell_installer_installs_updates_and_uninstalls_extension():
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        source = make_extension_source(temp / "src", "v1")
        extension_dir = temp / "qrenderdoc" / "extensions"
        installed = extension_dir / "renderdoc_mcp_bridge"

        result = run_installer(
            "install",
            "-ExtensionSource",
            source,
            "-ExtensionDir",
            extension_dir,
        )

        assert result.returncode == 0, result.stdout
        assert (installed / "extension.json").is_file()
        assert (installed / "__init__.py").read_text(encoding="utf-8") == "MARKER = 'v1'\n"
        assert not (installed / "__pycache__").exists()
        assert "renderdoc_mcp_bridge" in read_ui_config(extension_dir)["AlwaysLoad_Extensions"]

        (source / "__init__.py").write_text("MARKER = 'v2'\n", encoding="utf-8")
        (installed / "removed_on_update.txt").write_text("old", encoding="utf-8")

        result = run_installer(
            "install",
            "-ExtensionSource",
            source,
            "-ExtensionDir",
            extension_dir,
        )

        assert result.returncode == 0, result.stdout
        assert (installed / "__init__.py").read_text(encoding="utf-8") == "MARKER = 'v2'\n"
        assert not (installed / "removed_on_update.txt").exists()

        result = run_installer(
            "uninstall",
            "-ExtensionSource",
            source,
            "-ExtensionDir",
            extension_dir,
        )

        assert result.returncode == 0, result.stdout
        assert not installed.exists()
        assert "renderdoc_mcp_bridge" not in read_ui_config(extension_dir)["AlwaysLoad_Extensions"]
