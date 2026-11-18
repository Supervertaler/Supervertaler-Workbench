"""Where Workbench keeps its data: the config pointer file and the data folder.

Moved out of Supervertaler.py unchanged (Nov 2026), so the Settings code in
modules/main_window/ can use it too.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional


def get_config_pointer_path() -> Path:
    """
    Get path to the config pointer file that stores the user's chosen data location.
    
    This is stored in a standard config location:
    - Windows: %APPDATA%/Supervertaler/config.json
    - macOS:   ~/Library/Application Support/Supervertaler/config.json
    - Linux:   ~/.config/Supervertaler/config.json
    
    This small file just contains a pointer to where the real data lives.
    """
    if sys.platform == 'win32':
        # Windows: %APPDATA% (Roaming, syncs across machines on domain)
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        return Path(appdata) / "Supervertaler" / "config.json"
    elif sys.platform == 'darwin':
        # macOS: ~/Library/Application Support/
        return Path.home() / "Library" / "Application Support" / "Supervertaler" / "config.json"
    else:
        # Linux/BSD: ~/.config/ (XDG_CONFIG_HOME)
        xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        return Path(xdg_config) / "Supervertaler" / "config.json"


def get_default_user_data_path() -> Path:
    """
    Get the DEFAULT user data path (visible in home folder).
    
    - Windows: C:/Users/Username/Supervertaler/
    - macOS:   ~/Supervertaler/
    - Linux:   ~/Supervertaler/
    
    This is intentionally a visible, easily-accessible location.
    """
    return Path.home() / "Supervertaler"


def save_user_data_path(path: Path) -> None:
    """Save the user's chosen data path to the config pointer file."""
    pointer_path = get_config_pointer_path()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    
    config = {"user_data_path": str(path)}
    with open(pointer_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def load_user_data_path_from_config() -> Optional[Path]:
    """
    Load the user's chosen data path from config pointer file.
    Returns None if no config exists or path is invalid.
    """
    pointer_path = get_config_pointer_path()
    
    if not pointer_path.exists():
        return None
    
    try:
        # utf-8-sig: Notepad and PowerShell 5 can save the file with a BOM, which
        # plain utf-8 rejects - and then the data folder seemed to vanish.
        with open(pointer_path, 'r', encoding='utf-8-sig') as f:
            config = json.load(f)
        
        path_str = config.get('user_data_path')
        if path_str:
            return Path(path_str)
    except (json.JSONDecodeError, OSError):
        pass
    
    return None


def get_user_data_path() -> Path:
    """
    Get the path to user data directory.
    
    Resolution order:
    1. Check config pointer file for user's saved preference
    2. If pointer missing, check if default location has data (auto-recover)
    3. If nothing found, return default (first-run dialog will be shown later)
    
    All users (EXE, pip, dev) use the same unified system:
    - Default: ~/Supervertaler/ (visible in home folder)
    - User can choose custom location on first run
    - Config pointer remembers their choice
    
    This ensures data survives pip upgrades, EXE updates, and is easy to find/backup.
    """
    # 1. Check if user has a saved preference
    saved_path = load_user_data_path_from_config()
    if saved_path and saved_path.exists():
        return saved_path
    
    # 2. Check if default location has data (auto-recovery if pointer deleted)
    default_path = get_default_user_data_path()
    if get_config_pointer_path().exists():
        # The pointer is there but unreadable or points at a missing folder (an
        # unplugged drive, say). Use the default for now, but never overwrite the
        # user's choice: it must still work once the folder is back.
        return default_path
    if default_path.exists():
        # Check if it has actual content
        has_content = False
        try:
            for item in default_path.iterdir():
                if item.is_file() or (item.is_dir() and any(item.iterdir())):
                    has_content = True
                    break
        except OSError:
            pass
        
        if has_content:
            # Auto-recover: recreate the pointer file
            save_user_data_path(default_path)
            print(f"[Data Paths] Auto-recovered config pointer to: {default_path}")
            return default_path
    
    # 3. Return default path (first-run dialog will be shown by MainWindow)
    # Don't create folder yet - let the dialog handle that
    return default_path


def needs_first_run_data_dialog() -> bool:
    """
    Check if we need to show the first-run data location dialog.
    
    Returns True if:
    - No config pointer file exists, AND
    - Default data location doesn't have existing content
    
    This means it's truly a fresh install.
    """
    # If config pointer exists, user has already chosen a location
    pointer_path = get_config_pointer_path()
    if pointer_path.exists():
        return False
    
    # If default location has content, we auto-recovered (no dialog needed)
    default_path = get_default_user_data_path()
    if default_path.exists():
        try:
            for item in default_path.iterdir():
                if item.is_file() or (item.is_dir() and any(item.iterdir())):
                    return False  # Has content, no dialog needed
        except OSError:
            pass
    
    # Truly a fresh install - need to show dialog
    return True
