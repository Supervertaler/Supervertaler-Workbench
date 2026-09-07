"""Create ZIP file(s) for GitHub releases.

This script zips a PyInstaller *dist* folder (NOT the intermediate build output).

Supports multiple build flavors (e.g. core/full) by allowing callers to specify
the dist folder and output zip path.
"""
import zipfile
import os
import re
from pathlib import Path
import argparse


def _ensure_readme_first(dist_dir: Path, version: str, flavor: str | None = None) -> None:
    """Write a small readme into the dist folder to prevent common launch mistakes."""
    readme_path = dist_dir / "README_FIRST.txt"

    flavor_label = f" ({flavor})" if flavor else ""
    text = (
        f"Supervertaler v{version} (Windows){flavor_label}\n"
        "\n"
        "How to run\n"
        "----------\n"
        "1) Extract the ZIP to a folder (e.g. Desktop\\Supervertaler).\n"
        "2) Run: Supervertaler\\Supervertaler.exe\n"
        "\n"
        "Optional: Add to Start Menu\n"
        "---------------------------\n"
        "Double-click 'Add Supervertaler to Start Menu.cmd'.\n"
        "This creates a Start Menu shortcut so you can launch Supervertaler easily.\n"
        "(Or, if you prefer, right-click create_start_menu_shortcut.ps1 -> Run with PowerShell.)\n"
        "\n"
        "Updating from an earlier version\n"
        "--------------------------------\n"
        "There is no installer and no auto-updater: a new release is just a new\n"
        "folder. It does not overwrite an older one, so you may briefly end up\n"
        "with two copies.\n"
        "\n"
        "To update: close Supervertaler, delete the old extracted folder, keep\n"
        "this one. If you added a Start Menu shortcut, re-run\n"
        "'Add Supervertaler to Start Menu.cmd' here to re-point it.\n"
        "\n"
        "Where your settings live\n"
        "------------------------\n"
        "Nothing you own is stored in this folder, so deleting it loses nothing.\n"
        "Your settings, API keys, prompt library, TMs and termbases live in your\n"
        "data folder (by default C:\\Users\\<you>\\Supervertaler_Data), and its\n"
        "location is recorded in C:\\Users\\<you>\\.supervertaler_config.json.\n"
        "Every copy of Supervertaler reads from that same place.\n"
        "\n"
        "Important\n"
        "---------\n"
        "- Do NOT run the EXE from inside the ZIP file.\n"
        "- Do NOT move Supervertaler.exe away from the _internal folder.\n"
        "  The _internal folder contains python312.dll and other runtime files.\n"
    )

    # Only overwrite if missing or content changed.
    if readme_path.exists():
        try:
            existing = readme_path.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing == text:
            return

    readme_path.write_text(text, encoding="utf-8")

def get_version():
    """Extract version from pyproject.toml (single source of truth)."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except Exception:
        return "unknown"

def create_release_zip(
    dist_dir: Path,
    output_zip: Path,
    *,
    flavor: str | None = None,
    version: str | None = None,
    verbose: bool = False,
) -> None:
    if version is None:
        version = get_version()

    if not dist_dir.exists():
        raise FileNotFoundError(
            f"Expected PyInstaller output folder not found: {dist_dir}. "
            "Run PyInstaller first."
        )

    _ensure_readme_first(dist_dir, version, flavor=flavor)

    print(f"Creating release ZIP: {output_zip}")
    print(f"Version: {version}")
    print(f"From directory: {dist_dir}")

    # Remove existing ZIP if it exists
    if output_zip.exists():
        output_zip.unlink()
        print(f"Removed existing ZIP")

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        added_files = 0
        for root, dirs, files in os.walk(dist_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(dist_dir.parent)
                if verbose:
                    print(f"Adding: {arcname}")
                zipf.write(file_path, arcname)
                added_files += 1

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"\nZIP created successfully!")
    if not verbose:
        print(f"Files added: {added_files}")
    print(f"Size: {size_mb:.1f} MB")
    print(f"Location: {output_zip.absolute()}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a versioned ZIP from a PyInstaller dist folder")
    parser.add_argument(
        "--dist-dir",
        default="dist/Supervertaler",
        help="PyInstaller output folder to zip (default: dist/Supervertaler)",
    )
    parser.add_argument(
        "--output-zip",
        default=None,
        help="Output ZIP path (default: dist/Supervertaler-v<version>-Windows.zip)",
    )
    parser.add_argument(
        "--flavor",
        default=None,
        help="Optional label written into README_FIRST.txt (e.g. core/full)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every file added to the ZIP (very noisy).",
    )
    args = parser.parse_args()

    version = get_version()
    dist_dir = Path(args.dist_dir)
    output_zip = Path(args.output_zip) if args.output_zip else Path(f"dist/Supervertaler-v{version}-Windows.zip")
    create_release_zip(
        dist_dir=dist_dir,
        output_zip=output_zip,
        flavor=args.flavor,
        version=version,
        verbose=args.verbose,
    )
