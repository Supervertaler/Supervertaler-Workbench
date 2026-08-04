"""
Database Manager Module

SQLite database backend for Translation Memories, Glossaries, and related resources.
Replaces in-memory JSON-based storage with efficient database storage.

Schema includes:
- Translation units (TM entries)
- Termbase terms
- Non-translatables
- Segmentation rules
- Project metadata
- Resource file references
"""

import sqlite3
import os
import json
import hashlib
import threading
import unicodedata
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from difflib import SequenceMatcher


# ── CJK / space-less script helpers (for fuzzy-TM retrieval) ──────────────
# Chinese/Japanese/Korean write words with no separating spaces, so the
# default unicode61 FTS tokenizer indexes a whole run as a single token and
# can't retrieve similar-but-not-identical segments as fuzzy candidates. We
# maintain a parallel trigram-tokenised FTS index (CJK rows only) and query it
# with the source's overlapping 3-grams instead. See the trigram FTS table in
# init_database() and _search_single_tm_fuzzy().

def _dbm_is_cjk_char(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF or    # CJK Unified Ideographs
        0x3400 <= o <= 0x4DBF or    # CJK Extension A
        0x3040 <= o <= 0x30FF or    # Hiragana + Katakana
        0xAC00 <= o <= 0xD7AF or    # Hangul syllables
        0xF900 <= o <= 0xFAFF       # CJK Compatibility Ideographs
    )


def _dbm_contains_cjk(text: str) -> bool:
    return bool(text) and any(_dbm_is_cjk_char(c) for c in text)


# GLOB pattern matching any CJK/Kana/Hangul character – used to gate the
# trigram FTS triggers/populate to CJK rows only (verified against SQLite GLOB).
_CJK_GLOB = "*[一-鿿㐀-䶿぀-ヿ가-힯]*"


def _cjk_trigrams(text: str, cap: int = 40) -> List[str]:
    """Overlapping 3-character windows of `text` that contain at least one CJK
    character, de-duplicated and capped. Used to build the trigram-FTS MATCH
    query for a CJK source segment."""
    s = (text or "").strip()
    grams: List[str] = []
    seen = set()
    for i in range(len(s) - 2):
        g = s[i:i + 3]
        if g in seen:
            continue
        if any(_dbm_is_cjk_char(c) for c in g):
            seen.add(g)
            grams.append(g)
            if len(grams) >= cap:
                break
    return grams


def _normalize_for_matching(text: str) -> str:
    """Normalize text for exact matching.

    Handles invisible differences that would cause exact match to fail:
    - Unicode normalization (NFC)
    - Multiple whitespace -> single space
    - Leading/trailing whitespace
    - Non-breaking spaces -> regular spaces
    """
    if not text:
        return ""
    # Unicode normalize (NFC form)
    text = unicodedata.normalize('NFC', text)
    # Convert non-breaking spaces and other whitespace to regular space
    text = text.replace('\u00a0', ' ')  # NBSP
    text = text.replace('\u2007', ' ')  # Figure space
    text = text.replace('\u202f', ' ')  # Narrow NBSP
    # Collapse multiple whitespace to single space
    text = re.sub(r'\s+', ' ', text)
    # Strip leading/trailing whitespace
    text = text.strip()
    return text


class DatabaseManager:
    """Manages SQLite database for translation resources"""
    
    def __init__(self, db_path: str = None, log_callback=None):
        """
        Initialize database manager
        
        Args:
            db_path: Path to SQLite database file (default: user_data/supervertaler.db)
            log_callback: Optional logging function
        """
        self.log = log_callback if log_callback else print
        
        # Set default database path if not provided
        if db_path is None:
            # Will be set by application - defaults to user_data folder
            self.db_path = "supervertaler.db"
        else:
            self.db_path = db_path
        
        self.connection = None
        self.cursor = None

        # Per-thread read-only connections for use from worker threads.
        # The main self.connection is owned by the thread that called connect()
        # (sqlite3 connections default to check_same_thread=True). Worker threads
        # that want to run SELECTs concurrently — e.g. the Sidekick lookup fan-out —
        # should call get_reader_connection() to obtain their own connection.
        self._thread_local = threading.local()

    def get_reader_connection(self) -> sqlite3.Connection:
        """Return a read-only sqlite3.Connection scoped to the current thread.

        Each thread gets its own lazily-opened connection that is reused for the
        thread's lifetime. WAL is set on the file by connect(), so opening more
        connections is cheap and they coexist with the main writer connection
        without blocking. query_only=1 prevents accidental writes from worker
        threads.
        """
        conn = getattr(self._thread_local, 'reader', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA query_only=1")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
            self._thread_local.reader = conn
        return conn

    def close_reader_connection(self):
        """Close the current thread's reader connection if one exists.

        Worker threads (e.g. QRunnable.run()) should call this just before
        returning so the connection is released promptly rather than living
        until the thread is destroyed.
        """
        conn = getattr(self._thread_local, 'reader', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._thread_local.reader = None

    def connect(self):
        """Connect to database and create tables if needed"""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
            
            # Connect to database with busy timeout to handle concurrent access
            self.connection = sqlite3.connect(self.db_path, timeout=15)
            self.connection.row_factory = sqlite3.Row  # Access columns by name
            self.cursor = self.connection.cursor()

            # Enable WAL mode for better concurrent read/write performance
            # WAL allows readers to proceed while a writer is active
            self.cursor.execute("PRAGMA journal_mode=WAL")

            # Enable foreign keys
            self.cursor.execute("PRAGMA foreign_keys = ON")
            
            # Create tables
            self._create_tables()
            
            # Run database migrations (adds new columns/tables as needed)
            try:
                from modules.database_migrations import check_and_migrate
                migration_success = check_and_migrate(self)
                if not migration_success:
                    self.log("[WARNING] Database migration reported failure")
            except Exception as e:
                self.log(f"[WARNING] Database migration check failed: {e}")
                import traceback
                traceback.print_exc()
            
            # Auto-sync FTS5 index if out of sync
            try:
                fts_status = self.check_fts_index()
                if not fts_status.get('in_sync', True):
                    self.log(f"[TM] FTS5 index out of sync ({fts_status.get('fts_count', 0)} vs {fts_status.get('main_count', 0)}), rebuilding...")
                    self.rebuild_fts_index()
            except Exception as e:
                self.log(f"[WARNING] FTS5 index check failed: {e}")
            
            self.log(f"[OK] Database connected: {os.path.basename(self.db_path)}")
            return True
            
        except Exception as e:
            self.log(f"[ERROR] Database connection failed: {e}")
            return False
    
    def _create_tables(self):
        """Create database schema"""
        print("📊 Creating database tables...")
        
        # ============================================
        # TRANSLATION MEMORY TABLES
        # ============================================
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                tm_id TEXT NOT NULL,
                project_id TEXT,
                
                -- Context for better matching
                context_before TEXT,
                context_after TEXT,
                
                -- Fast exact matching
                source_hash TEXT NOT NULL,
                -- Fast *reverse* exact matching: md5 of the normalised target,
                -- so an opposite-direction exact match (our source == a TM
                -- entry's target) is an indexed lookup instead of a full scan.
                target_hash TEXT,

                -- Metadata
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usage_count INTEGER DEFAULT 0,
                created_by TEXT,
                notes TEXT,
                
                -- Indexes
                UNIQUE(source_hash, target_text, tm_id)
            )
        """)
        
        # Indexes for translation_units
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tu_source_hash
            ON translation_units(source_hash)
        """)
        # idx_tu_target_hash (for reverse exact matching) is created by
        # migrate_translation_units_target_hash, which also backfills the
        # column on existing databases. Creating it here would fail on a
        # pre-target_hash DB where the column isn't added until that migration.

        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tu_tm_id 
            ON translation_units(tm_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tu_project_id 
            ON translation_units(project_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tu_langs 
            ON translation_units(source_lang, target_lang)
        """)
        
        # Full-text search for fuzzy matching
        self.cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS translation_units_fts 
            USING fts5(
                source_text, 
                target_text,
                content=translation_units,
                content_rowid=id
            )
        """)
        
        # Triggers to keep FTS index in sync
        self.cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tu_fts_insert AFTER INSERT ON translation_units BEGIN
                INSERT INTO translation_units_fts(rowid, source_text, target_text)
                VALUES (new.id, new.source_text, new.target_text);
            END
        """)
        
        # translation_units_fts is an EXTERNAL-CONTENT FTS5 table (content=…), so
        # rows must be removed with the special 'delete' command carrying the OLD
        # column values — a plain `DELETE … WHERE rowid` makes FTS5 read the
        # (already-changed/gone) content row and raises "database disk image is
        # malformed". See migrate_fts_external_content_delete.
        self.cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tu_fts_delete AFTER DELETE ON translation_units BEGIN
                INSERT INTO translation_units_fts(translation_units_fts, rowid, source_text, target_text)
                VALUES ('delete', old.id, old.source_text, old.target_text);
            END
        """)

        self.cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tu_fts_update AFTER UPDATE ON translation_units BEGIN
                INSERT INTO translation_units_fts(translation_units_fts, rowid, source_text, target_text)
                VALUES ('delete', old.id, old.source_text, old.target_text);
                INSERT INTO translation_units_fts(rowid, source_text, target_text)
                VALUES (new.id, new.source_text, new.target_text);
            END
        """)

        # CJK fuzzy-retrieval index. The unicode61 FTS above indexes a space-less
        # CJK run as one token, so similar (non-identical) Chinese/Japanese
        # segments can't be found as fuzzy candidates. A trigram-tokenised index
        # makes them retrievable. To avoid burdening English-only TMs with a
        # second large index, the triggers and the one-time populate only index
        # rows that actually contain CJK/Kana/Hangul characters. Wrapped in
        # try/except: the trigram tokenizer needs SQLite >= 3.34 — on older
        # builds we skip it and CJK fuzzy retrieval falls back to the unicode61
        # path (degraded, but functional).
        self._trigram_fts_available = False
        try:
            self.cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='translation_units_trigram'")
            _trigram_existed = self.cursor.fetchone() is not None

            # Regular (self-contained) FTS5 — NOT external-content. This index
            # only holds the CJK subset of rows (see the gated triggers below),
            # which an external-content table can't represent: external content
            # assumes every content row is indexed, so removing a non-indexed
            # row's tokens corrupts it ("malformed"). A regular table stores its
            # own copy of the (tiny) CJK text and supports plain rowid deletes.
            self.cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS translation_units_trigram
                USING fts5(source_text, target_text, tokenize='trigram')
            """)
            self.cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS tu_trig_insert AFTER INSERT ON translation_units
                WHEN new.source_text GLOB '{_CJK_GLOB}' OR new.target_text GLOB '{_CJK_GLOB}'
                BEGIN
                    INSERT INTO translation_units_trigram(rowid, source_text, target_text)
                    VALUES (new.id, new.source_text, new.target_text);
                END
            """)
            self.cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS tu_trig_delete AFTER DELETE ON translation_units BEGIN
                    DELETE FROM translation_units_trigram WHERE rowid = old.id;
                END
            """)
            self.cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS tu_trig_update AFTER UPDATE ON translation_units BEGIN
                    DELETE FROM translation_units_trigram WHERE rowid = old.id;
                    INSERT INTO translation_units_trigram(rowid, source_text, target_text)
                    SELECT new.id, new.source_text, new.target_text
                    WHERE new.source_text GLOB '{_CJK_GLOB}' OR new.target_text GLOB '{_CJK_GLOB}';
                END
            """)

            # One-time populate from existing CJK rows (migration for DBs that
            # predate this index). Only runs the first time the table is created.
            if not _trigram_existed:
                self.cursor.execute(f"""
                    INSERT INTO translation_units_trigram(rowid, source_text, target_text)
                    SELECT id, source_text, target_text FROM translation_units
                    WHERE source_text GLOB '{_CJK_GLOB}' OR target_text GLOB '{_CJK_GLOB}'
                """)
            self._trigram_fts_available = True
        except Exception as e:
            try:
                self.log(f"⚠ Trigram FTS unavailable; CJK fuzzy retrieval uses the fallback path: {e}")
            except Exception:
                pass

        # ============================================
        # TRANSLATION MEMORY METADATA
        # ============================================
        
        # Translation Memories table - tracks individual TM names/metadata
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                source_lang TEXT,
                target_lang TEXT,
                tm_id TEXT NOT NULL UNIQUE,  -- The tm_id used in translation_units table
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                entry_count INTEGER DEFAULT 0,  -- Cached count, updated on changes
                last_used TIMESTAMP,
                is_project_tm BOOLEAN DEFAULT 0,  -- Whether this is the special project TM
                read_only BOOLEAN DEFAULT 1,  -- Whether this TM should not be updated (default: read-only, Write unchecked)
                project_id INTEGER  -- Which project this TM belongs to (NULL = global)
            )
        """)
        
        # TM activation (tracks which TMs are active for which projects)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tm_activation (
                tm_db_id INTEGER NOT NULL,   -- numeric translation_memories.id (NOT the string slug)
                project_id INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                activated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tm_db_id, project_id),
                FOREIGN KEY (tm_db_id) REFERENCES translation_memories(id) ON DELETE CASCADE
            )
        """)
        
        # Index for fast tm_id lookups
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tm_tm_id 
            ON translation_memories(tm_id)
        """)
        
        # Migration: Add is_project_tm, read_only, and project_id columns if they don't exist
        try:
            self.cursor.execute("PRAGMA table_info(translation_memories)")
            columns = [row[1] for row in self.cursor.fetchall()]
            
            if 'is_project_tm' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN is_project_tm BOOLEAN DEFAULT 0")
                print("✓ Added is_project_tm column to translation_memories")
            
            if 'read_only' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN read_only BOOLEAN DEFAULT 1")
                print("✓ Added read_only column to translation_memories (default: read-only)")
            
            if 'project_id' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN project_id INTEGER")
                print("✓ Added project_id column to translation_memories")

            # External-source mirror columns. When a row has a non-NULL
            # external_source_path we periodically delta-sync it from that
            # file (currently used for Trados Studio .sdltm attach).
            if 'external_source_path' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN external_source_path TEXT")
                print("✓ Added external_source_path column to translation_memories")
            if 'external_last_sync_id' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN external_last_sync_id INTEGER DEFAULT 0")
                print("✓ Added external_last_sync_id column to translation_memories")
            if 'external_last_sync_date' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN external_last_sync_date TEXT")
                print("✓ Added external_last_sync_date column to translation_memories")
            if 'external_last_mtime' not in columns:
                self.cursor.execute("ALTER TABLE translation_memories ADD COLUMN external_last_mtime REAL")
                print("✓ Added external_last_mtime column to translation_memories")

            # v1.10.212: bridged_to_trados flag. When TRUE the Trados plugin's
            # SupervertalerTmProvider (Phase 2 of the Shared TM work, tracked
            # in Trados issue #31) exposes this TM as an attachable
            # translation provider inside Trados Studio. Both products read
            # and write to the same `supervertaler.db` already – the flag
            # just controls which TMs are user-opted-in for cross-product
            # visibility, so a freelancer with client-A and client-B TMs in
            # the same DB doesn't see client-A hits leak into a Trados
            # session opened on a client-B project.
            #
            # Default 0 (NOT bridged) so existing users see no change in
            # Trados-side visibility until they actively opt in per TM.
            if 'bridged_to_trados' not in columns:
                self.cursor.execute(
                    "ALTER TABLE translation_memories ADD COLUMN bridged_to_trados BOOLEAN DEFAULT 0"
                )
                print("✓ Added bridged_to_trados column to translation_memories")

            # SuperLookup inclusion flag. Independent of the Read flag
            # (tm_activation.is_active): Read controls whether a TM is used
            # for matching the *active project*, while this controls whether
            # SuperLookup searches the TM at all. Decoupling them lets a user
            # keep only a couple of TMs Read during a project while still
            # having SuperLookup search every TM. DEFAULT 1 (included) so on
            # upgrade SuperLookup searches everything, then the user opts out
            # per TM via the SuperLookup column on the TMs tab.
            if 'superlookup_enabled' not in columns:
                self.cursor.execute(
                    "ALTER TABLE translation_memories ADD COLUMN superlookup_enabled BOOLEAN DEFAULT 1"
                )
                print("✓ Added superlookup_enabled column to translation_memories (default: included)")

            self.connection.commit()
        except Exception as e:
            print(f"Migration info: {e}")
        
        # ============================================
        # TERMBASE TABLES
        # ============================================
        
        # Termbases container table (terminology, never "termbase")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS termbases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                source_lang TEXT,
                target_lang TEXT,
                project_id INTEGER,  -- NULL = global, set = project-specific
                is_global BOOLEAN DEFAULT 1,
                is_project_termbase BOOLEAN DEFAULT 0,  -- True if this is a project-specific termbase
                priority INTEGER DEFAULT 50,  -- DEPRECATED: Use ranking instead
                ranking INTEGER,  -- Termbase activation ranking: 1 = highest priority, 2 = second highest, etc. Only for activated termbases.
                read_only BOOLEAN DEFAULT 1,  -- Whether this termbase should not be updated (default: read-only, Write unchecked)
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Migration: Add priority column if it doesn't exist (for existing databases)
        try:
            self.cursor.execute("ALTER TABLE termbases ADD COLUMN priority INTEGER DEFAULT 50")
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass
        
        # Migration: Add is_project_termbase column if it doesn't exist
        try:
            self.cursor.execute("ALTER TABLE termbases ADD COLUMN is_project_termbase BOOLEAN DEFAULT 0")
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass
        
        # Migration: Add ranking column if it doesn't exist
        try:
            self.cursor.execute("ALTER TABLE termbases ADD COLUMN ranking INTEGER")
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass
        
        # Migration: Add read_only column if it doesn't exist
        try:
            self.cursor.execute("ALTER TABLE termbases ADD COLUMN read_only BOOLEAN DEFAULT 1")
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass

        # Migration (v1.10.28, default flipped in v1.10.29): Add
        # voice_dictation_enabled column. Per-termbase opt-in flag
        # for voice-dictation vocabulary biasing – when on, the
        # termbase's target-language terms are appended to Whisper's
        # initial_prompt by the Voice tab's "Also bias from your
        # termbases" toggle. **Default 0 (off)** so users with many
        # termbases don't get a noisy prompt by default – they pick
        # a small handful in Termbase Manager's 🎤 Voice column.
        # (v1.10.28 shipped with DEFAULT 1; v1.10.29 flipped to
        # DEFAULT 0 + added a one-shot Supervertaler.py-side reset
        # to clear v1.10.28 rows that came in with 1s.) Shared
        # between Workbench and Supervertaler for Trados via the
        # common database file, so flipping the flag in either
        # product takes effect in the other.
        try:
            self.cursor.execute(
                "ALTER TABLE termbases ADD COLUMN voice_dictation_enabled BOOLEAN DEFAULT 0"
            )
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass

        # SuperLookup inclusion flag (mirror of the translation_memories
        # column above). Independent of the Read flag
        # (termbase_activation.is_active): Read gates terminology matching
        # for the active project; this gates whether SuperLookup searches
        # the termbase at all. DEFAULT 1 (included) so SuperLookup keeps
        # searching every termbase on upgrade until the user opts out per
        # termbase via the SuperLookup column on the Termbases tab.
        try:
            self.cursor.execute(
                "ALTER TABLE termbases ADD COLUMN superlookup_enabled BOOLEAN DEFAULT 1"
            )
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass

        # Legacy support: create glossaries as alias for termbases
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS glossaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                source_lang TEXT,
                target_lang TEXT,
                project_id INTEGER,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Termbase activation (tracks which termbases are active for which projects)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS termbase_activation (
                termbase_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                activated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                priority INTEGER,  -- Manual priority (1=highest, 2=second, etc.). Multiple termbases can share same priority.
                PRIMARY KEY (termbase_id, project_id),
                FOREIGN KEY (termbase_id) REFERENCES termbases(id) ON DELETE CASCADE
            )
        """)
        
        # Migration: Add priority column to termbase_activation if it doesn't exist
        try:
            self.cursor.execute("ALTER TABLE termbase_activation ADD COLUMN priority INTEGER")
            self.connection.commit()
        except Exception:
            # Column already exists, ignore
            pass

        # Data repair (v1.10.360): clear stale is_project_termbase flags.
        #
        # Until v1.10.359 a startup migration (formerly above, next to the
        # termbases table) flagged EVERY project-scoped termbase as "the
        # project termbase", conflating project-scoped with the
        # project-termbase role and silently desynchronising the legacy flag
        # from the authoritative representation
        # (termbase_activation.priority = 1, which is what the Termbases tab
        # displays and matching ranks by). Result: term extraction refused to
        # create a project termbase "because one already exists" while the
        # UI showed none.
        #
        # That migration is gone; this repair clears any flag that has no
        # backing priority=1 activation row anywhere. Idempotent — normally
        # updates 0 rows. Flags that DO match a priority=1 row are kept in
        # sync by TermbaseManager.set_termbase_priority (the single write
        # path for the project-termbase role).
        try:
            self.cursor.execute("""
                UPDATE termbases
                SET is_project_termbase = 0
                WHERE is_project_termbase = 1
                AND id NOT IN (
                    SELECT termbase_id FROM termbase_activation
                    WHERE priority = 1 AND is_active = 1
                )
            """)
            updated_count = self.cursor.rowcount
            if updated_count > 0:
                self.log(f"✅ Data repair: cleared stale is_project_termbase flag on {updated_count} termbase(s)")
            self.connection.commit()
        except Exception as e:
            self.log(f"⚠️ Data repair warning (is_project_termbase): {e}")
            pass

        # Legacy support: termbase_project_activation as alias
        # Note: Foreign key now references termbases for consistency with Qt version
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS termbase_project_activation (
                termbase_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                activated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (termbase_id, project_id),
                FOREIGN KEY (termbase_id) REFERENCES termbases(id) ON DELETE CASCADE
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS termbase_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_term TEXT NOT NULL,
                target_term TEXT NOT NULL,
                source_lang TEXT DEFAULT 'unknown',
                target_lang TEXT DEFAULT 'unknown',
                termbase_id INTEGER NOT NULL,   -- numeric termbases.id (was TEXT; see id-migration)
                priority INTEGER DEFAULT 99,
                project_id TEXT,
                
                -- Terminology-specific fields
                synonyms TEXT,
                forbidden_terms TEXT,
                definition TEXT,
                context TEXT,
                part_of_speech TEXT,
                domain TEXT,
                case_sensitive BOOLEAN DEFAULT 0,
                forbidden BOOLEAN DEFAULT 0,
                is_nontranslatable BOOLEAN DEFAULT 0,

                -- Link to TM entry (optional)
                tm_source_id INTEGER,
                
                -- Metadata
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usage_count INTEGER DEFAULT 0,
                notes TEXT,
                note TEXT,
                project TEXT,
                client TEXT,
                term_uuid TEXT,
                
                FOREIGN KEY (tm_source_id) REFERENCES translation_units(id) ON DELETE SET NULL
            )
        """)
        
        # Indexes for termbase_terms
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gt_source_term 
            ON termbase_terms(source_term)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gt_termbase_id 
            ON termbase_terms(termbase_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gt_project_id
            ON termbase_terms(project_id)
        """)

        # Migration: add url + per-language abbreviation columns to
        # termbase_terms. Both fields are present in the Trados plugin's
        # term editor and were finally surfaced in the Workbench dialog
        # in v1.9.478. ALTER TABLE ADD COLUMN is the safe SQLite-friendly
        # path; existing rows simply get NULL for the new fields.
        try:
            self.cursor.execute("PRAGMA table_info(termbase_terms)")
            tt_columns = [row[1] for row in self.cursor.fetchall()]
            if 'url' not in tt_columns:
                self.cursor.execute("ALTER TABLE termbase_terms ADD COLUMN url TEXT")
                print("✓ Added url column to termbase_terms")
            if 'source_abbreviation' not in tt_columns:
                self.cursor.execute("ALTER TABLE termbase_terms ADD COLUMN source_abbreviation TEXT")
                print("✓ Added source_abbreviation column to termbase_terms")
            if 'target_abbreviation' not in tt_columns:
                self.cursor.execute("ALTER TABLE termbase_terms ADD COLUMN target_abbreviation TEXT")
                print("✓ Added target_abbreviation column to termbase_terms")
            self.connection.commit()
        except Exception as e:
            print(f"termbase_terms migration info: {e}")


        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_gt_domain 
            ON termbase_terms(domain)
        """)
        
        # Full-text search for termbase
        self.cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS termbase_terms_fts 
            USING fts5(
                source_term,
                target_term,
                definition,
                content=termbase_terms,
                content_rowid=id
            )
        """)
        
        # ============================================
        # NON-TRANSLATABLES
        # ============================================
        # The standalone non_translatables table was removed in v1.9.393.
        # NTs are now flagged on individual termbase entries via the
        # is_nontranslatable column on termbase_terms – same convention as
        # the Trados plugin uses, so the two products share storage.
        # Existing databases that still have the legacy table are left
        # untouched (it's just an unused table sitting on disk).

        # ============================================
        # SEGMENTATION RULES
        # ============================================
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS segmentation_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                source_lang TEXT,
                rule_type TEXT NOT NULL,
                pattern TEXT NOT NULL,
                description TEXT,
                priority INTEGER DEFAULT 100,
                enabled BOOLEAN DEFAULT 1,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sr_source_lang 
            ON segmentation_rules(source_lang)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sr_priority 
            ON segmentation_rules(priority)
        """)
        
        # ============================================
        # PROJECT METADATA
        # ============================================
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_lang TEXT,
                target_lang TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_opened TIMESTAMP,
                
                -- Linked resources (JSON arrays)
                active_tm_ids TEXT,
                active_termbase_ids TEXT,
                active_prompt_file TEXT,
                active_style_guide TEXT,
                
                -- Statistics
                segment_count INTEGER DEFAULT 0,
                translated_count INTEGER DEFAULT 0,
                
                -- Settings (JSON blob)
                settings TEXT
            )
        """)
        
        # ============================================
        # FILE METADATA (for prompts and style guides)
        # ============================================
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS prompt_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                file_type TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                last_used TIMESTAMP,
                use_count INTEGER DEFAULT 0
            )
        """)
        
        # ============================================
        # TMX EDITOR TABLES (for database-backed TMX files)
        # ============================================
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tmx_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                file_name TEXT NOT NULL,
                original_file_path TEXT,  -- Original file path when imported
                load_mode TEXT NOT NULL,  -- 'ram' or 'database'
                file_size INTEGER,  -- File size in bytes
                
                -- Header metadata (JSON)
                header_data TEXT NOT NULL,
                
                -- Statistics
                tu_count INTEGER DEFAULT 0,
                languages TEXT,  -- JSON array of language codes
                
                -- Timestamps
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tmx_translation_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tmx_file_id INTEGER NOT NULL,
                tu_id INTEGER NOT NULL,  -- Original TU ID from TMX file
                
                -- System attributes
                creation_date TEXT,
                creation_id TEXT,
                change_date TEXT,
                change_id TEXT,
                srclang TEXT,
                
                -- Custom attributes (JSON)
                custom_attributes TEXT,
                
                -- Comments (JSON array)
                comments TEXT,
                
                FOREIGN KEY (tmx_file_id) REFERENCES tmx_files(id) ON DELETE CASCADE,
                UNIQUE(tmx_file_id, tu_id)
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tmx_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tu_id INTEGER NOT NULL,  -- References tmx_translation_units.id
                lang TEXT NOT NULL,
                text TEXT NOT NULL,
                
                -- Language-specific attributes
                creation_date TEXT,
                creation_id TEXT,
                change_date TEXT,
                change_id TEXT,
                
                FOREIGN KEY (tu_id) REFERENCES tmx_translation_units(id) ON DELETE CASCADE,
                UNIQUE(tu_id, lang)
            )
        """)
        
        # Indexes for TMX tables
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tmx_tu_file_id 
            ON tmx_translation_units(tmx_file_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tmx_tu_tu_id 
            ON tmx_translation_units(tu_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tmx_seg_tu_id 
            ON tmx_segments(tu_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tmx_seg_lang 
            ON tmx_segments(lang)
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS style_guide_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                language TEXT NOT NULL,
                last_used TIMESTAMP,
                use_count INTEGER DEFAULT 0
            )
        """)
        
        # Commit schema
        try:
            self.connection.commit()
            print("✅ Database tables created and committed successfully")
        except Exception as e:
            print(f"❌ Error committing database schema: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.connection = None
            self.cursor = None
    
    # ============================================
    # TRANSLATION MEMORY METHODS
    # ============================================
    
    def _normalize_lang(self, value):
        """Normalise a language label to a canonical code before it is stored on
        a translation unit ('English'/'en-US'/'Dutch (Belgium)' -> 'en'/'en-US'/'nl-BE').

        Callers pass whatever the project holds, which for some projects is a
        display name ('English'/'Dutch') rather than a code. Storing those
        verbatim left a TM with a mix of name- and code-tagged rows, which
        defeated the Trados bridge's per-row source/target orientation and
        inserted the wrong language. Routing every write through
        ``language_codes.canonical`` (the single normalisation authority)
        converts display names to codes while preserving any region subtag
        ("keep region, match on base"), so a TM's rows stay consistent. Falls
        back to the original value if the normaliser is unavailable or doesn't
        recognise the input, so a write never fails here.
        """
        try:
            try:
                from modules import language_codes as _lc
            except ImportError:
                import language_codes as _lc
            return _lc.canonical(value) or (value or '')
        except Exception:
            return value or ''

    def add_translation_unit(self, source: str, target: str, source_lang: str,
                            target_lang: str, tm_id: str = 'project',
                            project_id: str = None, context_before: str = None,
                            context_after: str = None, notes: str = None,
                            overwrite: bool = False) -> int:
        """
        Add translation unit to database

        Args:
            source: Source text
            target: Target text
            source_lang: Source language code
            target_lang: Target language code
            tm_id: TM identifier
            project_id: Optional project ID
            context_before: Optional context before
            context_after: Optional context after
            notes: Optional notes
            overwrite: If True, delete existing entries with same source before inserting
                      (implements "Save only latest translation" mode)

        Returns: ID of inserted/updated entry, or None if rejected/failed
        """
        # v1.10.213: phantom-TM write guard. Without this, callers that pass
        # a tm_id whose row no longer exists in translation_memories (e.g.
        # because the TM was deleted from the tab but a stale activation /
        # cached id is still floating around) would silently create orphan
        # rows that the UI can never surface. Refuse the insert instead.
        if not self._tm_id_exists(tm_id):
            self.log(f"⚠️ Refusing to add TU: tm_id '{tm_id}' has no row in translation_memories (orphan write blocked)")
            return None

        # Store languages as canonical codes so a TM never mixes display
        # names and codes across rows (which reverses bridged-TM matches).
        source_lang = self._normalize_lang(source_lang)
        target_lang = self._normalize_lang(target_lang)

        # Generate hash from NORMALIZED source for consistent exact matching
        # This handles invisible differences like Unicode normalization, whitespace variations
        normalized_source = _normalize_for_matching(source)
        source_hash = hashlib.md5(normalized_source.encode('utf-8')).hexdigest()
        # Same for the target, so reverse (opposite-direction) exact matches are
        # an indexed lookup rather than a target_text full-table scan.
        target_hash = hashlib.md5(_normalize_for_matching(target).encode('utf-8')).hexdigest()

        try:
            # If overwrite mode, delete ALL existing entries with same source_hash and tm_id
            # This ensures only the latest translation is kept
            if overwrite:
                self.cursor.execute("""
                    DELETE FROM translation_units
                    WHERE source_hash = ? AND tm_id = ?
                """, (source_hash, tm_id))

            self.cursor.execute("""
                INSERT INTO translation_units
                (source_text, target_text, source_lang, target_lang, tm_id,
                 project_id, context_before, context_after, source_hash, target_hash, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_hash, target_text, tm_id) DO UPDATE SET
                    usage_count = usage_count + 1,
                    modified_date = CURRENT_TIMESTAMP
            """, (source, target, source_lang, target_lang, tm_id,
                  project_id, context_before, context_after, source_hash, target_hash, notes))

            self.connection.commit()
            return self.cursor.lastrowid

        except Exception as e:
            self.log(f"Error adding translation unit: {e}")
            return None

    def _tm_id_exists(self, tm_id: str) -> bool:
        """
        Cheap existence check on translation_memories.
        Used by add_translation_unit / add_translation_units_batch to refuse
        writes to phantom TMs.

        v1.10.214: the codebase carries TWO tm_id conventions in active use:
          - string tm_id like 'patents' / 'brants_ursu_008_be_ep'
            (save-on-confirm path passes this form)
          - integer PK of translation_memories stringified, like '42' / '86'
            (bulk "Update Active TMs" path passes THIS form, because its
            dropdown stores translation_memories.id as the combo data)
        Both forms are legitimate; rejecting one of them broke real writes
        in v1.10.213. We accept either.
        """
        if not tm_id:
            return False
        try:
            self.cursor.execute(
                "SELECT 1 FROM translation_memories "
                "WHERE tm_id = ? OR CAST(id AS TEXT) = ? LIMIT 1",
                (tm_id, tm_id)
            )
            return self.cursor.fetchone() is not None
        except Exception:
            # If the check itself fails, fall back to permissive behaviour –
            # we'd rather risk an occasional orphan than block a real write
            # because of a transient cursor problem.
            return True
    
    def add_translation_units_batch(self, entries: list, source_lang: str,
                                    target_lang: str, tm_id: str = 'project') -> int:
        """
        Batch-insert translation units with a single commit.

        Much faster than calling add_translation_unit() per row, which commits
        after every INSERT (millions of disk syncs for large TMX files).

        Args:
            entries: List of (source_text, target_text) tuples
            source_lang: Source language code
            target_lang: Target language code
            tm_id: TM identifier

        Returns: Number of entries inserted
        """
        if not entries:
            return 0

        # v1.10.213: same phantom-TM guard as add_translation_unit. Check
        # once up-front rather than per-row – a batch always writes to a
        # single tm_id, so one existence check suffices.
        if not self._tm_id_exists(tm_id):
            self.log(f"⚠️ Refusing batch insert: tm_id '{tm_id}' has no row in translation_memories (orphan write blocked)")
            return 0

        # Canonicalise the pair's languages once (a batch is single-direction)
        # so stored rows never mix display names with codes – see _normalize_lang.
        source_lang = self._normalize_lang(source_lang)
        target_lang = self._normalize_lang(target_lang)

        inserted = 0
        try:
            for source, target in entries:
                normalized_source = _normalize_for_matching(source)
                source_hash = hashlib.md5(normalized_source.encode('utf-8')).hexdigest()
                target_hash = hashlib.md5(_normalize_for_matching(target).encode('utf-8')).hexdigest()

                self.cursor.execute("""
                    INSERT INTO translation_units
                    (source_text, target_text, source_lang, target_lang, tm_id,
                     project_id, context_before, context_after, source_hash, target_hash, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_hash, target_text, tm_id) DO UPDATE SET
                        usage_count = usage_count + 1,
                        modified_date = CURRENT_TIMESTAMP
                """, (source, target, source_lang, target_lang, tm_id,
                      None, None, None, source_hash, target_hash, None))
                inserted += 1

            self.connection.commit()
            return inserted

        except Exception as e:
            self.log(f"Error in batch insert: {e}")
            try:
                self.connection.commit()  # Commit what we have so far
            except:
                pass
            return inserted

    def get_exact_match(self, source: str, tm_ids: List[str] = None,
                       source_lang: str = None, target_lang: str = None,
                       bidirectional: bool = True, touch: bool = True) -> Optional[Dict]:
        """
        Get exact match from TM

        Args:
            source: Source text to match
            tm_ids: List of TM IDs to search (None = all)
            source_lang: Filter by source language (base code matching: 'en' matches 'en-US', 'en-GB', etc.)
            target_lang: Filter by target language (base code matching)
            bidirectional: If True, search both directions (nl→en AND en→nl)
            touch: If True (default), bump the matched row's usage_count (a write).
                   Set False for read-only callers such as the background prefetch
                   worker, which must not write on every segment it scans.

        Returns: Dictionary with match data or None
        """
        from modules.tmx_generator import get_base_lang_code
        import re

        # Try multiple hash variants for robust matching:
        # 1. Original source hash
        # 2. Normalized source hash (handles whitespace, Unicode)
        # 3. Tag-stripped hash (handles TM entries stored with/without tags)
        # 4. Tag-stripped + normalized hash
        source_hash = hashlib.md5(source.encode('utf-8')).hexdigest()
        normalized_source = _normalize_for_matching(source)
        normalized_hash = hashlib.md5(normalized_source.encode('utf-8')).hexdigest()

        # Also try with HTML/XML tags stripped (handles structural tags like <p>, <li-o>)
        source_no_tags = re.sub(r'<[^>]+>', '', source)
        source_no_tags_hash = hashlib.md5(source_no_tags.encode('utf-8')).hexdigest()
        normalized_no_tags = _normalize_for_matching(source_no_tags)
        normalized_no_tags_hash = hashlib.md5(normalized_no_tags.encode('utf-8')).hexdigest()

        # Get base language codes for comparison
        src_base = get_base_lang_code(source_lang) if source_lang else None
        tgt_base = get_base_lang_code(target_lang) if target_lang else None

        # Search using all hash variants.
        #
        # PERF (v1.10.283): force idx_tu_source_hash and use IN(...) instead of a
        # 4-way OR. On a large TM (this user's is 1.5M+ rows) SQLite's planner
        # otherwise picks idx_tu_tm_id and scans the *entire* matching TM — ~700 ms
        # per call, which is what made every segment click hang for seconds on
        # Windows. source_hash is an md5 and extremely selective (≤ a couple dozen
        # rows across the whole DB), so an index SEARCH here is sub-millisecond.
        # The 4-way OR defeated the optimizer; IN(...) with an explicit INDEXED BY
        # is index-friendly. idx_tu_source_hash is always present (base schema).
        query = """
            SELECT * FROM translation_units INDEXED BY idx_tu_source_hash
            WHERE source_hash IN (?, ?, ?, ?)
        """
        params = [source_hash, normalized_hash, source_no_tags_hash, normalized_no_tags_hash]
        
        if tm_ids:
            placeholders = ','.join('?' * len(tm_ids))
            query += f" AND tm_id IN ({placeholders})"
            params.extend(tm_ids)
        
        # Use flexible language matching (matches 'nl', 'nl-NL', 'Dutch', etc.)
        from modules.tmx_generator import get_lang_match_variants
        if src_base:
            src_variants = get_lang_match_variants(source_lang)
            src_conditions = []
            for variant in src_variants:
                src_conditions.append("source_lang = ?")
                params.append(variant)
                src_conditions.append("source_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(src_conditions)})"
        
        if tgt_base:
            tgt_variants = get_lang_match_variants(target_lang)
            tgt_conditions = []
            for variant in tgt_variants:
                tgt_conditions.append("target_lang = ?")
                params.append(variant)
                tgt_conditions.append("target_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(tgt_conditions)})"
        
        query += " ORDER BY usage_count DESC, modified_date DESC LIMIT 1"
        
        self.cursor.execute(query, params)
        row = self.cursor.fetchone()
        
        if row:
            # Update usage count (skipped for read-only callers, e.g. prefetch)
            if touch:
                self.cursor.execute("""
                    UPDATE translation_units
                    SET usage_count = usage_count + 1
                    WHERE id = ?
                """, (row['id'],))
                self.connection.commit()

            return dict(row)

        # If bidirectional and no forward match, try reverse direction
        if bidirectional and src_base and tgt_base:
            # Search where our source text is in the target field (reverse direction).
            # Matched via the indexed target_hash (md5 of the normalised target) using
            # the same hash variants computed for the forward lookup, so this is an
            # index SEARCH rather than a target_text full-table scan. Rows written
            # before the target_hash migration have NULL target_hash and simply
            # don't match here (the background worker still backfills on use).
            # PERF (v1.10.283): same index-forcing rationale as the forward query —
            # force idx_tu_target_hash + IN(...) so the reverse lookup is an index
            # SEARCH rather than a tm_id scan. (idx_tu_target_hash is created by
            # migrate_translation_units_target_hash, which runs at startup.)
            query = """
                SELECT * FROM translation_units INDEXED BY idx_tu_target_hash
                WHERE target_hash IN (?, ?, ?, ?)
            """
            params = [source_hash, normalized_hash, source_no_tags_hash, normalized_no_tags_hash]
            
            if tm_ids:
                placeholders = ','.join('?' * len(tm_ids))
                query += f" AND tm_id IN ({placeholders})"
                params.extend(tm_ids)
            
            # Reversed: search where TM source_lang matches our target_lang (flexible matching)
            # Note: for reverse, we swap - TM source_lang should match our target_lang
            tgt_variants = get_lang_match_variants(target_lang)
            src_variants = get_lang_match_variants(source_lang)
            
            src_conditions = []
            for variant in tgt_variants:  # TM source_lang = our target_lang
                src_conditions.append("source_lang = ?")
                params.append(variant)
                src_conditions.append("source_lang LIKE ?")
                params.append(f"{variant}-%")
            
            tgt_conditions = []
            for variant in src_variants:  # TM target_lang = our source_lang
                tgt_conditions.append("target_lang = ?")
                params.append(variant)
                tgt_conditions.append("target_lang LIKE ?")
                params.append(f"{variant}-%")
            
            query += f" AND ({' OR '.join(src_conditions)}) AND ({' OR '.join(tgt_conditions)})"
            
            query += " ORDER BY usage_count DESC, modified_date DESC LIMIT 1"
            
            self.cursor.execute(query, params)
            row = self.cursor.fetchone()
            
            if row:
                # Update usage count (skipped for read-only callers, e.g. prefetch)
                if touch:
                    self.cursor.execute("""
                        UPDATE translation_units
                        SET usage_count = usage_count + 1
                        WHERE id = ?
                    """, (row['id'],))
                    self.connection.commit()

                # Swap source/target since this is a reverse match
                result = dict(row)
                result['source_text'], result['target_text'] = result['target_text'], result['source_text']
                result['source_lang'], result['target_lang'] = result['target_lang'], result['source_lang']
                result['reverse_match'] = True
                return result
        
        return None

    def get_exact_matches_batch(self, sources: List[str], tm_ids: List[str] = None,
                                source_lang: str = None, target_lang: str = None,
                                bidirectional: bool = True) -> Dict[str, Dict]:
        """
        Batch exact match lookup for multiple source texts in a single operation.

        Strategy: compute all hashes upfront, query per-hash-variant in bulk using
        a single SQL query per chunk. Uses only the source_hash index for speed.

        Args:
            sources: List of source texts to match
            tm_ids: List of TM IDs to search (None = all)
            source_lang: Filter by source language
            target_lang: Filter by target language
            bidirectional: If True, after the forward sweep, take any sources that
                didn't match and run a reverse-direction lookup against target_text
                (mirrors :py:meth:`get_exact_match`). Lets a project pull matches
                out of a TM whose source/target languages are the inverse of the
                project's — e.g. an ``en→nl`` TM attached to an ``nl→en`` project.

        Returns:
            Dict mapping source_text -> match dict (only for sources that had matches).
            Reverse-direction hits carry a ``reverse_match: True`` key and have their
            source/target fields swapped so downstream consumers can treat them
            identically to forward hits.
        """
        if not sources:
            return {}

        from modules.tmx_generator import get_base_lang_code, get_lang_match_variants

        # Pre-compute language filters ONCE (not per segment)
        src_base = get_base_lang_code(source_lang) if source_lang else None
        tgt_base = get_base_lang_code(target_lang) if target_lang else None
        src_variants = get_lang_match_variants(source_lang) if src_base else []
        tgt_variants = get_lang_match_variants(target_lang) if tgt_base else []

        # Build the language filter SQL once (reused for every chunk)
        lang_sql = ""
        lang_params = []

        if tm_ids:
            tm_placeholders = ','.join('?' * len(tm_ids))
            lang_sql += f" AND tm_id IN ({tm_placeholders})"
            lang_params.extend(tm_ids)

        if src_base and src_variants:
            src_conditions = []
            for variant in src_variants:
                src_conditions.append("source_lang = ?")
                lang_params.append(variant)
                src_conditions.append("source_lang LIKE ?")
                lang_params.append(f"{variant}-%")
            lang_sql += f" AND ({' OR '.join(src_conditions)})"

        if tgt_base and tgt_variants:
            tgt_conditions = []
            for variant in tgt_variants:
                tgt_conditions.append("target_lang = ?")
                lang_params.append(variant)
                tgt_conditions.append("target_lang LIKE ?")
                lang_params.append(f"{variant}-%")
            lang_sql += f" AND ({' OR '.join(tgt_conditions)})"

        # Pre-compile tag-stripping regex
        tag_re = re.compile(r'<[^>]+>')

        # Pre-compute all hash variants for all sources
        # For efficiency: try the simplest hash first (original text), only compute
        # more expensive variants (normalized, tag-stripped) if not all matched.
        # Group by hash for reverse lookup.
        source_to_hashes = {}  # source_text -> [hash1, hash2, ...]
        hash_to_sources = {}   # hash -> [source_text, ...]

        for source in sources:
            hashes = []
            # Variant 1: original text hash
            h1 = hashlib.md5(source.encode('utf-8')).hexdigest()
            hashes.append(h1)
            # Variant 2: normalized hash
            normalized = _normalize_for_matching(source)
            h2 = hashlib.md5(normalized.encode('utf-8')).hexdigest()
            if h2 != h1:
                hashes.append(h2)
            # Variant 3: tag-stripped hash
            no_tags = tag_re.sub('', source)
            h3 = hashlib.md5(no_tags.encode('utf-8')).hexdigest()
            if h3 != h1 and h3 != h2:
                hashes.append(h3)
            # Variant 4: tag-stripped + normalized hash
            norm_no_tags = _normalize_for_matching(no_tags)
            h4 = hashlib.md5(norm_no_tags.encode('utf-8')).hexdigest()
            if h4 != h1 and h4 != h2 and h4 != h3:
                hashes.append(h4)

            source_to_hashes[source] = hashes
            for h in hashes:
                hash_to_sources.setdefault(h, []).append(source)

        # Collect all unique hashes
        all_hashes_list = list(hash_to_sources.keys())

        if not all_hashes_list:
            return {}

        # Query in chunks (SQLite variable limit ~999, leave room for lang params)
        max_hash_params = 900 - len(lang_params)
        if max_hash_params < 50:
            max_hash_params = 50  # Safety floor

        results = {}
        matched_ids = []

        for i in range(0, len(all_hashes_list), max_hash_params):
            chunk = all_hashes_list[i:i + max_hash_params]

            placeholders = ','.join('?' * len(chunk))
            # PERF (v1.10.283): force idx_tu_source_hash. Without the hint SQLite
            # picks idx_tu_tm_id and scans the whole TM (~545 ms/chunk on a 1.5M-row
            # DB); with it the prefetch chunk is ~6 ms. Same root cause as the fix in
            # get_exact_match(). The IN-list is already index-friendly — only the
            # planner's index *choice* needed forcing.
            query = (
                f"SELECT id, source_text, target_text, source_lang, target_lang, "
                f"tm_id, source_hash, usage_count "
                f"FROM translation_units INDEXED BY idx_tu_source_hash "
                f"WHERE source_hash IN ({placeholders})"
                f"{lang_sql}"
            )
            params = list(chunk) + lang_params

            try:
                self.cursor.execute(query, params)
                rows = self.cursor.fetchall()
            except Exception as e:
                print(f"[DEBUG] get_exact_matches_batch: SQL ERROR: {e}")
                continue

            # Group rows by hash for efficient lookup
            rows_by_hash = {}
            for row in rows:
                row_dict = dict(row)
                h = row_dict['source_hash']
                # Keep the row with highest usage_count per hash
                if h not in rows_by_hash or row_dict.get('usage_count', 0) > rows_by_hash[h].get('usage_count', 0):
                    rows_by_hash[h] = row_dict

            # Map results back to original source texts
            for h, row_dict in rows_by_hash.items():
                matched_ids.append(row_dict['id'])
                if h in hash_to_sources:
                    for original_source in hash_to_sources[h]:
                        if original_source not in results:
                            results[original_source] = row_dict

        # === REVERSE-DIRECTION FALLBACK ===
        # Mirrors get_exact_match()'s bidirectional path: any source that didn't
        # match in the forward sweep gets a second chance against target_text,
        # with the language filters swapped. Lets an `en→nl` TM serve an `nl→en`
        # project without the user having to re-import the TMX.
        #
        # Note: like the single-segment reverse path, this uses literal
        # `target_text = ?` equality (no hash variants), so the matching is less
        # forgiving of whitespace/tag differences than the forward path. That
        # parity is deliberate — both directions behave the same way.
        if bidirectional and src_base and tgt_base and src_variants and tgt_variants:
            unmatched_sources = [s for s in sources if s not in results]
            if unmatched_sources:
                # Build reverse language filter once, then chunk the IN-list.
                reverse_lang_sql = ""
                reverse_lang_params = []

                if tm_ids:
                    tm_placeholders = ','.join('?' * len(tm_ids))
                    reverse_lang_sql += f" AND tm_id IN ({tm_placeholders})"
                    reverse_lang_params.extend(tm_ids)

                # TM source_lang should match our TARGET language (in reverse)
                src_conditions_rev = []
                for variant in tgt_variants:
                    src_conditions_rev.append("source_lang = ?")
                    reverse_lang_params.append(variant)
                    src_conditions_rev.append("source_lang LIKE ?")
                    reverse_lang_params.append(f"{variant}-%")
                reverse_lang_sql += f" AND ({' OR '.join(src_conditions_rev)})"

                # TM target_lang should match our SOURCE language (in reverse)
                tgt_conditions_rev = []
                for variant in src_variants:
                    tgt_conditions_rev.append("target_lang = ?")
                    reverse_lang_params.append(variant)
                    tgt_conditions_rev.append("target_lang LIKE ?")
                    reverse_lang_params.append(f"{variant}-%")
                reverse_lang_sql += f" AND ({' OR '.join(tgt_conditions_rev)})"

                # Chunk the IN clause to respect SQLite's ~999-param ceiling.
                max_text_params = 900 - len(reverse_lang_params)
                if max_text_params < 50:
                    max_text_params = 50

                # Track which sources got reverse hits so we can dedupe within
                # this loop too (multiple TM rows could share the same
                # target_text — first wins).
                for j in range(0, len(unmatched_sources), max_text_params):
                    text_chunk = unmatched_sources[j:j + max_text_params]
                    placeholders = ','.join('?' * len(text_chunk))
                    rev_query = (
                        f"SELECT id, source_text, target_text, source_lang, target_lang, "
                        f"tm_id, source_hash, usage_count "
                        f"FROM translation_units "
                        f"WHERE target_text IN ({placeholders})"
                        f"{reverse_lang_sql}"
                    )
                    rev_params = list(text_chunk) + reverse_lang_params

                    try:
                        self.cursor.execute(rev_query, rev_params)
                        rev_rows = self.cursor.fetchall()
                    except Exception as e:
                        print(f"[DEBUG] get_exact_matches_batch (reverse): SQL ERROR: {e}")
                        continue

                    for row in rev_rows:
                        row_dict = dict(row)
                        # In the row, target_text matches one of our source strings.
                        # Capture that mapping BEFORE swapping fields.
                        original_source = row_dict['target_text']
                        if original_source in results:
                            continue  # already matched (forward or earlier reverse hit)

                        # Swap fields so downstream code sees a normal-looking
                        # forward match. Mark the result so the UI can distinguish
                        # it (e.g. show a "reversed" badge in the Match Panel).
                        row_dict['source_text'], row_dict['target_text'] = (
                            row_dict['target_text'], row_dict['source_text']
                        )
                        row_dict['source_lang'], row_dict['target_lang'] = (
                            row_dict['target_lang'], row_dict['source_lang']
                        )
                        row_dict['reverse_match'] = True
                        results[original_source] = row_dict
                        matched_ids.append(row_dict['id'])

        # Batch update usage counts in a single operation
        if matched_ids:
            unique_ids = list(set(matched_ids))
            for i in range(0, len(unique_ids), 900):
                chunk = unique_ids[i:i + 900]
                placeholders = ','.join('?' * len(chunk))
                try:
                    self.cursor.execute(
                        f"UPDATE translation_units SET usage_count = usage_count + 1 WHERE id IN ({placeholders})",
                        chunk
                    )
                except Exception:
                    pass
            self.connection.commit()

        return results

    def search_fuzzy_matches_batch(self, sources: List[str], tm_ids: List[str] = None,
                                    threshold: float = 0.75,
                                    source_lang: str = None, target_lang: str = None,
                                    progress_callback=None,
                                    bidirectional: bool = True) -> Dict[str, Dict]:
        """
        Batch fuzzy match lookup for multiple source texts.

        Loads all TM candidates into memory once, then compares each source
        against candidates using word-overlap pre-filtering + SequenceMatcher.
        This eliminates per-segment SQL overhead which dominates with large TMs.

        Args:
            sources: List of source texts to match (should exclude already exact-matched)
            tm_ids: List of TM IDs to search (None = all)
            threshold: Minimum similarity (0.0-1.0)
            source_lang: Filter by source language
            target_lang: Filter by target language
            progress_callback: Optional callback(current, total) called every N segments
            bidirectional: If True, also load reverse-direction candidates (TMs whose
                source/target languages are the inverse of the project's) and pre-swap
                their source/target text so they participate in scoring identically
                to forward candidates. Mirrors :py:meth:`search_fuzzy_matches`'s
                bidirectional behaviour for the batch path.

        Returns:
            Dict mapping source_text -> best match dict (with 'similarity' and 'match_pct').
            Reverse-direction hits carry a ``reverse_match: True`` key.
        """
        if not sources:
            return {}

        from modules.tmx_generator import get_base_lang_code, get_lang_match_variants

        # Pre-compute language filters ONCE
        src_base = get_base_lang_code(source_lang) if source_lang else None
        tgt_base = get_base_lang_code(target_lang) if target_lang else None
        src_variants = get_lang_match_variants(source_lang) if src_base else []
        tgt_variants = get_lang_match_variants(target_lang) if tgt_base else []

        tag_re = re.compile(r'<[^>]+>')

        # Build language filter SQL (for single bulk query)
        lang_sql = ""
        lang_params = []

        if tm_ids:
            tm_placeholders = ','.join('?' * len(tm_ids))
            lang_sql += f" AND tm_id IN ({tm_placeholders})"
            lang_params.extend(tm_ids)

        if src_base and src_variants:
            src_conditions = []
            for variant in src_variants:
                src_conditions.append("source_lang = ?")
                lang_params.append(variant)
                src_conditions.append("source_lang LIKE ?")
                lang_params.append(f"{variant}-%")
            lang_sql += f" AND ({' OR '.join(src_conditions)})"

        if tgt_base and tgt_variants:
            tgt_conditions = []
            for variant in tgt_variants:
                tgt_conditions.append("target_lang = ?")
                lang_params.append(variant)
                tgt_conditions.append("target_lang LIKE ?")
                lang_params.append(f"{variant}-%")
            lang_sql += f" AND ({' OR '.join(tgt_conditions)})"

        # === PHASE A: Load all TM candidates into memory (single query) ===
        query = f"""
            SELECT id, source_text, target_text, tm_id, usage_count
            FROM translation_units
            WHERE 1=1 {lang_sql}
        """
        try:
            self.cursor.execute(query, lang_params)
            all_rows = self.cursor.fetchall()
        except Exception:
            return {}

        if not all_rows:
            return {}

        # Pre-compute cleaned text + word sets for all TM candidates
        candidates = []
        for row in all_rows:
            source_text = row['source_text']
            clean = tag_re.sub('', source_text).lower()
            words = set(clean.split())
            candidates.append({
                'id': row['id'],
                'source_text': source_text,
                'target_text': row['target_text'],
                'tm_id': row['tm_id'],
                'usage_count': row['usage_count'],
                'clean': clean,
                'clean_len': len(clean),
                'words': words,
                'reverse_match': False,
            })

        # === REVERSE-DIRECTION CANDIDATES ===
        # Mirrors search_fuzzy_matches()'s bidirectional path: also pull rows
        # whose lang pair is the inverse of the project's, and pre-swap their
        # source/target text so Phase B (the scorer) doesn't need to know
        # about direction. Each reverse candidate carries reverse_match=True
        # so the result dict can propagate the flag downstream.
        if bidirectional and src_base and tgt_base and src_variants and tgt_variants:
            reverse_lang_sql = ""
            reverse_lang_params = []

            if tm_ids:
                tm_placeholders = ','.join('?' * len(tm_ids))
                reverse_lang_sql += f" AND tm_id IN ({tm_placeholders})"
                reverse_lang_params.extend(tm_ids)

            # TM source_lang = our TARGET language
            src_conditions_rev = []
            for variant in tgt_variants:
                src_conditions_rev.append("source_lang = ?")
                reverse_lang_params.append(variant)
                src_conditions_rev.append("source_lang LIKE ?")
                reverse_lang_params.append(f"{variant}-%")
            reverse_lang_sql += f" AND ({' OR '.join(src_conditions_rev)})"

            # TM target_lang = our SOURCE language
            tgt_conditions_rev = []
            for variant in src_variants:
                tgt_conditions_rev.append("target_lang = ?")
                reverse_lang_params.append(variant)
                tgt_conditions_rev.append("target_lang LIKE ?")
                reverse_lang_params.append(f"{variant}-%")
            reverse_lang_sql += f" AND ({' OR '.join(tgt_conditions_rev)})"

            rev_query = f"""
                SELECT id, source_text, target_text, tm_id, usage_count
                FROM translation_units
                WHERE 1=1 {reverse_lang_sql}
            """
            try:
                self.cursor.execute(rev_query, reverse_lang_params)
                rev_rows = self.cursor.fetchall()
            except Exception:
                rev_rows = []

            for row in rev_rows:
                # In reverse mode the row's TARGET_TEXT is what we score against,
                # and its SOURCE_TEXT is what we'd hand back as the translation.
                # Pre-swap so the candidate slots into Phase B unchanged.
                match_text = row['target_text']  # was target, now plays as source
                output_text = row['source_text']  # was source, now plays as target
                clean = tag_re.sub('', match_text).lower()
                words = set(clean.split())
                candidates.append({
                    'id': row['id'],
                    'source_text': match_text,
                    'target_text': output_text,
                    'tm_id': row['tm_id'],
                    'usage_count': row['usage_count'],
                    'clean': clean,
                    'clean_len': len(clean),
                    'words': words,
                    'reverse_match': True,
                })

        # Pre-compute cleaned texts and word sets for all sources
        source_data = []
        for source in sources:
            clean = tag_re.sub('', source).lower()
            words = set(clean.split())
            source_data.append({
                'source': source,
                'clean': clean,
                'clean_len': len(clean),
                'words': words,
            })

        # === PHASE B: For each source, find best match using pre-filters ===
        results = {}
        total = len(sources)

        for idx, sd in enumerate(source_data):
            # Report progress every 10 segments
            if progress_callback and idx % 10 == 0:
                progress_callback(idx, total)

            source = sd['source']
            source_clean = sd['clean']
            source_len = sd['clean_len']
            source_words = sd['words']
            # CJK/space-less sources collapse to a single "word", so the word-
            # overlap pre-filter below would wrongly reject every non-identical
            # candidate. Skip it for them and let the length + char-level
            # quick_ratio/SequenceMatcher gates decide.
            source_is_cjk = _dbm_contains_cjk(source_clean)

            if source_len == 0:
                continue

            best_match = None
            best_similarity = 0.0

            for cand in candidates:
                cand_len = cand['clean_len']
                if cand_len == 0:
                    continue

                # Pre-filter 1: Length ratio (very cheap)
                len_ratio = min(source_len, cand_len) / max(source_len, cand_len)
                if len_ratio < threshold:
                    continue

                # Pre-filter 2: Word overlap (cheap set intersection)
                # If word overlap is too low, SequenceMatcher won't hit threshold.
                # Skipped for CJK sources (see source_is_cjk above).
                if not source_is_cjk and source_words and cand['words']:
                    overlap = len(source_words & cand['words'])
                    max_words = max(len(source_words), len(cand['words']))
                    if max_words > 0 and overlap / max_words < threshold * 0.5:
                        continue

                # Pre-filter 3: quick_ratio (O(n) upper bound)
                sm = SequenceMatcher(None, source_clean, cand['clean'])
                if sm.quick_ratio() <= best_similarity:
                    continue

                # Full similarity (O(n²) but only for promising candidates)
                similarity = sm.ratio()

                if similarity >= threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_match = {
                        'id': cand['id'],
                        'source_text': cand['source_text'],
                        'target_text': cand['target_text'],
                        'tm_id': cand['tm_id'],
                        'usage_count': cand['usage_count'],
                        'similarity': similarity,
                        'match_pct': int(similarity * 100),
                        'reverse_match': cand.get('reverse_match', False),
                    }

                    # Early exit on perfect match
                    if similarity >= 0.999:
                        break

            if best_match:
                results[source] = best_match

        return results

    def calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate similarity ratio between two texts using SequenceMatcher.
        Tags and line breaks are normalised before comparison so that segments
        that differ only by an inline line break (e.g. a Trados <lb/> heading
        prefix like "Door stops↵\n") still score well against the body text.

        Returns: Similarity score from 0.0 to 1.0
        """
        import re
        # Strip HTML/XML tags for comparison
        clean1 = re.sub(r'<[^>]+>', '', text1).lower()
        clean2 = re.sub(r'<[^>]+>', '', text2).lower()
        # Normalise line breaks and surrounding whitespace to a single space,
        # matching the behaviour of _normalize_for_matching() used for exact match.
        # This prevents segments that differ only by a heading line or an inline
        # line break from scoring very low in fuzzy matching.
        clean1 = re.sub(r'\s*\n\s*', ' ', clean1).strip()
        clean2 = re.sub(r'\s*\n\s*', ' ', clean2).strip()
        return SequenceMatcher(None, clean1, clean2).ratio()

    def search_fuzzy_matches(self, source: str, tm_ids: List[str] = None,
                            threshold: float = 0.75, max_results: int = 5,
                            source_lang: str = None, target_lang: str = None,
                            bidirectional: bool = True) -> List[Dict]:
        """
        Search for fuzzy matches using FTS5 with proper similarity calculation
        
        Args:
            bidirectional: If True, search both directions (nl→en AND en→nl)
        
        Returns: List of matches with similarity scores
        
        Note: When multiple TMs are provided, searches each TM separately to ensure
        good matches from smaller TMs aren't pushed out by BM25 keyword ranking
        from larger TMs. Results are merged and sorted by actual similarity.
        """
        # For better FTS5 matching, tokenize the query and escape special chars
        # FTS5 special characters: " ( ) - : , . ! ? 
        import re
        from modules.tmx_generator import get_base_lang_code, get_lang_match_variants
        
        # Strip HTML/XML tags from source for clean text search
        text_without_tags = re.sub(r'<[^>]+>', '', source)
        
        # Remove special FTS5 characters and split into words (from tag-stripped text)
        clean_text = re.sub(r'[^\w\s]', ' ', text_without_tags)  # Replace special chars with spaces
        search_terms_clean = [term for term in clean_text.strip().split() if len(term) > 2]  # Min 3 chars
        
        # Also get search terms from original source (in case TM was indexed with tags)
        clean_text_with_tags = re.sub(r'[^\w\s]', ' ', source)
        search_terms_with_tags = [term for term in clean_text_with_tags.strip().split() if len(term) > 2]
        
        # Combine both sets of search terms (deduplicated)
        all_search_terms = list(dict.fromkeys(search_terms_clean + search_terms_with_tags))
        
        # For long segments, prioritize longer/rarer words to get better FTS5 candidates
        # Sort by length (longer words are usually more discriminating)
        all_search_terms.sort(key=len, reverse=True)
        
        # Limit search terms to avoid overly complex queries (top 20 longest words)
        # This helps find similar long segments more reliably
        search_terms_for_query = all_search_terms[:20]
        
        if not search_terms_for_query:
            # If no valid terms, return empty results
            return []
        
        # Quote each term to prevent FTS5 syntax errors
        fts_query = ' OR '.join(f'"{term}"' for term in search_terms_for_query)
        fts_table = 'translation_units_fts'

        # CJK source: the unicode61 index can't retrieve similar space-less
        # segments (a whole run is one token), so query the trigram index with
        # the source's overlapping 3-grams instead. Falls back to the word query
        # above if the trigram index is unavailable or yields no CJK trigrams.
        if getattr(self, '_trigram_fts_available', False) and _dbm_contains_cjk(text_without_tags):
            grams = _cjk_trigrams(text_without_tags)
            if grams:
                fts_query = ' OR '.join('"' + g.replace('"', '""') + '"' for g in grams)
                fts_table = 'translation_units_trigram'

        # Get base language codes for comparison
        src_base = get_base_lang_code(source_lang) if source_lang else None
        tgt_base = get_base_lang_code(target_lang) if target_lang else None
        
        # MULTI-TM FIX: Search each TM separately to avoid BM25 ranking issues
        # When a large TM is combined with a small TM, the large TM's many keyword matches
        # push down genuinely similar sentences from the small TM
        tms_to_search = tm_ids if tm_ids else [None]  # None means search all TMs together
        
        all_results = []
        
        for tm_id in tms_to_search:
            # Search this specific TM (or all if tm_id is None)
            tm_results = self._search_single_tm_fuzzy(
                source, fts_query, [tm_id] if tm_id else None,
                threshold, max_results, src_base, tgt_base,
                source_lang, target_lang, bidirectional, fts_table
            )
            all_results.extend(tm_results)
        
        # Deduplicate by source_text (keep highest similarity for each unique source)
        seen = {}
        for result in all_results:
            key = result['source_text']
            if key not in seen or result['similarity'] > seen[key]['similarity']:
                seen[key] = result
        
        deduped_results = list(seen.values())
        
        # Sort ALL results by similarity (highest first) - this ensures the 76% match
        # appears before 40% matches regardless of which TM they came from
        deduped_results.sort(key=lambda x: x['similarity'], reverse=True)
        
        return deduped_results[:max_results]
    
    def _search_single_tm_fuzzy(self, source: str, fts_query: str, tm_ids: List[str],
                                 threshold: float, max_results: int,
                                 src_base: str, tgt_base: str,
                                 source_lang: str, target_lang: str,
                                 bidirectional: bool,
                                 fts_table: str = 'translation_units_fts') -> List[Dict]:
        """Search a single TM (or all TMs if tm_ids is None) for fuzzy matches.

        fts_table selects which FTS index to retrieve candidates from:
        the default unicode61 'translation_units_fts', or the CJK
        'translation_units_trigram' for space-less source segments.
        """
        from modules.tmx_generator import get_lang_match_variants
        # Internal, fixed table name (never user input) – safe to interpolate.
        if fts_table not in ('translation_units_fts', 'translation_units_trigram'):
            fts_table = 'translation_units_fts'

        # Build query for this TM
        query = f"""
            SELECT tu.*,
                   bm25({fts_table}) as relevance
            FROM translation_units tu
            JOIN {fts_table} ON tu.id = {fts_table}.rowid
            WHERE {fts_table} MATCH ?
        """
        params = [fts_query]
        
        if tm_ids and tm_ids[0] is not None:
            placeholders = ','.join('?' * len(tm_ids))
            query += f" AND tu.tm_id IN ({placeholders})"
            params.extend(tm_ids)
        
        # Use flexible language matching (matches 'nl', 'nl-NL', 'Dutch', etc.)
        if src_base:
            src_variants = get_lang_match_variants(source_lang)
            src_conditions = []
            for variant in src_variants:
                src_conditions.append("tu.source_lang = ?")
                params.append(variant)
                src_conditions.append("tu.source_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(src_conditions)})"
        
        if tgt_base:
            tgt_variants = get_lang_match_variants(target_lang)
            tgt_conditions = []
            for variant in tgt_variants:
                tgt_conditions.append("tu.target_lang = ?")
                params.append(variant)
                tgt_conditions.append("tu.target_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(tgt_conditions)})"
        
        # Per-TM candidate limit - INCREASED to catch more potential fuzzy matches
        # When multiple TMs are searched, BM25 ranking can push genuinely similar
        # entries far down the list due to common word matches in other entries
        candidate_limit = max(500, max_results * 50)
        query += f" ORDER BY relevance DESC LIMIT {candidate_limit}"
        
        try:
            self.cursor.execute(query, params)
            all_rows = self.cursor.fetchall()
        except Exception as e:
            return []
        
        results = []
        
        for row in all_rows:
            match_dict = dict(row)
            # Calculate actual similarity using SequenceMatcher
            similarity = self.calculate_similarity(source, match_dict['source_text'])
            
            # Only include matches above threshold
            if similarity >= threshold:
                match_dict['similarity'] = similarity
                match_dict['match_pct'] = int(similarity * 100)
                results.append(match_dict)
        
        # If bidirectional, also search reverse direction
        if bidirectional and src_base and tgt_base:
            query = f"""
                SELECT tu.*,
                       bm25({fts_table}) as relevance
                FROM translation_units tu
                JOIN {fts_table} ON tu.id = {fts_table}.rowid
                WHERE {fts_table} MATCH ?
            """
            params = [fts_query]
            
            if tm_ids and tm_ids[0] is not None:
                placeholders = ','.join('?' * len(tm_ids))
                query += f" AND tu.tm_id IN ({placeholders})"
                params.extend(tm_ids)
            
            # Reversed language filters with flexible matching
            src_variants = get_lang_match_variants(source_lang)
            tgt_variants = get_lang_match_variants(target_lang)
            
            # TM target_lang = our source_lang
            tgt_conditions = []
            for variant in src_variants:
                tgt_conditions.append("tu.target_lang = ?")
                params.append(variant)
                tgt_conditions.append("tu.target_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(tgt_conditions)})"
            
            # TM source_lang = our target_lang  
            src_conditions = []
            for variant in tgt_variants:
                src_conditions.append("tu.source_lang = ?")
                params.append(variant)
                src_conditions.append("tu.source_lang LIKE ?")
                params.append(f"{variant}-%")
            query += f" AND ({' OR '.join(src_conditions)})"
            
            query += f" ORDER BY relevance DESC LIMIT {max_results * 5}"
            
            try:
                self.cursor.execute(query, params)
                
                for row in self.cursor.fetchall():
                    match_dict = dict(row)
                    # Calculate similarity against target_text (since we're reversing)
                    similarity = self.calculate_similarity(source, match_dict['target_text'])
                    
                    # Only include matches above threshold
                    if similarity >= threshold:
                        # Swap source/target for reverse match
                        match_dict['source_text'], match_dict['target_text'] = match_dict['target_text'], match_dict['source_text']
                        match_dict['source_lang'], match_dict['target_lang'] = match_dict['target_lang'], match_dict['source_lang']
                        match_dict['similarity'] = similarity
                        match_dict['match_pct'] = int(similarity * 100)
                        match_dict['reverse_match'] = True
                        results.append(match_dict)
            except Exception as e:
                print(f"[DEBUG] _search_single_tm_fuzzy (reverse): SQL ERROR: {e}")
        
        return results
    
    def search_all(self, source: str, tm_ids: List[str] = None, enabled_only: bool = True,
                   threshold: float = 0.75, max_results: int = 10) -> List[Dict]:
        """
        Search for matches across TMs (both exact and fuzzy)
        
        Args:
            source: Source text to search for
            tm_ids: List of TM IDs to search (None = all)
            enabled_only: Currently ignored (all TMs enabled)
            threshold: Minimum similarity threshold (0.0-1.0)
            max_results: Maximum number of results
            
        Returns:
            List of matches with source, target, match_pct, tm_name
        """
        # First try exact match
        exact = self.get_exact_match(source, tm_ids=tm_ids)
        if exact:
            return [{
                'source': exact['source_text'],
                'target': exact['target_text'],
                'match_pct': 100,
                'tm_name': exact['tm_id'].replace('_', ' ').title(),
                'tm_id': exact['tm_id'],
                # v1.10.51: preserve reverse-match flag for UI badge propagation
                'reverse_match': exact.get('reverse_match', False),
            }]

        # No exact match, try fuzzy
        fuzzy_matches = self.search_fuzzy_matches(
            source,
            tm_ids=tm_ids,
            threshold=threshold,
            max_results=max_results
        )

        results = []
        for match in fuzzy_matches:
            results.append({
                'source': match['source_text'],
                'target': match['target_text'],
                'match_pct': match['match_pct'],
                'tm_name': match['tm_id'].replace('_', ' ').title(),
                'tm_id': match['tm_id'],
                'reverse_match': match.get('reverse_match', False),
            })

        return results
    
    def get_tm_entries(self, tm_id: str, limit: int = None) -> List[Dict]:
        """Get all entries from a specific TM"""
        query = "SELECT * FROM translation_units WHERE tm_id = ? ORDER BY id"
        params = [tm_id]
        
        if limit:
            query += f" LIMIT {limit}"
        
        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]
    
    def get_tm_count(self, tm_id: str = None) -> int:
        """Get entry count for TM(s)"""
        if tm_id:
            self.cursor.execute("""
                SELECT COUNT(*) FROM translation_units WHERE tm_id = ?
            """, (tm_id,))
        else:
            self.cursor.execute("SELECT COUNT(*) FROM translation_units")
        
        return self.cursor.fetchone()[0]
    
    def clear_tm(self, tm_id: str):
        """Clear all entries from a TM"""
        self.cursor.execute("""
            DELETE FROM translation_units WHERE tm_id = ?
        """, (tm_id,))
        self.connection.commit()
    
    def delete_entry(self, tm_id: str, source: str, target: str):
        """Delete a specific entry from a TM"""
        # Get the ID first
        self.cursor.execute("""
            SELECT id FROM translation_units 
            WHERE tm_id = ? AND source_text = ? AND target_text = ?
        """, (tm_id, source, target))
        
        result = self.cursor.fetchone()
        if not result:
            return  # Entry not found
        
        entry_id = result['id']
        
        # Delete from FTS5 index first
        try:
            self.cursor.execute("""
                DELETE FROM tm_fts WHERE rowid = ?
            """, (entry_id,))
        except Exception:
            pass  # FTS5 table might not exist
        
        # Delete from main table
        self.cursor.execute("""
            DELETE FROM translation_units 
            WHERE id = ?
        """, (entry_id,))
        
        self.connection.commit()

    def update_entry(self, tm_id: str, old_source: str, old_target: str,
                     new_source: str, new_target: str) -> bool:
        """Update an existing TM entry (source and/or target text).

        Preserves created_date, usage_count, notes, and other metadata.
        Returns True if the entry was found and updated, False otherwise.
        """
        import hashlib

        self.cursor.execute("""
            SELECT id, source_lang, target_lang, context_before, context_after, notes,
                   usage_count, created_by, created_date
            FROM translation_units
            WHERE tm_id = ? AND source_text = ? AND target_text = ?
        """, (tm_id, old_source, old_target))

        row = self.cursor.fetchone()
        if not row:
            return False

        entry_id = row['id']
        new_source_stripped = new_source.strip()
        new_target_stripped = new_target.strip()
        new_hash = hashlib.md5(new_source_stripped.lower().encode('utf-8')).hexdigest()
        # Keep target_hash in step with the edited target so reverse exact
        # matching stays correct after an edit.
        new_target_hash = hashlib.md5(
            _normalize_for_matching(new_target_stripped).encode('utf-8')).hexdigest()

        # Update FTS5 index
        try:
            self.cursor.execute("""
                UPDATE tm_fts SET source_text = ?, target_text = ?
                WHERE rowid = ?
            """, (new_source_stripped, new_target_stripped, entry_id))
        except Exception:
            pass  # FTS5 table might not exist

        # Update main table
        self.cursor.execute("""
            UPDATE translation_units
            SET source_text = ?, target_text = ?, source_hash = ?, target_hash = ?,
                modified_date = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_source_stripped, new_target_stripped, new_hash, new_target_hash, entry_id))

        self.connection.commit()
        return True

    def concordance_search(self, query: str, tm_ids: List[str] = None, direction: str = 'both',
                            source_lang = None, target_lang = None, connection=None) -> List[Dict]:
        """
        Search for text in source and/or target (concordance search)
        Uses FTS5 full-text search for fast matching on millions of segments.
        Falls back to LIKE queries if FTS5 fails.

        Language filters define what you're searching FOR and what translation you want:
        - "From: Dutch, To: English" = Search for Dutch text, show English translations
        - Searches ALL TMs (regardless of their stored language pair direction)
        - Automatically swaps columns when needed (e.g., finds Dutch in target column of EN→NL TM)
        - This is MORE intuitive than traditional CAT tools that only search specific TM directions

        Args:
            query: Text to search for
            tm_ids: List of TM IDs to search (None = all)
            direction: 'source' = search source only, 'target' = search target only, 'both' = bidirectional
            source_lang: Filter by source language - can be a string OR a list of language variants (None = any)
            target_lang: Filter by target language - can be a string OR a list of language variants (None = any)
            connection: Optional sqlite3.Connection for thread-safe access from worker threads.
        """
        # Resolve cursor: caller may pass a thread-local connection (e.g. from
        # the SuperLookup worker), otherwise we use the main-thread cursor.
        if connection is not None:
            cur = connection.cursor()
            _close_cur = True
        else:
            cur = self.cursor
            _close_cur = False

        # Normalize language filters to lists for consistent handling
        source_langs = source_lang if isinstance(source_lang, list) else ([source_lang] if source_lang else None)
        target_langs = target_lang if isinstance(target_lang, list) else ([target_lang] if target_lang else None)
        
        # Escape FTS5 special characters and wrap words for prefix matching
        # FTS5 special chars: " * ( ) : ^
        fts_query = query.replace('"', '""')
        # Wrap in quotes for phrase search
        fts_query = f'"{fts_query}"'
        
        # When language filters specified, we need to search intelligently:
        # - Don't filter by TM language pair (search ALL TMs)
        # - Search in BOTH columns to find text
        # - Swap columns if needed to show correct language order
        use_smart_search = (source_langs or target_langs)
        
        try:
            # Use FTS5 for fast full-text search
            if direction == 'source':
                fts_sql = """
                    SELECT tu.* FROM translation_units tu
                    JOIN translation_units_fts fts ON tu.id = fts.rowid
                    WHERE fts.source_text MATCH ?
                """
                params = [fts_query]
            elif direction == 'target':
                fts_sql = """
                    SELECT tu.* FROM translation_units tu
                    JOIN translation_units_fts fts ON tu.id = fts.rowid
                    WHERE fts.target_text MATCH ?
                """
                params = [fts_query]
            else:
                # Both directions - search in combined FTS index
                fts_sql = """
                    SELECT tu.* FROM translation_units tu
                    JOIN translation_units_fts fts ON tu.id = fts.rowid
                    WHERE translation_units_fts MATCH ?
                """
                params = [fts_query]
            
            if tm_ids:
                placeholders = ','.join('?' * len(tm_ids))
                fts_sql += f" AND tu.tm_id IN ({placeholders})"
                params.extend(tm_ids)
            
            # DON'T filter by language when smart search active
            # (we need to search all TMs and figure out which column has our language)
            if not use_smart_search:
                # Traditional filtering when no language filters
                if source_langs:
                    placeholders = ','.join('?' * len(source_langs))
                    fts_sql += f" AND tu.source_lang IN ({placeholders})"
                    params.extend(source_langs)
                if target_langs:
                    placeholders = ','.join('?' * len(target_langs))
                    fts_sql += f" AND tu.target_lang IN ({placeholders})"
                    params.extend(target_langs)
            
            fts_sql += " ORDER BY tu.modified_date DESC LIMIT 100"

            cur.execute(fts_sql, params)
            raw_results = [dict(row) for row in cur.fetchall()]
            
            # Smart search: Filter and swap based on language metadata
            if use_smart_search:
                # TM language tags are wildly inconsistent across the store —
                # base codes ('nl','en'), regional variants ('nl-BE','en-GB',
                # 'nl-NL') and even full names ('Dutch','English') all coexist,
                # and the From/To dropdowns may pass any of these. Compare on
                # BASE codes (Dutch->nl, nl-BE->nl) on BOTH sides so a
                # Dutch->English search matches nl->en rows however either was
                # tagged. (Previously this used raw exact membership, so e.g.
                # 'nl' was never 'in' ['Dutch'] and every row was dropped.)
                from modules.tmx_generator import get_base_lang_code
                def _base(x):
                    return get_base_lang_code(x) if x else ''
                src_norms = {_base(x) for x in source_langs} if source_langs else None
                tgt_norms = {_base(x) for x in target_langs} if target_langs else None

                processed_results = []
                for row in raw_results:
                    row_src_norm = _base(row.get('source_lang', ''))
                    row_tgt_norm = _base(row.get('target_lang', ''))

                    # Accept the row if its language pair matches the requested
                    # pair in EITHER orientation (swap the columns when reversed).
                    matches = False
                    needs_swap = False

                    if src_norms and tgt_norms:
                        if row_src_norm in src_norms and row_tgt_norm in tgt_norms:
                            matches = True; needs_swap = False
                        elif row_src_norm in tgt_norms and row_tgt_norm in src_norms:
                            matches = True; needs_swap = True
                    elif src_norms:
                        if row_src_norm in src_norms:
                            matches = True; needs_swap = False
                        elif row_tgt_norm in src_norms:
                            matches = True; needs_swap = True
                    elif tgt_norms:
                        if row_tgt_norm in tgt_norms:
                            matches = True; needs_swap = False
                        elif row_src_norm in tgt_norms:
                            matches = True; needs_swap = True

                    if matches:
                        # CRITICAL CHECK: Verify the search text is actually in the correct column
                        # If user searches for Dutch with "From: Dutch", the text must be in the source column (after any swap)
                        # This prevents finding Dutch text when user asks to search FOR English
                        
                        if needs_swap:
                            # After swap, check if query is in the NEW source column (was target)
                            text_to_check = row['target_text'].lower()
                        else:
                            # No swap, check if query is in source column
                            text_to_check = row['source_text'].lower()
                        
                        # Only include if query text is actually in the source column
                        if query.lower() in text_to_check:
                            if needs_swap:
                                # Swap columns to show correct language order
                                swapped_row = row.copy()
                                swapped_row['source'] = row['target_text']
                                swapped_row['target'] = row['source_text']
                                swapped_row['source_lang'] = row['target_lang']
                                swapped_row['target_lang'] = row['source_lang']
                                processed_results.append(swapped_row)
                            else:
                                # No swap needed - just rename columns
                                processed_row = row.copy()
                                processed_row['source'] = row['source_text']
                                processed_row['target'] = row['target_text']
                                processed_results.append(processed_row)

                if _close_cur:
                    try: cur.close()
                    except Exception: pass
                return processed_results
            else:
                # No language filters - just rename columns
                processed_results = []
                for row in raw_results:
                    processed_row = row.copy()
                    processed_row['source'] = row['source_text']
                    processed_row['target'] = row['target_text']
                    processed_results.append(processed_row)
                if _close_cur:
                    try: cur.close()
                    except Exception: pass
                return processed_results

        except Exception as e:
            # Fallback to LIKE query if FTS5 fails (e.g., index not built)
            print(f"[TM] FTS5 search failed, falling back to LIKE: {e}")
            search_query = f"%{query}%"
            
            if direction == 'source':
                sql = """
                    SELECT * FROM translation_units 
                    WHERE source_text LIKE ?
                """
                params = [search_query]
            elif direction == 'target':
                sql = """
                    SELECT * FROM translation_units 
                    WHERE target_text LIKE ?
                """
                params = [search_query]
            else:
                sql = """
                    SELECT * FROM translation_units 
                    WHERE (source_text LIKE ? OR target_text LIKE ?)
                """
                params = [search_query, search_query]
            
            if tm_ids:
                placeholders = ','.join('?' * len(tm_ids))
                sql += f" AND tm_id IN ({placeholders})"
                params.extend(tm_ids)
            
            # Add language filters (support for list of variants)
            if source_langs:
                placeholders = ','.join('?' * len(source_langs))
                sql += f" AND source_lang IN ({placeholders})"
                params.extend(source_langs)
            if target_langs:
                placeholders = ','.join('?' * len(target_langs))
                sql += f" AND target_lang IN ({placeholders})"
                params.extend(target_langs)
            
            sql += " ORDER BY modified_date DESC LIMIT 100"

            cur.execute(sql, params)
            results = [dict(row) for row in cur.fetchall()]
            if _close_cur:
                try: cur.close()
                except Exception: pass
            return results
    
    def rebuild_fts_index(self) -> int:
        """
        Rebuild the FTS5 full-text search index from scratch.
        Use this after importing TMs or if FTS search isn't returning results.
        
        Returns:
            Number of entries indexed
        """
        try:
            # Clear existing FTS data
            self.cursor.execute("DELETE FROM translation_units_fts")
            
            # Repopulate from translation_units table
            self.cursor.execute("""
                INSERT INTO translation_units_fts(rowid, source_text, target_text)
                SELECT id, source_text, target_text FROM translation_units
            """)
            
            self.conn.commit()
            
            # Get count
            self.cursor.execute("SELECT COUNT(*) FROM translation_units_fts")
            count = self.cursor.fetchone()[0]
            print(f"[TM] FTS5 index rebuilt with {count:,} entries")
            return count
        except Exception as e:
            print(f"[TM] Error rebuilding FTS index: {e}")
            return 0
    
    def check_fts_index(self) -> Dict:
        """
        Check if FTS5 index is in sync with main table.
        
        Returns:
            Dict with 'main_count', 'fts_count', 'in_sync' keys
        """
        try:
            self.cursor.execute("SELECT COUNT(*) FROM translation_units")
            main_count = self.cursor.fetchone()[0]
            
            self.cursor.execute("SELECT COUNT(*) FROM translation_units_fts")
            fts_count = self.cursor.fetchone()[0]
            
            return {
                'main_count': main_count,
                'fts_count': fts_count,
                'in_sync': main_count == fts_count
            }
        except Exception as e:
            return {'main_count': 0, 'fts_count': 0, 'in_sync': False, 'error': str(e)}

    # ============================================
    # termbase METHODS (Placeholder for Phase 3)
    # ============================================
    
    def add_termbase_term(self, source_term: str, target_term: str,
                         source_lang: str, target_lang: str,
                         termbase_id: str = 'main', **kwargs) -> int:
        """Add term to termbase (Phase 3)"""
        # TODO: Implement in Phase 3
        pass
    
    def search_termbases(self, search_term: str, source_lang: str = None,
                        target_lang: str = None, project_id: str = None,
                        min_length: int = 0, bidirectional: bool = True) -> List[Dict]:
        """
        Search termbases for matching terms (bidirectional by default)

        Args:
            search_term: Term to search for
            source_lang: Filter by source language (optional)
            target_lang: Filter by target language (optional)
            project_id: Filter by project (optional)
            min_length: Minimum term length to return
            bidirectional: If True, also search target_term and swap results (default True)

        Returns:
            List of termbase hits
            Each result includes 'match_direction' ('source' or 'target') indicating
            which column matched. For 'target' matches, source_term and target_term
            are swapped so results are always oriented correctly for the current project.
        """
        # Build query with filters - include termbase name and ranking via JOIN
        # Note: termbase_id is stored as TEXT in termbase_terms but INTEGER in termbases
        # Use CAST to ensure proper comparison
        # IMPORTANT: Join with termbase_activation to get the ACTUAL priority for this project
        # CRITICAL FIX: Also match when search_term starts with the glossary term
        # This handles cases like searching for "ca." when glossary has "ca."
        # AND searching for "ca" when glossary has "ca."
        # We also strip trailing punctuation from glossary terms for comparison

        # Build matching conditions for a given column
        def build_match_conditions(column: str) -> str:
            return f"""(
                LOWER(t.{column}) = LOWER(?) OR
                LOWER(t.{column}) LIKE LOWER(?) OR
                LOWER(t.{column}) LIKE LOWER(?) OR
                LOWER(t.{column}) LIKE LOWER(?) OR
                LOWER(RTRIM(t.{column}, '.!?,;:')) = LOWER(?) OR
                LOWER(?) LIKE LOWER(t.{column}) || '%' OR
                LOWER(?) = LOWER(RTRIM(t.{column}, '.!?,;:'))
            )"""

        # Build match params for one direction
        def build_match_params() -> list:
            return [
                search_term,
                f"{search_term} %",
                f"% {search_term}",
                f"% {search_term} %",
                search_term,  # For RTRIM comparison
                search_term,  # For reverse LIKE
                search_term   # For reverse RTRIM comparison
            ]

        # Matching patterns:
        # 1. Exact match: column = search_term
        # 2. Glossary term starts with search: column LIKE "search_term %"
        # 3. Glossary term ends with search: column LIKE "% search_term"
        # 4. Glossary term contains search: column LIKE "% search_term %"
        # 5. Glossary term (stripped) = search_term: RTRIM(column) = search_term (handles "ca." = "ca")
        # 6. Search starts with glossary term: search_term LIKE column || '%'
        # 7. Search = glossary term stripped: search_term = RTRIM(column)

        # Base SELECT for forward matches (source_term matches)
        base_select_forward = """
            SELECT
                t.id, t.source_term, t.target_term, t.termbase_id,
                t.forbidden, t.source_lang, t.target_lang, t.definition, t.domain,
                t.notes, t.project, t.client,
                tb.name as termbase_name,
                tb.source_lang as termbase_source_lang,
                tb.target_lang as termbase_target_lang,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as is_project_termbase,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as ranking,
                'source' as match_direction
            FROM termbase_terms t
            LEFT JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
            LEFT JOIN termbase_activation ta ON ta.termbase_id = tb.id AND ta.project_id = ? AND ta.is_active = 1
            WHERE {match_conditions}
            AND ta.is_active = 1
        """.format(match_conditions=build_match_conditions('source_term'))

        # Base SELECT for reverse matches (target_term matches) - swap source/target in output
        base_select_reverse = """
            SELECT
                t.id, t.target_term as source_term, t.source_term as target_term,
                t.termbase_id,
                t.forbidden, t.target_lang as source_lang, t.source_lang as target_lang,
                t.definition, t.domain,
                t.notes, t.project, t.client,
                tb.name as termbase_name,
                tb.target_lang as termbase_source_lang,
                tb.source_lang as termbase_target_lang,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as is_project_termbase,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as ranking,
                'target' as match_direction
            FROM termbase_terms t
            LEFT JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
            LEFT JOIN termbase_activation ta ON ta.termbase_id = tb.id AND ta.project_id = ? AND ta.is_active = 1
            WHERE {match_conditions}
            AND ta.is_active = 1
        """.format(match_conditions=build_match_conditions('target_term'))

        # Build params
        project_param = project_id if project_id else 0
        forward_params = [project_param] + build_match_params()
        reverse_params = [project_param] + build_match_params()

        # Build language filter conditions.
        #
        # A single column's language clause matches when:
        #   * the term-level language matches (case-insensitive), OR
        #   * the term-level language is a regional variant of it
        #     (search 'en' matches a stored 'en-US'), OR
        #   * the term row is UNTAGGED — NULL / '' / 'unknown' — and the
        #     termbase-level language matches (or is itself untagged).
        # This is strictly ADDITIVE versus the old exact-match filter: it can
        # only add matches, never drop one. It recovers untagged terms (the
        # bulk of the store relies on the termbase-level fallback), rows tagged
        # the literal string 'unknown', and regional variants like en-US.
        def _lang_clause(term_col: str, tb_col: str) -> str:
            return f""" AND (
                LOWER({term_col}) = LOWER(?) OR
                LOWER({term_col}) LIKE LOWER(?) || '-%' OR
                (({term_col} IS NULL OR {term_col} = '' OR LOWER({term_col}) = 'unknown')
                    AND (LOWER({tb_col}) = LOWER(?) OR LOWER({tb_col}) LIKE LOWER(?) || '-%')) OR
                (({term_col} IS NULL OR {term_col} = '' OR LOWER({term_col}) = 'unknown')
                    AND ({tb_col} IS NULL OR {tb_col} = '' OR LOWER({tb_col}) = 'unknown'))
            )"""

        lang_conditions_forward = ""
        lang_conditions_reverse = ""
        lang_params_forward = []
        lang_params_reverse = []

        if source_lang:
            # Forward: term's source side. Reverse: term's target side (swapped).
            lang_conditions_forward += _lang_clause('t.source_lang', 'tb.source_lang')
            lang_params_forward.extend([source_lang] * 4)
            lang_conditions_reverse += _lang_clause('t.target_lang', 'tb.target_lang')
            lang_params_reverse.extend([source_lang] * 4)

        if target_lang:
            # Forward: term's target side. Reverse: term's source side (swapped).
            lang_conditions_forward += _lang_clause('t.target_lang', 'tb.target_lang')
            lang_params_forward.extend([target_lang] * 4)
            lang_conditions_reverse += _lang_clause('t.source_lang', 'tb.source_lang')
            lang_params_reverse.extend([target_lang] * 4)

        # Project filter conditions
        project_conditions = ""
        project_params = []
        if project_id:
            project_conditions = " AND (t.project_id = ? OR t.project_id IS NULL)"
            project_params = [project_id]

        # Min length conditions
        min_len_forward = ""
        min_len_reverse = ""
        if min_length > 0:
            min_len_forward = f" AND LENGTH(t.source_term) >= {min_length}"
            min_len_reverse = f" AND LENGTH(t.target_term) >= {min_length}"

        # Build forward query
        forward_query = base_select_forward + lang_conditions_forward + project_conditions + min_len_forward
        forward_params.extend(lang_params_forward)
        forward_params.extend(project_params)

        if bidirectional:
            # Build reverse query
            reverse_query = base_select_reverse + lang_conditions_reverse + project_conditions + min_len_reverse
            reverse_params.extend(lang_params_reverse)
            reverse_params.extend(project_params)

            # Combine with UNION and sort
            query = f"""
                SELECT * FROM (
                    {forward_query}
                    UNION ALL
                    {reverse_query}
                ) combined
                ORDER BY ranking DESC, source_term ASC
            """
            params = forward_params + reverse_params
        else:
            # Original forward-only behavior
            query = forward_query + " ORDER BY ranking DESC, source_term ASC"
            params = forward_params

        self.cursor.execute(query, params)
        results = []
        seen_combinations = set()  # Track (source_term, target_term, termbase_id) to avoid duplicates

        for row in self.cursor.fetchall():
            result_dict = dict(row)

            # Deduplicate: same term pair from same termbase should only appear once
            # Prefer 'source' match over 'target' match
            combo_key = (
                result_dict.get('source_term', '').lower(),
                result_dict.get('target_term', '').lower(),
                result_dict.get('termbase_id')
            )
            if combo_key in seen_combinations:
                continue
            seen_combinations.add(combo_key)

            # SQLite stores booleans as 0/1, explicitly convert to Python bool
            if 'is_project_termbase' in result_dict:
                result_dict['is_project_termbase'] = bool(result_dict['is_project_termbase'])

            # Fetch target synonyms for this term and include them in the result
            term_id = result_dict.get('id')
            match_direction = result_dict.get('match_direction', 'source')
            if term_id:
                try:
                    # For reverse matches, fetch 'source' synonyms since they become targets
                    synonym_lang = 'source' if match_direction == 'target' else 'target'
                    self.cursor.execute("""
                        SELECT synonym_text, forbidden FROM termbase_synonyms
                        WHERE term_id = ? AND language = ?
                        ORDER BY display_order ASC
                    """, (term_id, synonym_lang))
                    synonyms = []
                    for syn_row in self.cursor.fetchall():
                        syn_text = syn_row[0]
                        syn_forbidden = bool(syn_row[1])
                        if not syn_forbidden:  # Only include non-forbidden synonyms
                            synonyms.append(syn_text)
                    result_dict['target_synonyms'] = synonyms
                except Exception:
                    result_dict['target_synonyms'] = []

            results.append(result_dict)
        return results
    
    # ============================================
    # UTILITY METHODS
    # ============================================
    
    def get_all_tms(self, enabled_only: bool = True) -> List[Dict]:
        """
        Get list of all translation memories
        
        Args:
            enabled_only: If True, only return enabled TMs
            
        Returns:
            List of TM info dictionaries with tm_id, name, entry_count, enabled
        """
        # Get distinct TM IDs from translation_units
        query = "SELECT DISTINCT tm_id FROM translation_units ORDER BY tm_id"
        self.cursor.execute(query)
        tm_ids = [row[0] for row in self.cursor.fetchall()]
        
        tm_list = []
        for tm_id in tm_ids:
            entry_count = self.get_tm_count(tm_id)
            tm_info = {
                'tm_id': tm_id,
                'name': tm_id.replace('_', ' ').title(),
                'entry_count': entry_count,
                'enabled': True,  # For now, all TMs are enabled
                'read_only': False
            }
            tm_list.append(tm_info)
        
        return tm_list
    
    def get_tm_list(self, enabled_only: bool = True) -> List[Dict]:
        """Alias for get_all_tms for backward compatibility"""
        return self.get_all_tms(enabled_only=enabled_only)
    
    def get_entry_count(self, enabled_only: bool = True) -> int:
        """
        Get total number of translation entries
        
        Args:
            enabled_only: Currently ignored (all TMs enabled)
            
        Returns:
            Total number of translation units
        """
        return self.get_tm_count()
    
    def vacuum(self):
        """Optimize database (VACUUM)"""
        self.cursor.execute("VACUUM")
        self.connection.commit()
    
    # ============================================
    # TMX EDITOR METHODS (database-backed TMX files)
    # ============================================
    
    def tmx_store_file(self, file_path: str, file_name: str, original_file_path: str,
                       load_mode: str, file_size: int, header_data: dict,
                       tu_count: int, languages: List[str]) -> int:
        """
        Store TMX file metadata in database
        
        Returns:
            tmx_file_id (int)
        """
        languages_json = json.dumps(languages)
        header_json = json.dumps(header_data)
        
        # Check if file already exists
        self.cursor.execute("SELECT id FROM tmx_files WHERE file_path = ?", (file_path,))
        existing = self.cursor.fetchone()
        
        if existing:
            # Update existing
            self.cursor.execute("""
                UPDATE tmx_files 
                SET file_name = ?, original_file_path = ?, load_mode = ?, file_size = ?,
                    header_data = ?, tu_count = ?, languages = ?, last_accessed = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (file_name, original_file_path, load_mode, file_size, header_json,
                  tu_count, languages_json, existing['id']))
            self.connection.commit()
            return existing['id']
        else:
            # Insert new
            self.cursor.execute("""
                INSERT INTO tmx_files 
                (file_path, file_name, original_file_path, load_mode, file_size,
                 header_data, tu_count, languages)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (file_path, file_name, original_file_path, load_mode, file_size,
                  header_json, tu_count, languages_json))
            self.connection.commit()
            return self.cursor.lastrowid
    
    def tmx_store_translation_unit(self, tmx_file_id: int, tu_id: int,
                                   creation_date: str = None, creation_id: str = None,
                                   change_date: str = None, change_id: str = None,
                                   srclang: str = None, custom_attributes: dict = None,
                                   comments: List[str] = None, commit: bool = True) -> int:
        """
        Store a translation unit in database
        
        Args:
            commit: If False, don't commit (for batch operations)
        
        Returns:
            Internal TU ID (for referencing segments)
        """
        custom_attrs_json = json.dumps(custom_attributes) if custom_attributes else None
        comments_json = json.dumps(comments) if comments else None
        
        self.cursor.execute("""
            INSERT OR REPLACE INTO tmx_translation_units
            (tmx_file_id, tu_id, creation_date, creation_id, change_date, change_id,
             srclang, custom_attributes, comments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (tmx_file_id, tu_id, creation_date, creation_id, change_date, change_id,
              srclang, custom_attrs_json, comments_json))
        if commit:
            self.connection.commit()
        return self.cursor.lastrowid
    
    def tmx_store_segment(self, tu_db_id: int, lang: str, text: str,
                         creation_date: str = None, creation_id: str = None,
                         change_date: str = None, change_id: str = None,
                         commit: bool = True):
        """
        Store a segment (language variant) for a translation unit
        
        Args:
            commit: If False, don't commit (for batch operations)
        """
        self.cursor.execute("""
            INSERT OR REPLACE INTO tmx_segments
            (tu_id, lang, text, creation_date, creation_id, change_date, change_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tu_db_id, lang, text, creation_date, creation_id, change_date, change_id))
        if commit:
            self.connection.commit()
    
    def tmx_get_file_id(self, file_path: str) -> Optional[int]:
        """Get TMX file ID by file path"""
        self.cursor.execute("SELECT id FROM tmx_files WHERE file_path = ?", (file_path,))
        row = self.cursor.fetchone()
        return row['id'] if row else None
    
    def tmx_get_translation_units(self, tmx_file_id: int, offset: int = 0,
                                  limit: int = 50, src_lang: str = None,
                                  tgt_lang: str = None, src_filter: str = None,
                                  tgt_filter: str = None, ignore_case: bool = True) -> List[Dict]:
        """
        Get translation units with pagination and filtering
        
        Returns:
            List of dicts with TU data including segments
        """
        # Build base query
        query = """
            SELECT tu.id as tu_db_id, tu.tu_id, tu.creation_date, tu.creation_id,
                   tu.change_date, tu.change_id, tu.srclang, tu.custom_attributes, tu.comments
            FROM tmx_translation_units tu
            WHERE tu.tmx_file_id = ?
        """
        params = [tmx_file_id]
        
        # Add filters
        if src_filter or tgt_filter:
            query += """
                AND EXISTS (
                    SELECT 1 FROM tmx_segments seg1
                    WHERE seg1.tu_id = tu.id
            """
            if src_lang:
                query += " AND seg1.lang = ?"
                params.append(src_lang)
            if src_filter:
                if ignore_case:
                    query += " AND LOWER(seg1.text) LIKE LOWER(?)"
                    params.append(f"%{src_filter}%")
                else:
                    query += " AND seg1.text LIKE ?"
                    params.append(f"%{src_filter}%")
            
            if tgt_filter:
                query += """
                    AND EXISTS (
                        SELECT 1 FROM tmx_segments seg2
                        WHERE seg2.tu_id = tu.id
                """
                if tgt_lang:
                    query += " AND seg2.lang = ?"
                    params.append(tgt_lang)
                if ignore_case:
                    query += " AND LOWER(seg2.text) LIKE LOWER(?)"
                    params.append(f"%{tgt_filter}%")
                else:
                    query += " AND seg2.text LIKE ?"
                    params.append(f"%{tgt_filter}%")
                query += ")"
            
            query += ")"
        
        query += " ORDER BY tu.tu_id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        self.cursor.execute(query, params)
        rows = self.cursor.fetchall()
        
        # Fetch segments for each TU
        result = []
        for row in rows:
            tu_data = dict(row)
            # Get segments
            self.cursor.execute("""
                SELECT lang, text, creation_date, creation_id, change_date, change_id
                FROM tmx_segments
                WHERE tu_id = ?
            """, (tu_data['tu_db_id'],))
            segments = {}
            for seg_row in self.cursor.fetchall():
                seg_dict = dict(seg_row)
                segments[seg_dict['lang']] = seg_dict
            
            tu_data['segments'] = segments
            if tu_data['custom_attributes']:
                tu_data['custom_attributes'] = json.loads(tu_data['custom_attributes'])
            if tu_data['comments']:
                tu_data['comments'] = json.loads(tu_data['comments'])
            
            result.append(tu_data)
        
        return result
    
    def tmx_count_translation_units(self, tmx_file_id: int, src_lang: str = None,
                                    tgt_lang: str = None, src_filter: str = None,
                                    tgt_filter: str = None, ignore_case: bool = True) -> int:
        """Count translation units matching filters"""
        query = """
            SELECT COUNT(DISTINCT tu.id)
            FROM tmx_translation_units tu
            WHERE tu.tmx_file_id = ?
        """
        params = [tmx_file_id]
        
        # Add same filters as tmx_get_translation_units
        if src_filter or tgt_filter:
            query += """
                AND EXISTS (
                    SELECT 1 FROM tmx_segments seg1
                    WHERE seg1.tu_id = tu.id
            """
            if src_lang:
                query += " AND seg1.lang = ?"
                params.append(src_lang)
            if src_filter:
                if ignore_case:
                    query += " AND LOWER(seg1.text) LIKE LOWER(?)"
                    params.append(f"%{src_filter}%")
                else:
                    query += " AND seg1.text LIKE ?"
                    params.append(f"%{src_filter}%")
            
            if tgt_filter:
                query += """
                    AND EXISTS (
                        SELECT 1 FROM tmx_segments seg2
                        WHERE seg2.tu_id = tu.id
                """
                if tgt_lang:
                    query += " AND seg2.lang = ?"
                    params.append(tgt_lang)
                if ignore_case:
                    query += " AND LOWER(seg2.text) LIKE LOWER(?)"
                    params.append(f"%{tgt_filter}%")
                else:
                    query += " AND seg2.text LIKE ?"
                    params.append(f"%{tgt_filter}%")
                query += ")"
            
            query += ")"
        
        self.cursor.execute(query, params)
        return self.cursor.fetchone()[0]
    
    def tmx_update_segment(self, tmx_file_id: int, tu_id: int, lang: str, text: str):
        """Update a segment text"""
        # Get internal TU ID
        self.cursor.execute("""
            SELECT tu.id FROM tmx_translation_units tu
            WHERE tu.tmx_file_id = ? AND tu.tu_id = ?
        """, (tmx_file_id, tu_id))
        tu_row = self.cursor.fetchone()
        if not tu_row:
            return False
        
        tu_db_id = tu_row['id']
        change_date = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        
        # Update segment
        self.cursor.execute("""
            UPDATE tmx_segments
            SET text = ?, change_date = ?
            WHERE tu_id = ? AND lang = ?
        """, (text, change_date, tu_db_id, lang))
        
        # Update TU change date
        self.cursor.execute("""
            UPDATE tmx_translation_units
            SET change_date = ?
            WHERE id = ?
        """, (change_date, tu_db_id))
        
        # Update file last_modified
        self.cursor.execute("""
            UPDATE tmx_files
            SET last_modified = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (tmx_file_id,))
        
        self.connection.commit()
        return True
    
    def tmx_delete_file(self, tmx_file_id: int):
        """Delete TMX file and all its data (CASCADE will handle TUs and segments)"""
        self.cursor.execute("DELETE FROM tmx_files WHERE id = ?", (tmx_file_id,))
        self.connection.commit()
    
    def tmx_get_file_info(self, tmx_file_id: int) -> Optional[Dict]:
        """Get TMX file metadata"""
        self.cursor.execute("""
            SELECT id, file_path, file_name, original_file_path, load_mode,
                   file_size, header_data, tu_count, languages,
                   created_date, last_accessed, last_modified
            FROM tmx_files
            WHERE id = ?
        """, (tmx_file_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        
        info = dict(row)
        info['header_data'] = json.loads(info['header_data'])
        info['languages'] = json.loads(info['languages'])
        return info
    
    def get_database_info(self) -> Dict:
        """Get database statistics"""
        info = {
            'path': self.db_path,
            'size_bytes': os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            'tm_entries': self.get_tm_count(),
        }
        
        # Get size in MB
        info['size_mb'] = round(info['size_bytes'] / (1024 * 1024), 2)

        return info

    # ============================================================
    # CLIPBOARD HISTORY
    # ============================================================

    def add_clipboard_item(self, text: str, max_text_items: int = 200,
                           max_image_items: int = 50) -> Optional[int]:
        """Insert a new TEXT clipboard item and trim text rows to max_text_items.

        Returns the new row id, or None on failure.
        """
        try:
            self.cursor.execute(
                "INSERT INTO clipboard_history (text, kind) VALUES (?, 'text')",
                (text,)
            )
            new_id = self.cursor.lastrowid
            # Trim only TEXT rows beyond the cap (image rows have a separate budget)
            self.cursor.execute("""
                DELETE FROM clipboard_history
                WHERE kind = 'text'
                  AND id NOT IN (
                      SELECT id FROM clipboard_history
                      WHERE kind = 'text'
                      ORDER BY id DESC
                      LIMIT ?
                  )
            """, (max_text_items,))
            self.connection.commit()
            return new_id
        except Exception as e:
            self.log(f"[Clipboard] Failed to add text item: {e}")
            return None

    def add_clipboard_image(self, label: str, image_bytes: bytes,
                            max_image_items: int = 50) -> Optional[int]:
        """Insert a new IMAGE clipboard item (PNG bytes) and trim image rows.

        ``label`` is a human-readable string like "Image 1920×1080" stored in
        the ``text`` column for display purposes.
        """
        try:
            self.cursor.execute(
                "INSERT INTO clipboard_history (text, kind, image_data) "
                "VALUES (?, 'image', ?)",
                (label, image_bytes)
            )
            new_id = self.cursor.lastrowid
            # Trim only IMAGE rows beyond the cap
            self.cursor.execute("""
                DELETE FROM clipboard_history
                WHERE kind = 'image'
                  AND id NOT IN (
                      SELECT id FROM clipboard_history
                      WHERE kind = 'image'
                      ORDER BY id DESC
                      LIMIT ?
                  )
            """, (max_image_items,))
            self.connection.commit()
            return new_id
        except Exception as e:
            self.log(f"[Clipboard] Failed to add image item: {e}")
            return None

    def get_clipboard_items(self, limit: int = 250) -> List[Dict]:
        """Return clipboard items newest-first.

        Image rows include the PNG bytes in 'image_data'.  For lazy loading
        of large image payloads, prefer ``get_clipboard_image_data(id)``.
        """
        try:
            self.cursor.execute("""
                SELECT id, text, copied_at, pasted, kind, image_data
                FROM clipboard_history
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            self.log(f"[Clipboard] Failed to get items: {e}")
            return []

    def get_clipboard_image_data(self, item_id: int) -> Optional[bytes]:
        """Return the PNG bytes for a single image clip (for lazy paste)."""
        try:
            self.cursor.execute(
                "SELECT image_data FROM clipboard_history WHERE id = ?",
                (item_id,)
            )
            row = self.cursor.fetchone()
            return row['image_data'] if row else None
        except Exception as e:
            self.log(f"[Clipboard] Failed to fetch image data: {e}")
            return None

    def mark_clipboard_item_pasted(self, item_id: int) -> bool:
        """Set pasted=1 for the given row."""
        try:
            self.cursor.execute(
                "UPDATE clipboard_history SET pasted = 1 WHERE id = ?",
                (item_id,)
            )
            self.connection.commit()
            return True
        except Exception as e:
            self.log(f"[Clipboard] Failed to mark item pasted: {e}")
            return False

    def delete_clipboard_item(self, item_id: int) -> bool:
        """Delete a single clipboard history row."""
        try:
            self.cursor.execute(
                "DELETE FROM clipboard_history WHERE id = ?",
                (item_id,)
            )
            self.connection.commit()
            return True
        except Exception as e:
            self.log(f"[Clipboard] Failed to delete item: {e}")
            return False

    def clear_clipboard_history(self) -> bool:
        """Delete all rows from clipboard_history."""
        try:
            self.cursor.execute("DELETE FROM clipboard_history")
            self.connection.commit()
            return True
        except Exception as e:
            self.log(f"[Clipboard] Failed to clear history: {e}")
            return False

    def purge_clipboard_items_older_than(self, minutes: int) -> List[int]:
        """Delete clipboard rows older than `minutes` minutes (issue #246
        auto-delete). Returns the ids of the deleted rows so the UI can drop
        exactly those items from its lists without a full reload.

        copied_at is stored in UTC (the column default is datetime('now')),
        so the cutoff also uses SQLite's own clock - comparing it against a
        Python-side local timestamp would silently shift the window by the
        UTC offset.
        """
        try:
            self.cursor.execute(
                "SELECT id FROM clipboard_history WHERE copied_at < datetime('now', ?)",
                (f"-{int(minutes)} minutes",)
            )
            ids = [row[0] for row in self.cursor.fetchall()]
            if ids:
                self.cursor.executemany(
                    "DELETE FROM clipboard_history WHERE id = ?",
                    [(i,) for i in ids]
                )
                self.connection.commit()
            return ids
        except Exception as e:
            self.log(f"[Clipboard] Failed to purge old items: {e}")
            return []
