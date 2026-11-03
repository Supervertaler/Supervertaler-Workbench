# Supervertaler Keyboard Shortcuts

**Version:** v1.9.5 | **Last Updated:** November 27, 2025

Complete reference of all keyboard shortcuts in Supervertaler.

---

## 📋 Table of Contents

1. [Navigation](#navigation)
2. [Translation & AI](#translation--ai)
3. [Editing](#editing)
4. [Formatting Tags](#formatting-tags-v194)
5. [memoQ Tags](#memoq-tags-v195)
6. [Translation Memory](#translation-memory)
7. [Project & Files](#project--files)
8. [Voice & Lookup](#voice--lookup)
9. [View Controls](#view-controls)

---

## Navigation

| Shortcut | Action | Context |
|----------|--------|---------|
| **↑ / ↓** | Move to previous/next segment | Grid view |
| **Page Up** | Scroll up through segments | Grid view |
| **Page Down** | Scroll down through segments | Grid view |
| **Home** | Go to first segment | Grid view |
| **End** | Go to last segment | Grid view |
| **Ctrl+G** | Go to segment number | Grid view |
| **Tab** | Move to next cell | Grid editing |
| **Shift+Tab** | Move to previous cell | Grid editing |

---

## Translation & AI

| Shortcut | Action | Notes |
|----------|--------|-------|
| **F5** | Translate current segment | Uses selected AI provider |
| **Shift+F5** | Translate selected segments | Batch translation |
| **Enter** | Accept AI response to target | In AI Response panel |
| **Escape** | Cancel current operation | Various contexts |

---

## Editing

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+Z** | Undo | Up to 100 levels |
| **Ctrl+Y** | Redo | Up to 100 levels |
| **Ctrl+Shift+Z** | Redo (alternative) | Same as Ctrl+Y |
| **Ctrl+C** | Copy | Standard copy |
| **Ctrl+X** | Cut | Standard cut |
| **Ctrl+V** | Paste | Standard paste |
| **Ctrl+A** | Select all | In text editor |
| **Ctrl+F** | Find & Replace dialog | Opens search dialog |
| **F3** | Find next | After search |
| **Shift+F3** | Find previous | After search |
| **Delete** | Delete selection | Or delete character |
| **Backspace** | Delete previous character | Standard backspace |

---

## Formatting Tags (v1.9.4+)

For memoQ bilingual files with inline formatting.

| Shortcut | Action | Tag Applied |
|----------|--------|-------------|
| **Ctrl+B** | Toggle bold | `<b>text</b>` |
| **Ctrl+I** | Toggle italic | `<i>text</i>` |
| **Ctrl+U** | Toggle underline | `<u>text</u>` |
| **Ctrl+Alt+T** | Toggle Tag view | Show/hide raw tags |

**How it works:**
- Select text, then press shortcut to wrap with tags
- Press again on tagged text to remove tags
- In Tag view: see raw tags like `<b>bold</b>`
- In WYSIWYG view: see formatted **bold** text

---

## memoQ Tags (v1.9.5+)

Insert memoQ placeholder tags from source segment.

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+,** | Insert next tag pair | Or wrap selection with tags |

**Tag formats supported:**
- Paired tags: `[1}...{1]`, `[2}...{2]`
- Standalone tags: `[3]`, `[4]`

**How it works:**
1. Tags are detected from the source segment
2. Without selection: inserts next unused tag pair at cursor
3. With selection: wraps selected text with next unused tag pair
4. Tags are inserted in order (1, 2, 3...)

---

## Translation Memory

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+1** | Insert TM match #1 | Best match |
| **Ctrl+2** | Insert TM match #2 | Second best |
| **Ctrl+3** | Insert TM match #3 | Third best |
| **Ctrl+4** | Insert TM match #4 | Fourth best |
| **Ctrl+5** | Insert TM match #5 | Fifth best |
| **Ctrl+6** | Insert TM match #6 | Sixth best |
| **Ctrl+7** | Insert TM match #7 | Seventh best |
| **Ctrl+8** | Insert TM match #8 | Eighth best |
| **Ctrl+9** | Insert TM match #9 | Ninth best |
| **Ctrl+M** | Show TM matches | For current segment |

---

## Project & Files

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+N** | New project | Create new project |
| **Ctrl+O** | Open project/file | Open existing |
| **Ctrl+S** | Save project | Save current state |
| **Ctrl+Shift+S** | Save As | Save with new name |
| **Ctrl+E** | Export | Export dialog |
| **Ctrl+W** | Close project | Close current project |

---

## Voice & Lookup

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+Alt+O** | Switch voice commands on/off | System-wide hotkey |
| **Ctrl+Alt+L** | Universal TM lookup | System-wide hotkey |

**Voice commands (Ctrl+Alt+O):**
- Press Ctrl+Alt+O to switch voice commands on or off (or use the Voice tab or the tray icon)
- Say a command, such as "next segment" or "translate segment"
- Speech that isn't a command is ignored
- For running text, use a dictation tool such as Windows voice typing (Win+H)

**Universal Lookup (Ctrl+Alt+L):**
- Works from ANY application
- Select text, press Ctrl+Alt+L
- Supervertaler searches your TMs
- Results appear in popup

---

## View Controls

| Shortcut | Action | Notes |
|----------|--------|-------|
| **Ctrl+Alt+T** | Toggle Tag view | Show/hide formatting tags |
| **Ctrl++** | Zoom in | Increase font size |
| **Ctrl+-** | Zoom out | Decrease font size |
| **Ctrl+0** | Reset zoom | Default font size |
| **F11** | Toggle fullscreen | Full screen mode |

---

## Status Changes

Use the status dropdown in the grid, or right-click for context menu.

| Status | Meaning |
|--------|---------|
| **Draft** | Initial state, not translated |
| **Translated** | AI or manual translation applied |
| **Reviewed** | Reviewed by translator |
| **Approved** | Approved for delivery |
| **Needs Review** | Flagged for review |
| **Final** | Locked, ready for export |

---

## Tips

### Efficient Workflow
1. **Ctrl+↓** to move through segments
2. **F5** to translate
3. **Enter** to accept
4. Repeat

### Multi-Select Operations
- **Ctrl+Click** - Add/remove individual segments
- **Shift+Click** - Select range of segments
- Then use **Translate Selected** or **Edit → Bulk Operations**

### Quick TM Lookup
- TM matches appear automatically for each segment
- Use **Ctrl+1** through **Ctrl+9** to insert matches instantly

---

## Customizing Shortcuts

Currently, keyboard shortcuts are not user-configurable. This is planned for a future release.

---

*See also: [Supervertaler Help](https://docs.supervertaler.com/workbench/) | [FAQ](../../FAQ.md)*
