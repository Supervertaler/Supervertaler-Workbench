"""The project data model: Comment, Segment and Project. Moved unchanged
from Supervertaler.py.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from modules.statuses import DEFAULT_STATUS
from typing import (
    Any,
    Dict,
    List,
    Optional,
)
from modules.tag_text import strip_invisible_markers


@dataclass
class Comment:
    """A user-authored comment attached to a segment.

    v1.10.57 introduced this as part of the move from single-string
    per-segment notes (``segment.notes``) to a list of structured
    comments (``segment.comments``). The structured form supports:

    * Multiple comments per segment.
    * Each comment carrying its own author + timestamp.
    * Optional anchoring to a specific character range within the
      segment's source or target text. When ``anchor_field`` is set
      to ``"source"`` or ``"target"`` and ``anchor_end > anchor_start``,
      the comment is anchored to that character range (Python-slice
      semantics: start inclusive, end exclusive). DOCX export uses
      this to produce Word comments that highlight only the
      referenced text, the same way Trados and memoQ do it. When
      ``anchor_field`` is empty, the comment is segment-level
      (anchors to the whole paragraph in export).

    Backward-compat: old ``.svproj`` files store user notes as a
    single string in ``segment.notes``. ``Segment.from_dict`` migrates
    those to a single segment-level Comment with the legacy text.
    """
    id: str = ""             # UUID hex; auto-assigned in __post_init__ if blank
    text: str = ""           # Comment body
    author: str = ""         # Author name (translator's name, "AI", etc.)
    created: str = ""        # ISO timestamp; auto-set in __post_init__ if blank
    anchor_field: str = ""   # "source", "target", or "" (segment-level)
    anchor_start: int = 0    # Character offset (inclusive)
    anchor_end: int = 0      # Character offset (exclusive)
    imported: bool = False   # True = read in from the source document (e.g. a
                             # Word review comment). Shown for context but NOT
                             # re-exported, since the original still lives in
                             # the source file (the Okapi round-trip preserves it).

    def __post_init__(self):
        if not self.id:
            import uuid as _uuid
            self.id = _uuid.uuid4().hex
        if not self.created:
            self.created = datetime.now().isoformat()

    @property
    def is_anchored(self) -> bool:
        """True iff this comment is anchored to a specific text range."""
        return bool(self.anchor_field) and self.anchor_end > self.anchor_start

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Comment':
        """Build a Comment from a dict, dropping unknown keys."""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class Segment:
    """Translation segment (matches tkinter version format)"""
    id: int
    source: str
    target: str = ""
    status: str = DEFAULT_STATUS.key
    type: str = "para"  # para, heading, list_item, table_cell
    notes: str = ""  # DEPRECATED: legacy single-string note. New code should use comments[]
                     # instead. Kept for backward-compat with .svproj files (and for code
                     # paths not yet migrated). Kept in sync with comments[] by
                     # _sync_notes_from_comments() and the segment-helper methods below.
    comments: List['Comment'] = field(default_factory=list)  # v1.10.57: structured comments
    proofreading_notes: Dict[str, str] = field(default_factory=dict)  # LLM model name → proofreading issue text
    match_percent: Optional[int] = None  # memoQ match score if provided
    memoQ_status: str = ""  # Raw memoQ status text
    locked: bool = False  # For compatibility with tkinter version
    paragraph_id: int = 0  # Group segments by paragraph for document flow
    style: str = "Normal"  # Heading 1, Heading 2, Title, Subtitle, Normal, etc.
    document_position: int = 0  # Position in original document
    is_table_cell: bool = False  # Whether this segment is in a table
    table_info: Optional[tuple] = None  # (table_idx, row_idx, cell_idx) if is_table_cell
    modified: bool = False  # Track if segment has been edited
    created_at: str = ""  # Creation timestamp
    modified_at: str = ""  # Last modification timestamp
    list_number: Optional[int] = None  # For numbered lists: 1, 2, 3, etc. None for bullets or non-lists
    list_type: str = ""  # "numbered", "bullet", or "" for non-list items
    file_id: Optional[int] = None  # ID of the file this segment belongs to (for multi-file projects)
    file_name: str = ""  # Name of the file this segment belongs to (for multi-file projects)
    dejavu_segment_id: str = ""  # Déjà Vu segment ID for round-trip export
    dejavu_row_index: Optional[int] = None  # Déjà Vu row index for export mapping
    sdl_segment_id: str = ""  # SDLXLIFF segment ID for round-trip export
    okapi_tu_id: str = ""  # Okapi text unit ID for merge round-trip
    okapi_segment_index: int = -1  # Segment index within Okapi text unit (-1 = not from Okapi)
    category: str = ""  # Okapi sub-document category: "" (body) / "comment" / "header" / "footer" / "property" / "notes"
    join_before: Optional[str] = None  # Whitespace between this segment and the previous one on the same line when Supervertaler split the line (issue #191); plain-text export uses it to rebuild the line exactly. None = unknown → a single space

    def __post_init__(self):
        """Initialize timestamps and reconcile comments[] ↔ notes."""
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.modified_at:
            self.modified_at = datetime.now().isoformat()
        # v1.10.57: bring comments[] and notes into a consistent state.
        # - If we have notes but no comments → migrate: create one
        #   segment-level Comment from the notes string.
        # - If we have comments but no notes → derive notes for any
        #   legacy code path that still reads segment.notes directly.
        # - If we have both: trust comments[] (it's the new source of
        #   truth); regenerate notes from it so the two agree.
        if self.notes and not self.comments:
            self.comments = [Comment(
                text=self.notes,
                author='',  # Unknown for migrated legacy notes
                created=self.modified_at,
                anchor_field='',
                anchor_start=0,
                anchor_end=0,
            )]
        elif self.comments:
            self.notes = self._joined_comment_text()

    # ── Comment helpers (v1.10.57) ──
    def _joined_comment_text(self) -> str:
        """Return the joined text of all comments (used to keep
        legacy segment.notes in sync with the new comments[] list)."""
        return '\n\n'.join(c.text for c in self.comments if c.text)

    def add_comment(self, text: str, author: str = '',
                    anchor_field: str = '', anchor_start: int = 0,
                    anchor_end: int = 0) -> 'Comment':
        """Append a new Comment, sync legacy notes, return the Comment.

        Use this instead of mutating ``segment.comments`` directly so
        that the legacy ``segment.notes`` mirror stays consistent for
        any code path that still reads it (memoQ bilingual exports,
        tooltips on the grid status column, the all-comments list
        widget, etc.).
        """
        comment = Comment(
            text=text,
            author=author,
            anchor_field=anchor_field,
            anchor_start=anchor_start,
            anchor_end=anchor_end,
        )
        self.comments.append(comment)
        self.notes = self._joined_comment_text()
        return comment

    def update_comment(self, comment_id: str, text: str) -> bool:
        """Update an existing comment's text. Returns True if found."""
        for c in self.comments:
            if c.id == comment_id:
                c.text = text
                self.notes = self._joined_comment_text()
                return True
        return False

    def remove_comment(self, comment_id: str) -> bool:
        """Remove a comment by id. Returns True if found."""
        before = len(self.comments)
        self.comments = [c for c in self.comments if c.id != comment_id]
        if len(self.comments) < before:
            self.notes = self._joined_comment_text()
            return True
        return False

    def get_comment(self, comment_id: str) -> Optional['Comment']:
        for c in self.comments:
            if c.id == comment_id:
                return c
        return None

    def replace_all_comments_with_text(self, text: str, author: str = '') -> None:
        """Legacy bridge: replace the entire comments list with a single
        segment-level Comment whose text is ``text``. Empty text leaves
        the list empty. Used by the pre-v1.10.57 single-string editor
        flow (``_on_bottom_notes_changed``) which writes to segment.notes
        as if it were one string."""
        if text and text.strip():
            # Preserve author/anchor from the previous single comment when
            # possible — a no-op edit shouldn't churn the metadata.
            if (len(self.comments) == 1
                    and not self.comments[0].is_anchored):
                self.comments[0].text = text
            else:
                self.comments = [Comment(
                    text=text,
                    author=author,
                    anchor_field='',
                    anchor_start=0,
                    anchor_end=0,
                )]
        else:
            self.comments = []
        self.notes = self._joined_comment_text()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Segment':
        """Create Segment from dictionary, ignoring unknown fields"""
        # Only use fields that the dataclass knows about
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        # Defensively strip any Show-Invisibles markers that may have been
        # written to disk by an earlier buggy version of the app.
        if 'target' in filtered_data and isinstance(filtered_data['target'], str):
            filtered_data['target'] = strip_invisible_markers(filtered_data['target'])
        if 'source' in filtered_data and isinstance(filtered_data['source'], str):
            filtered_data['source'] = strip_invisible_markers(filtered_data['source'])
        # Migration: move legacy "⚠️ PROOFREAD:" content from notes to proofreading_notes dict
        notes_val = filtered_data.get('notes', '')
        if notes_val and "⚠️ PROOFREAD:" in notes_val and not filtered_data.get('proofreading_notes'):
            parts = notes_val.split("⚠️ PROOFREAD:", 1)
            before = parts[0].strip()
            proofread_and_rest = parts[1] if len(parts) > 1 else ""
            separator_parts = proofread_and_rest.split("---", 1)
            proofread_text = separator_parts[0].strip()
            after = separator_parts[1].strip() if len(separator_parts) > 1 else ""
            # Reconstruct clean notes (user notes only)
            clean_notes = before
            if after:
                clean_notes = (clean_notes + "\n" + after).strip() if clean_notes else after
            filtered_data['notes'] = clean_notes
            # Store proofreading text under "legacy" key (original LLM unknown)
            if proofread_text:
                filtered_data['proofreading_notes'] = {"legacy": proofread_text}
        # v1.10.57: deserialize nested Comment dicts into Comment objects.
        # asdict() on save produces plain dicts; this is the corresponding
        # inverse on load.
        raw_comments = filtered_data.get('comments')
        if raw_comments:
            filtered_data['comments'] = [
                Comment.from_dict(c) if isinstance(c, dict) else c
                for c in raw_comments
            ]
        return cls(**filtered_data)


@dataclass
class Project:
    """Translation project"""
    name: str
    source_lang: str = "en"
    target_lang: str = "nl"
    segments: List[Segment] = None
    created: str = ""
    modified: str = ""
    prompt_settings: Dict[str, Any] = None  # Store active prompt settings
    tm_settings: Dict[str, Any] = None  # Store activated TM settings
    termbase_settings: Dict[str, Any] = None  # Store activated termbase settings
    nt_settings: Dict[str, Any] = None  # Store activated non-translatables settings
    spellcheck_settings: Dict[str, Any] = None  # Store spellcheck settings {enabled, language}
    ui_settings: Dict[str, Any] = None  # Store UI settings (results pane zoom, etc.)
    general_settings_overrides: Dict[str, Any] = None  # Per-project overrides for general settings
    id: int = None  # Unique project ID for TM activation tracking
    original_docx_path: str = None  # Path to original DOCX for structure-preserving export
    trados_source_path: str = None  # Path to original Trados bilingual DOCX for round-trip export
    memoq_source_path: str = None  # Path to original memoQ bilingual DOCX for round-trip export
    mqxliff_source_path: str = None  # Path to original memoQ XLIFF for round-trip export
    mxliff_source_path: str = None  # Path to original Phrase MXLIFF for round-trip export
    phrase_source_path: str = None  # Path to original Phrase bilingual DOCX for round-trip export
    cafetran_source_path: str = None  # Path to original CafeTran bilingual DOCX for round-trip export
    sdlppx_source_path: str = None  # Path to original Trados SDLPPX package for SDLRPX export
    sdlxliff_source_paths: list = None  # Paths to standalone .sdlxliff files for round-trip export
    original_txt_path: str = None  # Path to original simple text file for round-trip export
    dejavu_source_path: str = None  # Path to original Déjà Vu bilingual RTF for round-trip export
    po_source_path: str = None  # Path to original GNU gettext .po / .pot for round-trip export
    bundled_sources: Dict[str, Any] = None  # {field: "source/<name>"} copies of the round-trip sources above (#228)
    concordance_geometry: Dict[str, int] = None  # Window geometry for Concordance Search {x, y, width, height}
    # Multi-file project support
    files: List[Dict[str, Any]] = None  # List of files in project: [{id, name, path, type, segment_count, ...}]
    is_multifile: bool = False  # True if this is a multi-file project
    views: List[Dict[str, Any]] = None  # Saved views: [{"name": "...", "file_ids": [1, 3]}]
    # Scratchpad for private translator notes (stored only in .svproj, never exported to CAT tools)
    scratchpad_notes: str = ""
    import_engine: str = ""  # "okapi" or "" (standard/built-in)
    import_options: Optional[Dict[str, Any]] = None  # per-file-type Okapi import toggles; reused at export/merge

    def __post_init__(self):
        if self.segments is None:
            self.segments = []
        if self.files is None:
            self.files = []
        if self.views is None:
            self.views = []
        if not self.created:
            self.created = datetime.now().isoformat()
        if not self.modified:
            self.modified = datetime.now().isoformat()
        if self.prompt_settings is None:
            self.prompt_settings = {}
        if self.tm_settings is None:
            self.tm_settings = {}
        if self.termbase_settings is None:
            self.termbase_settings = {}
        if self.nt_settings is None:
            self.nt_settings = {}
        if self.spellcheck_settings is None:
            self.spellcheck_settings = {}
        if self.ui_settings is None:
            self.ui_settings = {}
        if self.general_settings_overrides is None:
            self.general_settings_overrides = {}
        # Generate ID if not set (for backward compatibility with old projects)
        if self.id is None:
            import hashlib
            # Create stable ID from project name + created timestamp
            id_source = f"{self.name}_{self.created}"
            self.id = int(hashlib.md5(id_source.encode()).hexdigest()[:8], 16)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization.
        
        Structure is organized for human readability when opened in a text editor:
        1. Project identification (name, languages, dates, id)
        2. Settings (prompts, TM, termbases, spellcheck, etc.)
        3. Source file paths
        4. UI state (concordance geometry)
        5. Segments (at the end - the actual translation content)
        """
        # Start with core project metadata
        result = {
            'name': self.name,
            'source_lang': self.source_lang,
            'target_lang': self.target_lang,
            'created': self.created,
            'modified': self.modified,
            'id': self.id  # Save project ID
        }
        
        # Add settings (prompts, TM, termbases, etc.)
        if hasattr(self, 'prompt_settings'):
            result['prompt_settings'] = self.prompt_settings
        if self.tm_settings:
            result['tm_settings'] = self.tm_settings
        if self.termbase_settings:
            result['termbase_settings'] = self.termbase_settings
        if self.nt_settings:
            result['nt_settings'] = self.nt_settings
        if self.spellcheck_settings:
            result['spellcheck_settings'] = self.spellcheck_settings
        if self.ui_settings:
            result['ui_settings'] = self.ui_settings
        
        # Add source file paths
        if self.original_docx_path:
            result['original_docx_path'] = self.original_docx_path
        if self.trados_source_path:
            result['trados_source_path'] = self.trados_source_path
        if self.memoq_source_path:
            result['memoq_source_path'] = self.memoq_source_path
        if self.mqxliff_source_path:
            result['mqxliff_source_path'] = self.mqxliff_source_path
        if self.mxliff_source_path:
            result['mxliff_source_path'] = self.mxliff_source_path
        if self.phrase_source_path:
            result['phrase_source_path'] = self.phrase_source_path
        if self.cafetran_source_path:
            result['cafetran_source_path'] = self.cafetran_source_path
        if self.sdlppx_source_path:
            result['sdlppx_source_path'] = self.sdlppx_source_path
        if self.sdlxliff_source_paths:
            result['sdlxliff_source_paths'] = self.sdlxliff_source_paths
        if self.original_txt_path:
            result['original_txt_path'] = self.original_txt_path
        if self.dejavu_source_path:
            result['dejavu_source_path'] = self.dejavu_source_path
        if self.po_source_path:
            result['po_source_path'] = self.po_source_path
        if self.bundled_sources:
            result['bundled_sources'] = self.bundled_sources
        
        # Add UI state
        if self.concordance_geometry:
            result['concordance_geometry'] = self.concordance_geometry
        
        # Add multi-file project data
        if self.is_multifile:
            result['is_multifile'] = self.is_multifile
        if self.files:
            result['files'] = self.files
        
        # Add scratchpad notes (private translator notes, never exported to CAT tools)
        if self.scratchpad_notes:
            result['scratchpad_notes'] = self.scratchpad_notes

        # Add saved views for multi-file projects
        if self.views:
            result['views'] = self.views

        # Add import engine indicator (for Okapi round-trip export)
        if self.import_engine:
            result['import_engine'] = self.import_engine
        # Persist the import options so export/merge re-runs the filter
        # with the SAME toggles the document was extracted with.
        if self.import_options:
            result['import_options'] = self.import_options

        # Add segments LAST (so they appear at the end of the file)
        result['segments'] = [seg.to_dict() for seg in self.segments]
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Project':
        """Create Project from dictionary"""
        segments = [Segment.from_dict(seg) for seg in data.get('segments', [])]
        
        # Handle missing name field (use filename or default)
        name = data.get('name', 'Untitled Project')
        
        project = cls(
            name=name,
            source_lang=data.get('source_lang', 'en'),
            target_lang=data.get('target_lang', 'nl'),
            segments=segments,
            created=data.get('created', ''),
            modified=data.get('modified', ''),
            id=data.get('id', None)  # Load project ID (will auto-generate if missing)
        )
        # Store prompt settings if they exist
        if 'prompt_settings' in data:
            project.prompt_settings = data['prompt_settings']
        # Store TM settings if they exist
        if 'tm_settings' in data:
            project.tm_settings = data['tm_settings']
        # Store termbase settings if they exist
        if 'termbase_settings' in data:
            project.termbase_settings = data['termbase_settings']
        # Store non-translatables settings if they exist
        if 'nt_settings' in data:
            project.nt_settings = data['nt_settings']
        # Store spellcheck settings if they exist
        if 'spellcheck_settings' in data:
            project.spellcheck_settings = data['spellcheck_settings']
        # Store UI settings if they exist
        if 'ui_settings' in data:
            project.ui_settings = data['ui_settings']
        # Store original DOCX path if it exists
        if 'original_docx_path' in data:
            project.original_docx_path = data['original_docx_path']
        # Store Trados source path if it exists
        if 'trados_source_path' in data:
            project.trados_source_path = data['trados_source_path']
        # Store memoQ source path if it exists
        if 'memoq_source_path' in data:
            project.memoq_source_path = data['memoq_source_path']
        # Store memoQ XLIFF source path if it exists
        if 'mqxliff_source_path' in data:
            project.mqxliff_source_path = data['mqxliff_source_path']
        if 'mxliff_source_path' in data:
            project.mxliff_source_path = data['mxliff_source_path']
        if 'phrase_source_path' in data:
            project.phrase_source_path = data['phrase_source_path']
        # Store CafeTran source path if it exists
        if 'cafetran_source_path' in data:
            project.cafetran_source_path = data['cafetran_source_path']
        # Store SDLPPX source path if it exists
        if 'sdlppx_source_path' in data:
            project.sdlppx_source_path = data['sdlppx_source_path']
        # Store standalone SDLXLIFF source paths if they exist
        if 'sdlxliff_source_paths' in data:
            project.sdlxliff_source_paths = data['sdlxliff_source_paths']
        # Store original TXT path if it exists
        if 'original_txt_path' in data:
            project.original_txt_path = data['original_txt_path']
        # Store Déjà Vu source path if it exists
        if 'dejavu_source_path' in data:
            project.dejavu_source_path = data['dejavu_source_path']
        # Store .po / .pot source path if it exists
        if 'po_source_path' in data:
            project.po_source_path = data['po_source_path']
        if isinstance(data.get('bundled_sources'), dict):
            project.bundled_sources = data['bundled_sources']
        # Store concordance window geometry if it exists
        if 'concordance_geometry' in data:
            project.concordance_geometry = data['concordance_geometry']
        # Store multi-file project data if it exists
        if 'is_multifile' in data:
            project.is_multifile = data['is_multifile']
        if 'files' in data:
            project.files = data['files']
        # Store scratchpad notes if they exist
        if 'scratchpad_notes' in data:
            project.scratchpad_notes = data['scratchpad_notes']
        # Store saved views if they exist
        if 'views' in data:
            project.views = data['views']
        # Store import engine indicator (for Okapi round-trip export)
        if 'import_engine' in data:
            project.import_engine = data['import_engine']
        if 'import_options' in data:
            project.import_options = data['import_options']
        return project
