"""
Translation Memory Module - SQLite Database Backend

Manages translation memory with fuzzy matching capabilities using SQLite.
Supports multiple TMs: Project TM, Big Mama TM, and custom TMX files.

Migrated from in-memory dictionaries to SQLite for scalability.
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from modules.database_manager import DatabaseManager


class TM:
    """Individual Translation Memory with metadata"""
    
    def __init__(self, name: str, tm_id: str, enabled: bool = True, read_only: bool = False):
        self.name = name
        self.tm_id = tm_id
        self.enabled = enabled
        self.read_only = read_only
        self.entries: Dict[str, str] = {}  # source -> target mapping
        self.metadata = {
            'source_lang': None,
            'target_lang': None,
            'file_path': None,
            'created': datetime.now().isoformat(),
            'modified': datetime.now().isoformat()
        }
        self.fuzzy_threshold = 0.75
    
    def add_entry(self, source: str, target: str):
        """Add translation pair to this TM"""
        if not self.read_only and source and target:
            self.entries[source.strip()] = target.strip()
            self.metadata['modified'] = datetime.now().isoformat()
    
    def get_exact_match(self, source: str) -> Optional[str]:
        """Get exact match from this TM"""
        return self.entries.get(source.strip())
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity ratio between two texts"""
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    
    def get_fuzzy_matches(self, source: str, max_matches: int = 5) -> List[Dict]:
        """Get fuzzy matches from this TM"""
        source = source.strip()
        matches = []
        
        for tm_source, tm_target in self.entries.items():
            similarity = self.calculate_similarity(source, tm_source)
            if similarity >= self.fuzzy_threshold:
                matches.append({
                    'source': tm_source,
                    'target': tm_target,
                    'similarity': similarity,
                    'match_pct': int(similarity * 100),
                    'tm_name': self.name,
                    'tm_id': self.tm_id
                })
        
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        return matches[:max_matches]
    
    def get_entry_count(self) -> int:
        """Get number of entries in this TM"""
        return len(self.entries)
    
    def to_dict(self) -> Dict:
        """Serialize TM to dictionary for JSON storage"""
        return {
            'name': self.name,
            'tm_id': self.tm_id,
            'enabled': self.enabled,
            'read_only': self.read_only,
            'entries': self.entries,
            'metadata': self.metadata,
            'fuzzy_threshold': self.fuzzy_threshold
        }
    
    @staticmethod
    def from_dict(data: Dict) -> 'TM':
        """Deserialize TM from dictionary"""
        tm = TM(
            name=data.get('name', 'Unnamed TM'),
            tm_id=data.get('tm_id', 'unknown'),
            enabled=data.get('enabled', True),
            read_only=data.get('read_only', False)
        )
        tm.entries = data.get('entries', {})
        tm.metadata = data.get('metadata', {})
        tm.fuzzy_threshold = data.get('fuzzy_threshold', 0.75)
        return tm


class TMDatabase:
    """Manages multiple Translation Memories using SQLite backend"""
    
    def __init__(self, source_lang: str = None, target_lang: str = None, db_path: str = None, log_callback=None):
        """
        Initialize TM database
        
        Args:
            source_lang: Source language (e.g., "en" or "English")
            target_lang: Target language (e.g., "nl" or "Dutch")
            db_path: Path to SQLite database file (default: user_data/supervertaler.db)
            log_callback: Logging function
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.log = log_callback if log_callback else print
        
        # Initialize database manager
        self.db = DatabaseManager(db_path=db_path, log_callback=log_callback)
        self.db.connect()
        
        # Set language metadata if provided
        if source_lang and target_lang:
            self.set_tm_languages(source_lang, target_lang)
        
        # Global fuzzy threshold (75% minimum similarity for fuzzy matches)
        self.fuzzy_threshold = 0.75
        
        # TM metadata cache (populated from database as needed)
        # Note: Legacy 'project' and 'big_mama' TMs are no longer used.
        # All TMs are now managed through TMMetadataManager and stored in translation_memories table.
        self.tm_metadata = {}
    
    def set_tm_languages(self, source_lang: str, target_lang: str):
        """Set language pair for TMs"""
        # Convert to ISO codes
        from modules.tmx_generator import get_simple_lang_code
        self.source_lang = get_simple_lang_code(source_lang)
        self.target_lang = get_simple_lang_code(target_lang)
    
    def add_entry(self, source: str, target: str, tm_id: str = 'project',
                  context_before: str = None, context_after: str = None, notes: str = None,
                  overwrite: bool = False):
        """
        Add translation pair to TM

        Args:
            source: Source text
            target: Target text
            tm_id: TM identifier ('project', 'big_mama', or custom)
            context_before: Previous segment for context
            context_after: Next segment for context
            notes: Optional notes
            overwrite: If True, delete existing entries with the same source in
                       this TM before inserting (keeps only the latest target –
                       used by "Update Active TM(s)" so the TM stays clean).
        """
        if not source or not target:
            return

        self.db.add_translation_unit(
            source=source.strip(),
            target=target.strip(),
            source_lang=self.source_lang or 'en',
            target_lang=self.target_lang or 'nl',
            tm_id=tm_id,
            context_before=context_before,
            context_after=context_after,
            notes=notes,
            overwrite=overwrite
        )
    
    def add_to_project_tm(self, source: str, target: str):
        """Add entry to Project TM (convenience method)"""
        self.add_entry(source, target, tm_id='project')
    
    def get_exact_match(self, source: str, tm_ids: List[str] = None) -> Optional[Dict]:
        """
        Get exact match from TM(s)

        Args:
            source: Source text to match
            tm_ids: List of TM IDs to search (None = all enabled)

        Returns: Dictionary with match data (including 'target_text') or None
        """
        if tm_ids is None:
            # Search all enabled TMs
            tm_ids = [tm_id for tm_id, meta in self.tm_metadata.items() if meta.get('enabled', True)]

        match = self.db.get_exact_match(
            source=source,
            tm_ids=tm_ids,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )

        return match  # Return full dict (or None) - caller can access match['target_text']
    
    def search_all(self, source: str, tm_ids: List[str] = None, enabled_only: bool = True, max_matches: int = 5) -> List[Dict]:
        """
        Search across multiple TMs for fuzzy matches
        
        Args:
            source: Source text to search for
            tm_ids: Specific TM IDs to search (None = search all)
            enabled_only: Only search enabled TMs
            max_matches: Maximum number of results
        
        Returns:
            List of match dictionaries sorted by similarity
        """
        # Determine which TMs to search
        # If tm_ids is None or empty, search ALL TMs (don't filter by tm_id)
        if tm_ids is None and enabled_only:
            tm_ids = [tm_id for tm_id, meta in self.tm_metadata.items() if meta.get('enabled', True)]

        # If tm_ids is still empty, set to None to search ALL TMs
        if tm_ids is not None and len(tm_ids) == 0:
            tm_ids = None
        
        # First try exact match
        exact_match = self.db.get_exact_match(
            source=source,
            tm_ids=tm_ids,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )

        if exact_match:
            # Format as match dictionary
            return [{
                'source': exact_match['source_text'],
                'target': exact_match['target_text'],
                'similarity': 1.0,
                'match_pct': 100,
                'tm_name': self.tm_metadata.get(exact_match['tm_id'], {}).get('name', exact_match['tm_id']),
                'tm_id': exact_match['tm_id'],
                # v1.10.51: preserve the reverse-match flag so the Match Panel
                # can show its "⇄ reversed" chip on a fresh single-segment
                # lookup (previously dropped here, so the chip only ever showed
                # for the cached _batch_tm_match path).
                'reverse_match': exact_match.get('reverse_match', False),
            }]

        # Try fuzzy matches
        fuzzy_matches = self.db.search_fuzzy_matches(
            source=source,
            tm_ids=tm_ids,
            threshold=self.fuzzy_threshold,
            max_results=max_matches,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )

        # Format matches for UI
        formatted_matches = []
        for match in fuzzy_matches:
            formatted_matches.append({
                'source': match['source_text'],
                'target': match['target_text'],
                'similarity': match.get('similarity', 0.85),
                'match_pct': match.get('match_pct', 85),
                'tm_name': self.tm_metadata.get(match['tm_id'], {}).get('name', match['tm_id']),
                'tm_id': match['tm_id'],
                'reverse_match': match.get('reverse_match', False),
            })

        return formatted_matches

    def get_exact_matches_batch(self, sources: List[str], tm_ids: List[str] = None) -> Dict[str, Dict]:
        """
        Batch exact match lookup for multiple source texts.

        Args:
            sources: List of source texts to match
            tm_ids: List of TM IDs to search (None = all enabled)

        Returns:
            Dict mapping source_text -> match dict (with 'target_text', 'tm_id', etc.)
        """
        if tm_ids is None:
            tm_ids = [tm_id for tm_id, meta in self.tm_metadata.items() if meta.get('enabled', True)]

        return self.db.get_exact_matches_batch(
            sources=sources,
            tm_ids=tm_ids,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )

    def search_all_batch(self, sources: List[str], tm_ids: List[str] = None,
                         enabled_only: bool = True, progress_callback=None) -> Dict[str, Dict]:
        """
        Batch search across TMs for best match per source text (exact + fuzzy).

        First runs batch exact matching, then fuzzy matching on remaining unmatched.

        Args:
            sources: List of source texts to search
            tm_ids: Specific TM IDs to search (None = all enabled)
            enabled_only: Only search enabled TMs
            progress_callback: Optional callback(current, total) for fuzzy phase progress

        Returns:
            Dict mapping source_text -> best match dict with keys:
                'source', 'target', 'similarity', 'match_pct', 'tm_name', 'tm_id'
        """
        if tm_ids is None and enabled_only:
            tm_ids = [tm_id for tm_id, meta in self.tm_metadata.items() if meta.get('enabled', True)]
        if tm_ids is not None and len(tm_ids) == 0:
            tm_ids = None

        results = {}

        # Phase 1: Batch exact matching
        exact_matches = self.db.get_exact_matches_batch(
            sources=sources,
            tm_ids=tm_ids,
            source_lang=self.source_lang,
            target_lang=self.target_lang
        )

        for source_text, match in exact_matches.items():
            results[source_text] = {
                'source': match['source_text'],
                'target': match['target_text'],
                'similarity': 1.0,
                'match_pct': 100,
                'tm_name': self.tm_metadata.get(match['tm_id'], {}).get('name', match['tm_id']),
                'tm_id': match['tm_id'],
                # Preserve the reverse-match flag from the db layer so the UI
                # can mark these visually (e.g. a "reversed TM" chip) and so
                # downstream consumers can distinguish bidirectional hits.
                'reverse_match': match.get('reverse_match', False),
            }

        # Phase 2: Fuzzy matching for unmatched sources
        unmatched = [s for s in sources if s not in results]

        if unmatched:
            fuzzy_matches = self.db.search_fuzzy_matches_batch(
                sources=unmatched,
                tm_ids=tm_ids,
                threshold=self.fuzzy_threshold,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
                progress_callback=progress_callback
            )

            for source_text, match in fuzzy_matches.items():
                results[source_text] = {
                    'source': match['source_text'],
                    'target': match['target_text'],
                    'similarity': match.get('similarity', 0.75),
                    'match_pct': match.get('match_pct', 75),
                    'tm_name': self.tm_metadata.get(match['tm_id'], {}).get('name', match['tm_id']),
                    'tm_id': match['tm_id'],
                    'reverse_match': match.get('reverse_match', False),
                }

        return results

    def concordance_search(self, query: str, tm_ids: List[str] = None, direction: str = 'both',
                            source_lang: str = None, target_lang: str = None, connection=None) -> List[Dict]:
        """
        Search for text in both source and target
        
        Args:
            query: Search query
            tm_ids: TM IDs to search (None = all)
            direction: 'source' = search source only, 'target' = search target only, 'both' = bidirectional
            source_lang: Filter by source language (None = any)
            target_lang: Filter by target language (None = any)
        
        Returns: List of matching entries
        """
        results = self.db.concordance_search(query=query, tm_ids=tm_ids, direction=direction,
                                              source_lang=source_lang, target_lang=target_lang,
                                              connection=connection)
        
        # Format for UI
        formatted = []
        for result in results:
            formatted.append({
                'source': result['source_text'],
                'target': result['target_text'],
                'tm_name': self.tm_metadata.get(result['tm_id'], {}).get('name', result['tm_id']),
                'tm_id': result['tm_id'],
                'created': result.get('created_date', ''),
                'usage_count': result.get('usage_count', 0)
            })
        
        return formatted
    
    def get_tm_entries(self, tm_id: str, limit: int = None) -> List[Dict]:
        """
        Get all entries from a specific TM
        
        Args:
            tm_id: TM identifier
            limit: Maximum number of entries (None = all)
        
        Returns: List of entry dictionaries
        """
        entries = self.db.get_tm_entries(tm_id=tm_id, limit=limit)
        
        # Format for UI
        formatted = []
        for entry in entries:
            formatted.append({
                'source': entry['source_text'],
                'target': entry['target_text'],
                'created': entry.get('created_date', ''),
                'modified': entry.get('modified_date', ''),
                'usage_count': entry.get('usage_count', 0),
                'notes': entry.get('notes', '')
            })
        
        return formatted
    
    def get_entry_count(self, tm_id: str = None, enabled_only: bool = False) -> int:
        """
        Get entry count for TM(s)
        
        Args:
            tm_id: Specific TM ID (None = all)
            enabled_only: Only count enabled TMs
        
        Returns: Total entry count
        """
        if tm_id:
            return self.db.get_tm_count(tm_id=tm_id)
        
        # Count all TMs
        if enabled_only:
            tm_ids = [tm_id for tm_id, meta in self.tm_metadata.items() if meta.get('enabled', True)]
            return sum(self.db.get_tm_count(tm_id) for tm_id in tm_ids)
        else:
            return self.db.get_tm_count()
    
    def clear_tm(self, tm_id: str):
        """Clear all entries from a TM"""
        self.db.clear_tm(tm_id=tm_id)
    
    def delete_entry(self, tm_id: str, source: str, target: str):
        """Delete a specific entry from a TM"""
        self.db.delete_entry(tm_id, source, target)

    def update_entry(self, tm_id: str, old_source: str, old_target: str,
                     new_source: str, new_target: str) -> bool:
        """Update an existing TM entry's source and/or target text.

        Returns True if the entry was found and updated, False otherwise.
        """
        return self.db.update_entry(tm_id, old_source, old_target, new_source, new_target)

    def add_custom_tm(self, name: str, tm_id: str = None, read_only: bool = False):
        """Register a custom TM"""
        if tm_id is None:
            tm_id = f"custom_{len(self.tm_metadata)}"
        
        self.tm_metadata[tm_id] = {
            'name': name,
            'enabled': True,
            'read_only': read_only
        }
        
        return tm_id
    
    def remove_custom_tm(self, tm_id: str) -> bool:
        """Remove a custom TM and its entries"""
        if tm_id in self.tm_metadata and tm_id not in ['project', 'big_mama']:
            # Clear entries from database
            self.clear_tm(tm_id)
            # Remove metadata
            del self.tm_metadata[tm_id]
            return True
        return False
    
    def get_tm_list(self, enabled_only: bool = False) -> List[Dict]:
        """
        Get list of all TMs with metadata
        
        Returns: List of TM info dictionaries
        """
        tm_list = []
        for tm_id, meta in self.tm_metadata.items():
            if enabled_only and not meta.get('enabled', True):
                continue
            
            tm_list.append({
                'tm_id': tm_id,
                'name': meta.get('name', tm_id),
                'enabled': meta.get('enabled', True),
                'read_only': meta.get('read_only', False),
                'entry_count': self.db.get_tm_count(tm_id)
            })
        
        return tm_list
    
    def get_all_tms(self, enabled_only: bool = False) -> List[Dict]:
        """Alias for get_tm_list() for backward compatibility"""
        return self.get_tm_list(enabled_only=enabled_only)
    
    def load_tmx_file(self, filepath: str, src_lang: str, tgt_lang: str, 
                      tm_name: str = None, read_only: bool = False, 
                      strip_variants: bool = True, progress_callback=None) -> tuple[str, int]:
        """
        Load TMX file into a new custom TM
        
        Args:
            filepath: Path to TMX file
            src_lang: Source language code
            tgt_lang: Target language code
            tm_name: Custom name for TM (default: filename)
            read_only: Make TM read-only
            strip_variants: Match base languages ignoring regional variants (default: True)
            progress_callback: Optional callback function(current, total, message) for progress updates
        
        Returns: (tm_id, entry_count)
        """
        if tm_name is None:
            tm_name = os.path.basename(filepath).replace('.tmx', '')
        
        # Create custom TM
        tm_id = f"custom_{os.path.basename(filepath).replace('.', '_')}"
        self.add_custom_tm(tm_name, tm_id, read_only=read_only)
        
        # Load TMX content
        loaded_count = self._load_tmx_into_db(filepath, src_lang, tgt_lang, tm_id, 
                                             strip_variants=strip_variants, 
                                             progress_callback=progress_callback)
        
        self.log(f"✓ Loaded {loaded_count} entries from {os.path.basename(filepath)}")
        
        return tm_id, loaded_count
    
    def _load_tmx_into_db(self, filepath: str, src_lang: str, tgt_lang: str, tm_id: str,
                          strip_variants: bool = False, progress_callback=None) -> int:
        """
        Internal: Load TMX content into database with streaming XML and batch commits.

        Uses iterparse to stream the XML instead of loading the entire file into memory,
        and batch-commits chunks of entries instead of committing per row.
        This makes importing multi-GB TMX files feasible.

        Args:
            filepath: Path to TMX file
            src_lang: Target source language code
            tgt_lang: Target target language code
            tm_id: TM identifier
            strip_variants: If True, match base languages ignoring regional variants
            progress_callback: Optional callback function(current, total, message) for progress updates
        """
        loaded_count = 0
        chunk_size = 5000  # Batch commit size (larger = fewer disk syncs)
        chunk_buffer = []

        try:
            # Estimate total TUs from file size (avoid loading entire file just to count)
            # A typical TMX TU averages ~200-400 bytes; use 300 as estimate
            import os
            file_size = os.path.getsize(filepath)
            estimated_total = max(file_size // 300, 100)  # Rough estimate

            if progress_callback:
                progress_callback(0, estimated_total, "Starting import (streaming)...")

            xml_ns = "http://www.w3.org/XML/1998/namespace"

            # Normalize language codes
            from modules.tmx_generator import get_simple_lang_code, get_base_lang_code
            src_lang_normalized = get_simple_lang_code(src_lang)
            tgt_lang_normalized = get_simple_lang_code(tgt_lang)

            # If stripping variants, get base codes for comparison
            if strip_variants:
                src_base = get_base_lang_code(src_lang_normalized)
                tgt_base = get_base_lang_code(tgt_lang_normalized)

            processed = 0

            # Stream XML with iterparse - never loads entire file into memory
            for event, elem in ET.iterparse(filepath, events=('end',)):
                if elem.tag != 'tu':
                    continue

                src_text, tgt_text = None, None

                for tuv_node in elem.findall('tuv'):
                    lang_attr = tuv_node.get(f'{{{xml_ns}}}lang')
                    if not lang_attr:
                        continue

                    tmx_lang = get_simple_lang_code(lang_attr)

                    seg_node = tuv_node.find('seg')
                    if seg_node is not None:
                        try:
                            text = ET.tostring(seg_node, encoding='unicode', method='text').strip()
                        except:
                            text = "".join(seg_node.itertext()).strip()

                        # Match languages (exact or base code match if stripping variants)
                        if strip_variants:
                            if get_base_lang_code(tmx_lang) == src_base:
                                src_text = text
                            elif get_base_lang_code(tmx_lang) == tgt_base:
                                tgt_text = text
                        else:
                            if tmx_lang == src_lang_normalized:
                                src_text = text
                            elif tmx_lang == tgt_lang_normalized:
                                tgt_text = text

                if src_text and tgt_text:
                    chunk_buffer.append((src_text, tgt_text))
                    loaded_count += 1

                    # Batch commit when buffer is full
                    if len(chunk_buffer) >= chunk_size:
                        self.db.add_translation_units_batch(
                            chunk_buffer,
                            source_lang=src_lang_normalized,
                            target_lang=tgt_lang_normalized,
                            tm_id=tm_id
                        )
                        chunk_buffer.clear()

                        # Update progress
                        if progress_callback:
                            progress_callback(loaded_count, estimated_total,
                                            f"Imported {loaded_count:,} entries...")

                processed += 1

                # Free memory: clear the processed <tu> element and its parent refs
                elem.clear()

            # Process remaining entries in buffer
            if chunk_buffer:
                self.db.add_translation_units_batch(
                    chunk_buffer,
                    source_lang=src_lang_normalized,
                    target_lang=tgt_lang_normalized,
                    tm_id=tm_id
                )
                chunk_buffer.clear()

            # Final progress update
            if progress_callback:
                progress_callback(loaded_count, loaded_count, f"Completed: {loaded_count:,} entries imported")

            return loaded_count
        except Exception as e:
            self.log(f"✗ Error loading TMX: {e}")
            import traceback
            self.log(traceback.format_exc())
            return loaded_count  # Return what we managed to import
    
    def detect_tmx_languages(self, filepath: str) -> List[str]:
        """Detect all language codes present in a TMX file.

        Uses streaming XML parsing so it works on multi-GB files without
        loading everything into memory. Stops after scanning 100 TUs
        (languages are consistent across a TMX file).
        """
        try:
            xml_ns = "http://www.w3.org/XML/1998/namespace"
            languages = set()
            tu_count = 0

            for event, elem in ET.iterparse(filepath, events=('end',)):
                if elem.tag == 'tuv':
                    lang_attr = elem.get(f'{{{xml_ns}}}lang')
                    if lang_attr:
                        languages.add(lang_attr)
                elif elem.tag == 'tu':
                    tu_count += 1
                    elem.clear()
                    # Stop early – languages are consistent across the file
                    if tu_count >= 100:
                        break

            return sorted(list(languages))
        except:
            return []
    
    @staticmethod
    def detect_tmx_source_language(filepath: str) -> Optional[str]:
        """The source language the TMX header declares (``srclang``), or None
        when it is missing or ``*all*``. detect_tmx_languages() returns the
        languages sorted alphabetically, which says nothing about direction –
        the reason an en-GB → de-DE TMX was offered as de-DE → en-GB (#105)."""
        try:
            for _event, elem in ET.iterparse(filepath, events=('start',)):
                if elem.tag == 'header':
                    src = (elem.get('srclang') or '').strip()
                    return src if src and src.lower() != '*all*' else None
                if elem.tag == 'body':
                    return None
        except Exception:
            pass
        return None

    def check_language_compatibility(self, tmx_langs: List[str], target_src: str, target_tgt: str) -> dict:
        """
        Analyze if TMX languages match target TM languages, handling variants.
        Returns dict with compatibility info and suggestions.
        """
        from modules.tmx_generator import get_base_lang_code, languages_are_compatible
        
        if len(tmx_langs) < 2:
            return {'compatible': False, 'reason': 'tmx_incomplete'}
        
        # Get base codes
        tmx_bases = [get_base_lang_code(lang) for lang in tmx_langs]
        target_src_base = get_base_lang_code(target_src)
        target_tgt_base = get_base_lang_code(target_tgt)
        
        # Check if we can find matching pair
        src_match = None
        tgt_match = None
        
        for tmx_lang in tmx_langs:
            if get_base_lang_code(tmx_lang) == target_src_base and src_match is None:
                src_match = tmx_lang
            if get_base_lang_code(tmx_lang) == target_tgt_base and tgt_match is None:
                tgt_match = tmx_lang
        
        if not src_match or not tgt_match:
            return {
                'compatible': False,
                'reason': 'no_match',
                'tmx_langs': tmx_langs,
                'target_langs': [target_src, target_tgt]
            }
        
        # Check if exact match or variant match
        exact_match = (src_match == target_src and tgt_match == target_tgt)
        
        return {
            'compatible': True,
            'exact_match': exact_match,
            'variant_match': not exact_match,
            'tmx_source': src_match,
            'tmx_target': tgt_match,
            'target_source': target_src,
            'target_target': target_tgt
        }
    
    def close(self):
        """Close database connection"""
        if self.db:
            self.db.close()
    
    def __del__(self):
        """Ensure database is closed on cleanup"""
        self.close()
    
    # Legacy compatibility methods for old JSON format
    def to_dict(self) -> Dict:
        """Export to legacy dictionary format (for JSON serialization)"""
        # NOTE: This is a legacy method - new code should use database directly
        # Exporting large databases to JSON is not recommended
        self.log("⚠️ Warning: Exporting database to dict format. Use TMX export for large datasets.")
        
        return {
            'project_tm': {'entries': {e['source']: e['target'] for e in self.get_tm_entries('project')}},
            'big_mama_tm': {'entries': {e['source']: e['target'] for e in self.get_tm_entries('big_mama')}},
            'custom_tms': {},
            'fuzzy_threshold': self.fuzzy_threshold
        }
    
    @staticmethod
    def from_dict(data: Dict, db_path: str = None, log_callback=None) -> 'TMDatabase':
        """Import from legacy dictionary format (for JSON deserialization)"""
        # NOTE: This is a legacy method - new code should use database directly
        db = TMDatabase(db_path=db_path, log_callback=log_callback)
        
        # Import Project TM
        if 'project_tm' in data and 'entries' in data['project_tm']:
            for src, tgt in data['project_tm']['entries'].items():
                db.add_entry(src, tgt, tm_id='project')
        
        # Import Big Mama TM
        if 'big_mama_tm' in data and 'entries' in data['big_mama_tm']:
            for src, tgt in data['big_mama_tm']['entries'].items():
                db.add_entry(src, tgt, tm_id='big_mama')
        elif 'main_tm' in data and 'entries' in data['main_tm']:  # Legacy support
            for src, tgt in data['main_tm']['entries'].items():
                db.add_entry(src, tgt, tm_id='big_mama')
        
        db.fuzzy_threshold = data.get('fuzzy_threshold', 0.75)
        
        return db


class TMAgent:
    """Legacy wrapper for backwards compatibility - delegates to TMDatabase"""
    
    def __init__(self, db_path: str = None):
        self.tm_database = TMDatabase(db_path=db_path)
        self.fuzzy_threshold = 0.75
    
    @property
    def tm_data(self):
        """Legacy property - returns Project TM entries as dictionary"""
        entries = self.tm_database.get_tm_entries('project')
        return {e['source']: e['target'] for e in entries}
    
    @tm_data.setter
    def tm_data(self, value: Dict[str, str]):
        """Legacy property setter - loads entries into Project TM"""
        # Clear existing entries
        self.tm_database.clear_tm('project')
        # Add new entries
        for source, target in value.items():
            self.tm_database.add_entry(source, target, tm_id='project')
    
    def add_entry(self, source: str, target: str):
        """Add to Project TM"""
        self.tm_database.add_to_project_tm(source, target)
    
    def get_exact_match(self, source: str) -> Optional[Dict]:
        """Search all enabled TMs for exact match - returns dict with 'target_text' or None"""
        return self.tm_database.get_exact_match(source)
    
    def get_fuzzy_matches(self, source: str, max_matches: int = 5) -> List[Tuple[str, str, float]]:
        """Legacy format - returns tuples"""
        matches = self.tm_database.search_all(source, enabled_only=True, max_matches=max_matches)
        return [(m['source'], m['target'], m['similarity']) for m in matches]
    
    def get_best_match(self, source: str) -> Optional[Tuple[str, str, float]]:
        """Get best match in legacy format"""
        matches = self.get_fuzzy_matches(source, max_matches=1)
        return matches[0] if matches else None
    
    def load_from_tmx(self, filepath: str, src_lang: str = "en", tgt_lang: str = "nl") -> int:
        """Legacy TMX load - loads into a new custom TM"""
        tm_id, count = self.tm_database.load_tmx_file(filepath, src_lang, tgt_lang)
        return count
    
    def get_entry_count(self) -> int:
        """Get total entry count"""
        return self.tm_database.get_entry_count(enabled_only=False)
    
    def clear(self):
        """Clear Project TM only"""
        self.tm_database.clear_tm('project')
    
    def delete_entry(self, tm_id: str, source: str, target: str):
        """Delete a specific entry from a TM"""
        self.tm_database.delete_entry(tm_id, source, target)

    def update_entry(self, tm_id: str, old_source: str, old_target: str,
                     new_source: str, new_target: str) -> bool:
        """Update an existing TM entry's source and/or target text.

        Returns True if the entry was found and updated, False otherwise.
        """
        return self.tm_database.update_entry(tm_id, old_source, old_target, new_source, new_target)
