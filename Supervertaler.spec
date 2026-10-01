# -*- mode: python ; coding: utf-8 -*-

# LLM client libraries need collect_all() rather than plain hiddenimports.
# Their per-provider call methods in modules/llm_clients.py wrap imports in
# try/except ImportError, which PyInstaller's static analyzer treats as
# optional and skips. hiddenimports alone names a single module but does NOT
# pull in submodules or data files; collect_all does. Fix for issue #187.
from PyInstaller.utils.hooks import collect_all

_llm_datas, _llm_binaries, _llm_hiddenimports = [], [], []
# The first three are the LLM client libraries themselves. The rest are
# transitive deps that PyInstaller's static analysis fails to follow
# automatically (verified by inspecting the v1.9.431 first-build ZIP and
# finding openai+anthropic present but httpx/httpcore/anyio/h11/sniffio/
# distro/pydantic all missing — which would make every API call fail at
# runtime with ModuleNotFoundError). Naming them explicitly via collect_all
# guarantees they ship.
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
        # UI translation files (XLIFF 1.2). v1.10.208+ Workbench i18n MVP:
        # the .xlf files ship inside the bundle so the Language dropdown
        # in Settings → General finds them. The build script ALSO mirrors
        # them next to the .exe for end-user discoverability (so a
        # technically-inclined translator can drop a new locale's .xlf
        # in without re-running PyInstaller).
        ('translations', 'translations'),
        # Okapi sidecar (Java-based file filter service)
        ('okapi-sidecar/dist/okapi-sidecar.jar', 'okapi-sidecar'),
        ('okapi-sidecar/dist/jre', 'okapi-sidecar/jre'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PIL',
    ] + _llm_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 
        'tkinter',
        # CUDA/GPU - not needed for CPU inference
        'torch.cuda',
        'torch.distributed',
        'torch._C._cuda',
        'torch.backends.cuda',
        'torch.backends.cudnn',
        'triton',
        # Heavy ML backends not needed
        'tensorflow',
        'tensorboard',
        'keras',
        # Jupyter/notebook stuff
        'notebook',
        'jupyter',
        'IPython',
        # Testing frameworks
        'pytest',
        # NOTE: do NOT exclude 'unittest' here, even though it's only
        # used for running tests. pyparsing.testing imports unittest
        # *at module load* (and pyparsing is a transitive dep of
        # google-generativeai → google-api-core → pyparsing). Excluding
        # unittest breaks the Gemini import path with a
        # ModuleNotFoundError that gets swallowed upstream as a
        # misleading "Google AI library not installed" message – cost
        # us v1.10.33's first Gemini-using Windows tester a confused
        # debugging session. unittest only adds ~200 KB to the bundle.
        # Dev tools
        'black',
        'isort',
        'mypy',
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Supervertaler',
)
