**A big one: 22 issues from the tracker, and several bugs that quietly lost text or blocked typing.**

## Fixes you may have been hitting without knowing

- **Imported sentences could lose their first word.** With "Split lines into sentences" ticked, a sentence starting with an abbreviation lost it: "Dr. Smith works here." was imported, and exported, as "Smith works here.". Segments are now always cut from the original text, so nothing can be dropped. "Really?!" no longer becomes two segments either.
- **Markdown import could corrupt links.** A link whose text is inline code, such as ``[`guide.md`](guide.md)``, came through as an internal placeholder (`\x00MD0\x00`) in both the segment and the exported file.
- **Polish letters and other AltGr characters could not be typed anywhere while Supervertaler was running (Windows).** Windows reports AltGr as Ctrl+Alt, so Supervertaler's global hotkeys swallowed keys like AltGr+A (ą) system-wide.
- **An edited TM entry stopped being found as an exact match.**
- **Find & Replace "Whole words" replaced inside longer words.**
- **Every CafeTran project was created as English → Dutch.** Déjà Vu and memoQ RTF imports also fell back silently to a fixed pair. The language pair is now read from the file wherever it says.
- **TMX import** suggested the wrong direction, and could fail with "Failed to create TM metadata" or write into the wrong TM.
- **Settings could delete each other.** "Save General Settings" wiped fourteen settings belonging to other pages, and "Save AI Settings" deleted your custom MT endpoints.
- **The dark theme** left white panels and light-on-light text in the Clipboard, SuperLookup, info boxes and elsewhere.
- **AI Assistant:**
  - "include TM/termbase data" sent the AI nothing;
  - chatting with a local model that had no price entry failed.

## New

| Where | What |
|---|---|
| **Settings → 📏 Segmentation Rules** | Your own segmentation rules, SRX-style as in OmegaT: break and no-break rules with regular expressions, "Break after text…" for delimiters such as `<>`, extra abbreviations, one segment per line, and SRX import/export. A test box shows the result live. A plain-text file split without spaces exports back exactly as it was. |
| **Settings → 🏷️ Inline Codes** | Placeholders such as `{playerName}`, `%s`, `\n` and `<color=…>` are treated like tags. They are highlighted, inserted with Ctrl+, and checked by QA, and the AI is told to keep them. A TM match that differs only in its codes gets the codes of your segment (`{PK}{MN}` → `{PKMN}`). |
| **Settings, every page** | No more Save buttons. Each change is saved a moment after you make it. |
| **QA → Run QA Checks** | Saved find-only checks (a regex "linter"), with a starter set, plus a tags & codes check. |
| **QA → Check with LanguageTool** | Grammar and spelling via the public LanguageTool service or your own server. |
| **Help → 🎓 Open Sample Project** | A small English → Dutch project with its own TM and glossary, to see what everything does. |
| **Find & Replace** | "Also in writable TMs" makes the same change in your TMs. |
| **SuperLookup** | Add your own web resources, with a text size setting for TM results. |
| **AI cost** | Per-job cost, an estimate before a batch run, and amounts in euros. |
| **Grid filter** | Regular expressions: type `/pattern/` in the Source or Target filter. |
| **Match Panel** | Differences around inline tags are marked per word, not per tag-glued chunk. |
| **Also new** | Update a project by pasting bilingual text; add more source text to a project; back up every TM and termbase in one go; custom dictionary import/export; preview zoom; export concordance hits to Excel/CSV; the AI model shown in Project Information; an Ollama timeout setting. |

## Install

- **Windows standalone:** download the `.zip` below, extract it, and run `Supervertaler\Supervertaler.exe`. Keep the EXE next to its `_internal\` folder, because moving it out causes a missing `python312.dll` error.
- **pip:** `pip install --upgrade supervertaler`

Full changelog: https://github.com/Supervertaler/Supervertaler-Workbench/blob/main/CHANGELOG.md
