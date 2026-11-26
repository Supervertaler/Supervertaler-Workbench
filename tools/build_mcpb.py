#!/usr/bin/env python3
"""Build the Supervertaler Workbench .mcpb bundle (Claude Desktop extension).

Same server exe as the Trados and memoQ extensions - Supervertaler.McpServer,
which lives in the Supervertaler-for-Trados repo - packed with a manifest that
sets SUPERVERTALER_HOST=workbench. That one variable makes the exe look for
Workbench's handshakes (<Supervertaler data folder>\\workbench\\runtime\\instances\\),
use a separate tool cache, and word its messages for Workbench. The tools
themselves come from the running Workbench (modules/bridge_tools.py), so new
ones ship with a Workbench update and need no new bundle.

Windows only for now: the exe is published for win-x64.

The server project is found in a sibling checkout by default
(../Supervertaler-for-Trados); pass --server-project to point elsewhere.

Usage:
    python tools/build_mcpb.py [--version 0.1.0] [--skip-publish]
"""
import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SERVER_PROJECT = REPO.parent / "Supervertaler-for-Trados" / "src" / "Supervertaler.McpServer"
DIST = REPO / "dist"
ICON = REPO / "assets" / "icon_256.png"
BUNDLE = "Supervertaler-Workbench-MCP-Server.mcpb"


def manifest(version: str) -> dict:
    return {
        "manifest_version": "0.3",
        "name": "supervertaler-workbench",
        "display_name": "Supervertaler Workbench – MCP Server",
        "version": version,
        "description": "Connect your AI assistant to the project open in Supervertaler Workbench.",
        "long_description": (
            "Gives AI assistants a live view of the project open in Supervertaler Workbench: the project "
            "and its progress, its segments with source and target, the segment you are in, the terms "
            "your termbases have for a text, a search of your translation memories, checks on numbers, "
            "tags, non-breaking spaces and inconsistent translations, comments, and your prompt library. "
            "The assistant can move the cursor but cannot change the project. Requires "
            "Supervertaler Workbench to be running. Everything stays on your machine: the connection is "
            "loopback-only and token-authenticated."
        ),
        "author": {"name": "Supervertaler", "url": "https://supervertaler.com"},
        "homepage": "https://supervertaler.com",
        "icon": "icon.png",
        "server": {
            "type": "binary",
            "entry_point": "server/SupervertalerMcpServer.exe",
            "mcp_config": {
                "command": "${__dirname}/server/SupervertalerMcpServer.exe",
                "args": [],
                "env": {"SUPERVERTALER_HOST": "workbench"},
            },
        },
        "compatibility": {"platforms": ["win32"]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--server-project", default=str(DEFAULT_SERVER_PROJECT))
    ap.add_argument("--skip-publish", action="store_true", help="reuse what the last run published to dist/mcp-server")
    args = ap.parse_args()

    project = Path(args.server_project)
    if not (project / "Supervertaler.McpServer.csproj").exists():
        print(f"ERROR: server project not found at {project}", file=sys.stderr)
        return 1
    # Our own folder, not the server project's bin\...\publish: MCP clients may
    # be running an exe from there, and Windows will not overwrite a running exe.
    publish_dir = DIST / "mcp-server"

    if not args.skip_publish:
        print("== dotnet publish (Release, win-x64, self-contained single file) ==")
        subprocess.run(
            ["dotnet", "publish", "-c", "Release", "-r", "win-x64", "--self-contained", "true",
             "-p:PublishSingleFile=true", "-p:IncludeNativeLibrariesForSelfExtract=true",
             f"-p:Version={args.version}", "-o", str(publish_dir)],
            cwd=project, check=True)

    exe = publish_dir / "SupervertalerMcpServer.exe"
    if not exe.exists():
        print(f"ERROR: publish output not found at {exe}", file=sys.stderr)
        return 1

    DIST.mkdir(exist_ok=True)
    out = DIST / BUNDLE
    out.unlink(missing_ok=True)

    print(f"== packing {out.name} ==")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(args.version), indent=2))
        z.write(exe, "server/SupervertalerMcpServer.exe")
        if ICON.exists():
            z.write(ICON, "icon.png")
        else:
            print(f"  (icon not found at {ICON} - skipped)")

    print(f"Done: {out}  ({out.stat().st_size / (1024 * 1024):.1f} MB)")
    print("Install: Claude Desktop > Settings > Desktop app > Extensions > Manage > Advanced settings > Install extension")
    return 0


if __name__ == "__main__":
    sys.exit(main())
