"""
RenderDoc Extension Installer

Installs the RenderDoc MCP Bridge extension into one or more RenderDoc
installations (the official build, custom-named/forked builds such as
``qrenderzzs.exe``, portable builds, etc.).

Resolution order for the destination extension directory(ies):

1. CLI argument ``--extension-dir <path>`` (repeatable).
2. CLI argument ``--target <name>`` selecting an entry from the config file
   (repeatable). Use ``--target all`` to install to every configured target.
3. Environment variable ``RENDERDOC_EXTENSION_DIR`` (single path, optional;
   ``os.pathsep``-separated list also supported).
4. Config file ``.renderdocmcp.json`` (project root) — its ``targets`` map
   is used when no CLI/env overrides are present. If the config defines
   ``default_targets``, those are installed; otherwise every configured
   target is installed.
5. Built-in default: ``%APPDATA%\\qrenderdoc\\extensions`` on Windows,
   ``~/.local/share/qrenderdoc/extensions`` elsewhere.

Config file format (``.renderdocmcp.json``):

    {
      "targets": {
        "official":   { "extension_dir": "%APPDATA%/qrenderdoc/extensions" },
        "qrenderzzs": { "extension_dir": "%APPDATA%/qrenderzzs/extensions" }
      },
      "default_targets": ["official", "qrenderzzs"]
    }

Paths may contain ``~`` and ``%VAR%`` / ``$VAR`` style environment
variables — they are expanded before use.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


CONFIG_FILENAME = ".renderdocmcp.json"


def builtin_default_extension_dir() -> Path:
    """Built-in fallback when nothing else is configured."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is not set; cannot locate qrenderdoc directory")
        return Path(appdata) / "qrenderdoc" / "extensions"
    return Path.home() / ".local" / "share" / "qrenderdoc" / "extensions"


def expand_path(raw: str) -> Path:
    """Expand ``~`` and environment variables in a path string."""
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def load_config(project_root: Path) -> dict:
    """Load ``.renderdocmcp.json`` from the project root, or return {}."""
    config_path = project_root / CONFIG_FILENAME
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print("Warning: failed to read %s: %s" % (config_path, exc))
        return {}
    if not isinstance(data, dict):
        print("Warning: %s does not contain a JSON object; ignoring" % config_path)
        return {}
    return data


def resolve_targets(args, project_root: Path) -> list[tuple[str, Path]]:
    """Return a list of (label, extension_dir) pairs to install into."""
    config = load_config(project_root)
    targets_cfg = config.get("targets", {}) if isinstance(config.get("targets"), dict) else {}

    resolved: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, raw_path: str) -> None:
        path = expand_path(raw_path)
        if path in seen:
            return
        seen.add(path)
        resolved.append((label, path))

    # 1. Explicit --extension-dir entries.
    for raw in args.extension_dir or []:
        add(raw, raw)

    # 2. --target entries from the config file.
    if args.target:
        names = list(args.target)
        if "all" in names:
            names = list(targets_cfg.keys())
            if not names:
                print("Error: --target all requested but no targets are defined in %s"
                      % (project_root / CONFIG_FILENAME))
                sys.exit(2)
        for name in names:
            entry = targets_cfg.get(name)
            if not entry or "extension_dir" not in entry:
                print("Error: target %r not found in %s" % (name, project_root / CONFIG_FILENAME))
                sys.exit(2)
            add(name, entry["extension_dir"])

    if resolved:
        return resolved

    # 3. Environment variable.
    env_value = os.environ.get("RENDERDOC_EXTENSION_DIR")
    if env_value:
        for raw in env_value.split(os.pathsep):
            raw = raw.strip()
            if raw:
                add("env:RENDERDOC_EXTENSION_DIR", raw)
        if resolved:
            return resolved

    # 4. Config file defaults.
    if targets_cfg:
        default_names = config.get("default_targets")
        if not default_names:
            default_names = list(targets_cfg.keys())
        elif not isinstance(default_names, list):
            print("Warning: 'default_targets' in %s must be a list; ignoring"
                  % (project_root / CONFIG_FILENAME))
            default_names = list(targets_cfg.keys())
        for name in default_names:
            entry = targets_cfg.get(name)
            if entry and "extension_dir" in entry:
                add(name, entry["extension_dir"])
        if resolved:
            return resolved

    # 5. Built-in default.
    add("default", str(builtin_default_extension_dir()))
    return resolved


def copy_extension(src: Path, dest: Path) -> None:
    """Copy the extension source tree into ``dest`` (replacing any existing copy)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print("  Removing existing installation at %s" % dest)
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def install(args) -> None:
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    extension_src = project_root / "renderdoc_extension"

    if not extension_src.exists():
        print("Error: Extension source not found at %s" % extension_src)
        sys.exit(1)

    targets = resolve_targets(args, project_root)
    for label, ext_dir in targets:
        dest = ext_dir / "renderdoc_mcp_bridge"
        print("Installing to [%s] %s" % (label, dest))
        copy_extension(extension_src, dest)
        print("  Done.")

    print("")
    print("Please restart each affected RenderDoc instance and enable the extension in:")
    print("  Tools > Manage Extensions > RenderDoc MCP Bridge")


def uninstall(args) -> None:
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    targets = resolve_targets(args, project_root)
    for label, ext_dir in targets:
        dest = ext_dir / "renderdoc_mcp_bridge"
        if dest.exists():
            shutil.rmtree(dest)
            print("Uninstalled [%s] from %s" % (label, dest))
        else:
            print("Not installed [%s] at %s" % (label, dest))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or uninstall the RenderDoc MCP Bridge extension.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=("install", "uninstall"),
        help="Operation to perform (default: install).",
    )
    parser.add_argument(
        "--extension-dir",
        action="append",
        metavar="PATH",
        help="Explicit extension directory to install into. May be given multiple times.",
    )
    parser.add_argument(
        "--target",
        action="append",
        metavar="NAME",
        help="Named target from .renderdocmcp.json. May be given multiple times. "
             "Use 'all' to install to every configured target.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "uninstall":
        uninstall(args)
    else:
        install(args)


if __name__ == "__main__":
    main()
