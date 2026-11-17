"""
AI Actions Module

Provides structured action interface for AI Assistant to interact with Supervertaler
resources (Prompt Library, Translation Memories, Termbases).

Phase 2 of AI Assistant Enhancement Plan:
- Parses ACTION markers from AI responses
- Executes actions on prompt library
- Returns structured results for display

Action Format:
ACTION:function_name
PARAMS:{"param1": "value1", "param2": "value2"}

Example:
ACTION:list_prompts
PARAMS:{"folder": "Domain Expertise"}

ACTION:create_prompt
PARAMS:{"name": "Medical Translator", "content": "...", "folder": "Domain Expertise"}
"""

import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any


class AIActionSystem:
    """
    Handles parsing and execution of AI actions on Supervertaler resources.
    """

    # Actions that change the prompt library or the active prompt. They run
    # only after the user says yes: the reply comes from a model, and a
    # document or web page it was shown can steer what it asks for.
    CHANGING_ACTIONS = frozenset({
        'create_prompt', 'update_prompt', 'delete_prompt', 'create_folder',
        'toggle_quick_run', 'activate_prompt',
    })

    def __init__(self, prompt_library, parent_app=None, log_callback=None,
                 confirm_callback=None):
        """
        Initialize AI Action System.

        Args:
            prompt_library: UnifiedPromptLibrary instance
            parent_app: Reference to main application (for segment access)
            log_callback: Function to call for logging messages
            confirm_callback: Called with a list of plain descriptions of the
                changes a reply asks for; returns True to go ahead. Without
                one, changes are refused.
        """
        self.prompt_library = prompt_library
        self.parent_app = parent_app
        self.log = log_callback if log_callback else print
        self.confirm_callback = confirm_callback

        # Map action names to handler methods
        self.action_handlers = {
            'list_prompts': self._action_list_prompts,
            'get_prompt': self._action_get_prompt,
            'create_prompt': self._action_create_prompt,
            'update_prompt': self._action_update_prompt,
            'delete_prompt': self._action_delete_prompt,
            'search_prompts': self._action_search_prompts,
            'create_folder': self._action_create_folder,
            'toggle_quick_run': self._action_toggle_quick_run,
            'get_quick_run': self._action_get_quick_run,
            'get_folder_structure': self._action_get_folder_structure,
            'get_segment_count': self._action_get_segment_count,
            'get_segment_info': self._action_get_segment_info,
            'activate_prompt': self._action_activate_prompt,
        }

    def parse_and_execute(self, ai_response: str) -> Tuple[str, List[Dict]]:
        """
        Parse AI response for ACTION markers and execute actions.

        Args:
            ai_response: Full response text from AI

        Returns:
            Tuple of (cleaned_response, list of action results)
            cleaned_response: AI response with ACTION blocks removed
            action_results: List of {action, params, success, result/error}
        """
        action_results = []

        # Strip markdown code fences if present (Claude often wraps in ```yaml or ```)
        # Handle both block-level and inline code fences
        self.log(f"[DEBUG] Original response length: {len(ai_response)} chars")
        self.log(f"[DEBUG] First 200 chars: {ai_response[:200]}")

        # Remove opening fence: ```yaml, ```json, or just ``` (at start or after newline)
        ai_response = re.sub(r'(^|\n)```(?:yaml|json|)?\s*\n?', r'\1', ai_response)
        # Remove closing fence: ``` (at end or before newline)
        ai_response = re.sub(r'\n?```\s*($|\n)', r'\1', ai_response)
        # Remove any remaining standalone backticks or language markers
        ai_response = re.sub(r'^`(?:yaml|json|)\s*$', '', ai_response, flags=re.MULTILINE)

        self.log(f"[DEBUG] After fence stripping length: {len(ai_response)} chars")
        self.log(f"[DEBUG] After fence stripping first 200 chars: {ai_response[:200]}")

        cleaned_response = ai_response

        # Find all ACTION blocks
        # Pattern: ACTION:name PARAMS:... (with optional newline between)
        # Handles both "ACTION:name\nPARAMS:" and "ACTION:name PARAMS:"
        action_pattern = r'ACTION:(\w+)\s+PARAMS:\s*'
        matches = list(re.finditer(action_pattern, ai_response))

        self.log(f"[DEBUG] Found {len(matches)} ACTION blocks")

        # Read every block first, so the user is asked once, before
        # anything changes, with the whole list in front of them.
        parsed = []  # (action_name, params or None, params_str, json error, full_block)
        for i, match in enumerate(matches):
            action_name = match.group(1)
            start_pos = match.end()

            # Find where this action's params end (next ACTION: or end of string)
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(ai_response)

            params_str = ai_response[start_pos:end_pos].strip()

            # Extract just the JSON part (up to first newline followed by non-JSON char)
            # Look for the closing brace of the JSON object
            brace_count = 0
            json_end = 0
            in_string = False
            escape_next = False

            for idx, char in enumerate(params_str):
                if escape_next:
                    escape_next = False
                    continue

                if char == '\\':
                    escape_next = True
                    continue

                if char == '"' and not escape_next:
                    in_string = not in_string

                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = idx + 1
                            break

            if json_end > 0:
                params_str = params_str[:json_end]

            # The full ACTION block: from start of match to end of JSON
            full_block = ai_response[match.start():start_pos + json_end]
            try:
                params = json.loads(params_str)
                if not isinstance(params, dict):
                    raise json.JSONDecodeError("PARAMS must be a JSON object", params_str, 0)
                parsed.append((action_name, params, params_str, None, full_block))
            except json.JSONDecodeError as e:
                parsed.append((action_name, None, params_str, e, full_block))

        changes = [(name, params) for name, params, _, err, _ in parsed
                   if err is None and name in self.CHANGING_ACTIONS]
        approved = bool(changes) and self._confirm_changes(changes)

        for action_name, params, params_str, err, full_block in parsed:
            if err is not None:
                self.log(f"✗ Failed to parse action parameters: {err}")
                self.log(f"[DEBUG] Raw params_str: {params_str[:500]}...")
                action_results.append({
                    'action': action_name,
                    'params': params_str[:200],  # Truncate for display
                    'success': False,
                    'error': f"Invalid JSON parameters: {err}"
                })
                continue

            # The block comes out of the reply whether it runs or not.
            cleaned_response = cleaned_response.replace(full_block, '')

            if action_name in self.CHANGING_ACTIONS and not approved:
                action_results.append({
                    'action': action_name,
                    'params': params,
                    'success': False,
                    'error': "Not done: the change was not allowed"
                })
                continue

            result = self.execute_action(action_name, params)
            action_results.append(result)

            # Reload prompt library immediately if prompt was created/updated/deleted
            # This ensures subsequent actions (like activate_prompt) see the new prompt
            if result['success'] and action_name in ['create_prompt', 'update_prompt', 'delete_prompt']:
                self.prompt_library.load_all_prompts()
                self.log(f"✓ Reloaded prompt library after {action_name}")

        return cleaned_response.strip(), action_results

    @staticmethod
    def describe_change(action_name: str, params: Dict) -> str:
        """One plain line saying what a changing action would do."""
        def q(key):
            value = str(params.get(key) or '').strip()
            return value[:80] + ('…' if len(value) > 80 else '')
        if action_name == 'create_prompt':
            where = f" in the folder “{q('folder')}”" if params.get('folder') else ""
            return f"Create the prompt “{q('name')}”{where}"
        if action_name == 'update_prompt':
            return f"Change the prompt {q('path')}"
        if action_name == 'delete_prompt':
            return f"Delete the prompt {q('path')}"
        if action_name == 'create_folder':
            return f"Create the folder “{q('path')}”"
        if action_name == 'toggle_quick_run':
            return f"Add {q('path')} to, or remove it from, the QuickLauncher"
        if action_name == 'activate_prompt':
            if params.get('mode') == 'attach':
                return f"Attach the prompt {q('path')}"
            return f"Make {q('path')} the active prompt"
        return action_name

    def _confirm_changes(self, changes: List[Tuple[str, Dict]]) -> bool:
        """Ask the user once about all the changes in a reply."""
        if self.confirm_callback is None:
            self.log("✗ The AI asked to change the prompt library; nothing was changed (no way to ask you)")
            return False
        try:
            return bool(self.confirm_callback(
                [self.describe_change(name, params) for name, params in changes]))
        except Exception as e:
            self.log(f"✗ Could not ask about the AI's changes, so none were made: {e}")
            return False

    def execute_action(self, action_name: str, params: Dict) -> Dict:
        """
        Execute a single action with given parameters.

        Args:
            action_name: Name of the action
            params: Dictionary of parameters

        Returns:
            Dictionary with action result: {action, params, success, result/error}
        """
        if action_name not in self.action_handlers:
            return {
                'action': action_name,
                'params': params,
                'success': False,
                'error': f"Unknown action: {action_name}"
            }

        try:
            handler = self.action_handlers[action_name]
            result = handler(params)

            return {
                'action': action_name,
                'params': params,
                'success': True,
                'result': result
            }
        except Exception as e:
            self.log(f"✗ Error executing {action_name}: {e}")
            return {
                'action': action_name,
                'params': params,
                'success': False,
                'error': str(e)
            }

    # ========================================================================
    # ACTION HANDLERS
    # ========================================================================

    def _action_list_prompts(self, params: Dict) -> Dict:
        """
        List prompts in library, optionally filtered by folder.

        Params:
            folder (optional): Folder path to filter by
            include_content (optional): Include full content (default: False)

        Returns:
            {count: int, prompts: [{name, path, folder, quick_run, ...}]}
        """
        folder_filter = params.get('folder')
        include_content = params.get('include_content', False)

        prompts_list = []
        for path, prompt_data in self.prompt_library.prompts.items():
            # Apply folder filter if specified
            if folder_filter:
                if prompt_data.get('_folder') != folder_filter:
                    continue

            # Build prompt info
            prompt_info = {
                'name': prompt_data.get('name', path),
                'path': path,
                'folder': prompt_data.get('_folder', ''),
                'description': prompt_data.get('description', ''),
                'quick_run': prompt_data.get('quick_run', False),
            }

            if include_content:
                prompt_info['content'] = prompt_data.get('content', '')

            prompts_list.append(prompt_info)

        return {
            'count': len(prompts_list),
            'prompts': prompts_list
        }

    def _action_get_prompt(self, params: Dict) -> Dict:
        """
        Get full details of a specific prompt.

        Params:
            path (required): Relative path to prompt

        Returns:
            {name, path, folder, content, metadata...}
        """
        path = params.get('path')
        if not path:
            raise ValueError("Missing required parameter: path")

        if path not in self.prompt_library.prompts:
            raise ValueError(f"Prompt not found: {path}")

        prompt_data = self.prompt_library.prompts[path]

        return {
            'name': prompt_data.get('name', path),
            'path': path,
            'folder': prompt_data.get('_folder', ''),
            'description': prompt_data.get('description', ''),
            'content': prompt_data.get('content', ''),
            'quick_run': prompt_data.get('quick_run', False),
            'category': prompt_data.get('category', prompt_data.get('domain', '')),
            'app': prompt_data.get('app', 'both'),
            'created': prompt_data.get('created', ''),
            'modified': prompt_data.get('modified', '')
        }

    def _action_create_prompt(self, params: Dict) -> Dict:
        """
        Create a new prompt in the library.

        Params:
            name (required): Prompt name
            content (required): Prompt content
            folder (optional): Folder to create in (default: root)
            description (optional): Prompt description
            domain (optional): Domain category
            task_type (optional): Task type
            activate (optional): If True, activate as primary after creating

        Returns:
            {success: bool, path: str, message: str}
        """
        name = params.get('name')
        content = params.get('content')

        if not name or not content:
            # Log what we actually received for debugging
            self.log(f"[DEBUG] create_prompt received params: {params}")
            self.log(f"[DEBUG] name={repr(name)}, content={repr(content)[:100] if content else None}")
            missing = []
            if not name:
                missing.append("name")
            if not content:
                missing.append("content")
            raise ValueError(f"Missing required parameters: {', '.join(missing)}. Received keys: {list(params.keys())}")

        # Build relative path
        folder = params.get('folder', '') or ''
        self.prompt_library.inside_library(folder)  # raises if it leads out of the library
        # Sanitize filename - remove/replace invalid Windows filename characters
        # Invalid chars: < > : " / \ | ? * and also → (arrow) which AI likes to use
        filename = name
        invalid_chars = ['/', '\\', '<', '>', ':', '"', '|', '?', '*', '→', '←', '↔']
        for char in invalid_chars:
            filename = filename.replace(char, '-')
        # Also clean up multiple dashes and trim
        import re
        filename = re.sub(r'-+', '-', filename).strip('-')
        filename = filename + '.md'
        relative_path = f"{folder}/{filename}" if folder else filename

        # Build prompt data
        prompt_data = {
            'name': name,
            'content': content,
            'description': params.get('description', ''),
            'category': params.get('domain', params.get('category', '')),
            'quick_run': False,
            'created': datetime.now().strftime('%Y-%m-%d'),
            'modified': datetime.now().strftime('%Y-%m-%d')
        }

        # Save prompt
        success = self.prompt_library.save_prompt(relative_path, prompt_data)

        if success:
            message = f"Created prompt: {name}"

            # Auto-activate if requested
            if params.get('activate', False):
                self.prompt_library.set_primary_prompt(relative_path)
                message += " and set as the active Custom Prompt ⭐"

            return {
                'success': True,
                'path': relative_path,
                'message': message
            }
        else:
            raise Exception("Failed to save prompt")

    def _action_update_prompt(self, params: Dict) -> Dict:
        """
        Update an existing prompt.

        Params:
            path (required): Relative path to prompt
            content (optional): New content
            name (optional): New name
            description (optional): New description

        Returns:
            {success: bool, path: str, message: str}
        """
        path = params.get('path')
        if not path:
            raise ValueError("Missing required parameter: path")

        if path not in self.prompt_library.prompts:
            raise ValueError(f"Prompt not found: {path}")

        # Get existing prompt data
        prompt_data = self.prompt_library.prompts[path].copy()

        # Update fields
        if 'name' in params:
            prompt_data['name'] = params['name']
        if 'content' in params:
            prompt_data['content'] = params['content']
        if 'description' in params:
            prompt_data['description'] = params['description']

        prompt_data['modified'] = datetime.now().strftime('%Y-%m-%d')

        # Save updated prompt
        success = self.prompt_library.save_prompt(path, prompt_data)

        if success:
            return {
                'success': True,
                'path': path,
                'message': f"Updated prompt: {prompt_data['name']}"
            }
        else:
            raise Exception("Failed to update prompt")

    def _action_delete_prompt(self, params: Dict) -> Dict:
        """
        Delete a prompt from the library.

        Params:
            path (required): Relative path to prompt

        Returns:
            {success: bool, path: str, message: str}
        """
        path = params.get('path')
        if not path:
            raise ValueError("Missing required parameter: path")

        if path not in self.prompt_library.prompts:
            raise ValueError(f"Prompt not found: {path}")

        name = self.prompt_library.prompts[path].get('name', path)
        success = self.prompt_library.delete_prompt(path)

        if success:
            return {
                'success': True,
                'path': path,
                'message': f"Deleted prompt: {name}"
            }
        else:
            raise Exception("Failed to delete prompt")

    def _action_search_prompts(self, params: Dict) -> Dict:
        """
        Search prompts by name or content.

        Params:
            query (required): Search query
            search_in (optional): 'name', 'content', or 'all' (default: all)

        Returns:
            {count: int, results: [{name, path, folder, match_type, ...}]}
        """
        query = params.get('query', '').lower()
        search_in = params.get('search_in', 'all')

        if not query:
            raise ValueError("Missing required parameter: query")

        results = []
        for path, prompt_data in self.prompt_library.prompts.items():
            match_type = None

            # Search in name
            if search_in in ['name', 'all']:
                name = prompt_data.get('name', '').lower()
                if query in name:
                    match_type = 'name'

            # Search in content
            if not match_type and search_in in ['content', 'all']:
                content = prompt_data.get('content', '').lower()
                if query in content:
                    match_type = 'content'

            if match_type:
                results.append({
                    'name': prompt_data.get('name', path),
                    'path': path,
                    'folder': prompt_data.get('_folder', ''),
                    'description': prompt_data.get('description', ''),
                    'match_type': match_type
                })

        return {
            'count': len(results),
            'results': results
        }

    def _action_create_folder(self, params: Dict) -> Dict:
        """
        Create a new folder in the library.

        Params:
            path (required): Folder path (e.g., "Domain Expertise/Medical")

        Returns:
            {success: bool, path: str, message: str}
        """
        path = params.get('path')
        if not path:
            raise ValueError("Missing required parameter: path")
        self.prompt_library.inside_library(path)  # raises if it leads out of the library

        success = self.prompt_library.create_folder(path)

        if success:
            return {
                'success': True,
                'path': path,
                'message': f"Created folder: {path}"
            }
        else:
            raise Exception("Failed to create folder")

    def _action_toggle_quick_run(self, params: Dict) -> Dict:
        """
        Toggle QuickLauncher (legacy name: quick_run) status of a prompt.

        Params:
            path (required): Relative path to prompt

        Returns:
            {success: bool, path: str, quick_run: bool, message: str}
        """
        path = params.get('path')
        if not path:
            raise ValueError("Missing required parameter: path")

        success = self.prompt_library.toggle_quick_run(path)

        if success:
            is_quick_run = self.prompt_library.prompts[path].get('quick_run', False)
            return {
                'success': True,
                'path': path,
                'quick_run': is_quick_run,
                'message': f"{'Added to' if is_quick_run else 'Removed from'} QuickLauncher"
            }
        else:
            raise Exception("Failed to toggle quick run")

    def _action_get_quick_run(self, params: Dict) -> Dict:
        """
        Get list of QuickLauncher prompts (legacy name: quick_run).

        Returns:
            {count: int, prompts: [{name, path}]}
        """
        quick_run = self.prompt_library.get_quick_run_prompts()

        return {
            'count': len(quick_run),
            'prompts': [{'name': name, 'path': path} for path, name in quick_run]
        }

    def _action_get_folder_structure(self, params: Dict) -> Dict:
        """
        Get complete folder structure of the library.

        Returns:
            Nested dictionary representing folder structure
        """
        structure = self.prompt_library.get_folder_structure()

        return structure

    def _action_get_segment_count(self, params: Dict) -> Dict:
        """
        Get the total number of segments in the current project.

        Returns:
            {total_segments: int, translated: int, untranslated: int}
        """
        if not self.parent_app:
            raise ValueError("Parent app not available for segment access")

        if not hasattr(self.parent_app, 'current_project') or not self.parent_app.current_project:
            raise ValueError("No project currently loaded")

        project = self.parent_app.current_project
        if not hasattr(project, 'segments') or not project.segments:
            return {
                'total_segments': 0,
                'translated': 0,
                'untranslated': 0
            }

        segments = project.segments
        total = len(segments)
        translated = sum(1 for seg in segments if seg.target and seg.target.strip())
        untranslated = total - translated

        return {
            'total_segments': total,
            'translated': translated,
            'untranslated': untranslated
        }

    def _action_get_segment_info(self, params: Dict) -> Dict:
        """
        Get detailed information about specific segment(s).

        Params:
            segment_id (optional): Specific segment ID to retrieve
            segment_ids (optional): List of segment IDs to retrieve
            start_id (optional): Start of range (inclusive)
            end_id (optional): End of range (inclusive)

        Returns:
            {segments: [{id, source, target, status, type, notes, ...}]}
        """
        if not self.parent_app:
            raise ValueError("Parent app not available for segment access")

        if not hasattr(self.parent_app, 'current_project') or not self.parent_app.current_project:
            raise ValueError("No project currently loaded")

        project = self.parent_app.current_project
        if not hasattr(project, 'segments') or not project.segments:
            return {'segments': []}

        all_segments = project.segments

        # Determine which segments to retrieve
        segment_id = params.get('segment_id')
        segment_ids = params.get('segment_ids')
        start_id = params.get('start_id')
        end_id = params.get('end_id')

        target_segments = []

        if segment_id is not None:
            # Single segment by ID
            for seg in all_segments:
                if seg.id == segment_id:
                    target_segments.append(seg)
                    break
        elif segment_ids:
            # Multiple segments by IDs
            id_set = set(segment_ids)
            target_segments = [seg for seg in all_segments if seg.id in id_set]
        elif start_id is not None or end_id is not None:
            # Range of segments
            start = start_id if start_id is not None else 1
            end = end_id if end_id is not None else float('inf')
            target_segments = [seg for seg in all_segments if start <= seg.id <= end]
        else:
            # No filter specified - return all segments (limited to first 50)
            target_segments = all_segments[:50]

        # Convert segments to dictionaries
        segments_data = []
        for seg in target_segments:
            seg_dict = {
                'id': seg.id,
                'source': seg.source,
                'target': seg.target,
                'status': seg.status,
                'type': seg.type,
                'notes': seg.notes,
                'match_percent': seg.match_percent,
                'locked': seg.locked,
                'paragraph_id': seg.paragraph_id,
                'style': seg.style,
                'document_position': seg.document_position,
                'is_table_cell': seg.is_table_cell
            }
            segments_data.append(seg_dict)

        return {
            'segments': segments_data,
            'count': len(segments_data)
        }

    def _action_activate_prompt(self, params: Dict) -> Dict:
        """
        Activate/attach a prompt to the current project.

        Params:
            path (required): Path to the prompt to activate
            mode (optional): 'primary' or 'attach' (default: 'primary')

        Returns:
            {success: bool, message: str}
        """
        path = params.get('path')
        mode = params.get('mode', 'primary')

        if not path:
            raise ValueError("Missing required parameter: path")

        # Check if prompt exists
        if path not in self.prompt_library.prompts:
            raise ValueError(f"Prompt not found: {path}")

        if mode == 'primary':
            # Set as primary prompt
            self.prompt_library.set_primary_prompt(path)
            return {
                'success': True,
                'message': f"✓ Activated as primary prompt: {self.prompt_library.prompts[path].get('name', path)}"
            }
        elif mode == 'attach':
            # Attach as additional prompt
            if path not in self.prompt_library.attached_prompts:
                self.prompt_library.attached_prompts.append(path)
                self.prompt_library.save_active_state()
                return {
                    'success': True,
                    'message': f"✓ Attached prompt: {self.prompt_library.prompts[path].get('name', path)}"
                }
            else:
                return {
                    'success': False,
                    'message': f"Prompt already attached: {path}"
                }
        else:
            raise ValueError(f"Invalid mode: {mode}. Use 'primary' or 'attach'")

    def get_system_prompt_addition(self) -> str:
        """
        Get text to add to AI system prompt to enable action usage.

        Returns:
            String to append to AI system prompt
        """
        return """

## 🔧 Available Actions

You can interact with the Supervertaler Prompt Library using structured actions.
When you want to perform an action, use this format:

ACTION:function_name
PARAMS:{"param1": "value1", "param2": "value2"}

Available actions:

### 1. list_prompts
List all prompts, optionally filtered by folder.
PARAMS: {"folder": "Domain Expertise" (optional), "include_content": false (optional)}

### 2. get_prompt
Get full details of a specific prompt.
PARAMS: {"path": "Domain Expertise/Medical.md"}

### 3. create_prompt
Create a new prompt.
PARAMS: {
  "name": "Medical Translator",
  "content": "You are an expert medical translator...",
  "folder": "Domain Expertise" (optional),
  "description": "Expert medical translation" (optional),
}

### 4. update_prompt
Update an existing prompt.
PARAMS: {
  "path": "Domain Expertise/Medical.md",
  "content": "Updated content..." (optional),
  "name": "New name" (optional),
  "description": "New description" (optional)
}

### 5. delete_prompt
Delete a prompt.
PARAMS: {"path": "Domain Expertise/Medical.md"}

### 6. search_prompts
Search prompts by query.
PARAMS: {
  "query": "medical",
  "search_in": "all" (options: name, content, all)
}

### 7. create_folder
Create a new folder.
PARAMS: {"path": "Domain Expertise/Medical"}

### 8. toggle_quick_run
Toggle QuickLauncher status (legacy name: quick_run).
PARAMS: {"path": "Domain Expertise/Medical.md"}

### 9. get_quick_run
Get list of QuickLauncher prompts (legacy name: quick_run).
PARAMS: {}

### 10. get_folder_structure
Get complete folder structure.
PARAMS: {}

### 11. get_segment_count
Get total segment count and translation progress.
PARAMS: {}

### 12. get_segment_info
Get detailed information about specific segment(s).
PARAMS: {
  "segment_id": 5 (single segment) OR
  "segment_ids": [1, 5, 10] (multiple segments) OR
  "start_id": 1, "end_id": 10 (range of segments)
}

### 13. activate_prompt
Activate/attach a prompt to the current project.
PARAMS: {
  "path": "Domain Expertise/Medical.md",
  "mode": "primary" (or "attach")
}

**Important:**
- Actions are executed automatically when you include them in your response
- You'll see the results immediately
- You can include multiple actions in one response
- Always use valid JSON for PARAMS
- Wrap your normal conversational response around the actions
"""

    def format_action_results(self, action_results: List[Dict]) -> str:
        """
        Format action results for display in chat.

        Args:
            action_results: List of action result dictionaries

        Returns:
            Formatted string for display
        """
        if not action_results:
            return ""

        output = "\n\n**Action Results:**\n"

        for result in action_results:
            action_name = result['action']

            if result['success']:
                output += f"\n✓ **{action_name}**: {result['result'].get('message', 'Success')}\n"

                # Add additional details based on action type
                if action_name == 'list_prompts':
                    count = result['result']['count']
                    output += f"  Found {count} prompts\n"

                elif action_name == 'search_prompts':
                    count = result['result']['count']
                    output += f"  Found {count} matching prompts\n"
                    for match in result['result']['results'][:5]:  # Show first 5
                        output += f"  - {match['name']} ({match['folder']})\n"

                elif action_name == 'create_prompt':
                    output += f"  Path: {result['result']['path']}\n"

                elif action_name == 'activate_prompt':
                    # Just use the message from the result
                    pass

                elif action_name == 'get_segment_count':
                    total = result['result']['total_segments']
                    translated = result['result']['translated']
                    untranslated = result['result']['untranslated']
                    output += f"  Total segments: {total}\n"
                    output += f"  Translated: {translated}\n"
                    output += f"  Untranslated: {untranslated}\n"

                elif action_name == 'get_segment_info':
                    segments = result['result']['segments']
                    count = result['result']['count']
                    output += f"  Retrieved {count} segment(s)\n\n"
                    for seg in segments:
                        output += f"  **Segment {seg['id']}:**\n"
                        # Escape HTML entities for display
                        # Order matters: & must be first to avoid double-escaping
                        source = (seg['source']
                                  .replace('&', '&amp;')
                                  .replace('<', '&lt;')
                                  .replace('>', '&gt;')
                                  .replace('"', '&quot;'))
                        output += f"  Source: `{source}`\n"
                        if seg['target']:
                            target = (seg['target']
                                      .replace('&', '&amp;')
                                      .replace('<', '&lt;')
                                      .replace('>', '&gt;')
                                      .replace('"', '&quot;'))
                            output += f"  Target: `{target}`\n"
                        output += f"  Status: {seg['status']}\n"
                        if seg['notes']:
                            output += f"  Notes: {seg['notes']}\n"
                        output += "\n"
            else:
                output += f"\n✗ **{action_name}**: {result['error']}\n"
                # Show hint for common errors
                if 'Missing required parameters' in result['error']:
                    output += "  _Hint: The AI may not have generated the correct format. Try clicking the button again._\n"

        return output
