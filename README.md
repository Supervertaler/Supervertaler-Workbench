# Supervertaler Workbench

[![PyPI version](https://badge.fury.io/py/supervertaler.svg)](https://pypi.org/project/Supervertaler/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Professional AI-enhanced translation workbench** with multi-LLM support (GPT-4, Claude, Gemini, Mistral, Ollama), translation memory, glossary management, and seamless CAT tool integration (memoQ, Trados, CafeTran, Phrase, Déjà Vu).

See the [latest release notes](https://github.com/Supervertaler/Supervertaler-Workbench/releases/latest) for what changed in the most recent build.

---

## Download

Three ways to install. Pick whichever fits your setup.

| Option | Best for | Get it |
|--------|----------|--------|
| **Pip (any OS, Python 3.10+)** | Developers and power users; smallest download; updates with one command. | `pip install --upgrade supervertaler` then run `supervertaler` |
| **Windows standalone (.zip)** | Windows users who don't want to install Python. Self-contained, ~470 MB. | [Download from the latest release](https://github.com/Supervertaler/Supervertaler-Workbench/releases/latest) → `Supervertaler-vX.Y.Z-Windows.zip` |
| **macOS standalone (.dmg)** | Apple Silicon Macs (M1 / M2 / M3 / M4). Code-signed and Apple-notarised, opens without Gatekeeper warnings. | [Download from the latest release](https://github.com/Supervertaler/Supervertaler-Workbench/releases/latest) → `Supervertaler-vX.Y.Z.dmg` |

**Notes**

- **macOS Intel:** there's no Intel standalone build. Run from source after installing system Java 17 (`brew install --cask temurin@17`).
- **Linux:** pip or run from source.

### Run from source

```bash
git clone https://github.com/Supervertaler/Supervertaler-Workbench.git
cd Supervertaler-Workbench
pip install -r requirements.txt
python Supervertaler.py
```

---

## Key Features

- **Supervertaler Sidekick** - System-wide floating AI assistant (Ctrl+Alt+A from any app) with chat, text conversions, snippets, and prompt library actions
- **Multi-LLM AI Translation** - OpenAI GPT-4/5, Anthropic Claude, Google Gemini, Mistral AI, Local Ollama, OpenRouter (200+ models)
- **Translation Memory** - Fuzzy matching TM with TMX import/export
- **Glossary System** - Project/Background glossary highlighting with forbidden term marking
- **Superlookup** - Unified concordance search across TM, glossaries, MT, and web resources
- **CAT Tool Integration** - memoQ XLIFF, Trados SDLPPX/SDLRPX, CafeTran, Phrase, Déjà Vu X3
- **Voice Commands** - Hands-free control with offline voice commands (Vosk); use any dictation tool for running text
- **Document Support** - DOCX, bilingual DOCX/RTF, PDF, Markdown, plain text + built-in [Okapi Framework](https://okapiframework.org/) sidecar for industry-standard file extraction and round-trip export with full formatting preservation

---

## Documentation

| Resource | Description |
|----------|-------------|
| [Online Manual](https://docs.supervertaler.com/workbench/) | Quick start, guides, and troubleshooting |
| [Changelog](CHANGELOG.md) | Complete version history |
| [Keyboard Shortcuts](docs/guides/KEYBOARD_SHORTCUTS.md) | Shortcut reference |
| [FAQ](FAQ.md) | Common questions |
| [Similar Apps](docs/SIMILAR_APPS.md) | CotranslatorAI, TransAIde, TWAS Suite, and other translation tools |
| [Website](https://supervertaler.com) | Project homepage |

---

## Requirements

- Python 3.10+
- PyQt6
- Windows, macOS, or Linux

---

## Contributing

- [Report bugs](https://github.com/Supervertaler/Supervertaler-Workbench/issues)
- [Request features & ask questions](https://github.com/orgs/Supervertaler/discussions)
- [Contributing guide](CONTRIBUTING.md)

### Translations

Supervertaler Workbench has an early-stage i18n system (v1.10.208+). The menu bar, Settings tabs, and key group titles are translatable; dialog bodies and error messages remain in English for now.

Translation files are **XLIFF 1.2**, so translators can open them in **Trados, memoQ, Phrase, OmegaT, or Supervertaler Workbench itself** – any CAT tool that handles XLIFF (all of them do).

| Locale  | Language                          | Status                |
| ------- | --------------------------------- | --------------------- |
| `en`    | English                           | source language       |
| `zh_CN` | 简体中文 — Simplified Chinese     | scaffold (sanity-translated samples) |
| `zh_TW` | 繁體中文 — Traditional Chinese    | scaffold (untranslated) |
| `pl`    | Polski — Polish                   | scaffold (untranslated) |

Want to contribute a translation? See [`translations/TRANSLATING.md`](translations/TRANSLATING.md). Workflow: open the locale's `.xlf` in your normal CAT tool, translate, PR back. No build step on your end.

---

## About

**Supervertaler** is maintained by [Michael Beijer](https://beijer.uk), a professional translator with 30 years of experience in technical and patent translation.

- [Stargazers](https://github.com/Supervertaler/Supervertaler-Workbench/stargazers) - See who's starred the project
- [Gitstalk](https://gitstalk.netlify.app/michaelbeijer) - See what I'm up to on GitHub

**License:** MIT - Free for personal and commercial use.

---

**Current Version:** See [CHANGELOG.md](CHANGELOG.md) for the latest release notes.
