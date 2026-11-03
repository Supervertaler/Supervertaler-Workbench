# Building Supervertaler on macOS

This guide walks you through building Supervertaler as a native macOS `.app` bundle.

## Prerequisites

- **macOS 13 (Ventura)** or later (Apple Silicon or Intel)
- **Python 3.11+** (via Homebrew or python.org)
- **Git** (comes with Xcode Command Line Tools)
- **Xcode Command Line Tools**: `xcode-select --install`

## Step 1: Install Python (if needed)

```bash
# Option A: Homebrew (recommended)
brew install python@3.11

# Option B: python.org universal installer
# Download from https://www.python.org/downloads/macos/
```

Verify:
```bash
python3 --version   # Should be 3.11+
```

## Step 2: Clone the Repository

```bash
cd ~/Dev
git clone https://github.com/Supervertaler/Supervertaler-Workbench.git
cd Supervertaler
```

## Step 3: Create Build Virtual Environment

```bash
python3 -m venv .venv-build-macos
source .venv-build-macos/bin/activate
pip install --upgrade pip setuptools wheel
```

## Step 4: Install Dependencies

```bash
# Install all Supervertaler dependencies
pip install -e ".[dev]" 2>/dev/null || pip install -e .

# Install PyInstaller for building the .app bundle
pip install pyinstaller

# Install create-dmg for disk image packaging (optional)
brew install create-dmg
```

### Troubleshooting Dependencies

If PyQt6 fails to install on Apple Silicon:
```bash
pip install PyQt6 --only-binary :all:
pip install PyQt6-WebEngine --only-binary :all:
```

If `sounddevice` fails (needs PortAudio):
```bash
brew install portaudio
pip install sounddevice
```

## Step 5: Create the macOS Icon (.icns)

Supervertaler ships with PNG icons but macOS needs `.icns` format:

```bash
# Create iconset directory
mkdir -p assets/Supervertaler.iconset

# Copy existing PNGs (resize as needed)
cp assets/icon_16x16.png  assets/Supervertaler.iconset/icon_16x16.png
cp assets/icon_32x32.png  assets/Supervertaler.iconset/icon_16x16@2x.png
cp assets/icon_32x32.png  assets/Supervertaler.iconset/icon_32x32.png
cp assets/icon_64.png     assets/Supervertaler.iconset/icon_32x32@2x.png
cp assets/icon_128.png    assets/Supervertaler.iconset/icon_128x128.png
cp assets/icon_256.png    assets/Supervertaler.iconset/icon_128x128@2x.png
cp assets/icon_256.png    assets/Supervertaler.iconset/icon_256x256.png

# Generate 512px versions from 256px (or use the SVG)
sips -z 512 512 assets/icon_256.png --out assets/Supervertaler.iconset/icon_256x256@2x.png
sips -z 512 512 assets/icon_256.png --out assets/Supervertaler.iconset/icon_512x512.png
sips -z 1024 1024 assets/icon_256.png --out assets/Supervertaler.iconset/icon_512x512@2x.png

# Convert to .icns
iconutil -c icns assets/Supervertaler.iconset -o assets/icon.icns
```

## Step 6: Create the macOS PyInstaller Spec File

Create `Supervertaler_macOS.spec`:

```bash
cat > Supervertaler_macOS.spec << 'SPECEOF'
# -*- mode: python ; coding: utf-8 -*-

import sys
import os

a = Analysis(
    ['Supervertaler.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('docs', 'docs'),
        ('modules', 'modules'),
        ('assets', 'assets'),
        ('README.md', '.'),
        ('CHANGELOG.md', '.'),
        ('FAQ.md', '.'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5',
        'tkinter',
        # CUDA/GPU - not needed
        'torch.cuda', 'torch.distributed', 'torch._C._cuda',
        'torch.backends.cuda', 'torch.backends.cudnn', 'triton',
        # Heavy ML backends
        'tensorflow', 'tensorboard', 'keras',
        # Jupyter
        'notebook', 'jupyter', 'IPython',
        # Testing/dev
        'pytest', 'unittest', 'black', 'isort', 'mypy',
        # Windows-only packages
        'keyboard', 'ahk', 'pyautogui',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Supervertaler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX not recommended on macOS
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Supervertaler',
)

app = BUNDLE(
    coll,
    name='Supervertaler.app',
    icon='assets/icon.icns',
    bundle_identifier='com.michaelbeijer.supervertaler',
    info_plist={
        'CFBundleName': 'Supervertaler',
        'CFBundleDisplayName': 'Supervertaler',
        'CFBundleVersion': '1.9.254',
        'CFBundleShortVersionString': '1.9.254',
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': 'Supervertaler uses the microphone for voice dictation.',
        'NSAppleEventsUsageDescription': 'Supervertaler uses AppleScript to send keystrokes for global hotkeys.',
        'LSMinimumSystemVersion': '13.0',
        'CFBundleDocumentTypes': [],
        'NSRequiresAquaSystemAppearance': False,  # Supports dark mode
    },
)
SPECEOF
```

## Step 7: Build the App

```bash
# Make sure the build venv is active
source .venv-build-macos/bin/activate

# Build
pyinstaller Supervertaler_macOS.spec --noconfirm --clean

# The app will be at: dist/Supervertaler.app
```

## Step 8: Test the App

```bash
# Run it directly
open dist/Supervertaler.app

# Or run from terminal to see console output (useful for debugging)
dist/Supervertaler.app/Contents/MacOS/Supervertaler
```

## Step 9: Create a DMG (Optional)

```bash
# Using create-dmg (brew install create-dmg)
create-dmg \
    --volname "Supervertaler" \
    --volicon "assets/icon.icns" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "Supervertaler.app" 150 190 \
    --hide-extension "Supervertaler.app" \
    --app-drop-link 450 190 \
    "dist/Supervertaler-1.9.254-macOS.dmg" \
    "dist/Supervertaler.app"
```

## Step 9b: Ad-hoc Code Sign (Recommended)

Ad-hoc signing is **free** (no Apple Developer account needed) and helps macOS
treat the app as a legitimate application rather than an untrusted download:



This makes the app much more likely to launch correctly when double-clicked from
Finder, especially on macOS 15+ (Sequoia) which has stricter Gatekeeper policies.

**Note:** Users who download the DMG will still need to run
 to clear the quarantine flag,
unless you have a paid Apple Developer certificate for proper notarization.

## Step 10: Grant Permissions

On first launch, macOS will ask for several permissions:

1. **Accessibility** (System Settings → Privacy & Security → Accessibility)
   - Required for global hotkeys (⌃⌘L Superlookup, ⌃⌘M QuickTrans)

2. **Microphone** (automatic prompt)
   - Required for voice commands

3. **Gatekeeper** (first launch of unsigned app)
   - Right-click → Open → "Open Anyway" on first launch
   - Or: `xattr -cr dist/Supervertaler.app` to clear quarantine flag

## Quick Build Script

For convenience, here's a one-shot build script. Save as `build_macos.sh`:

```bash
#!/bin/bash
set -e

echo "=== Supervertaler macOS Build ==="

# Activate or create venv
if [ ! -d ".venv-build-macos" ]; then
    echo "Creating build venv..."
    python3 -m venv .venv-build-macos
fi
source .venv-build-macos/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip setuptools wheel pyinstaller -q
pip install -e . -q

# Generate .icns if missing
if [ ! -f "assets/icon.icns" ]; then
    echo "Generating macOS icon..."
    mkdir -p assets/Supervertaler.iconset
    cp assets/icon_16x16.png  assets/Supervertaler.iconset/icon_16x16.png
    cp assets/icon_32x32.png  assets/Supervertaler.iconset/icon_16x16@2x.png
    cp assets/icon_32x32.png  assets/Supervertaler.iconset/icon_32x32.png
    cp assets/icon_64.png     assets/Supervertaler.iconset/icon_32x32@2x.png
    cp assets/icon_128.png    assets/Supervertaler.iconset/icon_128x128.png
    cp assets/icon_256.png    assets/Supervertaler.iconset/icon_128x128@2x.png
    cp assets/icon_256.png    assets/Supervertaler.iconset/icon_256x256.png
    sips -z 512 512 assets/icon_256.png --out assets/Supervertaler.iconset/icon_256x256@2x.png 2>/dev/null
    sips -z 512 512 assets/icon_256.png --out assets/Supervertaler.iconset/icon_512x512.png 2>/dev/null
    sips -z 1024 1024 assets/icon_256.png --out assets/Supervertaler.iconset/icon_512x512@2x.png 2>/dev/null
    iconutil -c icns assets/Supervertaler.iconset -o assets/icon.icns
    rm -rf assets/Supervertaler.iconset
    echo "Icon created: assets/icon.icns"
fi

echo "Building .app bundle..."
pyinstaller Supervertaler_macOS.spec --noconfirm --clean

echo ""
echo "=== Build Complete ==="
echo "App: dist/Supervertaler.app"
echo ""
echo "To run:  open dist/Supervertaler.app"
echo "To test: dist/Supervertaler.app/Contents/MacOS/Supervertaler"
```

Make it executable: `chmod +x build_macos.sh`

## Running from Source (Development)

If you just want to run Supervertaler from source without building:

```bash
cd ~/Dev/Supervertaler
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 Supervertaler.py
```

## Notes

- **Apple Silicon**: PyQt6 wheels are available for arm64. The build will be native.
- **Intel Macs**: Everything works identically, just slower builds.
- **Code signing**: For distribution, you'll need an Apple Developer certificate. Without it, users need to right-click → Open on first launch.
- **Notarization**: Required for distribution outside the Mac App Store. Needs `codesign` + `xcrun notarytool`.
- **Universal binary**: Not currently supported. Build on the target architecture.
