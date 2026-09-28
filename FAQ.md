# Supervertaler Workbench - Frequently Asked Questions

**Current Version:** v1.9.180 (January 30, 2026)
**Framework:** PyQt6
**Last Updated:** January 30, 2026

Welcome to the Supervertaler Workbench FAQ! Find answers to common questions about installation, features, workflow, and troubleshooting.

---

## 📑 Table of Contents

1. [About Supervertaler](#about-supervertaler)
2. [Getting Started](#getting-started)
3. [Features & Capabilities](#features--capabilities)
4. [Workflow & Integration](#workflow--integration)
5. [Technical Questions](#technical-questions)
6. [Troubleshooting](#troubleshooting)
7. [Project Management](#project-management)
8. [Development & Community](#development--community)

---

## About Supervertaler

### What is Supervertaler?

Supervertaler is **The Ultimate Translation Workbench**. It works alongside your CAT tool (memoQ, Trados, CafeTran) to provide:

- **AI-enhanced translation** with GPT-4, Claude, and Gemini
- **Project termbases** with automatic term extraction
- **Translation memory** with fuzzy matching
- **AI Assistant** for conversational prompt generation
- **Voice dictation** (100+ languages via Whisper)
- **Unified prompt library** with 38+ built-in prompts
- **CAT tool integration** (memoQ, Trados, CafeTran)

Think of it as a **professional CAT tool** that specializes in AI integration and prompt management, designed to complement (not replace) your existing workflow.

### Who created Supervertaler?

**Michael Beijer** - Professional translator and language technology enthusiast  
**Website**: [beijer.uk](https://beijer.uk/)  
**GitHub**: [github.com/Supervertaler/Supervertaler-Workbench](https://github.com/Supervertaler/Supervertaler-Workbench)

Developed with assistance from **Claude AI** (Anthropic) using collaborative human-AI coding:
- Michael: Translation expertise, workflow design, feature requirements
- Claude: Implementation, architecture, debugging, optimization
- Framework: PyQt6 (modern Python GUI)
- Language: Python 3.12

### Who is Supervertaler for?

**Primary Users:**
- Professional translators seeking AI-enhanced workflows
- CAT tool users wanting better AI integration
- Freelance translators managing multiple language pairs
- Translation agencies handling diverse projects
- Anyone needing advanced prompt management for translation

**Not For:**
- Casual users needing quick Google Translate-style translation
- Users unwilling to manage Python/command-line tools
- Those seeking fully automated, zero-configuration translation

### How does Supervertaler compare to other translation tools?

**vs. DeepL/Google Translate:**
- ✅ Multi-provider AI (Claude, GPT-4, Gemini) vs single engine
- ✅ Custom prompts and instructions vs generic translation
- ✅ Translation memory and termbase integration
- ✅ Project-based workflow with status tracking
- ⚠️ Requires API keys (paid) vs free web interface

**vs. CAT Tools (memoQ, Trados):**
- ✅ Works *alongside* CAT tools as companion
- ✅ Better AI integration with prompt management
- ✅ Conversational AI Assistant for prompt generation
- ✅ Voice dictation built-in (100+ languages)
- ⚠️ Not a full CAT tool replacement (no alignment, LiveDocs, etc.)

**vs. AI Translation Plugins:**
- ✅ Provider-agnostic (switch between OpenAI, Claude, Gemini)
- ✅ Open source - audit and customize the code
- ✅ Advanced features: Superbench, AI Assistant, project termbases
- ✅ No vendor lock-in or monthly subscriptions

**Unique Selling Points:**
- **2-Layer Prompt Architecture** with AI-generated custom prompts
- **Project Termbases** with automatic term extraction
- **Supervoice** - F9 voice dictation in 100+ languages
- **Superbench** - Benchmark AI models on YOUR projects
- **Universal Lookup** - Ctrl+Alt+L system-wide TM search

---

## Getting Started

### What do I need to run Supervertaler?

**System Requirements:**
- **OS**: Windows 10/11, macOS 10.13+, or Linux
- **Python**: 3.8+ (Python 3.12 recommended)
- **RAM**: 4GB minimum, 8GB recommended
- **Disk**: ~500 MB
- **Internet**: Required for AI API calls

**Required API Key (at least one):**
- **Anthropic Claude** (recommended): https://console.anthropic.com/
- **OpenAI GPT**: https://platform.openai.com/
- **Google Gemini**: https://aistudio.google.com/

**Installation:**
```bash
# Clone or download from GitHub
git clone https://github.com/Supervertaler/Supervertaler-Workbench.git
cd Supervertaler

# Install dependencies
pip install -r requirements.txt

# Setup API keys
cp api_keys.example.txt api_keys.txt
# Edit api_keys.txt with your keys

# Launch
python Supervertaler.py
```

See [INSTALLATION.md](INSTALLATION.md) for detailed instructions.

### How much does it cost?

**Supervertaler Itself:**
- ✅ **100% Free** - MIT License, open source
- ✅ No subscriptions, no hidden fees
- ✅ Commercial use allowed

**AI API Costs (Pay-as-you-go):**
- **Claude 3.5 Sonnet**: ~$3 per million input tokens, $15 per million output tokens
- **GPT-4o**: ~$2.50 per million input tokens, $10 per million output tokens
- **Gemini 2.0 Flash**: Generous free tier, then ~$0.075/$0.30 per million tokens

**Typical Costs:**
- Average project (10,000 words): $0.50 - $2.00
- Heavy user (100,000 words/month): $5 - $20/month
- Much cheaper than DeepL Pro ($9.49/month) or CAT tool subscriptions

### How do I install Supervertaler?

```bash
# 1. Install Python 3.12 from python.org

# 2. Download Supervertaler
git clone https://github.com/Supervertaler/Supervertaler-Workbench.git
cd Supervertaler

# 3. Install dependencies
pip install -r requirements.txt

# 4. Setup API keys
cp api_keys.example.txt api_keys.txt
# Edit api_keys.txt with your keys

# 5. Launch
python Supervertaler.py
```

Full guide: [INSTALLATION.md](INSTALLATION.md)

### How do I get API keys?

**Anthropic Claude (Recommended):**
1. Visit: https://console.anthropic.com/
2. Sign up or log in
3. Settings → API Keys → "Create Key"
4. Copy key (starts with `sk-ant-api03-...`)
5. Add to `api_keys.txt`: `ANTHROPIC_API_KEY=sk-ant-api03-...`

**OpenAI GPT:**
1. Visit: https://platform.openai.com/
2. Sign up or log in
3. API Keys section → "Create new secret key"
4. Copy key (starts with `sk-proj-...` or `sk-...`)
5. Add to `api_keys.txt`: `OPENAI_API_KEY=sk-proj-...`

**Google Gemini:**
1. Visit: https://aistudio.google.com/
2. Sign up or log in
3. Click "Get API Key"
4. Copy key (starts with `AIza...`)
5. Add to `api_keys.txt`: `GOOGLE_API_KEY=AIza...`

**Security Tip:** Never commit `api_keys.txt` to version control!

---

## Features & Capabilities

### What are the key features of v1.9.27?

**📄 Simple Text File Import/Export (NEW in v1.9.27)**
- Import plain text files where each line becomes a source segment
- Export translations as matching text file with target text
- UTF-8, Latin-1, Windows-1252 encoding support with auto-detection
- Perfect for translating simple line-by-line text content
- File → Import → Simple Text File (TXT) / File → Export → Simple Text File - Translated (TXT)

**🔄 Automatic Model Version Checker (v1.9.26)**
- Auto-detects new LLM models from OpenAI, Anthropic, and Google
- Checks once per 24 hours on startup (configurable)
- Popup dialog with easy model selection when new models detected
- Settings → AI Settings → Model Version Checker
- Manual "Check for New Models Now" button

**🎨 UI Standardization (NEW in v1.9.26)**
- All checkboxes standardized to green with white checkmark design
- Refined 16x16px size for cleaner, modern appearance
- Consistent visual language throughout entire application

**📁 Custom File Extensions (v1.9.6)**
- Branded file extensions: `.svproj` (projects), `.svprompt` (prompts), `.svntl` (non-translatables)
- Full backward compatibility - opens legacy `.json`, `.md`, `.ntl` files
- Industry standards retained: `.tmx` for TM exports

**🌐 Monolingual DOCX Import (v1.9.6)**
- Language pair selection dialog when importing monolingual DOCX files
- Explicitly set source and target languages (12 languages supported)
- No more unreliable auto-detection

**📤 Target-Only DOCX Export (v1.9.6)**
- Export > Target Only (DOCX)... preserves original document structure
- Tables, formatting, styles, headers/footers all preserved
- Original DOCX path saved in project for reliable exports

**📤 Send Segments to TM (v1.9.5)**
- Bulk send translated segments to Translation Memories
- Access via Edit > Bulk Operations > Send Segments to TM
- Filter by scope: All segments, Current selection, or row range
- Filter by status: Translated, Reviewed, Approved, Needs Review, Final
- Select multiple TMs to write to simultaneously

**🏷️ memoQ Tag Shortcuts (v1.9.5)**
- **Ctrl+,** - Insert next memoQ tag pair or wrap selection with tags
- Smart detection of memoQ tags from source: `[1}`, `{1]`, `[3]`, etc.
- With selection: Wraps text with next unused tag pair
- Without selection: Inserts tag pair at cursor position

**🏷️ Tag-Based Formatting System (v1.9.4)**
- Import memoQ bilingual DOCX preserves bold/italic/underline as `<b>`, `<i>`, `<u>` tags
- Toggle between WYSIWYG and Tag view with **Ctrl+Alt+T**
- **Ctrl+B/I/U** shortcuts to apply formatting tags to selected text
- AI translation preserves and repositions tags intelligently
- Export converts tags back to Word formatting

**🔍 Filter Highlighting (v1.7.8)**
- Type search term in "Filter Source" or "Filter Target" box
- Matching segments highlighted in yellow
- Case-insensitive, supports multiple matches per cell
- Press Enter to apply filter

**🎯 Termbase Display Customization (v1.7.7)**
- Sort termbase matches: order of appearance, alphabetical, or by length
- Hide shorter matches contained within longer terms
- Settings → General → TM/Termbase Options

**📚 Project Termbases (v1.7.0)**
- One dedicated termbase per project
- Automatic term extraction from source segments
- Pink highlighting for project terms vs blue for background termbases
- Extract Terms button in Termbases tab

**💾 Auto-Backup System (v1.7.6)**
- Configurable intervals (1-60 minutes, default 5 min)
- Automatic project.json and TMX backups
- Never lose your work during translation

**🤖 AI Assistant**
- Conversational interface in Prompt Manager tab
- Analyze documents and generate custom prompts
- Attach files (PDF, DOCX, TXT, MD) for context
- Ask questions about segments and terminology

**🎤 Supervoice Voice Dictation**
- F9 global hotkey (press-to-start/stop)
- 100+ language support via OpenAI Whisper
- 5 model sizes (tiny to large)
- Dictate directly into target cells

**📊 Superbench (LLM Benchmarking)**
- Test GPT-4o, Claude, Gemini on YOUR projects
- chrF++ scoring with adaptive segment sampling
- Excel export with side-by-side comparison
- Find best model for your language pair/domain

**💾 Translation Memory**
- SQLite-based TM with FTS5 full-text search
- Fuzzy matching with visual diff
- TMX import/export
- Auto-propagation of exact matches

**🔍 Universal Lookup**
- Ctrl+Alt+L system-wide hotkey
- Search TM from ANY application
- Instant terminology lookup
- Works in Word, browser, email, etc.

**📝 TMX Editor**
- Database-backed professional TMX editor
- Handle massive 1GB+ TMX files
- Filter, search, bulk edit entries

### What are the three view modes?

Supervertaler offers three professional editing views:

**1. Grid View** (Default)
- Spreadsheet-like segment editor
- Columns: #, Source, Target, Status
- Inline editing, multi-selection
- Best for: segment-by-segment translation

**2. Document View**
- Natural document reading flow
- Paragraphs grouped intelligently
- Best for: final review, readability check

**3. List View**  
- Vertical stack of segment cards
- Full text visible without scrolling
- Best for: quick overview and context

**Switch views:** View menu or Home tab view mode buttons

### What is the Unified Prompt Library?

**2-Layer Prompt Architecture:**

**Layer 1: System Prompts**
- Core translation infrastructure
- CAT tag handling, formatting rules
- Settings → System Prompts

**Layer 2: Custom Prompts**
- 38+ built-in prompts in organized folders
- Domain expertise (Legal, Medical, Financial, Technical)
- Project-specific instructions
- Style guides

**Key Features:**
- **Multi-attach**: Combine multiple prompts
- **Favorites**: Star frequently-used prompts
- **AI-generated**: Ask AI Assistant to create prompts
- **Unlimited folders**: Organize your way

**Usage:**
1. Prompt Manager tab → Browse folders
2. Right-click prompt → "Set as Primary" or "Attach"
3. Translate normally - prompts automatically applied

See: [UNIFIED_PROMPT_LIBRARY_GUIDE.md](../UNIFIED_PROMPT_LIBRARY_GUIDE.md)

### What is figure/image context support?

Load images to provide visual context for AI translation - essential for technical documents.

**How it works:**
1. Prepare folder with images: `Figure 1.png`, `Figure 2A.jpg`, etc.
2. Resources → 🖼️ Load Figure Context → Select folder
3. When AI detects "Figure 1" in text, it includes the image automatically
4. AI can "see" diagrams, understand part numbers, spatial relationships

**Example:**
- Text: "As shown in Figure 1A, the motor (12) connects to shaft (14)..."
- AI receives: Text + Image of Figure 1A
- Result: Accurate translation of technical details with visual understanding

**Supported formats:** PNG, JPG, JPEG, WEBP, GIF, BMP, TIFF

### How does the translation memory work?

**SQLite-based TM:**
- Full-text search with FTS5
- Fuzzy matching algorithm
- Stores source + target + metadata

**Features:**
- **Auto-save:** Confirmed segments saved to active TMs
- **Fuzzy matching:** 70-99% matches with visual diff
- **Multiple TMs:** Activate/deactivate per project
- **TMX import/export:** Standard format compatibility

**Match display:**
- 100% match: Green
- 95-99% match: Light green with diff highlighting
- 70-94% match: Yellow with diff highlighting
- Match shortcuts: Ctrl+1-9 to insert

**Settings:**
- Settings → TM/Termbase Options
- Enable/disable matching
- Configure auto-propagation

### How do keyboard shortcuts work?

**Main Translation:**
- F5: Translate selected segments (or active segment if none selected)
- F9: Start/stop voice dictation (global hotkey - works in any app)
- Ctrl+Alt+L: Universal Lookup (system-wide TM search)
- Ctrl+P: Open Unified Prompt Library

**Navigation:**
- Ctrl+1/2/3: Switch between Grid/Document/List views
- Arrow keys: Navigate between segments in Grid view
- Page Up/Down: Scroll through segments

**Editing:**
- Double-click cell: Edit segment in-place (Grid view)
- Ctrl+C/V: Copy/paste segments
- Ctrl+F: Find & Replace dialog
- Ctrl+Z: Undo (where supported)

**TM Shortcuts:**
- Ctrl+1-9: Insert TM match (1=best match, 2=second best, etc.)
- Ctrl+M: Show TM matches for active segment

**Formatting (v1.9.4+):**
- Ctrl+B: Apply/toggle bold tags `<b>...</b>`
- Ctrl+I: Apply/toggle italic tags `<i>...</i>`
- Ctrl+U: Apply/toggle underline tags `<u>...</u>`
- Ctrl+Alt+T: Toggle Tag view (show/hide formatting tags)
- Ctrl+,: Insert memoQ tag pair or wrap selection with tags

**Project:**
- Ctrl+S: Save project
- Ctrl+O: Open project
- Ctrl+N: New project

See: Full keyboard shortcut list in [INSTALLATION.md](INSTALLATION.md#keyboard-shortcuts)

### What is CAT tag handling?

**What are CAT tags?**
- Formatting markers from CAT tools: `<b>text</b>`, `{1}`, `[tag]`
- Represent bold, variables, placeholders, etc.
- Must be preserved exactly in translation

**How Supervertaler handles them:**
1. **Detection**: System prompts identify common CAT tag patterns
2. **Instruction**: AI told to preserve ALL tags unchanged
3. **Validation**: Tags verified after translation (future feature)
4. **Recovery**: If AI modifies tags, manual correction needed

**Supported tag formats:**
- XML-style: `<tag>content</tag>`
- Curly braces: `{1}`, `{placeholder}`
- Square brackets: `[tag]`, `[0]`
- memoQ: `{0>text<0}`, `{1/}`
- Trados: `<cf>`, `<c0>content</c0>`

**Best practice:**
- Use specialized system prompts (memoQ, Trados, etc.)
- Review tag preservation after AI translation
- Lock segments with complex tags after confirming correctness

---

## Technical Questions

### What AI models are supported?

**OpenAI:**
- GPT-4o (recommended - best balance of speed/quality)
- GPT-4 Turbo
- GPT-4
- GPT-3.5 Turbo (economical option)

**Anthropic Claude:**
- Claude 3.5 Sonnet (excellent for translation)
- Claude 3.5 Haiku (fast and economical)
- Claude 3 Opus (highest capability, slower)
- Claude 3 Sonnet
- Claude 3 Haiku

**Google Gemini:**
- Gemini 1.5 Pro (2M token context window)
- Gemini 1.5 Flash (cost-effective)
- Gemini 2.0 Flash Exp (experimental)

**Recommendations:**
- **Best Quality:** Claude 3.5 Sonnet, GPT-4o
- **Best Value:** Gemini 1.5 Flash, Claude 3.5 Haiku
- **Best Context:** Gemini 1.5 Pro (2M tokens)
- **General Use:** GPT-4o or Claude 3.5 Sonnet

Test models with Superbench to find best for your language pair/domain!

### How does AI translation work in Supervertaler?

**Context Provision:**
Supervertaler provides AI with multiple layers of context:

1. **Document context:** Surrounding segments for coherence
2. **Translation Memory:** Fuzzy matches from active TMs
3. **Termbase matches:** Relevant terminology highlighted
4. **System prompt:** CAT tag handling instructions
5. **Custom prompts:** Domain expertise, style guides
6. **Figure context:** Images referenced in technical documents

**Translation Process:**
1. You select segments and press F5 (or Translate button)
2. Supervertaler assembles context: TM matches, termbase, prompts, figures
3. Sends source + context to AI API
4. AI generates translation following all instructions
5. Translation inserted into target cell
6. Confirmed segments auto-saved to active TMs

**Why this approach is powerful:**
- AI understands document flow and terminology
- Maintains consistency with your TM and termbases
- Adapts to domain-specific requirements
- Preserves CAT tags and formatting
- Cost-effective: only translates what you need

### What file formats are supported?

**Import Formats:**
- **DOCX:** Microsoft Word documents (segment by paragraph)
- **TXT:** Plain text files (segment by line or paragraph)
- **TMX:** Translation Memory eXchange format
- **XLIFF:** XML Localization Interchange File Format
- **JSON:** Project files (.json)
- **Images:** PNG/JPG/JPEG/WEBP/GIF/BMP/TIFF (figure context)

**Export Formats:**
- **DOCX:** Microsoft Word with translations
- **TMX:** Translation memory export
- **XLIFF:** XML format for CAT tools
- **TXT:** Plain text export
- **Excel (XLSX):** Spreadsheet with source/target columns

**CAT Tool Integration:**
- CafeTran bilingual DOCX (import/export)
- memoQ bilingual DOCX (import/export)
- Universal TMX (all CAT tools)
- XLIFF (most CAT tools)

### What programming language is it built with?

**Primary:** Python 3.8+

**Key Libraries:**
- **PyQt6:** Modern GUI framework
- **SQLite3:** Translation memory database
- **python-docx:** DOCX file handling
- **lxml:** XLIFF and XML processing
- **openai:** OpenAI API client
- **anthropic:** Claude API client
- **google-generativeai:** Gemini API client
- **openai-whisper (optional):** Offline/local voice dictation (very large dependency; installs PyTorch). The default/recommended path uses the OpenAI Whisper API via `openai`.
- **Pillow (PIL):** Image processing for figure context

**Architecture:**
- Modular design: 50+ modules in `modules/` folder
- Main file: `Supervertaler.py` (21,600+ lines)
- Database-backed TM with FTS5 full-text search
- Event-driven UI with PyQt6 signals/slots

**Why Python?**
- Rapid development
- Excellent AI/ML library ecosystem
- Cross-platform (Windows/Mac/Linux)
- Easy to read and contribute to

### Can I use Supervertaler offline?

**Partially:**

**Online Features (require internet):**
- AI translation via OpenAI/Claude/Gemini APIs
- Superbench model testing
- AI Assistant (document analysis, prompt generation)

**Offline Features:**
- Edit existing translations in Grid/Document/List views
- Search TM and termbases (if already loaded)
- Export to DOCX/TMX/XLIFF
- TMX Editor (work with TM files)
- Project management
- All UI features except AI translation

**Voice Dictation (Supervoice):**
- Fully offline if using "tiny" or "base" Whisper models
- Downloads once, runs locally on your machine
- No internet required after initial model download

**Local LLM (Ollama):**
- Fully offline AI translation on your own computer
- No API keys or internet required
- Complete privacy - your text never leaves your machine
- See "How do I set up Local LLM?" below

### How do I set up Local LLM (Ollama)?

Local LLM allows you to run AI translation entirely on your computer - no API costs, complete privacy, works offline.

**Step 1: Install Ollama**
1. Download Ollama from https://ollama.com
2. Run the installer (Windows/Mac/Linux supported)
3. Ollama starts automatically in the background

**Step 2: Download a Model**
Open a terminal/command prompt and run:
```bash
# Recommended – purpose-built translation model (12GB+ RAM):
ollama pull translategemma:12b

# For 24GB+ RAM (highest translation quality):
ollama pull translategemma:27b

# For 6GB RAM (lighter, still translation-tuned):
ollama pull translategemma:4b
```

**Step 3: Configure Supervertaler**
1. Go to **Settings → LLM Settings**
2. Select **"🖥️ Local LLM (Ollama)"** as your provider
3. Choose your downloaded model from the dropdown
4. Click **Save LLM Settings**

**Model Recommendations by RAM:**
| RAM | Recommended Model | Quality |
|-----|------------------|---------|
| 4 GB | qwen3:4b | ★★★☆☆ |
| 6 GB | translategemma:4b | ★★★★☆ |
| 12 GB | translategemma:12b | ★★★★★ |
| 24 GB+ | translategemma:27b | ★★★★★ |

**Tips:**
- First translation may take 30-60 seconds (model loading)
- Subsequent translations are much faster
- Use the **Setup...** button in Settings for guided installation
- GPU acceleration is automatic if you have NVIDIA/AMD GPU
- Translations timing out on a computer without a dedicated GPU? Raise **Request timeout** under **Settings → AI Settings → Local LLM (Ollama) Advanced Settings** (up to 24 hours; "Automatic" is 3–10 minutes depending on model size)

### What are the system requirements?

**Minimum:**
- **OS:** Windows 10/11, macOS 10.15+, or Linux
- **Python:** 3.8 or higher
- **RAM:** 4 GB (8 GB recommended for large projects)
- **Disk:** 500 MB for application + space for TM/projects
- **Internet:** Required for AI translation

**Recommended:**
- **RAM:** 8 GB or more
- **CPU:** Multi-core processor for faster processing
- **Disk:** SSD for faster TM searches
- **Display:** 1920x1080 or higher resolution

**For Voice Dictation (Supervoice):**
- **RAM:** 8 GB minimum (16 GB for "large" model)
- **Disk:** 1-3 GB for Whisper models (varies by size)
- **Microphone:** Any USB or built-in microphone

**For Local LLM (Ollama):**
- **RAM:** 4 GB minimum (8 GB+ recommended)
- **Disk:** 2-10 GB per model (varies by size)
- **GPU:** Optional but recommended (NVIDIA/AMD for faster inference)
- **Internet:** Only needed for initial model download

**For Superbench:**
- Multiple API keys (GPT-4o, Claude, Gemini)
- Sufficient API credits for testing runs

### Why is my antivirus flagging Supervertaler as malware?

This is a **known false positive** that affects applications built with PyInstaller (the tool used to create the Windows EXE).

**Why it happens:**
- PyInstaller bundles Python + all libraries into a single package
- This binary pattern triggers heuristic detection in some AV software (especially Norton 360)
- Generic detections like "Win64:Malware-gen" are pattern matches, not actual malware signatures
- The executable is not code-signed (certificates cost ~$300/year)

**Solutions:**

1. **Use pip install (Recommended)**
   - Install: `pip install supervertaler`
   - Run: `supervertaler`
   - No AV warnings because it runs from Python source

2. **Whitelist the application**
   - Add an exception in your antivirus settings
   - The application is safe - you can verify by checking the [source code on GitHub](https://github.com/Supervertaler/Supervertaler-Workbench)

3. **Report as false positive**
   - Norton: https://submit.norton.com/
   - Windows Defender: Click "More info" → "Run anyway"
   - Other AV: Check vendor's false positive submission page

**This is common for PyInstaller apps** - it affects tools like Calibre, many game mods, and scientific software. See the [PyInstaller FAQ](https://pyinstaller.org/en/stable/faq.html) for more details.

---

## Workflow & Integration

### How do I integrate Supervertaler with my CAT tool?

**Method 1: TMX Exchange (Most Compatible)**
1. **In CAT tool:** Export source segments as TMX
2. **In Supervertaler:** Import TMX → Translate → Export TMX
3. **In CAT tool:** Import translated TMX
4. **Result:** Target segments populated in your CAT project

**Method 2: XLIFF (Standard)**
1. **In CAT tool:** Export XLIFF file
2. **In Supervertaler:** Import XLIFF → Translate → Export XLIFF
3. **In CAT tool:** Import translated XLIFF
4. **Result:** Translations with segment metadata preserved

**Method 3: Copy-Paste (Quick)**
1. **In CAT tool:** Copy source segments
2. **In Supervertaler:** Paste as TXT → Import → Translate → Copy targets
3. **In CAT tool:** Paste translations
4. **Result:** Fast but manual, no metadata

**Method 4: CAT Tool-Specific**
- **CafeTran:** Bilingual DOCX import/export
- **memoQ:** Bilingual DOCX import/export
- Check documentation for CAT tool-specific formats

### What's the typical translation workflow?

**Quick Translation (Simple Document):**
1. Launch Supervertaler
2. File → Import → Select DOCX or TXT
3. Select segments (or Ctrl+A for all)
4. Press F5 to translate
5. Review and edit translations
6. File → Export → DOCX

**Professional Project Workflow:**
1. **Setup:**
   - Create new project (Ctrl+N)
   - Import document (DOCX/XLIFF/TMX)
   - Activate relevant TMs (TM tab)
   - Activate termbases (Termbases tab)
   - Select prompts (Prompt Manager tab)

2. **Translate:**
   - Select segments to translate
   - Press F5
   - AI translates with TM/termbase/prompt context
   - Segments auto-saved to active TMs

3. **Review & Edit:**
   - Switch to Document View for readability check
   - Use Grid View for detailed editing
   - Check termbase matches (blue/pink highlighting)
   - Insert TM matches manually if preferred (Ctrl+1-9)

4. **Quality Assurance:**
   - Search for specific terms (Ctrl+F)
   - Check consistency of key terminology
   - Review AI Assistant suggestions

5. **Export & Deliver:**
   - Export to DOCX/TMX/XLIFF
   - Save project (Ctrl+S) for future reference
   - Deliver to client or reimport to CAT tool

**Voice Dictation Workflow:**
1. Press F9 to start dictating (works system-wide)
2. Speak your translation
3. Press F9 to stop and insert text
4. Edit as needed in Grid View

### How do I handle large documents?

**Strategy 1: Segment-by-Segment**
- Grid View with 50 segments per page
- Translate one page at a time
- Full control over progress
- Best for: Documents requiring careful review

**Strategy 2: Batch Translation**
- Select all segments (Ctrl+A)
- Press F5 to translate all at once
- AI processes in batches automatically
- Best for: Clean source documents with good TM coverage

**Strategy 3: Selective Translation**
- Use Filter Source/Target to find untranslated segments
- Select filtered results
- Translate only what's needed
- Best for: Partially translated documents

**Performance Tips:**
- Auto-backup saves progress every 5 minutes (default)
- TM matching is fast (SQLite FTS5 full-text search)
- Large projects (10,000+ segments) still load quickly
- Export to TMX regularly as backup

**Cost Management:**
- Use Superbench to test which model gives best value for your content
- Consider using Gemini Flash (economical) for first draft
- Then use Claude 3.5 Sonnet or GPT-4o for final polish
- Check TM matches before translating (100% matches = free)

### Can I customize the AI prompts?

**Yes!** The Unified Prompt Library provides complete control:

**Built-in Prompts (38+):**
- Organized in folders: Legal, Medical, Financial, Technical, Creative, etc.
- Domain-specific expertise pre-configured
- Style guides for common requirements
- CAT tag handling templates

**Custom Prompts:**
1. Prompt Manager tab → Right-click folder → New Prompt
2. Write your instructions in plain language
3. Save to appropriate folder
4. Right-click prompt → "Set as Primary" or "Attach"

**AI-Generated Prompts:**
1. AI Assistant tab → Attach document (PDF/DOCX)
2. Ask: "Analyze this style guide and create a prompt"
3. AI generates custom prompt based on document
4. Save and use for your projects

**Multi-Attach:**
- Attach multiple prompts simultaneously
- Example: Legal domain + Client style guide + Formality preference
- All prompts combined and sent to AI

**System Prompts:**
- Core CAT tag handling instructions
- Manages formatting preservation
- Edit in Settings → System Prompts

**Variables:**
- `{source_lang}` - Automatically filled with source language
- `{target_lang}` - Automatically filled with target language
- `{client_name}` - Custom variable (if supported)

See: [UNIFIED_PROMPT_LIBRARY_GUIDE.md](../UNIFIED_PROMPT_LIBRARY_GUIDE.md) for full details

### How do I use Universal Lookup?

**System-Wide TM Search:**
1. Activate Universal Lookup: Settings → General → Enable Universal Lookup
2. Press Ctrl+Alt+L from ANY application (Word, browser, email, etc.)
3. Select text you want to look up
4. Press Ctrl+Alt+L
5. Supervertaler searches active TMs and shows matches in popup

**Use Cases:**
- Check terminology while writing emails
- Look up translations in Word documents
- Verify terms while browsing source documents
- Quick TM search without opening Supervertaler

**Requirements:**
- Supervertaler running in background
- At least one TM activated in TM tab
- Text selected before pressing hotkey

---

## Troubleshooting

### The UI text looks too small on Linux/macOS. How do I fix it?

Qt applications can sometimes render with smaller fonts on Linux and macOS, especially on high-DPI displays. Supervertaler includes a **Global UI Font Scale** setting to fix this:

1. Go to **Settings → View** tab
2. Find the **🖥️ Global UI Font Scale** section
3. Adjust the slider from 50% to 200% (default is 100%)
4. Click **Apply** to see changes immediately
5. Click **Save View Settings** to persist your choice

**Recommended values:**
- **Linux with HiDPI:** Try 120-150%
- **macOS Retina:** Usually 100% is fine, try 110-120% if needed
- **4K displays:** Try 130-150% depending on screen size
- **Accessibility needs:** Up to 200% for maximum readability

This setting affects menus, buttons, labels, tabs, and all other UI text throughout the application.

### Why is translation slow?

**Common Causes:**
1. **Model choice:** Claude 3 Opus, GPT-4 are slower than Flash/Haiku variants
2. **Large context:** Many TM matches, large document, figure images
3. **Internet speed:** Slow connection increases API response time
4. **Provider rate limits:** Some API plans throttle requests
5. **Many segments:** Translating 1000+ segments takes time

**Solutions:**
- **Try faster models:** Gemini 1.5 Flash, Claude 3.5 Haiku, GPT-3.5 Turbo
- **Reduce context:** Deactivate unused TMs, remove unnecessary termbases
- **Translate in batches:** Select 50-100 segments at a time instead of all
- **Check internet:** Use wired connection if possible
- **Upgrade API plan:** Higher-tier plans have better rate limits

**Use Superbench** to test which model gives best speed/quality balance for your content!

### API errors - what do they mean?

**"Invalid API Key" / "Authentication Failed":**
- Check `api_keys.txt` for typos
- Ensure format: `openai_key=sk-...` or `anthropic_key=sk-ant-...` or `gemini_key=AI...`
- Verify key is active on provider dashboard
- Check no extra spaces or quotes around key

**"Rate Limit Exceeded":**
- Too many requests too quickly
- Wait 1-2 minutes and retry
- Upgrade to higher API tier for increased limits
- Translate smaller batches

**"Context Length Exceeded" / "Token Limit":**
- Document + TM matches + prompts + images exceed model's context window
- **Solution 1:** Use model with larger context (Gemini 1.5 Pro: 2M tokens)
- **Solution 2:** Translate fewer segments at once
- **Solution 3:** Reduce number of TM matches shown
- **Solution 4:** Remove figure context if not needed

**"Insufficient Credits" / "Quota Exceeded":**
- API account out of credits
- Add funds to provider billing dashboard
- Check current balance and usage

**"Connection Error" / "Timeout":**
- Internet connection lost
- Firewall blocking API requests
- Try different network or VPN
- Check antivirus isn't blocking connections

### Filter highlighting not working

**Issue:** Text doesn't highlight when I type in Filter Source/Target box

**Solution:**
- Make sure you press **Enter** after typing search term
- Filter applies when you press Enter, not automatically while typing
- Check spelling of search term (case-insensitive but must match)

**Issue:** No results found but I know the term exists

**Solution:**
- Ensure you're searching in correct column (Source vs Target)
- Check for typos in search term
- Try simpler search (single word instead of phrase)

### Termbase matches not showing

**Issue:** Termbase loaded but no blue/pink highlighting

**Solution:**
1. Check termbase is activated: Termbases tab → Termbase checkbox should be checked
2. Ensure terms exist in termbase: Termbases tab → Right-click → View Terms
3. Check if source segments actually contain termbase terms
4. Try Extract Terms button if using project termbase

**Issue:** Too many/too few matches highlighted

**Solution:**
- Settings → General → TM/Termbase Options
- Adjust "Hide shorter matches" setting
- Change sort order (appearance / alphabetical / length)
- Create more specific termbase entries

### Voice dictation (Supervoice) not working

**Issue:** F9 doesn't start recording

**Solution:**
1. Check Supervoice is enabled: Settings → General → Enable Supervoice
2. Ensure Whisper model is downloaded: First use downloads model automatically
3. Check microphone permissions (Windows: Settings → Privacy → Microphone)
4. Test microphone in other apps to verify it works

**Issue:** Recording starts but no text appears

**Solution:**
- Wait a moment - processing takes a few seconds
- Check if you spoke loudly/clearly enough
- Try larger Whisper model (base or small instead of tiny)
- Ensure microphone volume is adequate

**Issue:** Wrong language recognized

**Solution:**
- Supervoice uses target language by default
- Make sure target language is set correctly for your project
- Some accents may be harder to recognize - try clearer pronunciation

### Universal Lookup not working

**Issue:** Ctrl+Alt+L does nothing

**Solution:**
1. Check Universal Lookup is enabled: Settings → General → Enable Universal Lookup
2. Ensure Supervertaler is running (doesn't need to be focused window)
3. Make sure you selected text before pressing Ctrl+Alt+L
4. Check if another program is using the same hotkey

**Issue:** "No matches found"

**Solution:**
- Ensure at least one TM is activated in TM tab
- Check selected text actually exists in your TMs
- Try selecting less text (single term instead of full sentence)

### How do I report a bug?

**Before Reporting:**
1. Check if issue is already known: [GitHub Issues](https://github.com/Supervertaler/Supervertaler-Workbench/issues)
2. Update to latest version (current: v1.7.8)
3. Update Python libraries: `pip install -r requirements.txt --upgrade`

**When Reporting:**
1. **Version info:**
   - Supervertaler version (shown in window title)
   - Python version: `python --version`
   - OS: Windows 10/11, macOS version, or Linux distro

2. **Error details:**
   - Exact error message (screenshot if possible)
   - Steps to reproduce the bug
   - What you expected vs what happened

3. **Context:**
   - Which feature were you using? (AI translation, voice dictation, TM, etc.)
   - File formats involved (DOCX, TMX, XLIFF, etc.)
   - Any relevant settings or configuration

**Submit to:** [github.com/Supervertaler/Supervertaler-Workbench/issues](https://github.com/Supervertaler/Supervertaler-Workbench/issues)

**Response time:** Typically 1-3 days depending on complexity

---

## Development & Community

### Why was Supervertaler created?

**Problem:**
- Existing CAT tools had limited or expensive AI integration
- Needed flexible multi-provider access (OpenAI, Claude, Gemini)
- Wanted custom prompt control and termbase integration
- Required context-aware translation, not just sentence-by-sentence MT

**Solution:**
Michael Beijer, a professional translator, created Supervertaler to:
- Provide powerful AI translation with full context
- Enable custom prompts and domain expertise
- Support multiple AI providers (avoid vendor lock-in)
- Integrate with existing CAT tools (TMX, XLIFF, bilingual DOCX)
- Offer professional features (TM, termbases, voice dictation)
- Share openly with translation community (open source)

**Philosophy:**
- Human translator + AI assistant = best results
- Flexibility over lock-in
- Context matters for quality
- Professional control essential
- Transparent and customizable

### What's the development history?

**v1.0 - v1.5 (Early 2025):**
- Initial prototype with tkinter GUI
- OpenAI GPT-4 translation
- Basic DOCX import/export
- Proof of concept

**v1.6 (September 2025):**
- Migration to PyQt6 (modern GUI framework)
- Complete UI rewrite
- Professional CAT tool interface
- Grid/Document/List views

**v1.7.0 (October 2025):**
- Project termbases (dedicated termbase per project)
- Improved TM fuzzy matching
- Auto-backup system
- UI refinements

**v1.7.6 (November 2025):**
- Configurable auto-backup intervals
- Enhanced error handling
- Performance optimizations

**v1.7.7 (November 2025):**
- Termbase display customization
- Sort options (appearance/alphabetical/length)
- Hide shorter matches setting

**v1.7.8 (November 22, 2025):**
- Filter highlighting with yellow background
- Bug fixes and stability improvements

**v1.9.16 (December 2025):**
- **Local LLM support (Ollama)** - Run AI translation offline on your computer
- No API keys needed for local models
- Complete privacy - text never leaves your machine
- Automatic hardware detection and model recommendations

**v1.9.27 (December 9, 2025):**
- **Simple Text File Import/Export** - Import TXT files line-by-line, translate, export
- Language pair selection and multiple encoding options
- Perfect for simple text translation workflows

**v1.9.26 (December 8, 2025):**
- **Automatic Model Version Checker** - Auto-detects new LLM models from providers
- Daily checks with popup notifications for new models
- Manual "Check Now" button in AI Settings
- **UI Standardization** - All checkboxes use consistent green design (16x16px)
- Cleaner, more professional appearance throughout application

**Future Plans:**
- Advanced QA tools
- Enhanced collaboration features
- More CAT tool integrations
- Machine learning for MT quality prediction

### Why is it open source?

**Benefits:**
1. **Transparency:** No hidden data collection, all code inspectable
2. **Community:** Translators worldwide can contribute and improve
3. **Customization:** Adapt to your specific workflow needs
4. **Trust:** See exactly what the software does
5. **Learning:** Educational resource for AI-assisted development
6. **Longevity:** Community can maintain even if original developer moves on

**License:** Check LICENSE file in repository

**Contributions Welcome:**
- Bug reports (GitHub Issues)
- Feature requests
- Code contributions (Pull Requests)
- Documentation improvements
- Translation of UI to other languages
- Testing and feedback

### Why the name "Supervertaler"?

**Etymology:**
- "Vertaler" = "translator" in Dutch
- "Super" = English prefix meaning "excellent/above"
- "Supervertaler" ≈ "Super Translator"

**Pronunciation:**
- Dutch: "SOO-per-fer-TAH-ler"
- English: "SUPER-ver-TAY-ler" (close enough!)

**Origin:**
- Created by Michael Beijer (Dutch translator)
- Reflects Netherlands origin
- Mix of English + Dutch = international appeal
- Memorable and unique name

---

## Privacy & Commercial Use

### Is my translation data private?

**Data Flow:**
- Source text sent to AI provider (OpenAI/Claude/Gemini) for translation
- Translations received back from AI provider
- **No data sent to Supervertaler developers**
- All data stored locally on your computer

**Privacy by Provider:**
- **OpenAI:** Does not train on API data (per policy as of 2025)
- **Anthropic:** Does not train on API data (per policy)
- **Google Gemini:** Check current API terms for data retention

**Local Storage:**
- Projects: `user_data/projects/`
- TMs: `user_data/resources/`
- Termbases: `user_data/resources/`
- All data remains on your machine

**No Telemetry:**
- Supervertaler collects NO usage data
- No analytics, no tracking
- Open source: Verify in source code yourself

**Best Practices for Sensitive Content:**
- Review AI provider data policies before using
- Consider anonymizing client names, personal data
- Use providers with strong privacy commitments
- For highly confidential work, wait for local LLM support (future)
- Keep local backups of all files

### Can I use Supervertaler commercially?

**Yes!** Supervertaler is designed for professional commercial use.

**Allowed:**
- Freelance translation work
- Translation agencies
- Corporate translation departments
- Language service providers
- Charge clients for your translation services

**Requirements:**
- Own valid API keys (your personal or business account)
- Comply with AI provider terms of service
- Respect open source license (check LICENSE file)

**Not Allowed:**
- Reselling Supervertaler itself as your own product
- Removing author attribution/credits
- Violating open source license terms

**Commercial Support:**
- Community support via GitHub Issues (free)
- No official paid support currently
- Consider contributing to the project if you use it professionally

### How can I support the project?

**For Users:**
1. ⭐ **Star the GitHub repository** - Increases visibility
2. 📢 **Share with colleagues** - Help other translators discover it
3. 🐛 **Report bugs** - Detailed reports improve quality
4. 💡 **Suggest features** - Share your workflow needs
5. 📝 **Improve documentation** - Fix typos, add examples

**For Developers:**
1. 🔧 **Contribute code** - Submit Pull Requests
2. 🧪 **Test beta features** - Early testing catches bugs
3. 🌍 **Translate UI** - Add support for more languages
4. 📚 **Write guides** - Share your workflows and tips

**For Everyone:**
- Use it professionally and share your experience!
- Write blog posts or reviews
- Help other users on the Supervertaler Forum

---

## More Resources

### Where can I learn more?

**Core Documentation:**
- [README.md](../../README.md) - Overview and features
- [INSTALLATION.md](INSTALLATION.md) - Setup instructions
- [CHANGELOG.md](../../CHANGELOG.md) - Version history
- [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) - Technical details

**Feature Guides:**
- [UNIFIED_PROMPT_LIBRARY_GUIDE.md](../UNIFIED_PROMPT_LIBRARY_GUIDE.md) - Prompt system
- [VOICE_DICTATION_GUIDE.md](../VOICE_DICTATION_GUIDE.md) - Supervoice setup
- [AI_ASSISTANT_GUIDE.md](../AI_ASSISTANT_GUIDE.md) - AI Assistant features
- [SUPERBROWSER_GUIDE.md](../SUPERBROWSER_GUIDE.md) - Document viewer
- [IMAGE_CONTEXT_FEATURE.md](../IMAGE_CONTEXT_FEATURE.md) - Figure context

**Online:**
- **GitHub:** [github.com/Supervertaler/Supervertaler-Workbench](https://github.com/Supervertaler/Supervertaler-Workbench)
- **Website:** [Supervertaler Documentation](https://michaelbeijer.github.io/Supervertaler/)
- **Issues:** [Report bugs / Request features](https://github.com/Supervertaler/Supervertaler-Workbench/issues)

### What's next for Supervertaler?

**Recently Added (v1.9.27):**
- ✅ Simple Text File Import/Export - Line-by-line text translation
- ✅ Automatic Model Version Checker - Get notified of new LLM models (v1.9.26)
- ✅ UI Standardization - Consistent green checkbox design throughout
- ✅ Local LLM support (Ollama) - Run AI translation offline (v1.9.16)
- ✅ Automatic hardware detection and model recommendations

**Near Future (v1.10+):**
- Enhanced QA tools (consistency checks, tag validation)
- Improved Superbench (more models, better scoring)
- Advanced filtering options
- Performance optimizations for very large projects

**Medium Term (v2.0):**
- Enhanced termbase management (bulk import, merge, deduplication)
- Advanced statistics and project analytics
- Team collaboration features (shared TMs, cloud sync)

**Long Term:**
- Real-time collaboration (multiple translators, one project)
- Quality prediction ML (estimate AI output quality before translating)
- Plugin system (community extensions)
- Integration with translation marketplaces
- Mobile companion app

**Experimental Ideas:**
- MT post-editing mode with quality estimation
- Automated terminology extraction from documents
- CAT tool plugins (direct integration with memoQ/Trados/etc.)
- Voice-to-voice translation (speak source, hear target)

**Community-Driven:** Feature priorities based on user feedback!

---

## Need More Help?

**Still Have Questions?**

1. 📖 **Read the guides** - Check docs/ folder for detailed guides
2. 🔍 **Search GitHub Issues** - Your question may already be answered
3. 💬 **Open a GitHub Issue** - Ask anything, get detailed answers
4. 🤝 **Supervertaler Forum** - Community tips and workflows

**Found a Bug?**  
→ [Report it on GitHub](https://github.com/Supervertaler/Supervertaler-Workbench/issues)

**Want a Feature?**  
→ [Request it on GitHub](https://github.com/Supervertaler/Supervertaler-Workbench/issues)

**Want to Contribute?**  
→ [Fork the repository](https://github.com/Supervertaler/Supervertaler-Workbench) and submit a Pull Request!

---

*Last updated: November 22, 2025*  
*Supervertaler v1.7.8*  
*Created by Michael Beijer with AI assistance*
