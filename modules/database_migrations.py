"""
Database Migration Functions

Handles schema updates and data migrations for the Supervertaler database.
"""

import sqlite3
from typing import Optional


def migrate_termbase_fields(db_manager) -> bool:
    """
    Migrate termbase_terms table to add new fields:
    - project (TEXT)
    - client (TEXT)
    - term_uuid (TEXT UNIQUE) - for tracking terms across import/export
    
    Note: 'notes' field already exists in schema, 'definition' is legacy (no longer used)
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        True if migration successful
    """
    try:
        cursor = db_manager.cursor
        
        # Check which columns exist
        cursor.execute("PRAGMA table_info(termbase_terms)")
        columns = {row[1] for row in cursor.fetchall()}
        
        migrations_needed = []
        
        # Add 'project' column if it doesn't exist
        if 'project' not in columns:
            migrations_needed.append(("project", "ALTER TABLE termbase_terms ADD COLUMN project TEXT"))
        
        # Add 'client' column if it doesn't exist
        if 'client' not in columns:
            migrations_needed.append(("client", "ALTER TABLE termbase_terms ADD COLUMN client TEXT"))
        
        # Add 'term_uuid' column if it doesn't exist
        # Note: SQLite doesn't allow adding UNIQUE constraint via ALTER TABLE,
        # so we add column first, then create unique index separately
        if 'term_uuid' not in columns:
            migrations_needed.append(("term_uuid", "ALTER TABLE termbase_terms ADD COLUMN term_uuid TEXT"))
        
        # Add 'note' column if it doesn't exist (legacy, kept for compatibility)
        if 'note' not in columns:
            migrations_needed.append(("note", "ALTER TABLE termbase_terms ADD COLUMN note TEXT"))
        
        # Add 'notes' column if it doesn't exist (used by termbase entry editor)
        if 'notes' not in columns:
            migrations_needed.append(("notes", "ALTER TABLE termbase_terms ADD COLUMN notes TEXT"))

        # Add 'is_nontranslatable' column if it doesn't exist (v1.9.393).
        # Mirrors the schema the Trados plugin already maintains, so flags
        # set in either product are visible to the other against a shared DB.
        if 'is_nontranslatable' not in columns:
            migrations_needed.append((
                "is_nontranslatable",
                "ALTER TABLE termbase_terms ADD COLUMN is_nontranslatable BOOLEAN DEFAULT 0",
            ))
        
        # Execute migrations
        for column_name, sql in migrations_needed:
            print(f"📊 Adding column '{column_name}' to termbase_terms...")
            cursor.execute(sql)
            print(f"  ✓ Column '{column_name}' added successfully")
        
        # Create UNIQUE index for term_uuid if column was added
        if 'term_uuid' in [name for name, _ in migrations_needed]:
            print("📊 Creating UNIQUE index for term_uuid...")
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_termbase_term_uuid 
                ON termbase_terms(term_uuid)
            """)
            print("  ✓ UNIQUE index created successfully")
        
        db_manager.connection.commit()
        
        if migrations_needed:
            print(f"✅ Database migration completed: {len(migrations_needed)} column(s) added")
        else:
            print("✅ Database schema is up to date")
        
        return True
        
    except Exception as e:
        print(f"❌ Database migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_synonyms_table(db_manager) -> bool:
    """
    Create termbase_synonyms table for storing term synonyms.
    
    Schema:
    - id: Primary key
    - term_id: Foreign key to termbase_terms
    - synonym_text: The synonym text
    - language: 'source' or 'target'
    - created_date: Timestamp
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        True if successful
    """
    try:
        cursor = db_manager.cursor
        
        # Check if table already exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='termbase_synonyms'
        """)
        
        if cursor.fetchone():
            print("✅ termbase_synonyms table already exists")
            return True
        
        print("📊 Creating termbase_synonyms table...")
        
        cursor.execute("""
            CREATE TABLE termbase_synonyms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term_id INTEGER NOT NULL,
                synonym_text TEXT NOT NULL,
                language TEXT NOT NULL CHECK(language IN ('source', 'target')),
                display_order INTEGER DEFAULT 0,
                forbidden INTEGER DEFAULT 0,
                created_date TEXT DEFAULT (datetime('now')),
                modified_date TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (term_id) REFERENCES termbase_terms(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes for fast lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_synonyms_term_id 
            ON termbase_synonyms(term_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_synonyms_text 
            ON termbase_synonyms(synonym_text)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_synonyms_language 
            ON termbase_synonyms(language)
        """)
        
        db_manager.connection.commit()
        print("✅ termbase_synonyms table created successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to create termbase_synonyms table: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_tm_activation_column_name(db_manager) -> bool:
    """Rename tm_activation.tm_id -> tm_db_id (idempotent).

    The column holds the NUMERIC translation_memories.id, but its old name
    collided with translation_units.tm_id (the string slug) — the confusion
    behind the v1.10.242 "0 TM matches" bug. Renaming makes the schema
    self-documenting. ALTER ... RENAME COLUMN rewrites the PK + FK definitions
    automatically.
    """
    try:
        cursor = db_manager.cursor
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(tm_activation)")]
        if 'tm_db_id' in cols or 'tm_id' not in cols:
            return True  # already migrated (or table absent)
        cursor.execute("ALTER TABLE tm_activation RENAME COLUMN tm_id TO tm_db_id")
        db_manager.connection.commit()
        print("✓ Renamed tm_activation.tm_id -> tm_db_id")
        return True
    except Exception as e:
        print(f"✗ tm_activation column rename failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_termbase_terms_id_to_integer(db_manager) -> bool:
    """Rebuild termbase_terms so termbase_id has INTEGER affinity (was TEXT),
    removing the TEXT-vs-INTEGER mismatch that forced CASTs in every join.
    Idempotent. Preserves all rows + the `id` PK (FTS content_rowid and the
    termbase_synonyms FK depend on it), recreates indexes, rebuilds FTS.
    Validated on a real 93k-row copy before shipping.
    """
    try:
        cursor = db_manager.cursor
        info = {r[1]: (r[2] or '') for r in cursor.execute("PRAGMA table_info(termbase_terms)")}
        if not info or info.get('termbase_id', '').upper() == 'INTEGER':
            return True  # already migrated (or table absent)

        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='termbase_terms'").fetchone()
        if not row:
            return True
        new_sql = row[0].replace("CREATE TABLE termbase_terms (",
                                 "CREATE TABLE termbase_terms_new (", 1)
        new_sql = new_sql.replace("termbase_id TEXT NOT NULL",
                                  "termbase_id INTEGER NOT NULL", 1)
        if "termbase_terms_new" not in new_sql or "termbase_id INTEGER NOT NULL" not in new_sql:
            print("✗ termbase_terms migration: unexpected DDL, skipping (no change made)")
            return False
        idx_sqls = [r[0] for r in cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='termbase_terms' "
            "AND sql IS NOT NULL")]

        conn = db_manager.connection
        old_iso = conn.isolation_level
        conn.isolation_level = None  # manage the transaction explicitly
        try:
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("BEGIN")
            cursor.execute(new_sql)
            cursor.execute("INSERT INTO termbase_terms_new SELECT * FROM termbase_terms")
            cursor.execute("DROP TABLE termbase_terms")
            cursor.execute("ALTER TABLE termbase_terms_new RENAME TO termbase_terms")
            for s in idx_sqls:
                cursor.execute(s)
            cursor.execute("INSERT OR REPLACE INTO sqlite_sequence(name, seq) "
                           "SELECT 'termbase_terms', COALESCE(MAX(id), 0) FROM termbase_terms")
            cursor.execute("INSERT INTO termbase_terms_fts(termbase_terms_fts) VALUES('rebuild')")
            cursor.execute("COMMIT")
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
            conn.isolation_level = old_iso

        viol = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if viol:
            print(f"⚠️ termbase_terms migration: {len(viol)} FK violation(s) after rebuild")
        print("✓ Rebuilt termbase_terms with INTEGER termbase_id")
        return True
    except Exception as e:
        print(f"✗ termbase_terms INTEGER migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def _backup_database_before_structural_migration(db_manager) -> bool:
    """Take a consistent, timestamped snapshot of the DB right before a
    structural migration (table rebuild / column rename) runs.

    These are the first non-additive migrations the app ships, so every user
    gets a safety net even though they didn't make a manual backup. Uses
    SQLite's online backup API, which is consistent even with the DB open in
    WAL mode. Best-effort: a backup failure is logged loudly but does NOT abort
    the migration (the individual migrations are transactional/atomic, and a
    disk-full backup failure would also roll back the rebuild safely).
    """
    import os
    import sqlite3
    from datetime import datetime
    try:
        db_path = getattr(db_manager, 'db_path', None)
        if not db_path or not os.path.exists(db_path):
            print("⚠️ Pre-migration backup skipped: database path unknown")
            return False
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{db_path}.pre-migration-{ts}.bak"
        dest = sqlite3.connect(backup_path)
        try:
            db_manager.connection.backup(dest)
        finally:
            dest.close()
        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        print(f"💾 Pre-migration backup written: {backup_path} ({size_mb:.0f} MB) — "
              f"delete it once the upgrade is confirmed working.")
        return True
    except Exception as e:
        print(f"⚠️ Pre-migration backup FAILED ({e}). Proceeding anyway — the "
              f"migrations are transactional, but no restore point was created.")
        return False


def _verify_structural_migrations(db_manager) -> bool:
    """After running migrations, confirm the structural changes actually took.
    A mismatch (e.g. an old bundled SQLite that couldn't RENAME COLUMN) would
    otherwise leave the app's code expecting a schema the DB doesn't have, so
    surface it LOUDLY rather than silently breaking search."""
    ok = True
    try:
        cur = db_manager.cursor
        tma = {r[1] for r in cur.execute("PRAGMA table_info(tm_activation)")}
        if tma and 'tm_db_id' not in tma:
            ok = False
            print("❌ POST-MIGRATION CHECK FAILED: tm_activation.tm_db_id is missing. "
                  "TM activation/search may not work. Restore the pre-migration "
                  "backup (*.pre-migration-*.bak) and report this.")
        tt = {r[1]: (r[2] or '') for r in cur.execute("PRAGMA table_info(termbase_terms)")}
        if tt and tt.get('termbase_id', '').upper() != 'INTEGER':
            # Not fatal — the column still works via affinity/CAST — but note it.
            print("⚠️ POST-MIGRATION CHECK: termbase_terms.termbase_id is still not "
                  "INTEGER (functional via affinity, but the rebuild did not complete).")
        if ok:
            print("✅ Post-migration check passed (tm_db_id present, termbase_id INTEGER)")
    except Exception as e:
        print(f"⚠️ Post-migration verification error: {e}")
    return ok


def run_all_migrations(db_manager) -> bool:
    """
    Run all pending database migrations.

    Args:
        db_manager: DatabaseManager instance

    Returns:
        True if all migrations successful
    """
    print("\n" + "="*60)
    print("DATABASE MIGRATIONS")
    print("="*60)

    success = True

    # Migration 1: Add new termbase fields
    if not migrate_termbase_fields(db_manager):
        success = False

    # Migration 2: Create synonyms table
    if not create_synonyms_table(db_manager):
        success = False

    # Migration 3: Add display_order and forbidden fields to synonyms
    if not migrate_synonym_fields(db_manager):
        success = False

    # Migration 4: Add ai_inject field to termbases
    if not migrate_termbase_ai_inject(db_manager):
        success = False

    # Migration 5: Remove the clipboard_history table (the Clipboard
    # Manager was retired in v1.10.374; it used to create it here).
    if not drop_clipboard_history_table(db_manager):
        success = False

    # Migration 5b: translation_units.target_hash (+ index) for fast reverse
    # exact matching. Additive and independent of the others.
    if not migrate_translation_units_target_hash(db_manager):
        success = False

    # Migration 5c: fix the external-content FTS5 sync triggers so TM-row
    # updates/deletes stop raising "database disk image is malformed".
    if not migrate_fts_external_content_delete(db_manager):
        success = False

    # Migration 6: Disambiguate tm_activation.tm_id (numeric id) -> tm_db_id
    if not migrate_tm_activation_column_name(db_manager):
        success = False

    # Migration 7: termbase_terms.termbase_id TEXT -> INTEGER. MUST run last —
    # it rebuilds the table, so it needs every column added by earlier
    # migrations to already be present.
    if not migrate_termbase_terms_id_to_integer(db_manager):
        success = False

    print("="*60)

    return success


def check_and_migrate(db_manager) -> bool:
    """
    Check if migrations are needed and run them if so.
    This is safe to call on every app startup.
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        True if migrations successful or not needed
    """
    try:
        print("🔍 Checking database schema for migrations...")
        cursor = db_manager.cursor
        
        # Quick check: do we need migrations?
        cursor.execute("PRAGMA table_info(termbase_terms)")
        columns = {row[1] for row in cursor.fetchall()}
        print(f"🔍 Found termbase_terms columns: {sorted(columns)}")
        
        needs_migration = (
            'project' not in columns or 
            'client' not in columns or
            'term_uuid' not in columns or
            'note' not in columns
        )
        
        # Check if synonyms table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='termbase_synonyms'
        """)
        needs_synonyms_table = cursor.fetchone() is None

        # Check if termbases table has ai_inject column
        cursor.execute("PRAGMA table_info(termbases)")
        termbase_columns = {row[1] for row in cursor.fetchall()}
        needs_ai_inject = 'ai_inject' not in termbase_columns

        # The retired Clipboard Manager's history table is still there
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='clipboard_history'
        """)
        needs_clipboard_drop = cursor.fetchone() is not None
        if needs_clipboard_drop:
            print("⚠️ Migration needed - remove the old clipboard_history table")

        if needs_migration:
            print(f"⚠️ Migration needed - missing columns: {', '.join([c for c in ['project', 'client', 'term_uuid', 'note'] if c not in columns])}")

        if needs_synonyms_table:
            print("⚠️ Migration needed - termbase_synonyms table missing")

        if needs_ai_inject:
            print("⚠️ Migration needed - termbases.ai_inject column missing")

        # target_hash for reverse exact matching. Gate on the index existing
        # (instant sqlite_master lookup, and the migration's completion marker)
        # so we never run a per-startup scan to detect it.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='translation_units'")
        tu_exists = cursor.fetchone() is not None
        needs_target_hash = False
        if tu_exists:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tu_target_hash'")
            needs_target_hash = cursor.fetchone() is None
        if needs_target_hash:
            print("⚠️ Migration needed - translation_units.target_hash index missing")

        # FTS external-content delete fix: gated on the fts delete trigger using
        # the 'delete' command (instant sqlite_master check).
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='tu_fts_delete'")
        _ftsrow = cursor.fetchone()
        needs_fts_fix = bool(_ftsrow and _ftsrow[0]) and "'delete'" not in _ftsrow[0]
        if needs_fts_fix:
            print("⚠️ Migration needed - FTS sync triggers use invalid external-content deletes")

        # Identifier-disambiguation migrations (2026-06-01):
        # tm_activation.tm_id -> tm_db_id, and termbase_terms.termbase_id TEXT -> INTEGER.
        cursor.execute("PRAGMA table_info(tm_activation)")
        tma_cols = {row[1] for row in cursor.fetchall()}
        needs_tm_activation_rename = ('tm_id' in tma_cols and 'tm_db_id' not in tma_cols)
        if needs_tm_activation_rename:
            print("⚠️ Migration needed - tm_activation.tm_id -> tm_db_id")

        cursor.execute("PRAGMA table_info(termbase_terms)")
        tt_info = {row[1]: (row[2] or '') for row in cursor.fetchall()}
        needs_termbase_id_int = bool(tt_info) and tt_info.get('termbase_id', '').upper() != 'INTEGER'
        if needs_termbase_id_int:
            print("⚠️ Migration needed - termbase_terms.termbase_id TEXT -> INTEGER")

        structural = needs_tm_activation_rename or needs_termbase_id_int

        if (needs_migration or needs_synonyms_table or needs_ai_inject or needs_clipboard_drop
                or needs_target_hash or needs_fts_fix or structural):
            # Structural migrations (table rebuild / column rename) get a
            # consistent timestamped backup first — the safety net every user
            # gets, since these are the first non-additive migrations we ship.
            if structural:
                _backup_database_before_structural_migration(db_manager)
            success = run_all_migrations(db_manager)
            if success:
                # Generate UUIDs for terms that don't have them
                generate_missing_uuids(db_manager)
            if structural:
                _verify_structural_migrations(db_manager)
            return success
        
        # Even if no schema migration needed, check for missing UUIDs
        print("✅ Database schema is current - checking UUIDs...")
        generate_missing_uuids(db_manager)

        # v1.10.360: the fix_project_termbase_flags() call that used to run
        # here is gone — it flagged every project-scoped termbase as "the
        # project termbase", desynchronising the legacy is_project_termbase
        # flag from the authoritative termbase_activation.priority = 1
        # representation. Stale flags are now repaired (cleared) at startup
        # by DatabaseManager's schema-init data repair instead.

        return True
        
    except Exception as e:
        print(f"❌ Migration check failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_synonym_fields(db_manager) -> bool:
    """
    Migrate termbase_synonyms table to add new fields:
    - display_order (INTEGER) - position in synonym list (0 = main term)
    - forbidden (INTEGER) - whether this synonym is forbidden (0/1)
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        True if migration successful
    """
    try:
        cursor = db_manager.cursor
        
        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='termbase_synonyms'
        """)
        
        if not cursor.fetchone():
            print("ℹ️ termbase_synonyms table doesn't exist yet - will be created with new schema")
            return True
        
        # Check which columns exist
        cursor.execute("PRAGMA table_info(termbase_synonyms)")
        columns = {row[1] for row in cursor.fetchall()}
        
        migrations_needed = []
        
        # Add 'display_order' column if it doesn't exist
        if 'display_order' not in columns:
            migrations_needed.append(("display_order", "ALTER TABLE termbase_synonyms ADD COLUMN display_order INTEGER DEFAULT 0"))
        
        # Add 'forbidden' column if it doesn't exist
        if 'forbidden' not in columns:
            migrations_needed.append(("forbidden", "ALTER TABLE termbase_synonyms ADD COLUMN forbidden INTEGER DEFAULT 0"))
        
        # Execute migrations
        for column_name, sql in migrations_needed:
            print(f"📊 Adding column '{column_name}' to termbase_synonyms...")
            cursor.execute(sql)
            print(f"  ✓ Column '{column_name}' added successfully")
        
        db_manager.connection.commit()
        
        if migrations_needed:
            print(f"✅ Synonym table migration completed: {len(migrations_needed)} column(s) added")
        else:
            print("✅ Synonym table schema is up to date")
        
        return True
        
    except Exception as e:
        print(f"❌ Synonym table migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_termbase_ai_inject(db_manager) -> bool:
    """
    Add ai_inject column to termbases table.
    When enabled, the termbase's terms will be injected into LLM translation prompts.

    Args:
        db_manager: DatabaseManager instance

    Returns:
        True if migration successful
    """
    try:
        cursor = db_manager.cursor

        # Check which columns exist
        cursor.execute("PRAGMA table_info(termbases)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'ai_inject' not in columns:
            print("📊 Adding 'ai_inject' column to termbases...")
            cursor.execute("ALTER TABLE termbases ADD COLUMN ai_inject BOOLEAN DEFAULT 0")
            db_manager.connection.commit()
            print("  ✓ Column 'ai_inject' added successfully")
        else:
            print("✅ termbases.ai_inject column already exists")

        return True

    except Exception as e:
        print(f"❌ ai_inject migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_translation_units_target_hash(db_manager) -> bool:
    """Add translation_units.target_hash (+ idx_tu_target_hash) and backfill it.

    target_hash is the md5 of the *normalised* target text, mirroring source_hash.
    It turns a reverse (opposite-direction) exact match — our segment's source
    equals a TM entry's target — into an indexed lookup instead of a target_text
    full-table scan, which on a large TM froze the grid for seconds on every click.

    translation_units carries AFTER UPDATE triggers that keep the FTS5 / trigram
    full-text indexes in sync (tu_fts_update, tu_trig_update). They fire on *any*
    update, so a naive backfill would rebuild an FTS row for every one of
    (potentially millions of) rows — agonisingly slow, and it fails outright if
    an FTS shadow table is in an inconsistent state ("database disk image is
    malformed"). target_hash is unrelated to the full-text content, so we drop
    those AFTER UPDATE triggers for the backfill and restore them immediately
    after. The whole thing runs in one transaction: if anything fails or is
    interrupted, it rolls back to the original state (triggers intact, no index)
    and re-runs cleanly on the next launch.

    The index's presence is the "done" marker, so checking whether the migration
    is needed is an instant sqlite_master lookup — no per-startup scan.
    """
    try:
        cursor = db_manager.cursor
        conn = db_manager.connection

        # Guard: table may be absent on a partially-built DB.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='translation_units'")
        if not cursor.fetchone():
            return True

        # Index present → already migrated.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tu_target_hash'")
        if cursor.fetchone():
            return True

        import hashlib
        from modules.database_manager import _normalize_for_matching

        # The AFTER UPDATE triggers we must suspend during the backfill (saved so
        # we can recreate them verbatim). INSERT/DELETE triggers don't fire on an
        # UPDATE, so we leave them alone.
        update_triggers = cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='translation_units' "
            "AND instr(lower(sql), 'after update') > 0"
        ).fetchall()

        old_isolation = conn.isolation_level
        conn.isolation_level = None  # manage the transaction explicitly
        total = 0
        try:
            cursor.execute("BEGIN IMMEDIATE")

            if 'target_hash' not in {r[1] for r in cursor.execute("PRAGMA table_info(translation_units)")}:
                print("📊 Adding 'target_hash' column to translation_units...")
                cursor.execute("ALTER TABLE translation_units ADD COLUMN target_hash TEXT")

            for name, _sql in update_triggers:
                cursor.execute(f"DROP TRIGGER IF EXISTS {name}")

            # Backfill, walking the primary key in batches so a huge TM neither
            # loads into memory at once nor degrades to repeated scans.
            last_id = 0
            while True:
                rows = cursor.execute(
                    "SELECT id, target_text FROM translation_units "
                    "WHERE id > ? ORDER BY id LIMIT 10000",
                    (last_id,),
                ).fetchall()
                if not rows:
                    break
                cursor.executemany(
                    "UPDATE translation_units SET target_hash = ? WHERE id = ?",
                    [(hashlib.md5(_normalize_for_matching(t or '').encode('utf-8')).hexdigest(), rid)
                     for (rid, t) in rows],
                )
                last_id = rows[-1][0]
                total += len(rows)

            # Restore the FTS-sync triggers exactly as they were.
            for name, sql in update_triggers:
                if sql:
                    cursor.execute(sql)

            # Create the index LAST — its existence marks the migration complete.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tu_target_hash ON translation_units(target_hash)")

            cursor.execute("COMMIT")
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.isolation_level = old_isolation

        if total:
            print(f"📊 Backfilled target_hash for {total} translation unit(s)")
        print("✅ translation_units.target_hash ready — reverse exact matches are now indexed")
        return True

    except Exception as e:
        print(f"❌ target_hash migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def migrate_fts_external_content_delete(db_manager) -> bool:
    """Fix the FTS5 sync triggers on translation_units.

    Both full-text indexes were created as EXTERNAL-CONTENT FTS5 (content=…), but
    their delete/update triggers used `DELETE … WHERE rowid`, which is invalid for
    external content: FTS5 then reads the already-changed/removed content row and
    raises "database disk image is malformed" on ANY update or delete of a
    translation_units row. That silently broke TM-entry edits and the usage
    'touch'. Fixes:
      • translation_units_fts (indexes every row) keeps external content, but now
        removes rows with the special 'delete' command carrying the OLD values,
        then rebuilds once to clear tokens orphaned by past bad deletes.
      • translation_units_trigram (indexes only the CJK subset) is recreated as a
        regular self-contained FTS5 table — external content can't represent a
        subset — so its plain rowid-delete triggers become correct.

    Idempotent and gated on the fts delete trigger already using the 'delete'
    command (an instant sqlite_master check, so no per-startup work once done).
    """
    try:
        cursor = db_manager.cursor
        conn = db_manager.connection

        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='tu_fts_delete'"
        ).fetchone()
        if not row or not row[0]:
            return True  # no FTS triggers on this DB — nothing to fix
        if "'delete'" in row[0]:
            return True  # already migrated

        from modules.database_manager import _CJK_GLOB

        # Preserve the existing trigram triggers so we can recreate them verbatim
        # against the rebuilt (regular) trigram table — they are already correct
        # for a self-contained FTS5 (plain rowid INSERT/DELETE).
        trig_triggers = cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('tu_trig_insert','tu_trig_delete','tu_trig_update')"
        ).fetchall()
        trigram_row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='translation_units_trigram'"
        ).fetchone()
        trigram_is_external = bool(
            trigram_row and trigram_row[0] and 'content=' in trigram_row[0].replace(' ', ''))

        old_isolation = conn.isolation_level
        conn.isolation_level = None
        try:
            cursor.execute("BEGIN IMMEDIATE")

            # 1) Correct the external-content fts delete/update triggers.
            cursor.execute("DROP TRIGGER IF EXISTS tu_fts_delete")
            cursor.execute("DROP TRIGGER IF EXISTS tu_fts_update")
            cursor.execute("""
                CREATE TRIGGER tu_fts_delete AFTER DELETE ON translation_units BEGIN
                    INSERT INTO translation_units_fts(translation_units_fts, rowid, source_text, target_text)
                    VALUES ('delete', old.id, old.source_text, old.target_text);
                END""")
            cursor.execute("""
                CREATE TRIGGER tu_fts_update AFTER UPDATE ON translation_units BEGIN
                    INSERT INTO translation_units_fts(translation_units_fts, rowid, source_text, target_text)
                    VALUES ('delete', old.id, old.source_text, old.target_text);
                    INSERT INTO translation_units_fts(rowid, source_text, target_text)
                    VALUES (new.id, new.source_text, new.target_text);
                END""")

            # 2) Rebuild fts to clear any tokens orphaned by past bad deletes.
            cursor.execute("INSERT INTO translation_units_fts(translation_units_fts) VALUES('rebuild')")

            # 3) Recreate the trigram index as a regular FTS5 table (only if it
            #    exists and is still external-content).
            if trigram_is_external:
                for name, _sql in trig_triggers:
                    cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
                cursor.execute("DROP TABLE IF EXISTS translation_units_trigram")
                cursor.execute(
                    "CREATE VIRTUAL TABLE translation_units_trigram "
                    "USING fts5(source_text, target_text, tokenize='trigram')")
                for name, sql in trig_triggers:
                    if sql:
                        cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO translation_units_trigram(rowid, source_text, target_text) "
                    "SELECT id, source_text, target_text FROM translation_units "
                    f"WHERE source_text GLOB '{_CJK_GLOB}' OR target_text GLOB '{_CJK_GLOB}'")

            cursor.execute("COMMIT")
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.isolation_level = old_isolation

        print("✅ FTS sync triggers fixed — TM edits/deletes no longer raise 'malformed'")
        return True

    except Exception as e:
        print(f"❌ FTS trigger migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def drop_clipboard_history_table(db_manager) -> bool:
    """
    Remove the clipboard_history table (v1.10.374).

    The Clipboard Manager was removed from Workbench, so nothing can show or
    clear these rows any more, and they can hold anything the user ever
    copied - passwords and licence keys among them. secure_delete makes
    SQLite overwrite the freed pages with zeros instead of leaving the old
    text in the database file.

    Args:
        db_manager: DatabaseManager instance

    Returns:
        True if successful (or nothing to remove)
    """
    try:
        cursor = db_manager.cursor
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='clipboard_history'
        """)
        if cursor.fetchone() is None:
            return True

        previous = cursor.execute("PRAGMA secure_delete").fetchone()[0]
        cursor.execute("PRAGMA secure_delete = ON")
        try:
            cursor.execute("DROP TABLE clipboard_history")
            db_manager.connection.commit()
        finally:
            cursor.execute(f"PRAGMA secure_delete = {int(previous)}")
        print("✅ clipboard_history table removed (the Clipboard Manager was retired)")
        return True

    except Exception as e:
        print(f"❌ Failed to remove clipboard_history table: {e}")
        import traceback
        traceback.print_exc()
        return False


def generate_missing_uuids(db_manager) -> bool:
    """
    Generate UUIDs for any termbase terms that don't have them.
    This ensures all existing terms get UUIDs after the term_uuid column is added.

    Args:
        db_manager: DatabaseManager instance

    Returns:
        True if successful
    """
    try:
        import uuid
        cursor = db_manager.cursor

        # Find terms without UUIDs
        cursor.execute("""
            SELECT id FROM termbase_terms
            WHERE term_uuid IS NULL OR term_uuid = ''
        """)
        terms_without_uuid = cursor.fetchall()

        if not terms_without_uuid:
            return True  # Nothing to do

        print(f"📊 Generating UUIDs for {len(terms_without_uuid)} existing terms...")

        # Generate and assign UUIDs
        for (term_id,) in terms_without_uuid:
            term_uuid = str(uuid.uuid4())
            cursor.execute("""
                UPDATE termbase_terms
                SET term_uuid = ?
                WHERE id = ?
            """, (term_uuid, term_id))

        db_manager.connection.commit()
        print(f"  ✓ Generated {len(terms_without_uuid)} UUIDs successfully")

        return True

    except Exception as e:
        print(f"❌ UUID generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# v1.10.360: fix_project_termbase_flags() was removed. It set
# is_project_termbase = 1 on every termbase with a project_id, conflating
# "project-scoped" with "the project termbase" and desynchronising the legacy
# flag from the authoritative representation
# (termbase_activation.priority = 1). The inverse repair — clearing flags
# with no backing priority=1 activation row — now runs in
# DatabaseManager._create_tables(), and TermbaseManager.set_termbase_priority
# is the single write path that keeps the flag consistent.
