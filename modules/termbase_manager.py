"""
Termbase Manager Module

Handles all termbase operations: creation, activation, term management, searching.
Uses 'termbase' terminology throughout (never 'glossary').

Termbases can be:
- Global (available to all projects)
- Project-specific (linked to particular project)

Activation system: termbases can be activated/deactivated per project.
"""

import sqlite3
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# Trailing sentence punctuation removed from terms on save (Scope 1).
# Deliberately excludes quotes and brackets/parens so wrapping characters
# like "term" or (25) are preserved. Matches the trailing-punctuation set the
# term-matching layer already strips at lookup time, keeping storage and
# matching consistent.
_TERM_TRAILING_PUNCT = '.,;:!?'


def normalize_term_for_save(text: str) -> str:
    """Strip surrounding whitespace and trailing sentence punctuation from a
    term before it is stored (e.g. 'circumference.' -> 'circumference')."""
    if not text:
        return text
    return text.strip().rstrip(_TERM_TRAILING_PUNCT).strip()


class TermbaseManager:
    """Manages termbase operations and term storage"""
    
    def __init__(self, db_manager, log_callback=None):
        """
        Initialize termbase manager
        
        Args:
            db_manager: DatabaseManager instance
            log_callback: Optional logging function
        """
        self.db_manager = db_manager
        self.log = log_callback if log_callback else print
    
    # ========================================================================
    # TERMBASE MANAGEMENT
    # ========================================================================
    
    def create_termbase(self, name: str, source_lang: Optional[str] = None, 
                       target_lang: Optional[str] = None, project_id: Optional[int] = None,
                       description: str = "", is_global: bool = True, is_project_termbase: bool = False) -> Optional[int]:
        """
        Create a new termbase
        
        Args:
            name: Termbase name
            source_lang: Source language code (e.g., 'en', 'nl')
            target_lang: Target language code
            project_id: If set, termbase is project-specific; if None, it's global
            description: Optional description
            is_global: Whether this is a global termbase (available to all projects)
            is_project_termbase: Whether this is the special project termbase (only one allowed per project)
            
        Returns:
            Termbase ID or None if failed
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()

            # If this is a project termbase, check if one already exists for
            # this project. v1.10.360: the check is activation-based
            # (priority=1) — the same definition the Termbases tab displays —
            # not the legacy is_project_termbase flag, which used to drift out
            # of sync and refuse creation for termbases the UI showed as
            # having no project role.
            if is_project_termbase and project_id:
                existing = self.get_project_termbase(project_id)
                if existing:
                    self.log(f"✗ Project {project_id} already has a project termbase: {existing['name']}")
                    return None

            # The legacy flag column is always inserted as 0; the project
            # termbase role is assigned below through the single write path
            # (set_termbase_priority), which keeps flag and activation in sync.
            cursor.execute("""
                INSERT INTO termbases (name, source_lang, target_lang, project_id,
                                      description, is_global, is_project_termbase, created_date, modified_date)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """, (name, source_lang, target_lang, project_id, description, is_global, now, now))

            self.db_manager.connection.commit()
            termbase_id = cursor.lastrowid
            tb_type = "project termbase" if is_project_termbase else "termbase"
            self.log(f"✓ Created {tb_type}: {name} (ID: {termbase_id})")

            if is_project_termbase and project_id:
                self.set_termbase_priority(termbase_id, project_id, 1)

            return termbase_id
        except Exception as e:
            self.log(f"✗ Error creating termbase: {e}")
            return None
    
    def get_all_termbases(self, connection=None) -> List[Dict]:
        """
        Get all termbases (global and project-specific)

        Args:
            connection: Optional sqlite3.Connection to use (for thread-safe access
                from worker threads). When None, uses the main-thread cursor.

        Returns:
            List of termbase dictionaries with fields: id, name, source_lang, target_lang,
            project_id, description, is_global, is_active, term_count, created_date, modified_date
        """
        try:
            if connection is not None:
                cursor = connection.cursor()
                _close_cursor = True
            else:
                cursor = self.db_manager.cursor
                _close_cursor = False

            cursor.execute("""
                SELECT 
                    t.id, t.name, t.source_lang, t.target_lang, t.project_id,
                    t.description, t.is_global, t.priority, t.is_project_termbase, 
                    t.ranking, t.read_only, t.created_date, t.modified_date,
                    COUNT(gt.id) as term_count,
                    COALESCE(t.superlookup_enabled, 1) as superlookup_enabled
                FROM termbases t
                LEFT JOIN termbase_terms gt ON CAST(t.id AS TEXT) = gt.termbase_id
                GROUP BY t.id
                ORDER BY t.is_project_termbase DESC, t.is_global DESC, t.name ASC
            """)
            
            termbases = []
            for row in cursor.fetchall():
                termbases.append({
                    'id': row[0],
                    'name': row[1],
                    'source_lang': row[2],
                    'target_lang': row[3],
                    'project_id': row[4],
                    'description': row[5],
                    'is_global': row[6],
                    'priority': row[7] or 50,  # Default to 50 if NULL (legacy)
                    'is_project_termbase': bool(row[8]),
                    'ranking': row[9],  # Termbase ranking
                    'read_only': bool(row[10]) if row[10] is not None else True,  # Default to read-only if NULL
                    'created_date': row[11],
                    'modified_date': row[12],
                    'term_count': row[13] or 0,
                    # SuperLookup inclusion (default True/included if the
                    # column predates this row — see database_manager migration).
                    'superlookup_enabled': bool(row[14]) if len(row) > 14 and row[14] is not None else True,
                })

            if _close_cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            return termbases
        except Exception as e:
            self.log(f"✗ Error fetching termbases: {e}")
            return []
    
    def get_termbase(self, termbase_id: int) -> Optional[Dict]:
        """Get single termbase by ID"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT 
                    t.id, t.name, t.source_lang, t.target_lang, t.project_id,
                    t.description, t.is_global, t.created_date, t.modified_date,
                    COUNT(gt.id) as term_count
                FROM termbases t
                LEFT JOIN termbase_terms gt ON t.id = gt.termbase_id
                WHERE t.id = ?
                GROUP BY t.id
            """, (termbase_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'source_lang': row[2],
                    'target_lang': row[3],
                    'project_id': row[4],
                    'description': row[5],
                    'is_global': row[6],
                    'created_date': row[7],
                    'modified_date': row[8],
                    'term_count': row[9] or 0
                }
            return None
        except Exception as e:
            self.log(f"✗ Error fetching termbase: {e}")
            return None
    
    def delete_termbase(self, termbase_id: int) -> bool:
        """
        Delete termbase and all its terms
        
        Args:
            termbase_id: Termbase ID
            
        Returns:
            True if successful
        """
        try:
            cursor = self.db_manager.cursor
            
            # Delete terms first
            cursor.execute("DELETE FROM termbase_terms WHERE termbase_id = ?", (termbase_id,))
            
            # Delete termbase
            cursor.execute("DELETE FROM termbases WHERE id = ?", (termbase_id,))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Deleted termbase ID: {termbase_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error deleting termbase: {e}")
            return False
    
    def rename_termbase(self, termbase_id: int, new_name: str) -> bool:
        """
        Rename a termbase
        
        Args:
            termbase_id: Termbase ID
            new_name: New name for the termbase
            
        Returns:
            True if successful
        """
        try:
            if not new_name or not new_name.strip():
                self.log(f"✗ Cannot rename termbase: empty name provided")
                return False
            
            new_name = new_name.strip()
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()
            
            cursor.execute("""
                UPDATE termbases 
                SET name = ?, modified_date = ?
                WHERE id = ?
            """, (new_name, now, termbase_id))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Renamed termbase ID {termbase_id} to '{new_name}'")
            return True
        except Exception as e:
            self.log(f"✗ Error renaming termbase: {e}")
            return False
    
    def get_active_termbases_for_project(self, project_id: int) -> List[Dict]:
        """
        Get all active termbases for a specific project
        
        Args:
            project_id: Project ID
            
        Returns:
            List of active termbase dictionaries
        """
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT 
                    t.id, t.name, t.source_lang, t.target_lang, t.project_id,
                    t.description, t.is_global, t.created_date, t.modified_date,
                    t.ranking, t.is_project_termbase,
                    COUNT(gt.id) as term_count
                FROM termbases t
                LEFT JOIN termbase_terms gt ON t.id = gt.termbase_id
                LEFT JOIN termbase_activation ta ON t.id = ta.termbase_id AND ta.project_id = ?
                WHERE (t.is_global = 1 OR t.project_id = ?)
                AND (ta.is_active = 1 OR ta.is_active IS NULL)
                GROUP BY t.id
                ORDER BY t.name ASC
            """, (project_id, project_id))
            
            termbases = []
            for row in cursor.fetchall():
                termbases.append({
                    'id': row[0],
                    'name': row[1],
                    'source_lang': row[2],
                    'target_lang': row[3],
                    'project_id': row[4],
                    'description': row[5],
                    'is_global': row[6],
                    'created_date': row[7],
                    'modified_date': row[8],
                    'ranking': row[9],
                    'is_project_termbase': row[10],
                    'term_count': row[11] or 0
                })
            
            return termbases
        except Exception as e:
            self.log(f"✗ Error fetching active termbases: {e}")
            return []
    
    # ========================================================================
    # TERMBASE ACTIVATION
    # ========================================================================
    
    def is_termbase_active(self, termbase_id: int, project_id: int) -> bool:
        """Check if termbase is active for a project"""
        try:
            cursor = self.db_manager.cursor
            
            cursor.execute("""
                SELECT is_active FROM termbase_activation 
                WHERE termbase_id = ? AND project_id = ?
            """, (termbase_id, project_id))
            
            result = cursor.fetchone()
            if result:
                return result[0] == 1
            
            # If no record exists, termbases are active by default
            return True
        except Exception as e:
            self.log(f"✗ Error checking termbase activation: {e}")
            return True
    
    def activate_termbase(self, termbase_id: int, project_id: int) -> bool:
        """Activate termbase for project (as background glossary by default)"""
        try:
            cursor = self.db_manager.cursor

            self.log(f"🔵 ACTIVATE: termbase_id={termbase_id}, project_id={project_id}")

            # Check if activation record already exists
            cursor.execute("""
                SELECT activated_date FROM termbase_activation
                WHERE termbase_id = ? AND project_id = ?
            """, (termbase_id, project_id))
            existing = cursor.fetchone()

            if existing:
                # Re-activate existing record, preserve priority (project glossary flag)
                cursor.execute("""
                    UPDATE termbase_activation
                    SET is_active = 1
                    WHERE termbase_id = ? AND project_id = ?
                """, (termbase_id, project_id))
                self.log(f"  ✓ Re-activated termbase (preserved timestamp)")
            else:
                # New activation – background glossary by default (priority=NULL)
                cursor.execute("""
                    INSERT INTO termbase_activation (termbase_id, project_id, is_active, priority)
                    VALUES (?, ?, 1, NULL)
                """, (termbase_id, project_id))
                self.log(f"  ✓ Created new activation record (background termbase)")

            self.db_manager.connection.commit()
            self.log(f"✓ Activated termbase {termbase_id} for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error activating termbase: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
            try:
                self.db_manager.connection.rollback()
            except Exception:
                pass
            return False

    def deactivate_termbase(self, termbase_id: int, project_id: int) -> bool:
        """Deactivate termbase for project and reassign rankings"""
        try:
            cursor = self.db_manager.cursor
            
            self.log(f"🔴 DEACTIVATE: termbase_id={termbase_id}, project_id={project_id}")
            
            cursor.execute("""
                INSERT OR REPLACE INTO termbase_activation (termbase_id, project_id, is_active)
                VALUES (?, ?, 0)
            """, (termbase_id, project_id))
            
            self.log(f"  ✓ Inserted deactivation record")
            
            # Note: Priority is preserved in termbase_activation table even when deactivated
            # This way if user re-activates, the priority is remembered
            
            self.db_manager.connection.commit()
            self.log(f"✓ Deactivated termbase {termbase_id} for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error deactivating termbase: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
            try:
                self.db_manager.connection.rollback()
            except Exception:
                pass
            return False
    
    def set_termbase_read_only(self, termbase_id: int, read_only: bool) -> bool:
        """Set termbase read-only status (True = read-only, False = writable)"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("""
                UPDATE termbases SET read_only = ? WHERE id = ?
            """, (1 if read_only else 0, termbase_id))
            self.db_manager.connection.commit()
            status = "read-only" if read_only else "writable"
            self.log(f"✓ Set termbase {termbase_id} to {status}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting termbase read_only: {e}")
            try:
                self.db_manager.connection.rollback()
            except Exception:
                pass
            return False

    def get_termbase_ai_inject(self, termbase_id: int) -> bool:
        """Get whether termbase terms should be injected into LLM prompts"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("SELECT ai_inject FROM termbases WHERE id = ?", (termbase_id,))
            result = cursor.fetchone()
            return bool(result[0]) if result and result[0] else False
        except Exception as e:
            self.log(f"✗ Error getting termbase ai_inject: {e}")
            return False

    def set_termbase_ai_inject(self, termbase_id: int, ai_inject: bool) -> bool:
        """Set whether termbase terms should be injected into LLM prompts"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("""
                UPDATE termbases SET ai_inject = ? WHERE id = ?
            """, (1 if ai_inject else 0, termbase_id))
            self.db_manager.connection.commit()
            status = "enabled" if ai_inject else "disabled"
            self.log(f"✓ AI injection {status} for termbase {termbase_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting termbase ai_inject: {e}")
            return False

    # ---------- Voice-dictation biasing (v1.10.28) ----------------
    # Per-termbase opt-in flag for voice-dictation vocabulary biasing.
    # When on, the termbase's target-language terms get appended to
    # Whisper's initial_prompt by the Voice tab's "Also bias from
    # your termbases" toggle. Shared between Workbench and Trados
    # plugin via the common database, no project context required.

    def get_termbase_voice_enabled(self, termbase_id: int) -> bool:
        """Return whether this termbase contributes to voice-dictation
        biasing. Defaults to **False** (opt-in) – users with many
        termbases shouldn't get them all biasing dictation unless
        they've explicitly ticked the 🎤 Voice column in Termbase
        Manager. (v1.10.28 originally defaulted True; v1.10.29
        flipped to opt-in based on user feedback.)
        """
        try:
            cursor = self.db_manager.cursor
            cursor.execute(
                "SELECT voice_dictation_enabled FROM termbases WHERE id = ?",
                (termbase_id,),
            )
            result = cursor.fetchone()
            return bool(result[0]) if result and result[0] is not None else False
        except Exception as e:
            self.log(f"✗ Error getting termbase voice_dictation_enabled: {e}")
            return False

    def set_termbase_voice_enabled(self, termbase_id: int, enabled: bool) -> bool:
        """Set the voice-dictation-biasing flag for a termbase."""
        try:
            cursor = self.db_manager.cursor
            cursor.execute(
                "UPDATE termbases SET voice_dictation_enabled = ? WHERE id = ?",
                (1 if enabled else 0, termbase_id),
            )
            self.db_manager.connection.commit()
            status = "enabled" if enabled else "disabled"
            self.log(f"✓ Voice-dictation bias {status} for termbase {termbase_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting termbase voice_dictation_enabled: {e}")
            return False

    def set_termbase_superlookup_enabled(self, termbase_id: int, enabled: bool) -> bool:
        """Set whether SuperLookup searches this termbase.

        Independent of the Read flag (termbase_activation.is_active): this
        is the single switch that controls SuperLookup inclusion, so a
        termbase can be searched by SuperLookup whether or not it's
        Read-active for the current project. Global per termbase, no
        project context.
        """
        try:
            cursor = self.db_manager.cursor
            cursor.execute(
                "UPDATE termbases SET superlookup_enabled = ? WHERE id = ?",
                (1 if enabled else 0, termbase_id),
            )
            self.db_manager.connection.commit()
            status = "included in" if enabled else "excluded from"
            self.log(f"✓ Termbase {termbase_id} {status} SuperLookup")
            return True
        except Exception as e:
            self.log(f"✗ Error setting termbase superlookup_enabled: {e}")
            try:
                self.db_manager.connection.rollback()
            except Exception:
                pass
            return False

    def get_voice_enabled_termbase_ids(self) -> list:
        """Return the IDs of every termbase whose voice-dictation
        bias flag is **explicitly on**. No project context required
        – this is a Workbench-wide setting, not per-project.

        Strict ``= 1`` match: NULL and 0 both mean "don't bias",
        consistent with the v1.10.29 opt-in default. Returns an
        empty list if the user hasn't ticked any termbases yet,
        which is the correct behaviour – the Voice tab's "Also bias
        from your termbases" toggle is still meaningful, it just
        contributes no terms beyond the built-in defaults until the
        user picks termbases in Termbase Manager.
        """
        try:
            cursor = self.db_manager.cursor
            cursor.execute(
                "SELECT id FROM termbases WHERE voice_dictation_enabled = 1"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            self.log(f"✗ Error listing voice-enabled termbases: {e}")
            return []

    def get_ai_inject_termbases(self, project_id: Optional[int] = None) -> List[Dict]:
        """
        Get all termbases with ai_inject enabled that are active for the given project.

        Args:
            project_id: Project ID (0 or None for global)

        Returns:
            List of termbase dictionaries with all terms
        """
        try:
            cursor = self.db_manager.cursor
            proj_id = project_id if project_id else 0

            cursor.execute("""
                SELECT t.id, t.name, t.source_lang, t.target_lang
                FROM termbases t
                LEFT JOIN termbase_activation ta ON t.id = ta.termbase_id AND ta.project_id = ?
                WHERE t.ai_inject = 1
                AND (ta.is_active = 1 OR (t.is_global = 1 AND ta.is_active IS NULL))
                ORDER BY CASE WHEN ta.priority = 1 THEN 0 ELSE 1 END ASC, t.name ASC
            """, (proj_id,))

            termbases = []
            for row in cursor.fetchall():
                termbases.append({
                    'id': row[0],
                    'name': row[1],
                    'source_lang': row[2],
                    'target_lang': row[3]
                })
            return termbases
        except Exception as e:
            self.log(f"✗ Error getting AI inject termbases: {e}")
            return []

    def get_ai_inject_terms(self, project_id: Optional[int] = None) -> List[Dict]:
        """
        Get all terms from AI-inject-enabled termbases for the given project.

        Args:
            project_id: Project ID (0 or None for global)

        Returns:
            List of term dictionaries with source_term, target_term, forbidden, termbase_name
        """
        try:
            # First get all AI-inject termbases
            ai_termbases = self.get_ai_inject_termbases(project_id)
            if not ai_termbases:
                return []

            all_terms = []
            cursor = self.db_manager.cursor

            for tb in ai_termbases:
                cursor.execute("""
                    SELECT source_term, target_term, forbidden
                    FROM termbase_terms
                    WHERE termbase_id = ?
                    ORDER BY source_term ASC
                """, (tb['id'],))

                for row in cursor.fetchall():
                    all_terms.append({
                        'source_term': row[0],
                        'target_term': row[1],
                        'forbidden': bool(row[2]) if row[2] else False,
                        'termbase_name': tb['name']
                    })

            self.log(f"📚 Retrieved {len(all_terms)} terms from {len(ai_termbases)} AI-inject glossar{'y' if len(ai_termbases) == 1 else 'ies'}")
            return all_terms
        except Exception as e:
            self.log(f"✗ Error getting AI inject terms: {e}")
            return []

    def set_termbase_priority(self, termbase_id: int, project_id: int, priority) -> bool:
        """
        Set a termbase as Project glossary (priority=1) or Background (priority=None).
        Only one termbase can be the Project glossary per project (exclusive).

        v1.10.360: this is the SINGLE write path for the project-termbase
        role. ``termbase_activation.priority = 1`` (with ``is_active = 1``)
        is the authoritative representation — it is what the Termbases tab
        displays and what matching ranks by. Promoting also:

        * ensures an active activation row exists (a project termbase must be
          readable to match, so ticking Project implies ticking Read), and
        * keeps the legacy ``termbases.is_project_termbase`` flag consistent
          for project-scoped termbases, so external readers of the shared
          database (e.g. Supervertaler for Trados) never see a phantom
          project termbase. Global termbases promoted per-project keep the
          flag at 0 — a single flag column cannot represent a per-project
          role (the same termbase can be the Project glossary of many
          projects).

        Args:
            termbase_id: Termbase ID
            project_id: Project ID
            priority: 1 to set as Project glossary, None/0/other to set as Background

        Returns:
            True if successful
        """
        try:
            cursor = self.db_manager.cursor
            is_project = (priority == 1)

            if is_project:
                # A project termbase must be readable: create/re-enable the
                # activation row before assigning the role (no-op if already
                # active).
                if not self.activate_termbase(termbase_id, project_id):
                    return False

                # Exclusive: clear project glossary flag from all other termbases in this project
                cursor.execute("""
                    UPDATE termbase_activation
                    SET priority = NULL
                    WHERE project_id = ? AND priority = 1
                """, (project_id,))

            # Set the requested termbase
            new_priority = 1 if is_project else None
            cursor.execute("""
                UPDATE termbase_activation
                SET priority = ?
                WHERE termbase_id = ? AND project_id = ?
            """, (new_priority, termbase_id, project_id))

            if cursor.rowcount == 0:
                self.log(f"⚠️ No activation record found for termbase {termbase_id}, project {project_id}")
                return False

            # Legacy-flag hygiene (project-scoped termbases only — see
            # docstring). On promote: this termbase carries the flag, no
            # other termbase of the project does. On demote: it loses it.
            if is_project:
                cursor.execute("""
                    UPDATE termbases SET is_project_termbase = 0
                    WHERE project_id = ? AND id != ? AND is_project_termbase = 1
                """, (project_id, termbase_id))
                cursor.execute("""
                    UPDATE termbases SET is_project_termbase = 1
                    WHERE id = ? AND project_id = ?
                """, (termbase_id, project_id))
            else:
                cursor.execute("""
                    UPDATE termbases SET is_project_termbase = 0
                    WHERE id = ? AND project_id = ?
                """, (termbase_id, project_id))

            self.db_manager.connection.commit()
            label = "Project termbase" if is_project else "Background"
            self.log(f"✓ Set termbase {termbase_id} as {label} for project {project_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error setting termbase priority: {e}")
            return False
    
    def get_termbase_priority(self, termbase_id: int, project_id: int) -> Optional[int]:
        """Get priority for a termbase in a specific project"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("""
                SELECT priority FROM termbase_activation 
                WHERE termbase_id = ? AND project_id = ? AND is_active = 1
            """, (termbase_id, project_id))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            self.log(f"✗ Error getting termbase priority: {e}")
            return None
    
    def set_as_project_termbase(self, termbase_id: int, project_id: int) -> bool:
        """
        Set a termbase as the project termbase for a project.
        Only one project termbase allowed per project - this will unset any existing one.

        v1.10.360: thin alias for the single write path — activates the
        termbase and sets ``termbase_activation.priority = 1`` (and keeps the
        legacy flag in sync). Kept for API compatibility.
        """
        return self.set_termbase_priority(termbase_id, project_id, 1)
    
    def get_active_termbase_ids(self, project_id: int) -> List[int]:
        """
        Get list of active termbase IDs for a project (for saving to project file)
        
        Returns:
            List of termbase IDs (not database IDs)
        """
        try:
            cursor = self.db_manager.cursor
            cursor.execute("""
                SELECT t.id
                FROM termbases t
                INNER JOIN termbase_activation ta ON t.id = ta.termbase_id
                WHERE ta.project_id = ? AND ta.is_active = 1
                ORDER BY ta.activated_date ASC
            """, (project_id,))
            
            active_ids = [row[0] for row in cursor.fetchall()]
            return active_ids
        except Exception as e:
            self.log(f"✗ Error getting active termbase IDs: {e}")
            return []
    
    def _reassign_rankings_for_project(self, project_id: int):
        """
        Reassign rankings to all activated termbases for a project.
        Rankings are assigned sequentially (1, 2, 3, ...) based on termbase ID order.
        Project termbases don't get rankings (they're always highlighted pink).
        """
        try:
            cursor = self.db_manager.cursor
            
            # Get all activated termbases for this project (excluding the
            # project termbase, i.e. the priority=1 activation row).
            # Order by activation timestamp so first activated gets #1, second gets #2, etc.
            cursor.execute("""
                SELECT t.id
                FROM termbases t
                INNER JOIN termbase_activation ta ON t.id = ta.termbase_id
                WHERE ta.project_id = ? AND ta.is_active = 1
                AND COALESCE(ta.priority, 0) != 1
                ORDER BY ta.activated_date ASC
            """, (project_id,))
            
            activated_termbase_ids = [row[0] for row in cursor.fetchall()]
            
            # Assign rankings sequentially
            for rank, termbase_id in enumerate(activated_termbase_ids, start=1):
                cursor.execute("""
                    UPDATE termbases SET ranking = ? WHERE id = ?
                """, (rank, termbase_id))
                self.log(f"  ✓ Assigned ranking #{rank} to termbase ID {termbase_id}")
            
            # Clear rankings for non-activated termbases
            if activated_termbase_ids:
                placeholders = ','.join('?' * len(activated_termbase_ids))
                cursor.execute(f"""
                    UPDATE termbases SET ranking = NULL 
                    WHERE id NOT IN ({placeholders})
                """, activated_termbase_ids)
            else:
                cursor.execute("UPDATE termbases SET ranking = NULL")
            
            # Commit the changes
            self.db_manager.connection.commit()
            self.log(f"✓ Assigned rankings to {len(activated_termbase_ids)} activated termbase(s) for project {project_id}")
                
        except Exception as e:
            self.log(f"✗ Error reassigning rankings: {e}")
    
    def unset_project_termbase(self, termbase_id: int) -> bool:
        """Remove project termbase designation from a termbase.

        v1.10.360: clears BOTH representations — every
        ``termbase_activation.priority = 1`` row this termbase holds (in any
        project) and the legacy ``is_project_termbase`` flag — so the two can
        never disagree afterwards.
        """
        try:
            cursor = self.db_manager.cursor

            cursor.execute("""
                UPDATE termbase_activation
                SET priority = NULL
                WHERE termbase_id = ? AND priority = 1
            """, (termbase_id,))

            cursor.execute("""
                UPDATE termbases
                SET is_project_termbase = 0
                WHERE id = ?
            """, (termbase_id,))

            self.db_manager.connection.commit()
            self.log(f"✓ Removed project termbase designation from termbase {termbase_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error unsetting project termbase: {e}")
            return False
    
    def get_project_termbase(self, project_id: int) -> Optional[Dict]:
        """Get the project termbase for a specific project.

        v1.10.360: activation-based — the project termbase is the termbase
        with ``termbase_activation.priority = 1`` (and ``is_active = 1``) for
        this project, exactly what the Termbases tab shows as the pink
        Project tick. The legacy ``is_project_termbase`` flag is no longer
        consulted: it used to drift out of sync (a startup migration flagged
        every project-scoped termbase), making this method report a project
        termbase the UI said didn't exist. Note the project termbase may be
        a global termbase promoted for this project, so there is no
        ``t.project_id`` filter.
        """
        try:
            cursor = self.db_manager.cursor

            cursor.execute("""
                SELECT
                    t.id, t.name, t.source_lang, t.target_lang, t.project_id,
                    t.description, t.is_global,
                    t.created_date, t.modified_date,
                    COUNT(gt.id) as term_count
                FROM termbases t
                INNER JOIN termbase_activation ta ON ta.termbase_id = t.id
                    AND ta.project_id = ? AND ta.priority = 1 AND ta.is_active = 1
                LEFT JOIN termbase_terms gt ON CAST(gt.termbase_id AS INTEGER) = t.id
                GROUP BY t.id
            """, (project_id,))

            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'source_lang': row[2],
                    'target_lang': row[3],
                    'project_id': row[4],
                    'description': row[5],
                    'is_global': row[6],
                    'priority': 1,
                    'is_project_termbase': True,
                    'created_date': row[7],
                    'modified_date': row[8],
                    'term_count': row[9] or 0
                }
            return None
        except Exception as e:
            self.log(f"✗ Error getting project termbase: {e}")
            return None
    
    # ========================================================================
    # TERM MANAGEMENT
    # ========================================================================
    
    def add_term(self, termbase_id: int, source_term: str, target_term: str,
                 domain: str = "", notes: str = "",
                 project: str = "", client: str = "",
                 forbidden: bool = False, source_lang: Optional[str] = None,
                 target_lang: Optional[str] = None, term_uuid: Optional[str] = None,
                 is_nontranslatable: bool = False,
                 definition: str = "", url: str = "",
                 source_abbreviation: str = "", target_abbreviation: str = "",
                 **kwargs) -> Optional[int]:
        """
        Add a term to termbase

        Args:
            termbase_id: Termbase ID
            source_term: Source language term
            target_term: Target language term
            domain: Domain/category
            notes: Optional notes
            project: Optional project name
            client: Optional client name
            forbidden: Whether this is a forbidden term
            source_lang: Source language code
            target_lang: Target language code
            term_uuid: Optional UUID for tracking term across imports/exports
            is_nontranslatable: Whether this term is a non-translatable
                                (copies through unchanged at translation time)
            definition: Brief gloss / definition (separate from notes)
            url: Optional reference URL
            source_abbreviation: Optional abbreviation for the source term
            target_abbreviation: Optional abbreviation for the target term

        Returns:
            Term ID or None if failed (returns None if duplicate found)
        """
        try:
            import uuid
            cursor = self.db_manager.cursor

            # Strip trailing sentence punctuation from translatable terms so
            # entries like "circumference." are stored as "circumference".
            # Non-translatables are left as-is (a trailing "." may be meaningful,
            # e.g. an abbreviation like "Inc.").
            if not is_nontranslatable:
                source_term = normalize_term_for_save(source_term)
                target_term = normalize_term_for_save(target_term)

            # Check for duplicate (case-insensitive check)
            cursor.execute("""
                SELECT id FROM termbase_terms
                WHERE termbase_id = ?
                AND LOWER(source_term) = LOWER(?)
                AND LOWER(target_term) = LOWER(?)
            """, (termbase_id, source_term, target_term))

            existing = cursor.fetchone()
            if existing:
                self.log(f"⚠️ Duplicate term not added: {source_term} → {target_term} (already exists in termbase {termbase_id})")
                return None

            # Generate UUID if not provided
            if not term_uuid:
                term_uuid = str(uuid.uuid4())

            cursor.execute("""
                INSERT INTO termbase_terms
                (termbase_id, source_term, target_term, domain, notes,
                 project, client, forbidden, source_lang, target_lang, term_uuid,
                 is_nontranslatable, definition, url,
                 source_abbreviation, target_abbreviation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (termbase_id, source_term, target_term, domain, notes,
                  project, client, forbidden, source_lang, target_lang, term_uuid,
                  1 if is_nontranslatable else 0, definition, url,
                  source_abbreviation, target_abbreviation))

            self.db_manager.connection.commit()
            term_id = cursor.lastrowid
            tag = " [NT]" if is_nontranslatable else ""
            self.log(f"✓ Added term to termbase {termbase_id}: {source_term} → {target_term}{tag}")
            return term_id
        except Exception as e:
            self.log(f"✗ Error adding term: {e}")
            return None

    def find_merge_matches(self, termbase_id: int, source_term: str,
                           target_term: str) -> List[Dict]:
        """Find existing entries in a termbase that PARTIALLY overlap a new pair.

        A partial overlap is an entry that shares the same source term but a
        different target (the new target is a target-synonym candidate), or
        the same target but a different source (a source-synonym candidate).
        Exact duplicates (both columns equal) are NOT returned — add_term's own
        duplicate check handles those.

        Inputs are expected in the termbase's own storage direction (callers
        orient via _orient_term_for_termbase first), exactly like add_term.

        Mirrors the Trados plugin's TermMergeChecker.FindMergeMatches so the two
        products offer the same "merge as synonym" prompt against the shared
        SQLite schema.

        Returns a list of dicts:
            {'term_id': int, 'source_term': str, 'target_term': str,
             'match_type': 'source'|'target'}
        where 'source' means the existing source_term matched and 'target' means
        the existing target_term matched. Empty list when there are no candidates.
        """
        matches: List[Dict] = []
        if (not source_term or not source_term.strip()
                or not target_term or not target_term.strip()):
            return matches
        try:
            cursor = self.db_manager.cursor
            cursor.execute("""
                SELECT id, source_term, target_term
                FROM termbase_terms
                WHERE termbase_id = ?
                  AND (
                    (LOWER(TRIM(source_term)) = LOWER(?)
                     AND LOWER(TRIM(target_term)) != LOWER(?))
                    OR
                    (LOWER(TRIM(target_term)) = LOWER(?)
                     AND LOWER(TRIM(source_term)) != LOWER(?))
                  )
            """, (termbase_id,
                  source_term.strip(), target_term.strip(),
                  target_term.strip(), source_term.strip()))
            for row in cursor.fetchall():
                existing_source = row[1] or ""
                existing_target = row[2] or ""
                source_matched = (existing_source.strip().lower()
                                  == source_term.strip().lower())
                matches.append({
                    'term_id': row[0],
                    'source_term': existing_source,
                    'target_term': existing_target,
                    'match_type': 'source' if source_matched else 'target',
                })
        except Exception as e:
            self.log(f"✗ Error checking for merge matches: {e}")
        return matches

    def set_nontranslatable(self, term_id: int, is_nontranslatable: bool) -> bool:
        """Toggle the non-translatable flag on an existing term.

        When marking a term as non-translatable, target_term is also synced
        to source_term – that's the convention the Trados plugin uses, and
        it ensures NT terms surface a visible "translation" (the original)
        in the TermLens panel.

        Returns True on success.
        """
        try:
            cursor = self.db_manager.cursor
            if is_nontranslatable:
                cursor.execute(
                    """
                    UPDATE termbase_terms
                    SET is_nontranslatable = 1,
                        target_term = source_term,
                        modified_date = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (term_id,),
                )
            else:
                cursor.execute(
                    """
                    UPDATE termbase_terms
                    SET is_nontranslatable = 0,
                        modified_date = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (term_id,),
                )
            self.db_manager.connection.commit()
            return True
        except Exception as e:
            self.log(f"✗ Error toggling NT flag on term {term_id}: {e}")
            return False
    
    def get_terms(self, termbase_id: int, connection=None) -> List[Dict]:
        """Get all terms in a termbase.

        connection: Optional sqlite3.Connection for thread-safe access from
            worker threads (uses connection.cursor() instead of the shared
            main-thread self.db_manager.cursor).
        """
        try:
            if connection is not None:
                cursor = connection.cursor()
                _close_cursor = True
            else:
                cursor = self.db_manager.cursor
                _close_cursor = False

            cursor.execute("""
                SELECT id, source_term, target_term, domain, notes,
                       project, client, forbidden, term_uuid,
                       COALESCE(is_nontranslatable, 0)
                FROM termbase_terms
                WHERE termbase_id = CAST(? AS TEXT)
                ORDER BY source_term ASC
            """, (termbase_id,))
            # termbase_terms.termbase_id is declared TEXT (stores e.g. '13') while
            # the caller passes the numeric termbases.id. Cast the PARAM to TEXT —
            # explicit (no reliance on SQLite implicit coercion) AND keeps the
            # idx_gt_termbase_id index usable. See the identifier convention in
            # modules/identifier_conventions.py.

            terms = []
            for row in cursor.fetchall():
                terms.append({
                    'id': row[0],
                    'source_term': row[1],
                    'target_term': row[2],
                    'domain': row[3],
                    'notes': row[4],
                    'project': row[5],
                    'client': row[6],
                    'forbidden': row[7],
                    'term_uuid': row[8],
                    'is_nontranslatable': bool(row[9]),
                })

            if _close_cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            return terms
        except Exception as e:
            self.log(f"✗ Error fetching terms: {e}")
            return []
    
    def update_term(self, term_id: int, source_term: Optional[str] = None,
                   target_term: Optional[str] = None,
                   domain: Optional[str] = None, notes: Optional[str] = None,
                   project: Optional[str] = None, client: Optional[str] = None,
                   forbidden: Optional[bool] = None, **kwargs) -> bool:
        """Update a term"""
        try:
            cursor = self.db_manager.cursor
            updates = []
            params = []
            
            if source_term is not None:
                updates.append("source_term = ?")
                params.append(source_term)
            if target_term is not None:
                updates.append("target_term = ?")
                params.append(target_term)
            if domain is not None:
                updates.append("domain = ?")
                params.append(domain)
            if notes is not None:
                updates.append("notes = ?")
                params.append(notes)
            if project is not None:
                updates.append("project = ?")
                params.append(project)
            if client is not None:
                updates.append("client = ?")
                params.append(client)
            if forbidden is not None:
                updates.append("forbidden = ?")
                params.append(forbidden)
            
            if not updates:
                return False

            # v1.10.74: always bump modified_date on UPDATE so the
            # snapshot-gated auto-refresh in
            # Supervertaler.force_refresh_matches sees the change.
            # SQLite's DEFAULT CURRENT_TIMESTAMP only fires on
            # INSERT, not UPDATE, so without this the row's
            # modified_date would stay frozen at INSERT time and
            # edit-only changes wouldn't propagate to TermLens.
            updates.append("modified_date = CURRENT_TIMESTAMP")

            params.append(term_id)
            sql = f"UPDATE termbase_terms SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(sql, params)
            self.db_manager.connection.commit()

            self.log(f"✓ Updated term {term_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error updating term: {e}")
            return False
    
    def delete_term(self, term_id: int) -> bool:
        """Delete a term"""
        try:
            cursor = self.db_manager.cursor
            cursor.execute("DELETE FROM termbase_terms WHERE id = ?", (term_id,))
            self.db_manager.connection.commit()
            self.log(f"✓ Deleted term {term_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error deleting term: {e}")
            return False
    
    # ========================================================================
    # SEARCH
    # ========================================================================
    
    def search_termbase(self, termbase_id: int, search_term: str, 
                       search_source: bool = True, search_target: bool = True) -> List[Dict]:
        """
        Search within a termbase (searches main terms AND synonyms)
        
        Args:
            termbase_id: Termbase ID to search in
            search_term: Term to search for
            search_source: Search in source terms and source synonyms
            search_target: Search in target terms and target synonyms
            
        Returns:
            List of matching terms (includes main term + synonyms as separate entries)
        """
        try:
            cursor = self.db_manager.cursor
            
            # Find matching term IDs (from main terms OR synonyms)
            matching_term_ids = set()
            
            if search_source:
                # Search main source terms
                cursor.execute("""
                    SELECT id FROM termbase_terms
                    WHERE termbase_id = ? AND source_term LIKE ?
                """, (termbase_id, f"%{search_term}%"))
                matching_term_ids.update(row[0] for row in cursor.fetchall())
                
                # Search source synonyms
                cursor.execute("""
                    SELECT term_id FROM termbase_synonyms
                    WHERE term_id IN (SELECT id FROM termbase_terms WHERE termbase_id = ?)
                    AND language = 'source' AND synonym_text LIKE ?
                """, (termbase_id, f"%{search_term}%"))
                matching_term_ids.update(row[0] for row in cursor.fetchall())
            
            if search_target:
                # Search main target terms
                cursor.execute("""
                    SELECT id FROM termbase_terms
                    WHERE termbase_id = ? AND target_term LIKE ?
                """, (termbase_id, f"%{search_term}%"))
                matching_term_ids.update(row[0] for row in cursor.fetchall())
                
                # Search target synonyms
                cursor.execute("""
                    SELECT term_id FROM termbase_synonyms
                    WHERE term_id IN (SELECT id FROM termbase_terms WHERE termbase_id = ?)
                    AND language = 'target' AND synonym_text LIKE ?
                """, (termbase_id, f"%{search_term}%"))
                matching_term_ids.update(row[0] for row in cursor.fetchall())
            
            if not matching_term_ids:
                return []
            
            # Get full details for matching terms
            placeholders = ','.join('?' * len(matching_term_ids))
            sql = f"""
                SELECT id, source_term, target_term, domain, definition, forbidden,
                       COALESCE(notes, ''),
                       COALESCE(url, ''),
                       COALESCE(source_abbreviation, ''),
                       COALESCE(target_abbreviation, ''),
                       COALESCE(project, ''),
                       COALESCE(client, '')
                FROM termbase_terms
                WHERE id IN ({placeholders})
                ORDER BY source_term ASC
            """

            cursor.execute(sql, list(matching_term_ids))

            results = []
            for row in cursor.fetchall():
                term_id = row[0]

                # Add main term
                results.append({
                    'id': term_id,
                    'source_term': row[1],
                    'target_term': row[2],
                    'domain': row[3],
                    'definition': row[4],
                    'forbidden': row[5],
                    'notes': row[6],
                    'url': row[7],
                    'source_abbreviation': row[8],
                    'target_abbreviation': row[9],
                    'project': row[10],
                    'client': row[11],
                })

                # Add target synonyms as separate entries (memoQ style)
                # Synonyms are ordered by display_order (position 0 = main/preferred)
                target_synonyms = self.get_synonyms(term_id, language='target')
                for syn in target_synonyms:
                    results.append({
                        'id': term_id,  # Same term ID
                        'source_term': row[1],  # Same source
                        'target_term': syn['synonym_text'],  # Synonym as target
                        'domain': row[3],
                        'definition': row[4],
                        'forbidden': syn['forbidden'],  # Use synonym's forbidden flag
                        'notes': row[6],
                        'url': row[7],
                        'source_abbreviation': row[8],
                        'target_abbreviation': row[9],
                        'project': row[10],
                        'client': row[11],
                    })
            
            return results
        except Exception as e:
            self.log(f"✗ Error searching termbase: {e}")
            return []
    
    # ========================================================================
    # SYNONYM MANAGEMENT
    # ========================================================================
    
    def add_synonym(self, term_id: int, synonym_text: str, language: str = 'target', 
                    display_order: int = 0, forbidden: bool = False) -> bool:
        """
        Add a synonym to a term
        
        Args:
            term_id: Term ID to add synonym to
            synonym_text: The synonym text
            language: 'source' or 'target' (default: 'target')
            display_order: Position in list (0 = main/top, higher = lower priority)
            forbidden: Whether this synonym is forbidden
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()

            # Synonyms are terms too: strip trailing sentence punctuation on save.
            synonym_text = normalize_term_for_save(synonym_text)

            # Check if synonym already exists
            cursor.execute("""
                SELECT id FROM termbase_synonyms
                WHERE term_id = ? AND synonym_text = ? AND language = ?
            """, (term_id, synonym_text, language))
            
            if cursor.fetchone():
                self.log(f"✗ Synonym already exists: {synonym_text}")
                return False
            
            cursor.execute("""
                INSERT INTO termbase_synonyms (term_id, synonym_text, language, display_order, forbidden, created_date, modified_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (term_id, synonym_text, language, display_order, 1 if forbidden else 0, now, now))
            
            self.db_manager.connection.commit()
            self.log(f"✓ Added synonym: {synonym_text}")
            return True
        except Exception as e:
            self.log(f"✗ Error adding synonym: {e}")
            return False
    
    def get_synonyms(self, term_id: int, language: Optional[str] = None) -> List[Dict]:
        """
        Get synonyms for a term, ordered by display_order (position)
        
        Args:
            term_id: Term ID to get synonyms for
            language: Optional filter - 'source', 'target', or None for both
            
        Returns:
            List of synonym dictionaries with fields: id, synonym_text, language, display_order, forbidden
        """
        try:
            cursor = self.db_manager.cursor
            
            if language:
                cursor.execute("""
                    SELECT id, synonym_text, language, display_order, forbidden, created_date, modified_date
                    FROM termbase_synonyms
                    WHERE term_id = ? AND language = ?
                    ORDER BY display_order ASC, created_date ASC
                """, (term_id, language))
            else:
                cursor.execute("""
                    SELECT id, synonym_text, language, display_order, forbidden, created_date, modified_date
                    FROM termbase_synonyms
                    WHERE term_id = ?
                    ORDER BY language DESC, display_order ASC, created_date ASC
                """, (term_id,))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'synonym_text': row[1],
                    'language': row[2],
                    'display_order': row[3],
                    'forbidden': bool(row[4]),
                    'created_date': row[5],
                    'modified_date': row[6]
                })
            
            return results
        except Exception as e:
            self.log(f"✗ Error getting synonyms: {e}")
            return []
    
    def update_synonym_order(self, synonym_id: int, new_order: int) -> bool:
        """
        Update the display order of a synonym
        
        Args:
            synonym_id: Synonym ID to update
            new_order: New display order (0 = top/main)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE termbase_synonyms 
                SET display_order = ?, modified_date = ?
                WHERE id = ?
            """, (new_order, now, synonym_id))
            self.db_manager.connection.commit()
            return True
        except Exception as e:
            self.log(f"✗ Error updating synonym order: {e}")
            return False
    
    def update_synonym_forbidden(self, synonym_id: int, forbidden: bool) -> bool:
        """
        Update the forbidden flag of a synonym
        
        Args:
            synonym_id: Synonym ID to update
            forbidden: New forbidden status
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.db_manager.cursor
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE termbase_synonyms 
                SET forbidden = ?, modified_date = ?
                WHERE id = ?
            """, (1 if forbidden else 0, now, synonym_id))
            self.db_manager.connection.commit()
            return True
        except Exception as e:
            self.log(f"✗ Error updating synonym forbidden status: {e}")
            return False
    
    def reorder_synonyms(self, term_id: int, language: str, synonym_ids_in_order: List[int]) -> bool:
        """
        Reorder synonyms for a term
        
        Args:
            term_id: Term ID
            language: 'source' or 'target'
            synonym_ids_in_order: List of synonym IDs in desired order
            
        Returns:
            True if successful, False otherwise
        """
        try:
            for order, syn_id in enumerate(synonym_ids_in_order):
                self.update_synonym_order(syn_id, order)
            return True
        except Exception as e:
            self.log(f"✗ Error reordering synonyms: {e}")
            return False
    
    def delete_synonym(self, synonym_id: int) -> bool:
        """
        Delete a synonym
        
        Args:
            synonym_id: Synonym ID to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.db_manager.cursor
            cursor.execute("DELETE FROM termbase_synonyms WHERE id = ?", (synonym_id,))
            self.db_manager.connection.commit()
            self.log(f"✓ Deleted synonym {synonym_id}")
            return True
        except Exception as e:
            self.log(f"✗ Error deleting synonym: {e}")
            return False
