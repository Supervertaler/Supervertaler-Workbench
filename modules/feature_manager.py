"""
Supervertaler Feature Manager
=============================

Manages feature availability and user enable/disable toggles. Features can be
toggled in Settings → Features.

Each feature module has:
- Required pip packages (some are optional extras)
- Size estimate for user information
- Availability check (are dependencies installed?)
- Enable/disable toggle (user preference)

Installation examples:
    pip install supervertaler                    # Recommended core install
    pip install supervertaler[all]               # Legacy alias (no-op; kept for compatibility)
"""

import json
import os
import importlib.util
import importlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from pathlib import Path


@dataclass
class FeatureModule:
    """Definition of an optional feature module."""
    id: str
    name: str
    description: str
    pip_extra: str  # Name used in pip install supervertaler[extra]
    packages: List[str]  # Required pip packages
    size_mb: int  # Approximate installed size in MB
    check_import: str  # Module to import to check availability
    icon: str = "📦"
    category: str = "Optional"
    enabled_by_default: bool = True
    
    def is_available(self) -> bool:
        """Check if required packages are installed."""
        # IMPORTANT: do NOT import the module here.
        # Some optional features (e.g., sentence-transformers/torch) can be slow to import
        # and may even crash in frozen/PyInstaller builds depending on native deps.
        # Availability checks should be cheap and side-effect free.
        try:
            return importlib.util.find_spec(self.check_import) is not None
        except (ImportError, ValueError):
            return False


# Define all optional feature modules
FEATURE_MODULES: Dict[str, FeatureModule] = {
    "voice": FeatureModule(
        id="voice",
        name="Voice commands",
        description=(
            "Hands-free voice commands, recognised offline by Vosk. "
            "Running text is left to a dictation tool."
        ),
        pip_extra="voice",
        packages=["sounddevice", "numpy", "vosk"],
        size_mb=60,
        check_import="vosk",
        icon="🎤",
        category="AI Features",
        enabled_by_default=False,
    ),
    "webengine": FeatureModule(
        id="webengine",
        name="Web Browser (Superlookup)",
        description="Built-in web browser for research resources in Superlookup. Access IATE, ProZ, Linguee, Wikipedia directly from the app.",
        pip_extra="web",
        packages=["PyQt6-WebEngine"],
        size_mb=100,
        check_import="PyQt6.QtWebEngineWidgets",
        icon="🌐",
        category="UI Features",
    ),
    "pdf": FeatureModule(
        id="pdf",
        name="PDF Rescue (OCR)",
        description="Extract text from scanned PDFs using AI OCR. Converts locked PDFs into editable DOCX files.",
        pip_extra="pdf",
        packages=["PyMuPDF"],
        size_mb=30,
        check_import="fitz",
        icon="📄",
        category="Document Processing",
    ),
    "mt_providers": FeatureModule(
        id="mt_providers",
        name="MT Providers (DeepL, Amazon)",
        description="Additional machine translation providers: DeepL API and Amazon Translate. Requires API keys.",
        pip_extra="mt",
        packages=["boto3", "deepl"],
        size_mb=30,
        check_import="deepl",
        icon="🔄",
        category="Translation",
    ),
    "hunspell": FeatureModule(
        id="hunspell",
        name="Hunspell Spellcheck",
        description="Advanced spellcheck using Hunspell dictionaries. Supports regional variants (en-US, en-GB, etc). Uses spylls (pure Python) on Windows.",
        pip_extra="hunspell",
        packages=["spylls"],  # Pure Python Hunspell - works on all platforms
        size_mb=20,
        check_import="spylls",
        icon="📝",
        category="Quality Assurance",
        enabled_by_default=True,  # spylls works everywhere
    ),
}


class FeatureManager:
    """
    Manages feature availability and user preferences.
    
    Usage:
        fm = FeatureManager(user_data_path)

        # Check if a feature can be used
        if fm.is_feature_usable("voice"):
            ...

        # Get all features for Settings UI
        for feature in fm.get_all_features():
            print(f"{feature.name}: {'✅' if fm.is_feature_usable(feature.id) else '❌'}")
    """
    
    def __init__(self, user_data_path: str = "user_data"):
        self.user_data_path = Path(user_data_path)
        self.settings_file = self.user_data_path / "workbench" / "settings" / "settings.json"
        self._preferences: Dict[str, bool] = {}
        self._load_preferences()
    
    def _load_preferences(self):
        """Load user feature preferences from unified settings/settings.json -> 'features' section."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._preferences = data.get("features", {})
            except (json.JSONDecodeError, IOError):
                self._preferences = {}

        # Apply defaults for any missing features
        for feature_id, feature in FEATURE_MODULES.items():
            if feature_id not in self._preferences:
                self._preferences[feature_id] = feature.enabled_by_default

    def _save_preferences(self):
        """Save user feature preferences to unified settings/settings.json -> 'features' section."""
        settings_dir = self.user_data_path / "workbench" / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)

        # Read entire settings file to preserve other sections
        data = {}
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {}

        data["features"] = self._preferences
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def get_feature(self, feature_id: str) -> Optional[FeatureModule]:
        """Get a feature module definition by ID."""
        return FEATURE_MODULES.get(feature_id)
    
    def get_all_features(self) -> List[FeatureModule]:
        """Get all feature module definitions."""
        return list(FEATURE_MODULES.values())
    
    def get_features_by_category(self) -> Dict[str, List[FeatureModule]]:
        """Get features grouped by category."""
        categories: Dict[str, List[FeatureModule]] = {}
        for feature in FEATURE_MODULES.values():
            if feature.category not in categories:
                categories[feature.category] = []
            categories[feature.category].append(feature)
        return categories
    
    def is_feature_available(self, feature_id: str) -> bool:
        """Check if a feature's dependencies are installed."""
        feature = FEATURE_MODULES.get(feature_id)
        if not feature:
            return False
        return feature.is_available()
    
    def is_feature_enabled(self, feature_id: str) -> bool:
        """Check if a feature is enabled by user preference."""
        return self._preferences.get(feature_id, True)
    
    def is_feature_usable(self, feature_id: str) -> bool:
        """Check if a feature can be used (available AND enabled)."""
        return self.is_feature_available(feature_id) and self.is_feature_enabled(feature_id)
    
    def set_feature_enabled(self, feature_id: str, enabled: bool):
        """Enable or disable a feature."""
        self._preferences[feature_id] = enabled
        self._save_preferences()
    
    def get_total_size_mb(self, only_enabled: bool = False) -> int:
        """Get total size of installed/enabled features."""
        total = 0
        for feature_id, feature in FEATURE_MODULES.items():
            if only_enabled and not self.is_feature_enabled(feature_id):
                continue
            if feature.is_available():
                total += feature.size_mb
        return total
    
    def get_install_command(self, feature_id: str) -> str:
        """Get the pip install command for a feature."""
        feature = FEATURE_MODULES.get(feature_id)
        if not feature:
            return ""
        return f"pip install supervertaler[{feature.pip_extra}]"
    
    def get_missing_features(self) -> List[FeatureModule]:
        """Get features that are enabled but not installed."""
        missing = []
        for feature_id, feature in FEATURE_MODULES.items():
            if self.is_feature_enabled(feature_id) and not feature.is_available():
                missing.append(feature)
        return missing


# Lazy import helpers - use these instead of direct imports
def lazy_import_chromadb():
    """Lazily import ChromaDB for vector storage."""
    try:
        import chromadb
        return chromadb
    except ImportError:
        return None


def lazy_import_sentence_transformers():
    """Lazily import sentence-transformers for embeddings."""
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer
    except ImportError:
        return None


def lazy_import_deepl():
    """Lazily import DeepL API client."""
    try:
        import deepl
        return deepl
    except ImportError:
        return None


def lazy_import_boto3():
    """Lazily import boto3 for Amazon Translate."""
    try:
        import boto3
        return boto3
    except ImportError:
        return None


def lazy_import_hunspell():
    """Lazily import Hunspell spellchecker."""
    try:
        import hunspell
        return hunspell
    except ImportError:
        return None


def lazy_import_webengine():
    """Lazily import PyQt6 WebEngine."""
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        return QWebEngineView
    except ImportError:
        return None


def lazy_import_fitz():
    """Lazily import PyMuPDF (fitz) for PDF processing."""
    try:
        import fitz
        return fitz
    except ImportError:
        return None


# Global feature manager instance (initialized on first use)
_feature_manager: Optional[FeatureManager] = None


def get_feature_manager(user_data_path: str = "user_data") -> FeatureManager:
    """Get or create the global feature manager instance."""
    global _feature_manager
    if _feature_manager is None:
        _feature_manager = FeatureManager(user_data_path)
    return _feature_manager


def check_feature(feature_id: str) -> bool:
    """Quick check if a feature is usable. Use at import time."""
    fm = get_feature_manager()
    return fm.is_feature_usable(feature_id)
