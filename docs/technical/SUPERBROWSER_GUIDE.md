# 🌐 Superbrowser - Multi-Chat AI Browser

**Version:** 1.6.4  
**Release Date:** November 18, 2025  
**Status:** Production Ready

![Superbrowser Screenshot](screenshots/Superbrowser_2025-11-18.jpg)

## Overview

Superbrowser is a revolutionary feature that allows you to interact with ChatGPT, Claude, and Gemini simultaneously in a single window. No more switching between browser tabs or windows – see all three AI assistants side-by-side in resizable columns.

## Why Superbrowser?

As translators and writers, we often need to:
- **Compare AI responses** - See how different models handle the same prompt
- **Maintain multiple contexts** - Keep separate conversations for different projects
- **Research efficiently** - Get multiple AI perspectives without context switching
- **Stay organized** - All your AI tools in one place within Supervertaler

## Features

### 🎯 Three-Column Layout
- **ChatGPT** (left, green) - OpenAI's flagship conversational AI
- **Claude** (center, copper) - Anthropic's helpful, harmless, and honest AI
- **Gemini** (right, blue) - Google's multimodal AI assistant

### 🔧 Collapsible Configuration
- **Start Visible** - Configuration panel shows on first open
- **Quick Toggle** - Click "▼ Hide Configuration" to maximize chat space
- **URL Customization** - Set custom URLs for specific chat sessions or projects
- **Update URLs** - Change all three AI URLs instantly with one button

### 🔐 Persistent Sessions
Your login sessions are automatically saved:
- **No Re-login Required** - Stay logged in between Supervertaler sessions
- **Separate Profiles** - Each AI has its own isolated storage for security
- **Cookie Management** - All cookies and local storage persisted
- **Cache Storage** - Fast loading with local cache

Storage locations:
- **Dev Mode:** `user_data_private/superbrowser_profiles/`
- **Production:** `user_data/superbrowser_profiles/`

### 🏠 Navigation Controls
Each column has:
- **URL Bar** - Navigate to any page within that AI service
- **Reload Button (↻)** - Refresh the current page
- **Home Button (⌂)** - Return to the configured home URL

### 📱 Minimal Design
- **Tiny Headers** - 10px colored headers identify each AI without wasting space
- **Compact UI** - Every pixel dedicated to the chat experience
- **Resizable Columns** - Drag dividers to adjust column widths as needed

## Getting Started

### 1. Access Superbrowser
1. Open Supervertaler
2. Navigate to **Specialised Tools** tab
3. Click **🌐 Superbrowser**

### 2. First-Time Setup
The configuration panel is visible by default:

1. **ChatGPT URL** - Default: `https://chatgpt.com/`
2. **Claude URL** - Default: `https://claude.ai/`
3. **Gemini URL** - Default: `https://gemini.google.com/`

You can customize these to:
- Specific chat sessions: `https://chatgpt.com/c/your-chat-id`
- Specific projects: Link directly to project-specific conversations
- Custom instances: Use enterprise or custom deployments

Click **"Update URLs"** to apply changes.

### 3. Log In to AI Services
Log in to each AI service once:
1. Click in the column you want to use
2. Complete the login process
3. Your session will be saved automatically

### 4. Hide Configuration (Optional)
Once set up, click **"▼ Hide Configuration"** to maximize screen space for your chats.

## Use Cases

### Translation Quality Comparison
**Scenario:** You want to see how different AI models translate a technical passage.

**Workflow:**
1. Paste the source text into ChatGPT
2. Copy the same text to Claude
3. Copy the same text to Gemini
4. Compare all three translations side-by-side
5. Choose the best translation or combine elements from each

### Multi-Project Management
**Scenario:** You're working on multiple translation projects simultaneously.

**Workflow:**
1. Set ChatGPT to your medical translation project chat
2. Set Claude to your legal translation project chat
3. Set Gemini to your marketing translation project chat
4. Switch between projects by clicking the appropriate column
5. All contexts remain separate and organized

### Research and Development
**Scenario:** You're developing a new translation methodology.

**Workflow:**
1. Ask ChatGPT for theoretical approaches
2. Ask Claude for practical implementation advice
3. Ask Gemini for code examples and automation ideas
4. Synthesize insights from all three perspectives

### Prompt Engineering
**Scenario:** You're refining a complex translation prompt.

**Workflow:**
1. Test initial prompt in ChatGPT
2. Refine based on results and test in Claude
3. Final optimization tested in Gemini
4. Compare which AI responds best to your refined prompt

## Tips & Best Practices

### 🎯 Efficient Workflow
- **Keep Configuration Hidden** - After initial setup, hide it for maximum chat space
- **Use Keyboard Shortcuts** - Each browser supports standard shortcuts (Ctrl+C, Ctrl+V, etc.)
- **Bookmark Chat Sessions** - Set URLs to frequently-used conversations for instant access
- **Resize Columns** - Give more space to the AI you're actively using

### 🔐 Security
- **Separate Profiles** - Each AI has isolated storage; cookies don't cross-contaminate
- **Dev Mode Isolation** - Development work uses `user_data_private/` (git-ignored)
- **Session Privacy** - Your conversations remain private within your local storage

### ⚡ Performance
- **Persistent Cache** - Pages load faster with local cache
- **Background Loading** - All three AIs load simultaneously
- **Minimal Headers** - More screen real estate = better productivity

### 🛠️ Troubleshooting
- **Session Lost?** - Try logging in again; profile storage may have been cleared
- **Page Not Loading?** - Click the reload button (↻) or check your internet connection
- **Column Too Narrow?** - Drag the dividers between columns to adjust widths

## Technical Details

### Architecture
- **Module:** `modules/superbrowser.py`
- **Main Class:** `SuperbrowserWidget`
- **Browser Engine:** `QtWebEngine` (Chromium-based)
- **Profile Management:** `QWebEngineProfile` with persistent storage

### Storage Structure
```
user_data_private/  (or user_data/)
└── superbrowser_profiles/
    ├── superbrowser_chatgpt/
    │   ├── cache/
    │   └── [cookies, sessions, storage]
    ├── superbrowser_claude/
    │   ├── cache/
    │   └── [cookies, sessions, storage]
    └── superbrowser_gemini/
        ├── cache/
        └── [cookies, sessions, storage]
```

### Dependencies
- **PyQt6** - Core GUI framework
- **PyQt6-WebEngine** - Chromium-based web browser component
- **PyQt6-WebEngineCore** - Web engine core functionality

### OpenGL Context Sharing
Superbrowser requires OpenGL context sharing to be enabled before QApplication initialization:
```python
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
```

This is automatically handled in Supervertaler's main entry point.

## Future Enhancements

Potential features for future versions:
- **Session Save/Restore** - Save and restore entire browser states
- **Tab Management** - Multiple tabs within each column
- **Synchronized Scrolling** - Scroll all three columns together
- **Screenshot Comparison** - Capture and compare AI responses visually
- **Export Conversations** - Export all three AI responses to markdown
- **Custom AI Services** - Add additional AI services beyond the default three

## Feedback & Support

Found a bug or have a feature request? Please open an issue on GitHub:
https://github.com/michaelbeijer/Supervertaler/issues

## Version History

- **v1.6.4** (2025-11-18) - Initial release of Superbrowser
  - Three-column layout with ChatGPT, Claude, and Gemini
  - Persistent sessions with profile management
  - Collapsible configuration panel
  - Minimal headers for maximum screen space

---

**Superbrowser** - Part of the Supervertaler suite of tools for translators and writers.
