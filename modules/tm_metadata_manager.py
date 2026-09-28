"""
Translation Memory Metadata Manager Module

Handles TM metadata operations: creation, activation, TM management.
Works alongside the existing translation_memory.py module which handles TM matching/searching.

TMs can be activated/deactivated per project (similar to termbases).
"""

import sqlite3
from typing import List, Dict, Optional
from datetime import datetime


class TMMetadataManager:
    """Manages translation memory metadata and activation"""
    
    def __init__(self, db_manager, log_callback=None):
        """
        Initialize TM metadata manager
        
        Args:
            db_manager: DatabaseManager instance
            log_callback: Optional logging function
        """
        self.db_manager = db_manager
        self.log = log_callback if log_callback else print
    
    # ========================================================================
    # TM MANAGEMENT
    # ========================================================================
    
    def tm_id_exists(self, tm_id: str) -> bool:
        """Check if a tm_id already exists in the database"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("SELECT 1 FROM translation_memories WHERE tm_id = ?", (tm_id,))
            return cursor.fetchone() is not None
        except Exception:
            return False
    
    def tm_name_exists(self, name: str) -> bool:
        """Check if a TM display name is taken (names are UNIQUE, like tm_ids)."""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("SELECT 1 FROM translation_memories WHERE name = ?", (name,))
            return cursor.fetchone() is not None
        except Exception:
            return False

    def get_unique_tm_id(self, base_tm_id: str) -> str:
        """
        Get a unique tm_id by appending a number suffix if needed.
        
        Args:
            base_tm_id: The desired tm_id base (e.g., 'my_tm')
            
        Returns:
            A unique tm_id (e.g., 'my_tm' or 'my_tm_2' if 'my_tm' exists)
        """
        if not self.tm_id_exists(base_tm_id):
            return base_tm_id
        
        # Find a unique suffix
        suffix = 2
        while True:
            candidate = f"{base_tm_id}_{suffix}"
            if not self.tm_id_exists(candidate):
                return candidate
            suffix += 1
            if suffix > 1000:  # Safety limit
                import uuid
                return f"{base_tm_id}_{uuid.uuid4().hex[:8]}"
    
    def create_tm(self, name: str, tm_id: str, source_lang: Optional[str] = None, 
                  target_lang: Optional[str] = None, description: str = "",
                  is_project_tm: bool = False, read_only: bool = False,
                  project_id: Optional[int] = None, auto_unique_id: bool = True) -> Optional[int]:
        """
        Create a new TM metadata entry
        
        Args:
            name: Display name for the TM (e.g., "ClientX_Medical_2024")
            tm_id: Unique identifier used in translation_units.tm_id field
            source_lang: Source language code (e.g., 'en', 'nl')
            target_lang: Target language code
            description: Optional description
            is_project_tm: Whether this is the special project TM (only one per project)
            read_only: Whether this TM should not be updated
            project_id: Which project this TM belongs to (NULL = global)
            auto_unique_id: If True, automatically make tm_id unique by appending suffix
            
        Returns:
            TM database ID or None if failed
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()
            
            # If this is a project TM, check if one already exists for this project
            if is_project_tm and project_id:
                cursor.execute("""
                    SELECT id, name FROM translation_memories 
                    WHERE project_id = ? AND is_project_tm = 1
                """, (project_id,))
                existing = cursor.fetchone()
                if existing:
                    self.log(f"✗ Project {project_id} already has a project TM: {existing[1]}")
                    return None
            
            # Make tm_id unique if it already exists
            if auto_unique_id and self.tm_id_exists(tm_id):
                original_tm_id = tm_id
                tm_id = self.get_unique_tm_id(tm_id)
                self.log(f"ℹ️ TM ID '{original_tm_id}' already exists, using '{tm_id}' instead")
            
            cursor.execute("""
                INSERT INTO translation_memories 
                (name, tm_id, source_lang, target_lang, description, created_date, modified_date, entry_count,
                 is_project_tm, read_only, project_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """, (name, tm_id, source_lang, target_lang, description, now, now, is_project_tm, read_only, project_id))
            
            self.db_manager.connection.commit()
            db_id = cursor.lastrowid
            tm_type = "project TM" if is_project_tm else "TM"
            self.log(f"✓ Created {tm_type}: {name} (ID: {db_id}, tm_id: {tm_id})")
            return db_id
        except Exception as e:
            self.log(f"✗ Error creating TM: {e}")
            return None
    
    def get_all_tms(self) -> List[Dict]:
        """
        Get all TMs with metadata
        
        Returns:
            List of TM dictionaries with fields: id, name, tm_id, source_lang, target_lang,
            description, entry_count, created_date, modified_date, last_used,
            is_project_tm, read_only, project_id
        """
        try:
            cursor = self.db_manager.cursor
            
            # Get TM metadata with actual entry counts from translation_units.
            # v1.10.212: added ``bridged_to_trados`` to the projection so the
            # TMs tab can show the per-TM "visible to Trados" checkbox.
            cursor.execute("""
                SELECT
                    tm.id, tm.name, tm.tm_id, tm.source_lang, tm.target_lang,
                    tm.description, tm.created_date, tm.modified_date, tm.last_used,
                    COUNT(tu.id) as actual_count,
                    tm.is_project_tm, tm.read_only, tm.project_id,
                    COALESCE(tm.bridged_to_trados, 0) as bridged_to_trados,
                    COALESCE(tm.superlookup_enabled, 1) as superlookup_enabled
                FROM translation_memories tm
                LEFT JOIN translation_units tu ON tm.tm_id = tu.tm_id
                GROUP BY tm.id
                ORDER BY tm.is_project_tm DESC, tm.name ASC
            """)

            tms = []
            for row in cursor.fetchall():
                tms.append({
                    'id': row[0],
                    'name': row[1],
                    'tm_id': row[2],
                    'source_lang': row[3],
                    'target_lang': row[4],
                    'description': row[5],
                    'created_date': row[6],
                    'modified_date': row[7],
                    'last_used': row[8],
                    'entry_count': row[9],
                    'is_project_tm': bool(row[10]) if len(row) > 10 else False,
                    'read_only': bool(row[11]) if len(row) > 11 else False,
                    'project_id': row[12] if len(row) > 12 else None,
                    'bridged_to_trados': bool(row[13]) if len(row) > 13 else False,
                    # SuperLookup inclusion (default True/included if the
                    # column predates this row — see database_manager migration).
                    'superlookup_enabled': bool(row[14]) if len(row) > 14 else True,
                })
            
            return tms
        except Exception as e:
            self.log(f"✗ Error fetching TMs: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_tm(self, tm_db_id: int) -> Optional[Dict]:
        """Get single TM by database ID"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT 
                    tm.id, tm.name, tm.tm_id, tm.source_lang, tm.target_lang,
                    tm.description, tm.created_date, tm.modified_date, tm.last_used,
                    COUNT(tu.id) as actual_count
                FROM translation_memories tm
                LEFT JOIN translation_units tu ON tm.tm_id = tu.tm_id
                WHERE tm.id = ?
                GROUP BY tm.id
            """, (tm_db_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'tm_id': row[2],
                    'source_lang': row[3],
                    'target_lang': row[4],
                    'description': row[5],
                    'created_date': row[6],
                    'modified_date': row[7],
                    'last_used': row[8],
                    'entry_count': row[9] or 0
                }
            return None
        except Exception as e:
            self.log(f"✗ Error fetching TM: {e}")
            return None
    
    def update_tm(self, tm_db_id: int, name: Optional[str] = None, 
                  description: Optional[str] = None, source_lang: Optional[str] = None,
                  target_lang: Optional[str] = None) -> bool:
        """Update TM metadata"""
        try:
            cursor = self.db_manager.cursor
            updates = []
            values = []
            
            if name is not None:
                updates.append("name = ?")
                values.append(name)
            if description is not None:
                updates.append("description = ?")
                values.append(description)
            if source_lang is not None:
                updates.append("source_lang = ?")
                values.append(source_lang)
            if target_lang is not None:
                updates.append("target_lang = ?")
                values.append(target_lang)
            
            if not updates:
                return True
            
            updates.append("modified_date = ?")
            values.append(datetime.now().isoformat())
            values.append(tm_db_id)
            
            sql = f"UPDATE translation_memories SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(sql, values)
            self.db_manager.connection.commit()
            
            self.log(f"✓ Updated TM (ID: {tm_db_id})")
            return True
        except Exception as e:
            self.log(f"✗ Error updating TM: {e}")
            return False
    
    def delete_tm(self, tm_db_id: int, delete_entries: bool = False) -> bool:
        """
        Delete TM metadata (and optionally its translation units)
        
        Args:
            tm_db_id: Database ID of the TM
            delete_entries: If True, also delete all translation_units with this tm_id
        """
        try:
            cursor = self.db_manager.cursor
            
            # Get tm_id first
            cursor.execute("SELECT tm_id FROM translation_memories WHERE id = ?", (tm_db_id,))
            row = cursor.fetchone()
            if not row:
                self.log(f"✗ TM not found: {tm_db_id}")
                return False
            
            tm_id = row[0]
            
            # Delete translation units if requested
            if delete_entries:
                cursor.execute("DELETE FROM translation_units WHERE tm_id = ?", (tm_id,))
                self.log(f"✓ Deleted translation units for tm_id: {tm_id}")
            
            # Delete TM metadata (this will cascade delete tm_activation entries)
            cursor.execute("DELETE FROM translation_memories WHERE id = ?", (tm_db_id,))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Deleted TM (ID: {tm_db_id})")
            return True
        except Exception as e:
            self.log(f"✗ Error deleting TM: {e}")
            return False
    
    def update_entry_count(self, tm_id: str) -> bool:
        """Update cached entry count for a TM"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                UPDATE translation_memories 
                SET entry_count = (
                    SELECT COUNT(*) FROM translation_units WHERE tm_id = ?
                ),
                modified_date = ?
                WHERE tm_id = ?
            """, (tm_id, datetime.now().isoformat(), tm_id))
            
            self.db_manager.connection.commit()
            return True
        except Exception as e:
            self.log(f"✗ Error updating entry count: {e}")
            return False
    
    # ========================================================================
    # TM ACTIVATION (per-project)
    # ========================================================================
    
    def activate_tm(self, tm_db_id: int, project_id: int) -> bool:
        """Activate a TM for a specific project"""
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()
            
            cursor.execute("""
                INSERT OR REPLACE INTO tm_activation (tm_db_id, project_id, is_active, activated_date)
                VALUES (?, ?, 1, ?)
            """, (tm_db_id, project_id, now))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Activated TM {tm_db_id} for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error activating TM: {e}")
            return False
    
    def deactivate_tm(self, tm_db_id: int, project_id: int) -> bool:
        """Deactivate a TM for a specific project.

        Uses INSERT OR REPLACE to ensure a project-specific record exists,
        which prevents stale global (project_id=0) activations from leaking through.
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()

            cursor.execute("""
                INSERT OR REPLACE INTO tm_activation (tm_db_id, project_id, is_active, activated_date)
                VALUES (?, ?, 0, ?)
            """, (tm_db_id, project_id, now))

            self.db_manager.connection.commit()
            self.log(f"✓ Deactivated TM {tm_db_id} for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error deactivating TM: {e}")
            return False
    
    def is_tm_active(self, tm_db_id: int, project_id: Optional[int]) -> bool:
        """Check if a TM is active for a project (or global when project_id=0).

        Project-specific records take priority over global (project_id=0) records.
        Global records are only used as fallback when no project-specific record exists.
        """
        if project_id is None:
            return False  # If None (not 0), default to inactive

        try:
            cursor = self.db_manager.cursor

            # Check project-specific record first (takes priority)
            if project_id != 0:
                cursor.execute("""
                    SELECT is_active FROM tm_activation
                    WHERE tm_db_id = ? AND project_id = ?
                """, (tm_db_id, project_id))
                row = cursor.fetchone()
                if row is not None:
                    return bool(row[0])

            # Fall back to global (project_id=0) only if no project-specific record
            cursor.execute("""
                SELECT is_active FROM tm_activation
                WHERE tm_db_id = ? AND project_id = 0
            """, (tm_db_id,))
            row = cursor.fetchone()
            if row is not None:
                return bool(row[0])

            # If no activation record exists, TM is inactive by default
            return False
        except Exception as e:
            self.log(f"✗ Error checking TM activation: {e}")
            return False
    
    def get_active_tm_ids(self, project_id: Optional[int]) -> List[str]:
        """
        Get list of active tm_id strings for a project
        
        Returns:
            List of tm_id strings that are active for the project
        """
        if project_id is None:
            # No project - return all TMs
            try:
                cursor = self.db_manager.cursor
                cursor.execute("SELECT tm_id FROM translation_memories")
                return [row[0] for row in cursor.fetchall()]
            except Exception as e:
                self.log(f"✗ Error fetching all tm_ids: {e}")
                return []
        
        try:
            cursor = self.db_manager.cursor

            # Project-specific activations take priority over global (project_id=0).
            # A TM is active if:
            #   1. It has a project-specific record with is_active=1, OR
            #   2. It has NO project-specific record but has a global record with is_active=1
            cursor.execute("""
                SELECT DISTINCT tm.tm_id
                FROM translation_memories tm
                INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id
                WHERE ta.project_id = ? AND ta.is_active = 1
            """, (project_id,))
            result = [row[0] for row in cursor.fetchall()]

            # Add globally activated TMs that don't have a project-specific record
            cursor.execute("""
                SELECT DISTINCT tm.tm_id
                FROM translation_memories tm
                INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id
                WHERE ta.project_id = 0 AND ta.is_active = 1
                AND tm.id NOT IN (
                    SELECT tm_db_id FROM tm_activation WHERE project_id = ?
                )
            """, (project_id,))
            result.extend(row[0] for row in cursor.fetchall())

            return result
        except Exception as e:
            self.log(f"✗ Error fetching active tm_ids: {e}")
            return []
    
    def get_writable_tm_ids(self, project_id: Optional[int]) -> List[str]:
        """
        Get list of writable tm_id strings for a project.
        
        Returns TMs where:
        - The TM has an activation record for this project AND
        - read_only = 0 (Write checkbox is enabled)
        
        This is used for SAVING segments to TM, separate from get_active_tm_ids()
        which is used for READING/matching from TM.
        
        Returns:
            List of tm_id strings that are writable for the project
        """
        if project_id is None:
            # No project - return all writable TMs
            try:
                cursor = self.db_manager.cursor
                cursor.execute("SELECT tm_id FROM translation_memories WHERE read_only = 0")
                return [row[0] for row in cursor.fetchall()]
            except Exception as e:
                self.log(f"✗ Error fetching all writable tm_ids: {e}")
                return []
        
        try:
            cursor = self.db_manager.cursor

            # Return TMs where Write checkbox is enabled (read_only = 0)
            # AND the TM is active for this project.
            # Project-specific records take priority over global (project_id=0).
            cursor.execute("""
                SELECT DISTINCT tm.tm_id
                FROM translation_memories tm
                INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id
                WHERE ta.project_id = ? AND ta.is_active = 1 AND tm.read_only = 0
            """, (project_id,))
            result = [row[0] for row in cursor.fetchall()]

            # Add globally activated writable TMs that don't have a project-specific record
            cursor.execute("""
                SELECT DISTINCT tm.tm_id
                FROM translation_memories tm
                INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id
                WHERE ta.project_id = 0 AND ta.is_active = 1 AND tm.read_only = 0
                AND tm.id NOT IN (
                    SELECT tm_db_id FROM tm_activation WHERE project_id = ?
                )
            """, (project_id,))
            result.extend(row[0] for row in cursor.fetchall())

            return result
        except Exception as e:
            self.log(f"✗ Error fetching writable tm_ids: {e}")
            return []
    
    # ========================================================================
    # PROJECT TM MANAGEMENT (similar to termbases)
    # ========================================================================
    
    def set_as_project_tm(self, tm_db_id: int, project_id: int) -> bool:
        """
        Set a TM as the project TM for a specific project.
        Only one TM can be the project TM per project (automatically unsets others).
        """
        try:
            cursor = self.db_manager.cursor
            
            # First, unset any existing project TM for this project
            cursor.execute("""
                UPDATE translation_memories 
                SET is_project_tm = 0 
                WHERE project_id = ? AND is_project_tm = 1
            """, (project_id,))
            
            # Then set the new one
            cursor.execute("""
                UPDATE translation_memories 
                SET is_project_tm = 1, project_id = ?
                WHERE id = ?
            """, (project_id, tm_db_id))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Set TM {tm_db_id} as project TM for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting project TM: {e}")
            return False
    
    def unset_project_tm(self, tm_db_id: int) -> bool:
        """Unset a TM as project TM"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                UPDATE translation_memories 
                SET is_project_tm = 0 
                WHERE id = ?
            """, (tm_db_id,))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Unset TM {tm_db_id} as project TM")
            return True
        except Exception as e:
            self.log(f"✗ Error unsetting project TM: {e}")
            return False
    
    def get_project_tm(self, project_id: int) -> Optional[Dict]:
        """Get the project TM for a specific project"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT 
                    tm.id, tm.name, tm.tm_id, tm.source_lang, tm.target_lang,
                    tm.description, tm.created_date, tm.modified_date, tm.last_used,
                    COUNT(tu.id) as actual_count,
                    tm.is_project_tm, tm.read_only, tm.project_id
                FROM translation_memories tm
                LEFT JOIN translation_units tu ON tm.tm_id = tu.tm_id
                WHERE tm.project_id = ? AND tm.is_project_tm = 1
                GROUP BY tm.id
            """, (project_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'tm_id': row[2],
                    'source_lang': row[3],
                    'target_lang': row[4],
                    'description': row[5],
                    'created_date': row[6],
                    'modified_date': row[7],
                    'last_used': row[8],
                    'entry_count': row[9],
                    'is_project_tm': bool(row[10]),
                    'read_only': bool(row[11]),
                    'project_id': row[12]
                }
            return None
        except Exception as e:
            self.log(f"✗ Error fetching project TM: {e}")
            return None
    
    def get_tm_by_tm_id(self, tm_id: str) -> Optional[Dict]:
        """Get TM by its tm_id string"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT 
                    tm.id, tm.name, tm.tm_id, tm.source_lang, tm.target_lang,
                    tm.description, tm.created_date, tm.modified_date, tm.last_used,
                    COUNT(tu.id) as actual_count,
                    tm.is_project_tm, tm.read_only, tm.project_id
                FROM translation_memories tm
                LEFT JOIN translation_units tu ON tm.tm_id = tu.tm_id
                WHERE tm.tm_id = ?
                GROUP BY tm.id
            """, (tm_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'tm_id': row[2],
                    'source_lang': row[3],
                    'target_lang': row[4],
                    'description': row[5],
                    'created_date': row[6],
                    'modified_date': row[7],
                    'last_used': row[8],
                    'entry_count': row[9],
                    'is_project_tm': bool(row[10]) if len(row) > 10 else False,
                    'read_only': bool(row[11]) if len(row) > 11 else False,
                    'project_id': row[12] if len(row) > 12 else None
                }
            return None
        except Exception as e:
            self.log(f"✗ Error fetching TM by tm_id: {e}")
            return None
    
    def set_read_only(self, tm_db_id: int, read_only: bool) -> bool:
        """Set whether a TM is read-only (cannot be updated)"""
        try:
            cursor = self.db_manager.cursor

            cursor.execute("""
                UPDATE translation_memories
                SET read_only = ?
                WHERE id = ?
            """, (read_only, tm_db_id))

            self.db_manager.connection.commit()
            status = "read-only" if read_only else "writable"
            self.log(f"✓ Set TM {tm_db_id} as {status}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting read-only status: {e}")
            return False

    def set_bridged_to_trados(self, tm_db_id: int, bridged: bool) -> bool:
        """Set whether a TM is visible to the Trados plugin (v1.10.212+).

        The Trados plugin reads the shared ``supervertaler.db`` (same file
        Workbench writes to) and exposes only those TMs whose
        ``bridged_to_trados`` flag is True as attachable translation
        providers. Defaults to False for every existing and newly-created
        TM so a freelancer's existing TM library doesn't suddenly leak
        across product boundaries on upgrade – users opt in per TM.

        Read-side wiring lands in the Trados plugin in a follow-up
        release (Phase 2 of Trados issue #31); this Workbench-side flag
        is the foundation.
        """
        try:
            cursor = self.db_manager.cursor

            cursor.execute("""
                UPDATE translation_memories
                SET bridged_to_trados = ?
                WHERE id = ?
            """, (1 if bridged else 0, tm_db_id))

            self.db_manager.connection.commit()
            status = "bridged" if bridged else "not bridged"
            self.log(f"✓ Set TM {tm_db_id} as {status} (Trados visibility)")
            return True
        except Exception as e:
            self.log(f"✗ Error setting bridged_to_trados status: {e}")
            return False

    def set_superlookup_enabled(self, tm_db_id: int, enabled: bool) -> bool:
        """Set whether SuperLookup searches this TM.

        Independent of the Read flag (tm_activation.is_active): this is the
        single switch that controls SuperLookup inclusion, so a TM can be
        searched by SuperLookup whether or not it's Read-active for the
        current project (and vice versa). Global per TM, no project context.
        """
        try:
            cursor = self.db_manager.cursor

            cursor.execute("""
                UPDATE translation_memories
                SET superlookup_enabled = ?
                WHERE id = ?
            """, (1 if enabled else 0, tm_db_id))

            self.db_manager.connection.commit()
            status = "included in" if enabled else "excluded from"
            self.log(f"✓ TM {tm_db_id} {status} SuperLookup")
            return True
        except Exception as e:
            self.log(f"✗ Error setting superlookup_enabled status: {e}")
            return False
