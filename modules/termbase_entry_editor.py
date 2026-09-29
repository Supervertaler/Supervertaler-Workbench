"""
Termbase Entry Editor Dialog

Single canonical dialog for both ADDING new terms to a termbase and EDITING
existing termbase entries. Mode is determined by whether ``term_id`` is
supplied:

* **Edit mode** (``term_id`` is not None): title is "Edit Termbase Entry",
  the dialog loads the row from the database on open, the Save button runs
  ``save_term()`` (UPDATE + synonym save), and a Delete button is shown.
* **Add mode** (``term_id`` is None): title is "Add Term to Termbase",
  there is no Delete button, and the Save button just calls ``accept()``.
  The caller reads the result via ``get_metadata()``, ``get_source_term()``,
  ``get_target_term()``, ``get_source_synonyms()``, ``get_target_synonyms()``
  and runs the INSERT itself.

This class is the merged successor of the historic ``TermMetadataDialog``
(add path) and the previous ``TermbaseEntryEditor`` (edit path); both
visuals were already identical, so the merge is mostly a behavioural one.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QGroupBox, QComboBox,
    QMessageBox, QListWidget, QListWidgetItem, QMenu, QScrollArea,
    QWidget, QToolButton, QApplication, QFormLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from typing import Optional

from modules.styled_widgets import CheckmarkCheckBox  # noqa: E402



class TermbaseEntryEditor(QDialog):
    """Unified dialog for adding/editing termbase entries."""

    def __init__(
        self,
        parent=None,
        *,
        # Edit-mode kwargs (term exists in DB)
        db_manager=None,
        termbase_id: Optional[int] = None,
        term_id: Optional[int] = None,
        # Add-mode kwargs (caller does the INSERT)
        source_term: str = "",
        target_term: str = "",
        active_termbases: Optional[list] = None,
        user_data_path=None,
        default_selected_termbase_ids=None,
    ):
        """
        Initialise the termbase entry editor.

        Args:
            parent: Parent widget.
            db_manager: DatabaseManager instance (edit mode).
            termbase_id: Termbase ID (used by both modes – edit mode targets
                the row in this termbase; add mode passes it through for
                callers that need it).
            term_id: Term ID to edit. If ``None``, the dialog runs in
                add mode.
            source_term: Initial source-term value (add mode).
            target_term: Initial target-term value (add mode).
            active_termbases: List of termbases the caller has shortlisted
                for the add operation. Stored for compatibility with the
                old ``TermMetadataDialog`` API; the dialog itself no longer
                shows a per-add picker.
            user_data_path: Path to the user data dir; used for the
                (no-op) saved-selection load/save shim.
            default_selected_termbase_ids: Pre-selected termbase IDs;
                stored for compatibility with the old API.
        """
        super().__init__(parent)
        # Edit-mode state
        self.db_manager = db_manager
        self.termbase_id = termbase_id
        self.term_id = term_id
        self.term_data = None

        # Add-mode state (stored for caller compatibility)
        self.source_term = source_term
        self.target_term = target_term
        self.active_termbases = active_termbases or []
        self.termbase_checkboxes = {}  # legacy attr; still referenced
        self.user_data_path = user_data_path
        self.default_selected_termbase_ids = set(default_selected_termbase_ids or [])
        self.saved_selections = self._load_termbase_selections()

        self.setModal(True)
        self.setMinimumWidth(550)

        # Auto-resize to fit screen (max 85% of screen height)
        screen = QApplication.primaryScreen().availableGeometry()
        max_height = int(screen.height() * 0.85)
        self.setMaximumHeight(max_height)

        # Start with very compact size for laptops
        self.resize(600, min(550, max_height))

        self.setup_ui()

        # Edit mode: load existing data after the UI is built
        if self.term_id is not None and self.db_manager is not None:
            self.load_term_data()

    # ------------------------------------------------------------------
    # Saved-selection shim (kept for behavioural parity with the old
    # TermMetadataDialog – the helper methods it tries to call live on
    # the main window, not on the dialog, so they raise AttributeError
    # and the try/except silently swallows it. Keeping the shim verbatim
    # so we don't change behaviour during the merge.)
    # ------------------------------------------------------------------
    def _load_termbase_selections(self):
        """Load saved termbase selections from preferences."""
        if not self.user_data_path:
            return None
        try:
            prefs = self._load_settings_section("ui")
            return prefs.get('add_term_termbase_selections', None)
        except Exception:
            return None

    def _save_termbase_selections(self):
        """Save current termbase selections to preferences."""
        if not self.user_data_path:
            return
        try:
            all_settings = self._load_unified_settings()
            selected_ids = [tb_id for tb_id, cb in self.termbase_checkboxes.items() if cb.isChecked()]
            all_settings.setdefault("ui", {})['add_term_termbase_selections'] = selected_ids
            self._save_unified_settings(all_settings)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Language helpers (v1.10.63)
    # ------------------------------------------------------------------
    # Local code → human-readable name map. Covers the language pairs
    # this dialog has historically labelled. Falls back to title-casing
    # the input for anything not in the table. Kept inline (rather than
    # imported from the main module) so this dialog stays standalone-
    # importable from any module without pulling Supervertaler.py.
    _LANGUAGE_NAMES = {
        'en': 'English', 'en-us': 'English', 'en-gb': 'English',
        'nl': 'Dutch',   'nl-nl': 'Dutch',   'nl-be': 'Dutch',
        'de': 'German',  'de-de': 'German',
        'fr': 'French',  'fr-fr': 'French',
        'es': 'Spanish', 'es-es': 'Spanish',
        'it': 'Italian', 'it-it': 'Italian',
        'pt': 'Portuguese',
        'pl': 'Polish',
        'ru': 'Russian',
        'zh': 'Chinese',
        'ja': 'Japanese',
        'ko': 'Korean',
    }

    @classmethod
    def _language_display_name(cls, code_or_name: str) -> str:
        """Return a human-readable language name for an ISO code or a
        full name. Idempotent: ``"Dutch"`` → ``"Dutch"``,
        ``"nl"`` → ``"Dutch"``, ``"nl-BE"`` → ``"Dutch"``.

        Falls back to the input (title-cased) for codes / names not in
        the table — so unfamiliar languages still get *something*
        readable instead of vanishing."""
        if not code_or_name:
            return ''
        key = code_or_name.strip().lower()
        # Exact match (covers ISO codes and locale variants).
        if key in cls._LANGUAGE_NAMES:
            return cls._LANGUAGE_NAMES[key]
        # Strip a locale tail and retry: "nl-be" → "nl".
        if '-' in key:
            base = key.split('-', 1)[0]
            if base in cls._LANGUAGE_NAMES:
                return cls._LANGUAGE_NAMES[base]
        # Reverse lookup — caller may already have passed a full name.
        if code_or_name.strip().title() in cls._LANGUAGE_NAMES.values():
            return code_or_name.strip().title()
        # Fallback: title-case the input.
        return code_or_name.strip().title()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def setup_ui(self):
        """Build the dialog UI. Identical visual for both modes."""
        # Title differs by mode
        self.setWindowTitle(
            "Edit Termbase Entry" if self.term_id is not None else "Add Term to Termbase"
        )

        # Create main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area for all content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Related-entries switcher (v1.10.78) ──────────────────
        # When the same source term has entries in multiple termbases
        # (very common: a project termbase like BRANTS plus a
        # background termbase like PATENTS both have "inrichting"),
        # this dropdown lets the user jump between them without
        # closing the dialog and re-opening it on a different chip.
        # Mirrors the Trados Edit Term Entry dialog's "Editing in:"
        # dropdown.
        #
        # Hidden by default — only shown if the query in
        # ``_populate_related_entries_combo()`` (called at the end
        # of ``load_term_data``) finds more than one entry sharing
        # the loaded entry's source-term or target-term surface
        # forms.
        self._related_row = QHBoxLayout()
        self._related_row.setSpacing(6)
        self._related_label = QLabel("Editing:")
        self._related_label.setStyleSheet("color: #666; font-weight: bold;")
        self._related_combo = QComboBox()
        self._related_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._related_combo.setToolTip(
            "Switch between termbase entries that share this source term. "
            "Each entry can be edited independently in this dialog."
        )
        # Suppress the change handler while we populate the combo
        # programmatically — otherwise the very first setCurrentIndex
        # call would trigger a load of the current entry (no-op but
        # wasteful) or worse, a different entry if the order shifts.
        self._related_combo_suppress = False
        self._related_combo.currentIndexChanged.connect(self._on_related_entry_selected)
        self._related_row.addWidget(self._related_label)
        self._related_row.addWidget(self._related_combo, stretch=1)
        # Container widget so we can show/hide the whole row.
        self._related_container = QWidget()
        self._related_container.setLayout(self._related_row)
        self._related_container.setVisible(False)
        layout.addWidget(self._related_container)

        # Term pair – editable, side by side (Trados-style).
        term_row = QHBoxLayout()
        term_row.setSpacing(12)

        # Resolve language names for the column captions.
        #
        # v1.10.63: in **edit mode**, the captions reflect the
        # *termbase's* declared direction (queried from the termbases
        # table), so the dialog matches the Termbase Editor grid view.
        # This fixes a confusing case where the termbase ran opposite
        # to the project: the dialog used to caption the columns by
        # project direction but populate the values from
        # source_term/target_term in storage order — making it look as
        # if the entry was reversed (e.g. "end" appearing under a
        # "Dutch:" caption in a NL→EN project with an EN→NL termbase).
        # The values are correct in storage; the captions just need to
        # tell the truth about what's stored where.
        #
        # In **add mode**, the dialog isn't tied to a specific termbase
        # yet (the v1.10.62 per-termbase orient in the writer picks the
        # direction at INSERT time), so we keep project-direction
        # captions there.
        #
        # Either way, ISO codes ("nl", "en") get expanded to full
        # human-readable names ("Dutch", "English") for readability.
        src_caption, tgt_caption = "Source", "Target"
        try:
            # Walk up the parent chain — this dialog can be opened from
            # surfaces (TermLens, results panel) that aren't the main
            # window directly.
            ancestor = self.parent()
            while ancestor is not None and not hasattr(ancestor, 'current_project'):
                ancestor = ancestor.parent() if callable(getattr(ancestor, 'parent', None)) else None
            proj = getattr(ancestor, 'current_project', None) if ancestor else None

            # Defaults: project direction (for add mode, or as a fallback
            # if edit-mode lookup fails).
            src_lang_for_caption = (proj.source_lang if proj else '') or 'Source'
            tgt_lang_for_caption = (proj.target_lang if proj else '') or 'Target'

            # Edit-mode override: use the termbase's declared direction
            # if we have it.
            #
            # v1.10.67 robustness: previously the lookup was
            #   ``SELECT source_lang, target_lang FROM termbases
            #     WHERE id = ?``
            # bound to ``self.termbase_id`` (passed in by the caller).
            # In the wild, a user report turned up cases where the
            # value flowing in was a hash-like negative integer (e.g.
            # -1343206784) rather than the actual termbase row's
            # primary key — likely a 32-bit narrowing of a Python int
            # through pyqtSignal(int, int) somewhere upstream in the
            # TermLens display pipeline. When that happened the
            # SELECT returned no rows and the caption code silently
            # fell back to **project** direction — exactly the bug
            # the v1.10.63 caption fix was meant to solve. The
            # symptom: in an NL→EN project with an EN→NL termbase,
            # the dialog would still show "Dutch:" left and "English:"
            # right while populating the fields from
            # ``source_term``/``target_term`` in (correct) termbase
            # storage order — so the English word landed under a
            # "Dutch:" caption and vice versa.
            #
            # The fix: join through ``termbase_terms`` using
            # ``self.term_id`` (which is reliable — load_term_data
            # uses it to fetch the row successfully). Whatever
            # ``self.termbase_id`` arrived as, the SQL finds the
            # actual termbase via the foreign-key cast and returns
            # the row's declared direction. Defensively also
            # backfills ``self.termbase_id`` with the resolved value
            # so downstream operations (delete-via-parent-walk in
            # ``delete_term``, sanity-check SELECT in ``save_term``)
            # use the correct ID even if the caller passed garbage.
            is_edit_mode = self.term_id is not None
            if is_edit_mode and self.db_manager is not None:
                try:
                    cur = self.db_manager.cursor
                    cur.execute(
                        """
                        SELECT tb.source_lang, tb.target_lang, tb.id
                        FROM termbase_terms t
                        JOIN termbases tb
                          ON CAST(t.termbase_id AS INTEGER) = tb.id
                        WHERE t.id = ?
                        """,
                        (self.term_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        tb_src, tb_tgt, real_tb_id = row[0] or '', row[1] or '', row[2]
                        if tb_src and tb_tgt:
                            src_lang_for_caption = tb_src
                            tgt_lang_for_caption = tb_tgt
                        # Backfill termbase_id with the real value
                        # whenever the caller's value disagrees — keeps
                        # subsequent save/delete operations consistent.
                        if real_tb_id is not None and self.termbase_id != real_tb_id:
                            self.termbase_id = real_tb_id
                except Exception:
                    pass  # fall through to project direction

            src_caption = self._language_display_name(src_lang_for_caption) or "Source"
            tgt_caption = self._language_display_name(tgt_lang_for_caption) or "Target"
        except Exception:
            pass

        # Stash so the synonym section labels below can reuse them.
        self._src_caption = src_caption
        self._tgt_caption = tgt_caption

        # Source column: term, abbreviation, (later) synonyms.
        # v1.10.78: caption QLabels stored as instance attributes so
        # the v1.10.78 related-entry switcher can update them when
        # the user picks a different termbase entry from the
        # dropdown (the new entry may have a different direction,
        # so the captions need to flip).
        source_col = QVBoxLayout()
        source_col.setSpacing(2)
        self._src_caption_label = QLabel(f"<b>{src_caption}:</b>")
        source_col.addWidget(self._src_caption_label)
        self.source_edit = QLineEdit(self.source_term)
        self.source_edit.setStyleSheet("padding: 4px;")
        source_col.addWidget(self.source_edit)
        source_col.addWidget(QLabel("Abbreviation:"))
        self.source_abbr_edit = QLineEdit()
        self.source_abbr_edit.setStyleSheet("padding: 4px;")
        source_col.addWidget(self.source_abbr_edit)

        # Target column: term, abbreviation, (later) synonyms
        target_col = QVBoxLayout()
        target_col.setSpacing(2)
        self._tgt_caption_label = QLabel(f"<b>{tgt_caption}:</b>")
        target_col.addWidget(self._tgt_caption_label)
        self.target_edit = QLineEdit(self.target_term)
        self.target_edit.setStyleSheet("padding: 4px;")
        target_col.addWidget(self.target_edit)
        target_col.addWidget(QLabel("Abbreviation:"))
        self.target_abbr_edit = QLineEdit()
        self.target_abbr_edit.setStyleSheet("padding: 4px;")
        target_col.addWidget(self.target_abbr_edit)

        # Stash the column layouts so the synonym group widgets built
        # later in this method can be dropped into the same columns as
        # their respective term/abbreviation pair.
        self._source_col_layout = source_col
        self._target_col_layout = target_col

        term_row.addLayout(source_col, 1)
        term_row.addLayout(target_col, 1)
        layout.addLayout(term_row)

        # Metadata fields
        meta_group = QGroupBox("Metadata (Optional)")
        meta_layout = QFormLayout()

        # Definition – Trados-style dedicated field, separate from notes.
        self.definition_edit = QTextEdit()
        self.definition_edit.setMaximumHeight(45)
        self.definition_edit.setPlaceholderText("Brief definition or gloss...")
        self.definition_edit.setStyleSheet("padding: 3px;")
        meta_layout.addRow("Definition:", self.definition_edit)

        # Domain
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("e.g., Patents, Legal, Medical, IT...")
        meta_layout.addRow("Domain:", self.domain_edit)

        # Notes
        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(45)
        self.notes_edit.setPlaceholderText("Usage notes, context...")
        self.notes_edit.setStyleSheet("padding: 3px;")
        meta_layout.addRow("Notes:", self.notes_edit)

        # URL (optional)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        meta_layout.addRow("URL:", self.url_edit)

        # Client
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Optional client name...")
        meta_layout.addRow("Client:", self.client_edit)

        # Project
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Optional project name...")
        meta_layout.addRow("Project:", self.project_edit)

        # Non-translatable checkbox – when ticked, the target field is
        # auto-synced to the source so the term copies through unchanged.
        self.nontranslatable_check = CheckmarkCheckBox(
            "Non-translatable (keep source text in target)"
        )
        self.nontranslatable_check.setToolTip(
            "Mark this entry as non-translatable. The source term is kept "
            "verbatim in the target whenever it appears in segments."
        )
        self.nontranslatable_check.toggled.connect(self._on_nontranslatable_toggled)
        meta_layout.addRow("", self.nontranslatable_check)

        # Forbidden term checkbox
        self.forbidden_check = CheckmarkCheckBox(
            "Forbidden term (warn when used in translation)"
        )
        self.forbidden_check.setToolTip("Forbidden terms trigger warnings when used")
        meta_layout.addRow("", self.forbidden_check)

        meta_group.setLayout(meta_layout)
        layout.addWidget(meta_group)

        # Source synonyms — always visible, added directly under the source
        # term column (no group box, tight spacing) to mirror the Trados term
        # editor and avoid wasted vertical space above the label.
        # v1.10.63: direction-aware caption; v1.10.78: stored as attribute for
        # the related-entry switcher.
        self._source_col_layout.addSpacing(6)
        self._source_syn_label = QLabel(f"{getattr(self, '_src_caption', 'Source')} synonyms:")
        self._source_syn_label.setStyleSheet("font-weight: bold;")
        self._source_col_layout.addWidget(self._source_syn_label)

        self.source_syn_content = QWidget()
        source_syn_layout = QVBoxLayout(self.source_syn_content)
        source_syn_layout.setContentsMargins(0, 0, 0, 0)

        # Input field + Add button + Forbidden checkbox
        source_add_layout = QHBoxLayout()
        self.source_synonym_edit = QLineEdit()
        self.source_synonym_edit.setPlaceholderText("Type synonym, press Enter or +")
        source_add_layout.addWidget(self.source_synonym_edit)

        self.source_synonym_forbidden_check = CheckmarkCheckBox("Forbidden")
        self.source_synonym_forbidden_check.setToolTip("Mark this source synonym as forbidden")
        source_add_layout.addWidget(self.source_synonym_forbidden_check)

        source_add_syn_btn = QPushButton("+")
        source_add_syn_btn.setMaximumWidth(30)
        source_add_syn_btn.setToolTip("Add synonym")
        source_add_syn_btn.clicked.connect(self.add_source_synonym)
        source_add_layout.addWidget(source_add_syn_btn)
        source_syn_layout.addLayout(source_add_layout)

        # Connect Enter key to add synonym
        self.source_synonym_edit.returnPressed.connect(self.add_source_synonym)

        # List of source synonyms with control buttons
        source_list_layout = QHBoxLayout()

        self.source_synonym_list = QListWidget()
        self.source_synonym_list.setMaximumHeight(100)
        self.source_synonym_list.setStyleSheet("QListWidget { background-color: #ffffff; }")
        self.source_synonym_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source_synonym_list.customContextMenuRequested.connect(self.show_source_synonym_context_menu)
        # v1.10.188: double-click promotes a synonym to the primary term
        # (Trados-style). Swaps the current primary text into the synonym
        # slot so no information is lost — the old primary becomes a
        # synonym, the chosen synonym becomes the primary.
        self.source_synonym_list.itemDoubleClicked.connect(
            lambda item: self._promote_synonym_to_primary(
                self.source_synonym_list, self.source_edit, item
            )
        )
        self.source_synonym_list.setToolTip(
            "Double-click to promote to the primary term\n"
            "Right-click for more options"
        )
        source_list_layout.addWidget(self.source_synonym_list)

        # Up/Down buttons for source synonyms
        source_button_col = QVBoxLayout()
        source_move_up_btn = QPushButton("▲")
        source_move_up_btn.setToolTip("Move synonym up (higher priority)")
        source_move_up_btn.setMaximumWidth(30)
        source_move_up_btn.clicked.connect(self.move_source_synonym_up)
        source_button_col.addWidget(source_move_up_btn)

        source_move_down_btn = QPushButton("▼")
        source_move_down_btn.setToolTip("Move synonym down (lower priority)")
        source_move_down_btn.setMaximumWidth(30)
        source_move_down_btn.clicked.connect(self.move_source_synonym_down)
        source_button_col.addWidget(source_move_down_btn)

        source_button_col.addStretch()

        source_delete_btn = QPushButton("✗")
        source_delete_btn.setToolTip("Delete synonym")
        source_delete_btn.setMaximumWidth(30)
        source_delete_btn.clicked.connect(self.delete_selected_source_synonym)
        source_button_col.addWidget(source_delete_btn)

        source_list_layout.addLayout(source_button_col)
        source_syn_layout.addLayout(source_list_layout)

        self._source_col_layout.addWidget(self.source_syn_content)

        # Target synonyms — always visible, added directly under the target
        # term column (no group box). See source counterpart above.
        self._target_col_layout.addSpacing(6)
        self._target_syn_label = QLabel(f"{getattr(self, '_tgt_caption', 'Target')} synonyms:")
        self._target_syn_label.setStyleSheet("font-weight: bold;")
        self._target_col_layout.addWidget(self._target_syn_label)

        self.target_syn_content = QWidget()
        target_syn_layout = QVBoxLayout(self.target_syn_content)
        target_syn_layout.setContentsMargins(0, 0, 0, 0)

        # Input field + Add button + Forbidden checkbox
        target_add_layout = QHBoxLayout()
        self.target_synonym_edit = QLineEdit()
        self.target_synonym_edit.setPlaceholderText("Type synonym, press Enter or +")
        target_add_layout.addWidget(self.target_synonym_edit)

        self.target_synonym_forbidden_check = CheckmarkCheckBox("Forbidden")
        self.target_synonym_forbidden_check.setToolTip("Mark this synonym as forbidden (warning when used)")
        target_add_layout.addWidget(self.target_synonym_forbidden_check)

        target_add_syn_btn = QPushButton("+")
        target_add_syn_btn.setMaximumWidth(30)
        target_add_syn_btn.setToolTip("Add synonym")
        target_add_syn_btn.clicked.connect(self.add_target_synonym)
        target_add_layout.addWidget(target_add_syn_btn)
        target_syn_layout.addLayout(target_add_layout)

        # Connect Enter key to add synonym
        self.target_synonym_edit.returnPressed.connect(self.add_target_synonym)

        # List of target synonyms with control buttons
        target_list_layout = QHBoxLayout()

        self.target_synonym_list = QListWidget()
        self.target_synonym_list.setMaximumHeight(100)
        self.target_synonym_list.setStyleSheet("QListWidget { background-color: #ffffff; }")
        self.target_synonym_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.target_synonym_list.customContextMenuRequested.connect(self.show_target_synonym_context_menu)
        # v1.10.188: double-click promotes a synonym to the primary term
        # (Trados-style). See source_synonym_list above for details.
        self.target_synonym_list.itemDoubleClicked.connect(
            lambda item: self._promote_synonym_to_primary(
                self.target_synonym_list, self.target_edit, item
            )
        )
        self.target_synonym_list.setToolTip(
            "Double-click to promote to the primary term\n"
            "Right-click for more options"
        )
        target_list_layout.addWidget(self.target_synonym_list)

        # Up/Down buttons for target synonyms
        target_button_col = QVBoxLayout()
        target_move_up_btn = QPushButton("▲")
        target_move_up_btn.setToolTip("Move synonym up (higher priority)")
        target_move_up_btn.setMaximumWidth(30)
        target_move_up_btn.clicked.connect(self.move_target_synonym_up)
        target_button_col.addWidget(target_move_up_btn)

        target_move_down_btn = QPushButton("▼")
        target_move_down_btn.setToolTip("Move synonym down (lower priority)")
        target_move_down_btn.setMaximumWidth(30)
        target_move_down_btn.clicked.connect(self.move_target_synonym_down)
        target_button_col.addWidget(target_move_down_btn)

        target_button_col.addStretch()

        target_delete_btn = QPushButton("✗")
        target_delete_btn.setToolTip("Delete synonym")
        target_delete_btn.setMaximumWidth(30)
        target_delete_btn.clicked.connect(self.delete_selected_target_synonym)
        target_button_col.addWidget(target_delete_btn)

        target_list_layout.addLayout(target_button_col)
        target_syn_layout.addLayout(target_list_layout)

        self._target_col_layout.addWidget(self.target_syn_content)

        # Buttons row
        button_layout = QHBoxLayout()

        # Delete button (only when editing an existing term)
        if self.term_id is not None:
            self.delete_btn = QPushButton("🗑️ Delete")
            self.delete_btn.setStyleSheet("""
                QPushButton {
                    padding: 8px 20px;
                    font-size: 11px;
                    font-weight: bold;
                    background-color: #f44336;
                    color: white;
                    border: none;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #d32f2f;
                }
                QPushButton:focus {
                    outline: none;
                }
            """)
            self.delete_btn.clicked.connect(self.delete_term)
            button_layout.addWidget(self.delete_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("💾 Save")
        save_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; "
            "padding: 5px 15px; border: none; outline: none;"
        )
        # Edit mode persists straight to the DB; add mode just accepts and
        # leaves the INSERT to the caller (which reads back via getters).
        if self.term_id is not None:
            save_btn.clicked.connect(self.save_term)
        else:
            save_btn.clicked.connect(self._accept_and_save)
        save_btn.setDefault(True)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        # Set the scroll area content
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)

        # Open at a size that shows the whole form without scrolling, capped at
        # the 85%-of-screen maximum set in __init__. (The synonym panels are now
        # always expanded, so the old fixed compact height forced scrolling.)
        content_widget.adjustSize()
        hint = content_widget.sizeHint()
        self.resize(
            max(self.width(), hint.width() + 24),
            min(hint.height() + 24, self.maximumHeight()),
        )

    # ========================================================================
    # NON-TRANSLATABLE MIRROR
    # ========================================================================

    def _on_nontranslatable_toggled(self, checked: bool):
        """When NT is turned on, mirror source into target so the entry
        renders as a copy-through. Untoggling leaves whatever the user
        last typed in the target field – they can edit it freely again.
        """
        if checked:
            source_text = self.source_edit.text().strip() if hasattr(self, 'source_edit') else ''
            if source_text:
                self.target_edit.setText(source_text)

    # ========================================================================
    # SOURCE SYNONYM METHODS
    # ========================================================================

    def add_source_synonym(self):
        """Add a source synonym to the list."""
        synonym = self.source_synonym_edit.text().strip()
        if synonym:
            # Check for duplicates
            for i in range(self.source_synonym_list.count()):
                item = self.source_synonym_list.item(i)
                item_text = item.data(Qt.ItemDataRole.UserRole).get('text', '')
                if item_text == synonym:
                    QMessageBox.warning(self, "Duplicate", f"Source synonym '{synonym}' already added.")
                    return

            # Don't allow the main source term as a synonym
            if synonym.lower() == self.source_term.lower():
                QMessageBox.warning(self, "Invalid Synonym", "Cannot add the main source term as a synonym.")
                return

            # Create list item with stored data
            is_forbidden = self.source_synonym_forbidden_check.isChecked()
            display_text = f"{'🚫 ' if is_forbidden else ''}{synonym}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, {
                'text': synonym,
                'forbidden': is_forbidden
            })

            if is_forbidden:
                item.setForeground(QColor('#d32f2f'))

            self.source_synonym_list.addItem(item)
            self.source_synonym_edit.clear()
            self.source_synonym_forbidden_check.setChecked(False)
            self.source_synonym_edit.setFocus()

    def move_source_synonym_up(self):
        """Move selected source synonym up in the list."""
        current_row = self.source_synonym_list.currentRow()
        if current_row > 0:
            item = self.source_synonym_list.takeItem(current_row)
            self.source_synonym_list.insertItem(current_row - 1, item)
            self.source_synonym_list.setCurrentRow(current_row - 1)

    def move_source_synonym_down(self):
        """Move selected source synonym down in the list."""
        current_row = self.source_synonym_list.currentRow()
        if current_row < self.source_synonym_list.count() - 1 and current_row >= 0:
            item = self.source_synonym_list.takeItem(current_row)
            self.source_synonym_list.insertItem(current_row + 1, item)
            self.source_synonym_list.setCurrentRow(current_row + 1)

    def delete_selected_source_synonym(self):
        """Delete selected source synonym."""
        current_row = self.source_synonym_list.currentRow()
        if current_row >= 0:
            self.source_synonym_list.takeItem(current_row)

    # ========================================================================
    # SYNONYM PROMOTION (v1.10.188)
    # ========================================================================

    def _promote_synonym_to_primary(self, synonym_list, primary_edit, item):
        """Promote a synonym to the primary term and demote the previous
        primary to the synonym slot.

        Called from itemDoubleClicked on either the source or target
        synonym list. Mirrors the behaviour of the Trados plugin's
        TermEntryEditorDialog.PromoteToPrimary (double-click on a row in
        either synonym list swaps the row's text with the primary
        term's text, so no information is lost).

        Notes:
        - Forbidden flag stays with the synonym entry, not with the
          text — i.e. if the user double-clicks a 🚫-marked synonym,
          the OLD primary becomes a 🚫-marked synonym after the swap.
          This matches the Trados behaviour (forbidden is a property of
          the synonym slot, not of the term) and is almost always what
          the user wants: they're correcting which translation is the
          canonical one, not changing whether it's forbidden.
        - If the previous primary was empty, the swap still happens —
          an empty string lands in the synonym row. The user can delete
          the empty row with the ✕ button if they want. This is rare
          enough (why would you have a synonym for an empty primary?)
          to not warrant special-case logic.
        """
        if item is None:
            return

        # Pull the synonym data
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        syn_text = (data.get('text') or '').strip()
        if not syn_text:
            return  # blank synonym — nothing to promote

        # Read and overwrite the primary
        old_primary = primary_edit.text().strip()
        primary_edit.setText(syn_text)

        # Put the old primary into the synonym row, keeping the row's
        # forbidden flag. Re-render the row text to match the new content
        # (and the existing forbidden indicator).
        new_data = {
            'text': old_primary,
            'forbidden': bool(data.get('forbidden', False)),
        }
        display_text = f"{'🚫 ' if new_data['forbidden'] else ''}{old_primary}"
        item.setData(Qt.ItemDataRole.UserRole, new_data)
        item.setText(display_text)
        if new_data['forbidden']:
            item.setForeground(QColor('#d32f2f'))
        else:
            # Clear the red colour if it was set on the previous text – back
            # to the theme's colour, not black, which was unreadable on the
            # dark theme (issue #78)
            item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def show_source_synonym_context_menu(self, position):
        """Show context menu for source synonym list."""
        if self.source_synonym_list.count() == 0:
            return

        current_item = self.source_synonym_list.currentItem()
        if not current_item:
            return

        menu = QMenu()

        # Toggle forbidden status
        data = current_item.data(Qt.ItemDataRole.UserRole)
        is_forbidden = data.get('forbidden', False)

        if is_forbidden:
            toggle_action = menu.addAction("Mark as Allowed")
        else:
            toggle_action = menu.addAction("Mark as Forbidden")

        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        action = menu.exec(self.source_synonym_list.mapToGlobal(position))

        if action == toggle_action:
            # Toggle forbidden status
            data['forbidden'] = not is_forbidden
            text = data['text']
            display_text = f"{'🚫 ' if data['forbidden'] else ''}{text}"
            current_item.setText(display_text)
            current_item.setData(Qt.ItemDataRole.UserRole, data)

            if data['forbidden']:
                current_item.setForeground(QColor('#d32f2f'))
            else:
                # the theme's colour, not black (unreadable on the dark theme, #78)
                current_item.setData(Qt.ItemDataRole.ForegroundRole, None)

        elif action == delete_action:
            self.source_synonym_list.takeItem(self.source_synonym_list.row(current_item))

    def get_source_synonyms(self):
        """Return list of source synonym dictionaries with text, forbidden flag, and order."""
        synonyms = []
        for i in range(self.source_synonym_list.count()):
            item = self.source_synonym_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            synonyms.append({
                'text': data['text'],
                'forbidden': data['forbidden'],
                'order': i
            })
        return synonyms

    # ========================================================================
    # TARGET SYNONYM METHODS
    # ========================================================================

    def add_target_synonym(self):
        """Add a target synonym to the list."""
        synonym = self.target_synonym_edit.text().strip()
        if synonym:
            # Check for duplicates
            for i in range(self.target_synonym_list.count()):
                item = self.target_synonym_list.item(i)
                item_text = item.data(Qt.ItemDataRole.UserRole).get('text', '')
                if item_text == synonym:
                    QMessageBox.warning(self, "Duplicate", f"Synonym '{synonym}' already added.")
                    return

            # Don't allow the main target term as a synonym
            if synonym.lower() == self.target_term.lower():
                QMessageBox.warning(self, "Invalid Synonym", "Cannot add the main target term as a synonym.")
                return

            # Create list item with stored data
            is_forbidden = self.target_synonym_forbidden_check.isChecked()
            display_text = f"{'🚫 ' if is_forbidden else ''}{synonym}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, {
                'text': synonym,
                'forbidden': is_forbidden
            })

            if is_forbidden:
                item.setForeground(QColor('#d32f2f'))

            self.target_synonym_list.addItem(item)
            self.target_synonym_edit.clear()
            self.target_synonym_forbidden_check.setChecked(False)
            self.target_synonym_edit.setFocus()

    def move_target_synonym_up(self):
        """Move selected target synonym up in the list."""
        current_row = self.target_synonym_list.currentRow()
        if current_row > 0:
            item = self.target_synonym_list.takeItem(current_row)
            self.target_synonym_list.insertItem(current_row - 1, item)
            self.target_synonym_list.setCurrentRow(current_row - 1)

    def move_target_synonym_down(self):
        """Move selected target synonym down in the list."""
        current_row = self.target_synonym_list.currentRow()
        if current_row < self.target_synonym_list.count() - 1 and current_row >= 0:
            item = self.target_synonym_list.takeItem(current_row)
            self.target_synonym_list.insertItem(current_row + 1, item)
            self.target_synonym_list.setCurrentRow(current_row + 1)

    def delete_selected_target_synonym(self):
        """Delete selected target synonym."""
        current_row = self.target_synonym_list.currentRow()
        if current_row >= 0:
            self.target_synonym_list.takeItem(current_row)

    def show_target_synonym_context_menu(self, position):
        """Show context menu for target synonym list."""
        if self.target_synonym_list.count() == 0:
            return

        current_item = self.target_synonym_list.currentItem()
        if not current_item:
            return

        menu = QMenu()

        # Toggle forbidden status
        data = current_item.data(Qt.ItemDataRole.UserRole)
        is_forbidden = data.get('forbidden', False)

        if is_forbidden:
            toggle_action = menu.addAction("Mark as Allowed")
        else:
            toggle_action = menu.addAction("Mark as Forbidden")

        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        action = menu.exec(self.target_synonym_list.mapToGlobal(position))

        if action == toggle_action:
            # Toggle forbidden status
            data['forbidden'] = not is_forbidden
            text = data['text']
            display_text = f"{'🚫 ' if data['forbidden'] else ''}{text}"
            current_item.setText(display_text)
            current_item.setData(Qt.ItemDataRole.UserRole, data)

            if data['forbidden']:
                current_item.setForeground(QColor('#d32f2f'))
            else:
                # the theme's colour, not black (unreadable on the dark theme, #78)
                current_item.setData(Qt.ItemDataRole.ForegroundRole, None)

        elif action == delete_action:
            self.target_synonym_list.takeItem(self.target_synonym_list.row(current_item))

    def get_target_synonyms(self):
        """Return list of target synonym dictionaries with text, forbidden flag, and order."""
        synonyms = []
        for i in range(self.target_synonym_list.count()):
            item = self.target_synonym_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            synonyms.append({
                'text': data['text'],
                'forbidden': data['forbidden'],
                'order': i
            })
        return synonyms

    # ========================================================================
    # ADD-MODE GETTERS
    # ========================================================================

    def get_source_term(self):
        """Return the (possibly edited) source term."""
        return self.source_edit.text().strip()

    def get_target_term(self):
        """Return the (possibly edited) target term."""
        return self.target_edit.text().strip()

    def get_metadata(self):
        """Return dictionary of metadata fields."""
        return {
            'definition': self.definition_edit.toPlainText().strip(),
            'domain': self.domain_edit.text().strip(),
            'notes': self.notes_edit.toPlainText().strip(),
            'url': self.url_edit.text().strip(),
            'project': self.project_edit.text().strip(),
            'client': self.client_edit.text().strip(),
            'source_abbreviation': self.source_abbr_edit.text().strip(),
            'target_abbreviation': self.target_abbr_edit.text().strip(),
            'forbidden': self.forbidden_check.isChecked(),
            'is_nontranslatable': self.nontranslatable_check.isChecked(),
        }

    def get_selected_termbases(self):
        """Compatibility shim – the dialog no longer asks the user to pick
        target glossaries. The caller computes the destination set from
        the Termbases tab's Read/Write toggles. Always returns an empty
        list; existing call sites that pass it through to the insert
        logic should treat empty-from-dialog as "use writable defaults"."""
        return []

    def _accept_and_save(self):
        """Save termbase selections (no-op shim) and accept the dialog."""
        self._save_termbase_selections()
        self.accept()

    # ========================================================================
    # EDIT-MODE: DB LOAD / SAVE / DELETE
    # ========================================================================

    # ------------------------------------------------------------------
    # Diagnostic log helper (v1.10.64)
    # ------------------------------------------------------------------
    # Routes log lines to the host window's log() if reachable; falls
    # back to print() otherwise. Used to instrument the load/save cycle
    # so a future "synonyms vanish on save" bug report can be triaged
    # from the session log rather than re-running the user's exact
    # click path.
    def _diag_log(self, msg: str):
        try:
            ancestor = self.parent()
            while ancestor is not None and not hasattr(ancestor, 'log'):
                ancestor = ancestor.parent() if callable(getattr(ancestor, 'parent', None)) else None
            if ancestor is not None:
                ancestor.log(msg)
                return
        except Exception:
            pass
        try:
            print(msg)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # v1.10.78 — Related-entries dropdown ("Editing in:" switcher)
    # ──────────────────────────────────────────────────────────────
    # Mirrors the Trados Edit Term Entry dialog. When the loaded term's
    # source word has entries in multiple termbases (or multiple
    # entries in the same termbase, e.g. one normal-direction and one
    # reverse-direction), the dropdown at the top of the dialog lets
    # the user switch between them in place without closing the dialog
    # and re-opening it on a different chip.
    #
    # Population (``_populate_related_entries_combo``) runs at the end
    # of every successful ``load_term_data`` — including after a
    # switch, so the dropdown stays consistent.
    #
    # The match is bidirectional: an entry is "related" if its
    # ``source_term`` OR ``target_term`` (case-insensitive) matches
    # the loaded entry's ``source_term`` OR ``target_term``. So for
    # a loaded entry "inrichting → device":
    #  - All entries with source_term="inrichting" are included
    #    (other termbases' normal-direction entries for this NL term)
    #  - All entries with target_term="inrichting" are included
    #    (reverse-direction entries — EN→NL termbases where the
    #    Dutch word lives in the target column)
    #  - All entries with source_term="device" are included
    #  - All entries with target_term="device" are included
    # Matches Trados's "anything containing this surface form".

    def _populate_related_entries_combo(self):
        """Query the DB for all entries sharing this entry's source
        or target text (case-insensitive, bidirectional). If more
        than one result, populate + show the dropdown; otherwise
        hide it. Idempotent — safe to call repeatedly.

        v1.10.79: filtered to **active termbases only** for the
        current project (plus the loaded entry's termbase
        unconditionally, in case the user opened the dialog on an
        inactive termbase via the Termbases tab editor). Matches
        Trados's Edit Term Entry dialog behaviour, and crucial for
        users with dozens or hundreds of termbases — the
        unfiltered v1.10.78 query would dump every termbase in the
        DB that happened to share the surface form, drowning the
        actually-relevant project termbase entries.
        """
        if (not self.db_manager) or (not self.term_id) or (not self.term_data):
            return
        try:
            src = (self.term_data.get('source_term') or '').strip()
            tgt = (self.term_data.get('target_term') or '').strip()
            if not (src or tgt):
                return

            # Resolve the current project id by walking the parent
            # chain to the main window (same pattern setup_ui uses
            # for the caption query). Falls back to None if we
            # can't find a project, in which case the activation
            # filter is skipped — we behave like the v1.10.78
            # unfiltered query rather than returning zero rows.
            project_id = None
            try:
                ancestor = self.parent()
                while ancestor is not None and not hasattr(ancestor, 'current_project'):
                    ancestor = ancestor.parent() if callable(getattr(ancestor, 'parent', None)) else None
                proj = getattr(ancestor, 'current_project', None) if ancestor else None
                if proj is not None:
                    project_id = getattr(proj, 'id', None)
            except Exception:
                pass

            cur = self.db_manager.cursor
            # Build the "surface-form match" WHERE conditions.
            # Bidirectional case-insensitive against both the loaded
            # entry's source_term AND target_term. Skip empty needles
            # so we don't false-match every empty column.
            params = []
            conds = []
            for needle in (src, tgt):
                if needle:
                    conds.append("LOWER(t.source_term) = LOWER(?)")
                    conds.append("LOWER(t.target_term) = LOWER(?)")
                    params.extend([needle, needle])
            surface_match = "(" + " OR ".join(conds) + ")"

            if project_id is not None:
                # v1.10.79 activation filter (v1.10.360: the legacy
                # ``tb.is_project_termbase = 1`` escape hatch is gone —
                # the project termbase always has an active activation
                # row now, and a stale flag used to resurrect inactive
                # termbases into the dropdown):
                #
                #   - The termbase must be active for this project
                #     (``ta.is_active = 1``), OR be the
                #     loaded entry's own termbase (defensive — covers
                #     the case where the dialog was opened on an
                #     inactive entry via the Termbases tab editor,
                #     in which case hiding it from the dropdown
                #     would be very confusing).
                #
                #   - The trailing ``t.id = ?`` clause ensures the
                #     loaded entry itself is always in the result
                #     set even if every other condition fails — so
                #     the dropdown is never empty when the dialog is
                #     successfully showing an entry.
                sql = f"""
                    SELECT t.id,
                           t.source_term,
                           t.target_term,
                           COALESCE(tb.name, '?') as tb_name,
                           tb.id as tb_id
                    FROM termbase_terms t
                    LEFT JOIN termbases tb
                        ON CAST(t.termbase_id AS INTEGER) = tb.id
                    LEFT JOIN termbase_activation ta
                        ON ta.termbase_id = tb.id
                        AND ta.project_id = ?
                    WHERE {surface_match}
                      AND (ta.is_active = 1
                           OR t.id = ?)
                    ORDER BY tb.name, t.id
                """
                params = [project_id] + params + [self.term_id]
            else:
                # No project context — fall back to the v1.10.78
                # unfiltered query (better than returning nothing).
                sql = f"""
                    SELECT t.id,
                           t.source_term,
                           t.target_term,
                           COALESCE(tb.name, '?') as tb_name,
                           tb.id as tb_id
                    FROM termbase_terms t
                    LEFT JOIN termbases tb
                        ON CAST(t.termbase_id AS INTEGER) = tb.id
                    WHERE {surface_match}
                    ORDER BY tb.name, t.id
                """
            cur.execute(sql, params)
            rows = cur.fetchall()
        except Exception:
            return

        # Filter to unique term_ids (defensive — the bidirectional OR
        # could in theory match the same row twice if e.g. source
        # and target text are identical strings).
        seen = set()
        entries = []
        for row in rows:
            tid = row[0]
            if tid in seen:
                continue
            seen.add(tid)
            entries.append({
                'term_id': tid,
                'source_term': row[1] or '',
                'target_term': row[2] or '',
                'termbase_name': row[3] or '?',
                'termbase_id': row[4],
            })

        # Hide the row entirely if there's only one entry (or none).
        if len(entries) <= 1:
            self._related_container.setVisible(False)
            return

        # Populate the combo. Suppress the change signal while we
        # rebuild so setCurrentIndex doesn't fire a phantom switch
        # to the entry we're already on.
        self._related_combo_suppress = True
        try:
            self._related_combo.clear()
            current_idx = 0
            for i, e in enumerate(entries):
                label = f"{e['termbase_name']}: {e['source_term']} → {e['target_term']}"
                # Stash the term_id on the item via UserData so the
                # change handler can look it up by index without
                # parsing the label text.
                self._related_combo.addItem(label, e['term_id'])
                if e['term_id'] == self.term_id:
                    current_idx = i
            self._related_combo.setCurrentIndex(current_idx)
        finally:
            self._related_combo_suppress = False
        self._related_container.setVisible(True)

    def _on_related_entry_selected(self, idx: int):
        """Dropdown selection changed — load the chosen entry's data.

        No "unsaved changes" check for now: matches the Trados dialog,
        which also switches in place without prompting. The Save
        button only acts on the currently-loaded entry, so unsaved
        edits to entry A are simply discarded when the user picks
        entry B. Add an explicit warning in a future iteration if
        users actually hit this.
        """
        if self._related_combo_suppress:
            return
        if idx < 0:
            return
        try:
            new_term_id = self._related_combo.itemData(idx)
            if new_term_id is None or new_term_id == self.term_id:
                return
            self._switch_to_term_id(int(new_term_id))
        except Exception as e:
            try:
                self._diag_log(f"[TermbaseEntryEditor] switch-entry failed: {e}")
            except Exception:
                pass

    def _switch_to_term_id(self, new_term_id: int):
        """Switch the dialog to edit a different term entry in place.

        Steps:
          1. Update self.term_id (which load_term_data + the caption
             query both key off).
          2. Re-query the termbase direction for the new term and
             update the column / synonym section captions.
          3. Clear the existing form fields (so leftover values
             from the previous entry don't bleed through if the
             new entry has empty fields).
          4. Call load_term_data which repopulates everything from
             the DB AND re-runs ``_populate_related_entries_combo``
             so the dropdown stays consistent.
          5. Update the window title to show the new entry's ID +
             termbase name.
        """
        self.term_id = new_term_id

        # Clear synonym lists — load_synonyms() appends without
        # clearing first, so without this we'd accumulate across
        # switches.
        try:
            self.source_synonym_list.clear()
            self.target_synonym_list.clear()
        except Exception:
            pass

        # Update captions from the new termbase's declared direction.
        self._update_captions_from_termbase()

        # Reload everything for the new entry. load_term_data already
        # calls _populate_related_entries_combo at the end on success.
        self.load_term_data()

        # Refresh the window title with the new entry's id + termbase.
        try:
            tb_name = ''
            if self.db_manager and self.termbase_id is not None:
                cur = self.db_manager.cursor
                cur.execute(
                    "SELECT name FROM termbases WHERE id = ?",
                    (self.termbase_id,),
                )
                r = cur.fetchone()
                if r:
                    tb_name = r[0] or ''
            title = "Edit Termbase Entry"
            if self.term_id is not None:
                title += f" (ID {self.term_id})"
            if tb_name:
                title += f" — {tb_name}"
            self.setWindowTitle(title)
        except Exception:
            pass

    def _update_captions_from_termbase(self):
        """Re-query the termbase's declared direction for the
        current ``self.term_id`` and update the column-caption
        QLabels (and synonym-section labels) accordingly. Called by
        ``_switch_to_term_id`` so a switch to a different-direction
        termbase's entry flips the captions to match.
        """
        if (self.db_manager is None) or (self.term_id is None):
            return
        try:
            cur = self.db_manager.cursor
            cur.execute(
                """
                SELECT tb.source_lang, tb.target_lang, tb.id
                FROM termbase_terms t
                JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
                WHERE t.id = ?
                """,
                (self.term_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            tb_src, tb_tgt, real_tb_id = row[0] or '', row[1] or '', row[2]
            if not (tb_src and tb_tgt):
                return
            if real_tb_id is not None:
                self.termbase_id = real_tb_id
            src = self._language_display_name(tb_src) or "Source"
            tgt = self._language_display_name(tb_tgt) or "Target"
            self._src_caption = src
            self._tgt_caption = tgt
            if hasattr(self, '_src_caption_label'):
                self._src_caption_label.setText(f"<b>{src}:</b>")
            if hasattr(self, '_tgt_caption_label'):
                self._tgt_caption_label.setText(f"<b>{tgt}:</b>")
            if hasattr(self, '_source_syn_label'):
                self._source_syn_label.setText(f"{src} Synonyms (Optional)")
            if hasattr(self, '_target_syn_label'):
                self._target_syn_label.setText(f"{tgt} Synonyms (Optional)")
        except Exception:
            pass

    def load_term_data(self):
        """Load existing term data from the database (edit mode only)."""
        if not self.db_manager or not self.term_id:
            return

        try:
            cursor = self.db_manager.cursor
            # is_nontranslatable wrapped in COALESCE so legacy databases
            # that have not yet had the migration run come back as 0
            # rather than blowing up the SELECT.
            # COALESCE on the new url / abbreviation columns so legacy
            # databases that haven't run the v1.9.478 migration yet still
            # return rows rather than blowing up the SELECT.
            cursor.execute("""
                SELECT source_term, target_term, domain, definition, forbidden,
                       notes, project, client,
                       COALESCE(is_nontranslatable, 0),
                       COALESCE(url, ''),
                       COALESCE(source_abbreviation, ''),
                       COALESCE(target_abbreviation, '')
                FROM termbase_terms
                WHERE id = ?
            """, (self.term_id,))

            row = cursor.fetchone()
            if row:
                self.term_data = {
                    'source_term': row[0],
                    'target_term': row[1],
                    'domain': row[2] or '',
                    'definition': row[3] or '',
                    'forbidden': row[4] or False,
                    'notes': row[5] or '',
                    'project': row[6] or '',
                    'client': row[7] or '',
                    'is_nontranslatable': bool(row[8]),
                    'url': row[9] or '',
                    'source_abbreviation': row[10] or '',
                    'target_abbreviation': row[11] or '',
                }
                # v1.10.64 diagnostic: log what the dialog actually
                # loaded from the DB so any future "values are wrong"
                # report can be checked against ground truth without
                # poking the DB by hand.
                self._diag_log(
                    f"[TermbaseEntryEditor] LOAD term_id={self.term_id} tb_id={self.termbase_id} "
                    f"src='{row[0]}' tgt='{row[1]}'"
                )

                # Populate fields.
                #
                # v1.10.70 regression fix: in v1.10.67 the LOAD MISS
                # else-branch below was added with the wrong
                # indentation, which inadvertently moved this entire
                # populate block INTO the else and AFTER its
                # ``return`` — making it unreachable in BOTH branches.
                # Effect on users: the LOAD diagnostic fired (visible
                # in the session log) but the fields displayed empty,
                # because setText was never actually called. Reported
                # by a user who right-clicked a freshly-added term
                # 'pipe.' → 'pijp' (term_id=93203 in PATENTS) and saw
                # an empty dialog despite the log line confirming the
                # row had been fetched. This restores the populate
                # block to its rightful place under ``if row:``.
                self.source_edit.setText(self.term_data['source_term'])
                self.target_edit.setText(self.term_data['target_term'])
                self.source_abbr_edit.setText(self.term_data['source_abbreviation'])
                self.target_abbr_edit.setText(self.term_data['target_abbreviation'])
                self.domain_edit.setText(self.term_data['domain'])
                self.definition_edit.setPlainText(self.term_data['definition'])
                self.notes_edit.setPlainText(self.term_data['notes'])
                self.url_edit.setText(self.term_data['url'])
                self.project_edit.setText(self.term_data['project'])
                self.client_edit.setText(self.term_data['client'])
                self.forbidden_check.setChecked(self.term_data['forbidden'])
                # Block the toggled signal on initial load so populating the
                # checkbox doesn't mirror source into target and overwrite
                # an intentionally-different target on a legacy NT entry.
                self.nontranslatable_check.blockSignals(True)
                self.nontranslatable_check.setChecked(self.term_data['is_nontranslatable'])
                self.nontranslatable_check.blockSignals(False)

                # Mirror source/target term into the instance attrs so the
                # synonym-add validation ("can't be the same as the main
                # term") checks against the freshly-loaded values rather
                # than the empty add-mode defaults.
                self.source_term = self.term_data['source_term']
                self.target_term = self.term_data['target_term']

                # Load synonyms
                self.load_synonyms()
                # v1.10.78 — populate the related-entries dropdown so
                # the user can switch between sibling entries from
                # other termbases that share this surface term. Done
                # here (end of successful load) rather than in
                # setup_ui because we need the loaded source_term /
                # target_term to seed the query.
                try:
                    self._populate_related_entries_combo()
                except Exception as _e:
                    # Non-critical — if the lookup fails the dropdown
                    # just stays hidden and the dialog works as before.
                    pass
            else:
                # v1.10.67: row is None — term_id doesn't match any
                # row in termbase_terms. Surfacing an empty dialog
                # silently is worse than telling the user, because:
                #  - they'll edit empty fields and the Save UPDATE
                #    will hit zero rows (no harm but no save either)
                #  - they have no idea their click landed on a
                #    stale/deleted reference until they re-open and
                #    see it's still gone
                # Show the diagnostic, warn the user, and close the
                # dialog so the caller's refresh handler can run
                # (typically a re-search of the current segment, which
                # will drop the stale TermLens pill on the next pass).
                self._diag_log(
                    f"[TermbaseEntryEditor] LOAD MISS term_id={self.term_id} "
                    f"tb_id={self.termbase_id}: no row in termbase_terms. "
                    f"Likely a stale TermLens entry (term was deleted in another session, "
                    f"or the in-memory index has a wrong term_id)."
                )
                QMessageBox.warning(
                    self,
                    "Term not found",
                    "This termbase entry could not be loaded — it may have been "
                    "deleted from the database already. The TermLens display is "
                    "probably showing a stale reference.\n\n"
                    "Close this dialog and refresh the segment (re-click it or "
                    "press F5) to clear the stale pill.",
                )
                # Defer reject() until after __init__ returns so the
                # caller's .exec() actually opens and immediately
                # closes (rather than blowing up in the constructor).
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.reject)
                return

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load term data: {e}")

    def load_synonyms(self):
        """Load synonyms for the current term (edit mode only)."""
        if not self.db_manager or not self.term_id:
            return

        try:
            cursor = self.db_manager.cursor

            # Check if forbidden column exists (backward compatibility)
            cursor.execute("PRAGMA table_info(termbase_synonyms)")
            columns = [row[1] for row in cursor.fetchall()]
            has_forbidden = 'forbidden' in columns
            has_display_order = 'display_order' in columns

            # Load source synonyms
            if has_forbidden and has_display_order:
                cursor.execute("""
                    SELECT synonym_text, forbidden FROM termbase_synonyms
                    WHERE term_id = ? AND language = 'source'
                    ORDER BY display_order ASC
                """, (self.term_id,))
            else:
                cursor.execute("""
                    SELECT synonym_text FROM termbase_synonyms
                    WHERE term_id = ? AND language = 'source'
                    ORDER BY created_date ASC
                """, (self.term_id,))

            for row in cursor.fetchall():
                text = row[0]
                forbidden = bool(row[1]) if has_forbidden and len(row) > 1 else False
                display = f"{'🚫 ' if forbidden else ''}{text}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, {'text': text, 'forbidden': forbidden})
                if forbidden:
                    item.setForeground(QColor('#d32f2f'))
                self.source_synonym_list.addItem(item)

            # Load target synonyms
            if has_forbidden and has_display_order:
                cursor.execute("""
                    SELECT synonym_text, forbidden FROM termbase_synonyms
                    WHERE term_id = ? AND language = 'target'
                    ORDER BY display_order ASC
                """, (self.term_id,))
            else:
                cursor.execute("""
                    SELECT synonym_text FROM termbase_synonyms
                    WHERE term_id = ? AND language = 'target'
                    ORDER BY created_date ASC
                """, (self.term_id,))

            for row in cursor.fetchall():
                text = row[0]
                forbidden = bool(row[1]) if has_forbidden and len(row) > 1 else False
                display = f"{'🚫 ' if forbidden else ''}{text}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, {'text': text, 'forbidden': forbidden})
                if forbidden:
                    item.setForeground(QColor('#d32f2f'))
                self.target_synonym_list.addItem(item)

        except Exception as e:
            # Silently fail for backward compatibility
            print(f"Warning: Could not load synonyms: {e}")

    def delete_term(self):
        """Delete this term from the database (edit mode only)."""
        if not self.db_manager or not self.term_id:
            return

        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Delete this termbase entry?\n\nSource: {self.source_edit.text()}\nTarget: {self.target_edit.text()}\n\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                cursor = self.db_manager.cursor
                cursor.execute("DELETE FROM termbase_terms WHERE id = ?", (self.term_id,))
                self.db_manager.connection.commit()
                # v1.10.64: defensive post-delete refresh in case the
                # outer caller doesn't run one. Walks the parent chain
                # to find an ancestor with _post_termbase_delete_refresh
                # (Supervertaler.py main window) and calls it. Safe
                # no-op if not found — the outer caller's own refresh
                # (e.g. _on_termlens_edit_entry) already covers the
                # main paths; this is belt-and-braces for any future
                # opener that forgets.
                try:
                    ancestor = self.parent()
                    while ancestor is not None and not hasattr(ancestor, '_post_termbase_delete_refresh'):
                        ancestor = ancestor.parent() if callable(getattr(ancestor, 'parent', None)) else None
                    if ancestor is not None:
                        ancestor._post_termbase_delete_refresh()
                except Exception:
                    pass
                QMessageBox.information(self, "Success", "Termbase entry deleted")
                self.accept()  # Close dialog with success
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete entry: {e}")

    def save_term(self):
        """Save term to the database (edit mode only)."""
        # Validate inputs
        source_term = self.source_edit.text().strip()
        target_term = self.target_edit.text().strip()

        if not source_term or not target_term:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Both source and target terms are required."
            )
            return

        if not self.db_manager:
            QMessageBox.critical(
                self,
                "Error",
                "No database connection available."
            )
            return

        try:
            cursor = self.db_manager.cursor

            # Gather data
            definition = self.definition_edit.toPlainText().strip() if hasattr(self, 'definition_edit') else ""
            domain = self.domain_edit.text().strip()
            notes = self.notes_edit.toPlainText().strip()
            url = self.url_edit.text().strip() if hasattr(self, 'url_edit') else ""
            project = self.project_edit.text().strip()
            client = self.client_edit.text().strip()
            forbidden = self.forbidden_check.isChecked()
            is_nt = self.nontranslatable_check.isChecked()
            source_abbr = self.source_abbr_edit.text().strip() if hasattr(self, 'source_abbr_edit') else ""
            target_abbr = self.target_abbr_edit.text().strip() if hasattr(self, 'target_abbr_edit') else ""

            # Strip trailing sentence punctuation from translatable terms on save
            # (e.g. "circumference." -> "circumference"), matching the add path.
            # Non-translatables keep a meaningful trailing "." (e.g. "Inc.").
            if not is_nt:
                from modules.termbase_manager import normalize_term_for_save
                source_term = normalize_term_for_save(source_term)
                target_term = normalize_term_for_save(target_term)

            if self.term_id:
                # v1.10.64 diagnostic: log what the dialog is about to
                # write so the LOAD ... SAVE round-trip can be audited
                # in the session log. Useful for chasing any future
                # "terms reversed after save" report — three lines per
                # save (pre-update DB state, dialog values, post-update
                # DB state) give the full picture.
                try:
                    cursor.execute(
                        "SELECT source_term, target_term FROM termbase_terms WHERE id = ?",
                        (self.term_id,),
                    )
                    before = cursor.fetchone() or (None, None)
                    self._diag_log(
                        f"[TermbaseEntryEditor] SAVE term_id={self.term_id} "
                        f"BEFORE-DB src='{before[0]}' tgt='{before[1]}' | "
                        f"WRITING src='{source_term}' tgt='{target_term}'"
                    )
                except Exception:
                    pass

                # Update existing term.
                #
                # v1.10.74: bump ``modified_date`` to CURRENT_TIMESTAMP.
                # The schema column was created with a
                # ``DEFAULT CURRENT_TIMESTAMP`` (which only fires on
                # INSERT, not on UPDATE — SQLite won't auto-bump it
                # for us), so prior UPDATEs left the row's
                # modified_date frozen at its INSERT time. That broke
                # the v1.10.69/v1.10.72 snapshot gating chain: an
                # edit-only change (e.g. user edits definition + URL
                # + notes via the Edit Termbase Entry dialog) doesn't
                # bump COUNT or MAX(id), and if it doesn't bump
                # MAX(modified_date) either, the snapshot looks
                # identical and ``force_refresh_matches`` skips the
                # index rebuild — the TermLens display keeps showing
                # the stale pre-edit index entry with empty metadata.
                # Bumping modified_date here flips MAX(modified_date)
                # on the next snapshot, so the rebuild fires correctly.
                cursor.execute("""
                    UPDATE termbase_terms
                    SET source_term = ?, target_term = ?,
                        definition = ?, domain = ?, notes = ?, url = ?,
                        project = ?, client = ?,
                        forbidden = ?, is_nontranslatable = ?,
                        source_abbreviation = ?, target_abbreviation = ?,
                        modified_date = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (source_term, target_term, definition, domain, notes, url,
                      project, client, forbidden, 1 if is_nt else 0,
                      source_abbr, target_abbr, self.term_id))
            else:
                # Insert new term (this branch is reachable only if a caller
                # constructs the dialog with a termbase_id but no term_id
                # AND wires Save to save_term(). The standard add-path uses
                # _accept_and_save() instead, so this is purely a safety net.)
                cursor.execute("""
                    INSERT INTO termbase_terms
                    (termbase_id, source_term, target_term, definition, domain, notes, url,
                     project, client, forbidden, is_nontranslatable,
                     source_abbreviation, target_abbreviation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (self.termbase_id, source_term, target_term, definition, domain, notes, url,
                      project, client, forbidden, 1 if is_nt else 0,
                      source_abbr, target_abbr))

            self.db_manager.connection.commit()

            # Save synonyms (get the term_id if this was a new term)
            if not self.term_id:
                self.term_id = cursor.lastrowid

            self.save_synonyms()

            # v1.10.64 diagnostic + sanity check: re-read the row we
            # just wrote and confirm the DB matches the dialog. If a
            # row mysteriously comes back with source/target swapped
            # vs what we wrote (the symptom in the open Bug 2 report),
            # log loudly so the next session log captures evidence of
            # whatever cross-process / trigger / shared-DB conflict is
            # at play.
            try:
                cursor.execute(
                    "SELECT source_term, target_term FROM termbase_terms WHERE id = ?",
                    (self.term_id,),
                )
                after = cursor.fetchone() or (None, None)
                self._diag_log(
                    f"[TermbaseEntryEditor] SAVE term_id={self.term_id} "
                    f"AFTER-DB src='{after[0]}' tgt='{after[1]}'"
                )
                if (after[0] != source_term) or (after[1] != target_term):
                    self._diag_log(
                        f"[TermbaseEntryEditor] ⚠️ SAVE MISMATCH term_id={self.term_id}: "
                        f"wrote src='{source_term}' tgt='{target_term}' but "
                        f"DB now has src='{after[0]}' tgt='{after[1]}'. "
                        f"Possible cross-process write or trigger."
                    )
            except Exception:
                pass

            # Success
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save term: {e}"
            )

    def save_synonyms(self):
        """Save synonyms to the database (edit mode only)."""
        if not self.db_manager or not self.term_id:
            return

        try:
            cursor = self.db_manager.cursor

            # Delete existing synonyms for this term
            cursor.execute("DELETE FROM termbase_synonyms WHERE term_id = ?", (self.term_id,))

            src_count = self.source_synonym_list.count()
            tgt_count = self.target_synonym_list.count()

            # Save source synonyms
            src_texts = []
            for i in range(src_count):
                item = self.source_synonym_list.item(i)
                data = item.data(Qt.ItemDataRole.UserRole)
                src_texts.append(data['text'])
                cursor.execute("""
                    INSERT INTO termbase_synonyms (term_id, synonym_text, language, display_order, forbidden)
                    VALUES (?, ?, 'source', ?, ?)
                """, (self.term_id, data['text'], i, 1 if data['forbidden'] else 0))

            # Save target synonyms
            tgt_texts = []
            for i in range(tgt_count):
                item = self.target_synonym_list.item(i)
                data = item.data(Qt.ItemDataRole.UserRole)
                tgt_texts.append(data['text'])
                cursor.execute("""
                    INSERT INTO termbase_synonyms (term_id, synonym_text, language, display_order, forbidden)
                    VALUES (?, ?, 'target', ?, ?)
                """, (self.term_id, data['text'], i, 1 if data['forbidden'] else 0))

            self.db_manager.connection.commit()

            # v1.10.64 diagnostic: log what was saved so the open Bug 2
            # report (synonym vanishes after save) can be triaged from
            # the session log. The wipe-and-reinsert pattern means a
            # silent failure would otherwise leave no trace.
            self._diag_log(
                f"[TermbaseEntryEditor] SAVE-SYNONYMS term_id={self.term_id} "
                f"src({src_count})={src_texts!r} tgt({tgt_count})={tgt_texts!r}"
            )

        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Failed to save synonyms: {e}")

    def get_term_data(self) -> Optional[dict]:
        """Get the current term data from the form fields (legacy helper)."""
        return {
            'source_term': self.source_edit.text().strip(),
            'target_term': self.target_edit.text().strip(),
            'domain': self.domain_edit.text().strip(),
            'note': self.notes_edit.toPlainText().strip(),
            'project': self.project_edit.text().strip(),
            'client': self.client_edit.text().strip(),
            'forbidden': self.forbidden_check.isChecked(),
            'is_nontranslatable': self.nontranslatable_check.isChecked(),
        }
