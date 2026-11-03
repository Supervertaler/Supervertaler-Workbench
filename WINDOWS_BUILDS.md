# Windows EXE Builds

## One Unified Build

Supervertaler Windows releases are published as a **single ZIP asset**:

- **File:** `Supervertaler-v<version>-Windows.zip` (e.g. `Supervertaler-v1.10.325-Windows.zip`)
- **Size:** ~480 MB (compressed)
- **Contents:** Full application (all core CAT-tool features, LLM translation, TM/glossaries, voice commands with the offline Vosk recogniser)

> The older two-flavor CORE / FULL split (and the offline Local Whisper "FULL" build) was retired; there is now just one build.

## Critical Installation Note

⚠️ **The EXE must be run from the extracted distribution folder.**

Do **NOT** separate `Supervertaler.exe` from the `_internal/` directory.

If users see an error like missing `python312.dll`, they are:
- Running the wrong EXE (from an intermediate build folder), or
- Moving the EXE away from `_internal/`

## Building

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_release.ps1
```

### Clean Build (remove the build venv + intermediate `build/` first)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_release.ps1 -Clean
```

The script:
1. Creates/reuses an isolated build venv (`.venv-build`).
2. Installs build tooling (pip/setuptools/wheel/PyInstaller) and the app itself (`pip install -e .`).
3. Runs PyInstaller against `Supervertaler.spec`.
4. Copies `user_data/` (dictionaries, Prompt_Library, Translation_Resources, voice_scripts), the `translations/` folder, and the Start Menu shortcut helpers next to the EXE.
5. Zips the result via `create_release_zip.py`.

## Output File

After a successful build:

- `dist\Supervertaler-v<version>-Windows.zip`

The ZIP contains:
- `Supervertaler.exe`
- `_internal\` directory with all dependencies (incl. `python312.dll`)
- `user_data\`, `translations\`
- `README_FIRST.txt` with installation instructions
- `Add Supervertaler to Start Menu.cmd` + `create_start_menu_shortcut.ps1`

## Build Environment

The build script uses an isolated Python environment:
- `.venv-build` — automatically created and managed by the build script.

## Posting to GitHub

1. Create a new release with tag `v<version>` (matching `pyproject.toml`).
2. Attach the Windows ZIP. Releases also carry a macOS `.dmg` — build that on a Mac (see `BUILD_MACOS.md`) and attach it to the same release.

```powershell
gh release create v<version> `
  --title "v<version> — <headline>" `
  --notes-file <notes.md> `
  "dist\Supervertaler-v<version>-Windows.zip"
```

## Version Update Checklist

Version lives in one source of truth: **`pyproject.toml`** (`__version__` in `Supervertaler.py` reads it at runtime). Before building, ensure:

- [x] `pyproject.toml` (`version`) bumped
- [x] `CHANGELOG.md` — new entry (this is where README and the docs site point for "current version"; they are not hardcoded)
