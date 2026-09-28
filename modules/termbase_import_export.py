"""
Termbase Import/Export Module

Handles importing and exporting termbases in TSV (Tab-Separated Values) format.
TSV is simple, universal, and works well with Excel, Google Sheets, and text editors.

Format:
- First row: header with column names
- Tab-delimited fields
- UTF-8 encoding with BOM for Excel compatibility
- Multi-line content wrapped in quotes
- Boolean values: TRUE/FALSE or 1/0

Standard columns:
- Source Term (required)
- Target Term (required)
- Domain (optional)
- Notes (optional, can be multi-line)
- Project (optional)
- Client (optional)
- Forbidden (optional, TRUE/FALSE)
"""

import csv
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ImportResult:
    """Result of a termbase import operation"""
    success: bool
    imported_count: int
    skipped_count: int
    error_count: int
    errors: List[Tuple[int, str]]  # (line_number, error_message)
    message: str


class TermbaseImporter:
    """Import termbases from TSV files"""
    
    # Standard column headers (case-insensitive matching)
    STANDARD_HEADERS = {
        'source': ['source term', 'source', 'src', 'term (source)', 'source language'],
        'target': ['target term', 'target', 'tgt', 'term (target)', 'target language'],
        'domain': ['domain', 'subject', 'field', 'category'],
        'notes': ['notes', 'note', 'definition', 'comment', 'comments', 'description'],
        'project': ['project', 'proj'],
        'client': ['client', 'customer'],
        'forbidden': ['forbidden', 'do not use', 'prohibited', 'banned'],
        'nontranslatable': ['non-translatable', 'nontranslatable', 'non translatable',
                            'do not translate', 'nt'],
        'term_uuid': ['term uuid', 'uuid', 'term id', 'id', 'term_uuid', 'termid']
    }
    
    # Common language names that can be used as column headers
    LANGUAGE_NAMES = [
        'dutch', 'english', 'german', 'french', 'spanish', 'italian', 'portuguese',
        'russian', 'chinese', 'japanese', 'korean', 'arabic', 'hebrew', 'turkish',
        'polish', 'czech', 'slovak', 'hungarian', 'romanian', 'bulgarian', 'greek',
        'swedish', 'norwegian', 'danish', 'finnish', 'estonian', 'latvian', 'lithuanian',
        'ukrainian', 'croatian', 'serbian', 'slovenian', 'bosnian', 'macedonian',
        'catalan', 'basque', 'galician', 'welsh', 'irish', 'scottish',
        'indonesian', 'malay', 'thai', 'vietnamese', 'hindi', 'bengali', 'tamil',
        'afrikaans', 'swahili', 'persian', 'farsi', 'urdu', 'punjabi',
        'nederlands', 'deutsch', 'français', 'español', 'italiano', 'português',
        'русский', '中文', '日本語', '한국어', 'العربية', 'עברית', 'türkçe'
    ]
    
    def __init__(self, db_manager, termbase_manager):
        """
        Initialize importer
        
        Args:
            db_manager: DatabaseManager instance
            termbase_manager: TermbaseManager instance
        """
        self.db_manager = db_manager
        self.termbase_manager = termbase_manager
    
    def import_tsv(self, filepath: str, termbase_id: int, 
                   skip_duplicates: bool = True,
                   update_duplicates: bool = False,
                   progress_callback=None) -> ImportResult:
        """
        Import terms from TSV file
        
        Args:
            filepath: Path to TSV file
            termbase_id: Target termbase ID
            skip_duplicates: Skip terms that already exist (based on source term)
            update_duplicates: Update existing terms instead of skipping
            progress_callback: Optional callback(current, total, message) for progress updates
            
        Returns:
            ImportResult with statistics and errors
        """
        errors = []
        imported_count = 0
        skipped_count = 0
        error_count = 0
        
        def report_progress(current, total, message):
            """Report progress if callback is provided"""
            if progress_callback:
                progress_callback(current, total, message)
        
        try:
            # Count total lines first for progress reporting
            total_lines = 0
            with open(filepath, 'r', encoding='utf-8-sig', newline='') as f:
                total_lines = sum(1 for _ in f) - 1  # Subtract header row
            
            report_progress(0, total_lines, f"Starting import of {total_lines} entries...")
            
            # Read file with UTF-8 encoding (handle BOM if present)
            with open(filepath, 'r', encoding='utf-8-sig', newline='') as f:
                # Use csv.DictReader with tab delimiter
                reader = csv.DictReader(f, delimiter='\t')
                
                # Get column mapping
                if not reader.fieldnames:
                    return ImportResult(
                        success=False,
                        imported_count=0,
                        skipped_count=0,
                        error_count=1,
                        errors=[(0, "File is empty or has no header row")],
                        message="Import failed: No header row found"
                    )
                
                column_map = self._map_columns(reader.fieldnames)
                
                report_progress(0, total_lines, f"Found columns: {', '.join(column_map.keys())}")
                
                if not column_map.get('source') or not column_map.get('target'):
                    return ImportResult(
                        success=False,
                        imported_count=0,
                        skipped_count=0,
                        error_count=1,
                        errors=[(0, f"Could not find required columns. Headers: {reader.fieldnames}")],
                        message="Import failed: Missing required columns (Source Term and Target Term)"
                    )
                
                # Get existing terms for duplicate detection
                existing_terms_by_source = {}
                existing_terms_by_uuid = {}
                if skip_duplicates or update_duplicates:
                    report_progress(0, total_lines, "Loading existing terms for duplicate detection...")
                    terms = self.termbase_manager.get_terms(termbase_id)
                    existing_terms_by_source = {term['source_term'].lower(): term for term in terms}
                    existing_terms_by_uuid = {term.get('term_uuid'): term for term in terms if term.get('term_uuid')}
                    report_progress(0, total_lines, f"Found {len(existing_terms_by_source)} existing terms")
                
                # Process each row
                for line_num, row in enumerate(reader, start=2):  # Start at 2 (line 1 is header)
                    current_row = line_num - 1  # Adjust for progress (1-indexed from data rows)
                    try:
                        # Extract data using column mapping
                        source_field = self._get_field(row, column_map.get('source', ''))
                        target_field = self._get_field(row, column_map.get('target', ''))
                        term_uuid = self._get_field(row, column_map.get('term_uuid', ''))
                        
                        # Validate required fields
                        if not source_field or not target_field:
                            errors.append((line_num, "Missing source or target term"))
                            error_count += 1
                            report_progress(current_row, total_lines, f"❌ Line {line_num}: Missing source or target")
                            continue
                        
                        # Parse source: first item = main term, rest = synonyms
                        source_parts = [s.strip() for s in source_field.split('|') if s.strip()]
                        source_term = source_parts[0] if source_parts else ''
                        source_synonym_parts = source_parts[1:] if len(source_parts) > 1 else []
                        
                        # Parse target: first item = main term, rest = synonyms  
                        target_parts = [s.strip() for s in target_field.split('|') if s.strip()]
                        target_term = target_parts[0] if target_parts else ''
                        target_synonym_parts = target_parts[1:] if len(target_parts) > 1 else []
                        
                        # Check for duplicates - UUID takes priority over source term matching
                        existing_term = None
                        
                        if term_uuid and term_uuid in existing_terms_by_uuid:
                            # UUID match - this is definitely the same term
                            existing_term = existing_terms_by_uuid[term_uuid]
                        elif source_term.lower() in existing_terms_by_source:
                            # Source term match (no UUID or UUID doesn't match)
                            existing_term = existing_terms_by_source[source_term.lower()]
                        
                        if existing_term:
                            if update_duplicates:
                                # Update existing term
                                self._update_term_from_row(existing_term['id'], row, column_map)
                                imported_count += 1
                                report_progress(current_row, total_lines, f"🔄 Updated: {source_term} → {target_term}")
                            else:
                                skipped_count += 1
                                report_progress(current_row, total_lines, f"⏭️ Skipped duplicate: {source_term}")
                            continue
                        
                        # Parse optional fields
                        domain = self._get_field(row, column_map.get('domain', ''))
                        notes = self._get_field(row, column_map.get('notes', ''))
                        project = self._get_field(row, column_map.get('project', ''))
                        client = self._get_field(row, column_map.get('client', ''))
                        forbidden = self._parse_boolean(
                            self._get_field(row, column_map.get('forbidden', ''))
                        )
                        nontranslatable = self._parse_boolean(
                            self._get_field(row, column_map.get('nontranslatable', ''))
                        )
                        
                        # Add term to termbase (pass UUID if present, otherwise one will be generated)
                        term_id = self.termbase_manager.add_term(
                            termbase_id=termbase_id,
                            source_term=source_term,
                            target_term=target_term,
                            domain=domain,
                            notes=notes,
                            project=project,
                            client=client,
                            forbidden=forbidden,
                            term_uuid=term_uuid if term_uuid else None,
                            is_nontranslatable=nontranslatable,
                        )
                        
                        if term_id:
                            imported_count += 1
                            report_progress(current_row, total_lines, f"✅ Imported: {source_term} → {target_term}")
                            
                            # Add source synonyms (already parsed from source_field above)
                            for order, syn_part in enumerate(source_synonym_parts):
                                # Check for forbidden marker [!text]
                                forbidden = False
                                synonym_text = syn_part
                                
                                if syn_part.startswith('[!') and syn_part.endswith(']'):
                                    forbidden = True
                                    synonym_text = syn_part[2:-1]  # Remove [! and ]
                                
                                self.termbase_manager.add_synonym(
                                    term_id, 
                                    synonym_text, 
                                    language='source',
                                    display_order=order,
                                    forbidden=forbidden
                                )
                            
                            # Add target synonyms (already parsed from target_field above)
                            for order, syn_part in enumerate(target_synonym_parts):
                                # Check for forbidden marker [!text]
                                forbidden = False
                                synonym_text = syn_part
                                
                                if syn_part.startswith('[!') and syn_part.endswith(']'):
                                    forbidden = True
                                    synonym_text = syn_part[2:-1]  # Remove [! and ]
                                
                                self.termbase_manager.add_synonym(
                                    term_id, 
                                    synonym_text, 
                                    language='target',
                                    display_order=order,
                                    forbidden=forbidden
                                )
                        else:
                            errors.append((line_num, "Failed to add term to database"))
                            error_count += 1
                            report_progress(current_row, total_lines, f"❌ Line {line_num}: Failed to add term")
                            
                    except Exception as e:
                        errors.append((line_num, f"Error processing row: {str(e)}"))
                        report_progress(current_row, total_lines, f"❌ Line {line_num}: {str(e)}")
                        error_count += 1
                        continue
            
            # Generate summary message
            message = f"Import complete: {imported_count} terms imported"
            if skipped_count > 0:
                message += f", {skipped_count} duplicates skipped"
            if error_count > 0:
                message += f", {error_count} errors"
            
            return ImportResult(
                success=True,
                imported_count=imported_count,
                skipped_count=skipped_count,
                error_count=error_count,
                errors=errors,
                message=message
            )
            
        except Exception as e:
            return ImportResult(
                success=False,
                imported_count=imported_count,
                skipped_count=skipped_count,
                error_count=error_count + 1,
                errors=errors + [(0, f"Fatal error: {str(e)}")],
                message=f"Import failed: {str(e)}"
            )
    
    def _map_columns(self, headers: List[str]) -> Dict[str, str]:
        """
        Map file headers to standard column names
        
        Args:
            headers: List of column headers from file
            
        Returns:
            Dictionary mapping standard names to actual column names
        """
        column_map = {}
        
        for header in headers:
            header_lower = header.lower().strip()
            
            # Check against each standard column
            for standard_name, variations in self.STANDARD_HEADERS.items():
                if header_lower in variations:
                    column_map[standard_name] = header
                    break
        
        # If source/target not found, check if first two columns are language names
        # This allows headers like "Dutch\tEnglish" or "French\tEnglish"
        if not column_map.get('source') or not column_map.get('target'):
            if len(headers) >= 2:
                first_header = headers[0].lower().strip()
                second_header = headers[1].lower().strip()
                
                # Check if both are language names
                first_is_lang = first_header in self.LANGUAGE_NAMES
                second_is_lang = second_header in self.LANGUAGE_NAMES
                
                if first_is_lang and second_is_lang:
                    # Use first column as source, second as target
                    if not column_map.get('source'):
                        column_map['source'] = headers[0]
                    if not column_map.get('target'):
                        column_map['target'] = headers[1]
                elif first_is_lang and not column_map.get('source'):
                    # Only first is a language - use as source
                    column_map['source'] = headers[0]
                elif second_is_lang and not column_map.get('target'):
                    # Only second is a language - use as target
                    column_map['target'] = headers[1]
        
        return column_map
    
    def _get_field(self, row: Dict, column_name: str) -> str:
        """Get field value from row, handling missing columns"""
        return row.get(column_name, '').strip()
    
    def _parse_boolean(self, value: str) -> bool:
        """Parse boolean value from various formats"""
        if not value:
            return False
        value_lower = value.lower().strip()
        return value_lower in ['true', '1', 'yes', 'y', 'forbidden', 'prohibited']
    
    def _update_term_from_row(self, term_id: int, row: Dict, column_map: Dict):
        """Update an existing term with data from row"""
        updates = {}
        
        if column_map.get('target'):
            updates['target_term'] = self._get_field(row, column_map['target'])
        if column_map.get('domain'):
            updates['domain'] = self._get_field(row, column_map['domain'])
        if column_map.get('notes'):
            updates['notes'] = self._get_field(row, column_map['notes'])
        if column_map.get('project'):
            updates['project'] = self._get_field(row, column_map['project'])
        if column_map.get('client'):
            updates['client'] = self._get_field(row, column_map['client'])
        if column_map.get('forbidden'):
            updates['forbidden'] = self._parse_boolean(self._get_field(row, column_map['forbidden']))
        
        self.termbase_manager.update_term(term_id, **updates)
        if column_map.get('nontranslatable'):
            self.termbase_manager.set_nontranslatable(
                term_id, self._parse_boolean(self._get_field(row, column_map['nontranslatable'])))


class TermbaseExporter:
    """Export termbases to TSV files"""
    
    def __init__(self, db_manager, termbase_manager):
        """
        Initialize exporter
        
        Args:
            db_manager: DatabaseManager instance
            termbase_manager: TermbaseManager instance
        """
        self.db_manager = db_manager
        self.termbase_manager = termbase_manager
    
    def export_tsv(self, termbase_id: int, filepath: str, 
                   include_metadata: bool = True) -> Tuple[bool, str]:
        """
        Export termbase to TSV file
        
        Args:
            termbase_id: Termbase ID to export
            filepath: Output file path
            include_metadata: Include all metadata fields
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Get all terms
            terms = self.termbase_manager.get_terms(termbase_id)
            
            if not terms:
                return (False, "Termbase is empty")
            
            # Define columns - always include UUID for tracking
            if include_metadata:
                columns = ['Term UUID', 'Source', 'Target', 'Domain',
                          'Notes', 'Project', 'Client', 'Forbidden', 'Non-translatable']
            else:
                columns = ['Term UUID', 'Source', 'Target', 'Domain', 'Notes']
            
            # Write to file with UTF-8 BOM for Excel compatibility
            with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
                
                # Write header
                writer.writerow(columns)
                
                # Write terms
                for term in terms:
                    # Build source term: main term + synonyms (pipe-delimited)
                    source_parts = [term.get('source_term', '')]
                    source_synonyms = self.termbase_manager.get_synonyms(term['id'], language='source')
                    for s in source_synonyms:
                        if s['forbidden']:
                            source_parts.append(f"[!{s['synonym_text']}]")
                        else:
                            source_parts.append(s['synonym_text'])
                    source_text = '|'.join(source_parts)
                    
                    # Build target term: main term + synonyms (pipe-delimited)
                    target_parts = [term.get('target_term', '')]
                    target_synonyms = self.termbase_manager.get_synonyms(term['id'], language='target')
                    for s in target_synonyms:
                        if s['forbidden']:
                            target_parts.append(f"[!{s['synonym_text']}]")
                        else:
                            target_parts.append(s['synonym_text'])
                    target_text = '|'.join(target_parts)
                    
                    row = [
                        term.get('term_uuid', ''),  # UUID first for tracking
                        source_text,  # Main source + synonyms pipe-delimited
                        target_text,  # Main target + synonyms pipe-delimited
                        term.get('domain', ''),
                        term.get('notes', '')
                    ]
                    
                    if include_metadata:
                        row.extend([
                            term.get('project', ''),
                            term.get('client', ''),
                            'TRUE' if term.get('forbidden', False) else 'FALSE',
                            'TRUE' if term.get('is_nontranslatable', False) else 'FALSE',
                        ])
                    
                    writer.writerow(row)
            
            return (True, f"Exported {len(terms)} terms to {os.path.basename(filepath)}")
            
        except Exception as e:
            return (False, f"Export failed: {str(e)}")
