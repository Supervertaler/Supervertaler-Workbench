# -*- mode: python ; coding: utf-8 -*-
# macOS build spec for Supervertaler
# Usage: pyinstaller Supervertaler_macOS.spec --noconfirm --clean

# Read version from pyproject.toml at build time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
with open('pyproject.toml', 'rb') as _f:
    _version = tomllib.load(_f)['project']['version']

# LLM client libraries need collect_all() rather than plain hiddenimports —
# see Supervertaler.spec for the full explanation. Issue #187.
from PyInstaller.utils.hooks import collect_all

_llm_datas, _llm_binaries, _llm_hiddenimports = [], [], []
# See Supervertaler.spec for the full explanation. Naming HTTP transitive
# deps explicitly because PyInstaller's analysis doesn't follow them from
# inside openai/anthropic's submodule tree.
for _pkg in (
    'google.generativeai', 'openai', 'anthropic',
    'httpx', 'httpcore', 'h11', 'sniffio', 'anyio',
    'distro', 'pydantic', 'jiter',
    # onnx-asr ships its audio preprocessors as .onnx data files (#198)
    'onnx_asr',
):
    _d, _b, _h = collect_all(_pkg)
    _llm_datas += _d
    _llm_binaries += _b
    _llm_hiddenimports += _h

a = Analysis(
    ['Supervertaler.py'],
    pathex=[],
    binaries=_llm_binaries,
    datas=_llm_datas + [
        ('pyproject.toml', '.'),
        ('docs', 'docs'),
        ('modules', 'modules'),
        ('assets', 'assets'),
        ('README.md', '.'),
        ('CHANGELOG.md', '.'),
        ('FAQ.md', '.'),
        # Okapi sidecar JAR (Java sidecar service)
        # The bundled JRE is intentionally NOT listed here. PyInstaller's
        # macOS binary-relocation pass extracts libjli.dylib from the JRE
        # tree out to Contents/Frameworks/libjli.dylib and rewrites its
        # load commands. The launcher then becomes incompatible with the
        # unmodified libjvm.dylib still inside the JRE — the JLI→JVM call
        # dispatches into a null function pointer and the JVM crashes at
        # init with SIGSEGV in libjli's launcher code.
        # Workaround: build_macos_signed.sh copies the JRE into the
        # bundle AFTER PyInstaller has finished, before code signing, so
        # PyInstaller never sees it. Both libjli and libjvm stay paired
        # and the JVM launches correctly. See changelog for v1.9.418.
        ('okapi-sidecar/dist/okapi-sidecar.jar', 'okapi-sidecar'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
        'PIL',
    ] + _llm_hiddenimports,
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
    upx=False,
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
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription':
            'Supervertaler uses the microphone for voice dictation.',
        'NSAppleEventsUsageDescription':
            'Supervertaler uses AppleScript to send keystrokes for global hotkeys.',
        'LSMinimumSystemVersion': '13.0',
        'CFBundleDocumentTypes': [],
        'NSRequiresAquaSystemAppearance': False,
        # Fix: Finder launches apps with minimal env (no LANG/LC_CTYPE),
        # causing locale-dependent code to crash silently.
        'LSEnvironment': {
            'LANG': 'en_US.UTF-8',
            'LC_CTYPE': 'en_US.UTF-8',
        },
    },
)
