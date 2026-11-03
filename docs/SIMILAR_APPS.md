# Apps Similar to Supervertaler

A curated list of translation tools and AI-powered assistants that offer similar functionality to Supervertaler.

---

## 🤖 AI-Powered Translation Tools

### TransAIde (Plugin for Trados Studio)
- **Website:** [posteditacat.xyz/transaide-plugin-for-trados-studio](https://posteditacat.xyz/transaide-plugin-for-trados-studio/)
- **Type:** Trados Studio plugin
- **Description:** Context-aware AI translation plugin that exports entire documents from Trados Studio, allowing translation with any AI model (Claude, Gemini, ChatGPT, etc.) or NMT system while preserving full context.
- **Key Features:**
  - Export/import full documents with context (text or JSON format)
  - Works with any AI model via chat, API, or agent systems
  - Termbase integration (exports required/forbidden terms)
  - Dedicated window showing AI translations segment-by-segment
  - Supports grammar checking (LanguageTool, Grammarly)
  - Compatible with Trados Studio 2021, 2022, 2024
- **Pricing:** Free up to 500 words, subscription for unlimited (39 EUR/year freelancer, 69 EUR/year agency)
- **Open Source:** ❌

### OpenAI Provider for Trados Studio
- **Website:** [appstore.rws.com/plugin/249](https://appstore.rws.com/plugin/249?lang=fr&tab=documentation)
- **Type:** Trados Studio plugin (Translation Provider)
- **Description:** Official free plugin from RWS that integrates OpenAI's language models as a translation provider within Trados Studio.
- **Key Features:**
  - Direct OpenAI API integration
  - Works as a standard translation provider in Trados
  - Segment-by-segment translation workflow
  - Requires OpenAI API key
- **Pricing:** Free (pay-per-use OpenAI API costs)
- **Open Source:** ❌
- **Note:** RWS also offers "AI Professional" plugin with more features (Azure OpenAI support, custom prompts, AI companion, terminology-aware suggestions)

### CotranslatorAI
- **Website:** [cotranslatorai.com](https://cotranslatorai.com/)
- **Type:** Desktop application (Windows)
- **Description:** AI-powered translation assistant for professional translators that works inside and alongside CAT tools. Emphasises translator agency in AI-assisted workflows, with structured methodologies like GAIT (Generative AI Iterative Translation) and GAIM (Generative AI Iterative Machine Translation Post-Editing).
- **Key Features:**
  - Works invisibly inside and outside your CAT tool
  - Structured AI translation workflows (GAIT, GAIM, GECR)
  - CAT tool integration
- **Open Source:** ❌

### GT4T
- **Website:** [gt4t.ai](https://gt4t.ai/)
- **Type:** Desktop application (Windows, Mac)
- **Description:** Lightweight translation assistant that works system-wide inside any application. Select text and press a global hotkey (Ctrl+J / Cmd+J) to get MT and LLM suggestions in a small popup; press a number to insert. Supports 29+ MT engines plus LLMs including ChatGPT. Also does batch file translation (DOCX, XLIFF, SDLXLIFF, subtitles, etc.) locally without uploading to servers. Popular with translators since ~2009. *Supervertaler QuickTrans is inspired by this workflow.*
- **Key Features:**
  - Global hotkey popup (Ctrl+J) – works inside any app including CAT tools
  - 29+ MT engines: Google, DeepL, Microsoft, Yandex, Baidu, Papago, and more
  - LLM support: ChatGPT (and local AI models)
  - Batch file translation with drag-and-drop (no file size limit)
  - Local-first processing – files stay on your machine
  - Dictionary lookup
  - Pay-per-use credit model (chars prepaid, no expiry); free tier available
- **Pricing:** Freemium – free tier + prepaid character credits (pay-as-you-go)
- **Open Source:** ❌

### Bureau Works
- **Website:** [bureauworks.com](https://www.bureauworks.com/)
- **Type:** Cloud-based Translation Management System (TMS) with integrated CAT editor
- **Description:** Enterprise-grade TMS with a native AI-enhanced in-browser CAT editor. Targets translation agencies and localization teams rather than individual freelancers. Handles end-to-end project management: quoting, task assignment, QA, invoicing, and payment – all in one platform.
- **Key Features:**
  - In-browser CAT editor with TM, terminology, and AI suggestions
  - Supports 54+ file types, 200+ languages
  - AI-powered workflow automation (project creation, QA, task assignment)
  - Connectors to GitHub, Figma, CMSs, and more
  - Built-in financials (quoting, invoicing, payments)
- **Pricing:** Custom (per-project / enterprise pricing)
- **Open Source:** ❌

### Wordscope
- **Website:** [pro.wordscope.com](https://pro.wordscope.com/en)
- **Type:** Web-based CAT tool
- **Description:** Web-based CAT tool combining neural MT, private and public translation memories, and terminology databases in one interface. One of the first CAT tools to add AI/ChatGPT-powered features (February 2023). Strong focus on European languages.
- **Key Features:**
  - Neural MT integration
  - Private + public translation memories
  - Terminology management
  - AI/ChatGPT-powered suggestions
  - Comparative revision tool and QA checks
  - Supports DOCX, XLSX, PPTX, TMX, HTML, XLIFF, SRT, and more
  - Supports Dutch, English, French, German, Italian, Portuguese, Spanish
- **Pricing:** From $40/user/month; free trial available
- **Open Source:** ❌

### Wordplay
- **Website:** [wordplay.icu](https://wordplay.icu/en)
- **Type:** Web-based AI translation platform
- **Description:** Suite of AI-powered translation and localisation tools designed for professional translators and localisation teams. Focuses on context-aware translation that goes beyond single-segment approaches, with tools for CAT tool integration, automated workflows, and corpus alignment.
- **Key Features:**
  - XLee – context-aware AI translation for XLIFF files, integrates with CAT tools
  - XTrans – automated translation workflow (file conversion → AI translation → target file → review)
  - XAligner – multilingual sentence alignment for building parallel corpora and TMs
  - Preserves formatting in translated files
  - English–Chinese translation and post-editing services also available
- **Pricing:** Free
- **Open Source:** ❌

### TWAS Suite / TWAS Assistant
- **Website:** [twas.info](https://twas-all-apps.netlify.app/)
- **Type:** Desktop application
- **Description:** Translation Workflow Automation Suite - a comprehensive set of tools for translators working with various CAT tools and file formats.
- **Key Features:**
  - Workflow automation
  - File format conversion
  - Batch processing
- **Open Source:** ❌

---

## 🔧 Open Source / Scripting Tools

### LLM-AutoHotkey-Assistant
- **Website:** [github.com/kdalanon/LLM-AutoHotkey-Assistant](https://github.com/kdalanon/LLM-AutoHotkey-Assistant)
- **Type:** Open-source AutoHotkey v2 script (Windows)
- **Description:** AutoHotkey v2 script that uses OpenRouter.ai to bring any LLM into your daily workflow via a global hotkey. Select text anywhere, press the hotkey (backtick by default), pick a prompt from the menu, and get the result – including translation. Supports multiple AI models simultaneously. Highly customisable via prompt arrays. *Conceptually similar to Supervertaler QuickLauncher.*
- **Key Features:**
  - Global hotkey (backtick) works system-wide in any app
  - Any LLM via OpenRouter.ai (ChatGPT, Claude, Gemini, and more)
  - Fully customisable prompts including translation prompts
  - Interact with multiple AI models simultaneously
  - Free and open source
- **Pricing:** Free (OpenRouter API costs only)
- **Open Source:** ✅ MIT License

---

## 📊 How They Compare

| Feature | Supervertaler | GT4T | TransAIde | OpenAI Provider | CotranslatorAI | Wordscope | Bureau Works | Wordplay | TWAS Suite |
|---------|---------------|------|-----------|-----------------|----------------|-----------|--------------|----------|------------|
| Multi-LLM Support | ✅ GPT, Claude, Gemini, Ollama | ➖ MT engines + ChatGPT | ✅ Any AI model | ❌ (OpenAI only) | ❌ | ✅ ChatGPT | ✅ AI-augmented | ✅ AI-powered | ❓ |
| Global hotkey popup | ✅ QuickTrans (Ctrl+Alt+Q) | ✅ Ctrl+J / Cmd+J | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Standalone App | ✅ Desktop | ✅ Desktop | ❌ (Trados plugin) | ❌ (Trados plugin) | ✅ Desktop | ✅ Web | ✅ Web | ✅ Web | ✅ Desktop |
| Local/Offline Mode | ✅ Ollama | ✅ local-first | ✅ (via local model) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Trados Integration | ✅ SDLPPX/SDLRPX | ✅ SDLXLIFF batch | ✅ Native plugin | ✅ Native plugin | ✅ | ❌ | ❓ | ✅ (via XLIFF) | ✅ |
| memoQ Integration | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❓ | ✅ (via XLIFF) | ✅ |
| CafeTran Integration | ✅ | ❌ | ❌ | ❌ | ❓ | ❌ | ❌ | ✅ (via XLIFF) | ✅ |
| Full Context Translation | ✅ | ❌ | ✅ (entire documents) | ❌ (segment-by-seg) | ❓ | ❌ | ❌ | ✅ (context-aware) | ❓ |
| Translation Memory | ✅ SQLite + TMX | ❌ | ➖ (uses Trados TM) | ➖ (uses Trados TM) | ✅ | ✅ | ✅ | ✅ (XAligner) | ✅ |
| Terminology Management | ✅ | ❌ | ➖ (exports from Trados) | ➖ (uses Trados TB) | ✅ | ✅ | ✅ | ❓ | ✅ |
| Voice Commands | ✅ Offline (Vosk) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Open Source | ✅ MIT License | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Free | ✅ | Freemium (pay-per-char) | Freemium (500 words) | ✅ (API costs) | ❓ | From $40/mo | Custom/Enterprise | ✅ Free | Paid |

---

## 💡 Why Choose Supervertaler?

Supervertaler stands out by being:

1. **Completely Free & Open Source** - No subscription, no hidden costs
2. **Privacy-Focused** - Run locally with Ollama, your data stays on your machine
3. **Multi-LLM Flexible** - Use any AI provider, switch between them freely
4. **Translator-Built** - Created by a working translator who understands real workflows
5. **CAT Tool Agnostic** - Works alongside memoQ, Trados, CafeTran, and others
6. **Standalone Application** - No need to buy or use specific CAT tools

---

*Know of another similar app that should be listed here? Open an issue or discussion on GitHub!*
