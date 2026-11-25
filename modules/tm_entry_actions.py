"""Editing and deleting a TM entry from a match.

The Match Panel, a match card and the compare boxes each had their own copy of
this dialog and its save. All three locked the source, and none told the main
window's match cache (translation_matches_cache: segment id -> the matches
looked up for it), so going back to a segment showed, and inserted, the entry
as it was before the edit.

`window` is the main window: it has the TM (tm_manager or db_manager), the
cache and its lock, and log().
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox,
                             QTextEdit, QVBoxLayout)


def ask_for_edit(parent, tm_name: str, source: str, target: str) -> Optional[Tuple[str, str]]:
    """Show the dialog. The new (source, target), or None if cancelled."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Edit TM Entry – {tm_name}")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(8)

    layout.addWidget(QLabel("Source:"))
    source_edit = QTextEdit()
    source_edit.setAcceptRichText(False)
    source_edit.setPlainText(source)
    source_edit.setMaximumHeight(120)
    layout.addWidget(source_edit)

    layout.addWidget(QLabel("Target:"))
    target_edit = QTextEdit()
    target_edit.setAcceptRichText(False)
    target_edit.setPlainText(target)
    target_edit.setMaximumHeight(120)
    layout.addWidget(target_edit)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

    def accept():
        if not source_edit.toPlainText().strip() or not target_edit.toPlainText().strip():
            QMessageBox.warning(dialog, "Cannot Save",
                                "A TM entry needs both a source and a target.\n"
                                "To remove it, use Delete TM Entry.")
            return
        dialog.accept()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    target_edit.setFocus()

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return source_edit.toPlainText().strip(), target_edit.toPlainText().strip()


def _store(window, method: str):
    for name in ('tm_manager', 'db_manager'):
        store = getattr(window, name, None)
        if store is not None and hasattr(store, method):
            return store
    return None


def _field(match, name: str) -> str:
    if isinstance(match, dict):
        return match.get(name) or ''
    if name == 'tm_id':
        return (getattr(match, 'metadata', None) or {}).get('tm_id') or ''
    return getattr(match, name, '') or ''


def forget_cached_matches(window, tm_id: str, source: str, target: str,
                          everything: bool = False) -> int:
    """Drop the cached matches that show this entry, so they are looked up again.

    Only the segments whose cached TM matches hold the entry (either way round:
    a reversed match shows it swapped) lose their cache; the rest stay, so
    moving around stays fast. `everything` clears the lot: after a source edit
    any segment may match the entry differently. One pass over the cached
    segments per edit.
    """
    cache = getattr(window, 'translation_matches_cache', None)
    lock = getattr(window, 'translation_matches_cache_lock', None)
    dropped = 0
    if cache and lock is not None:
        pair = (source.strip(), target.strip())
        with lock:
            if everything:
                dropped = len(cache)
                cache.clear()
            else:
                stale = [
                    seg_id for seg_id, entry in cache.items()
                    if any(_field(m, 'tm_id') == tm_id
                           and (_field(m, 'source').strip(), _field(m, 'target').strip())
                           in (pair, pair[::-1])
                           for m in (entry.get("TM") or []))
                ]
                for seg_id in stale:
                    del cache[seg_id]
                dropped = len(stale)
    # The grid skips a lookup for the row and source it showed last.
    for guard in ('_last_selected_row', '_instant_tm_guard'):
        if hasattr(window, guard):
            setattr(window, guard, None)
    return dropped


def edit_tm_entry(window, tm_id: str, tm_name: str, source: str, target: str,
                  reverse: bool = False) -> Optional[Tuple[str, str]]:
    """Ask for the change and save it. The new (source, target) as shown, or
    None if nothing was saved. `reverse`: the match shows the entry swapped
    (found in the other direction), so it is swapped back for the TM."""
    if not tm_id:
        QMessageBox.warning(window, "Cannot Edit",
                            "TM identifier is not available for this entry.")
        return None

    edited = ask_for_edit(window, tm_name, source, target)
    if edited is None or edited == (source.strip(), target.strip()):
        return None
    new_source, new_target = edited

    store = _store(window, 'update_entry')
    if store is None:
        QMessageBox.critical(window, "Error", "No TM manager available to save the change.")
        return None
    old_row, new_row = (source, target), (new_source, new_target)
    if reverse:
        old_row, new_row = old_row[::-1], new_row[::-1]
    try:
        updated = store.update_entry(tm_id, old_row[0], old_row[1], new_row[0], new_row[1])
    except Exception as e:
        QMessageBox.critical(window, "Error", f"Failed to update TM entry:\n{e}")
        return None
    if not updated:
        # What is on screen is not what the TM holds: have it looked up again.
        forget_cached_matches(window, tm_id, source, target)
        QMessageBox.warning(window, "Not Found",
                            "The entry could not be found in the database.\n"
                            "It may have already been changed or deleted.")
        return None

    if hasattr(window, 'log'):
        window.log(f"✏️ Updated TM entry in '{tm_name}'")
    forget_cached_matches(window, tm_id, source, target,
                          everything=new_source != source.strip())
    return new_source, new_target


def delete_tm_entry(window, tm_id: str, tm_name: str, source: str, target: str,
                    reverse: bool = False) -> bool:
    """Confirm and delete. True if the entry is gone from the TM."""
    if not tm_id:
        QMessageBox.warning(window, "Cannot Delete",
                            "TM identifier is not available for this entry.")
        return False

    reply = QMessageBox.question(
        window, "Confirm Deletion",
        f"Delete TM entry from '{tm_name}'?\n\n"
        f"Source: {source[:120]}\n"
        f"Target: {target[:120]}\n\n"
        "This action cannot be undone.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    if reply != QMessageBox.StandardButton.Yes:
        return False

    store = _store(window, 'delete_entry')
    if store is None:
        QMessageBox.critical(window, "Error", "No TM manager available to perform deletion.")
        return False
    row = (target, source) if reverse else (source, target)
    try:
        deleted = store.delete_entry(tm_id, row[0], row[1])
    except Exception as e:
        QMessageBox.critical(window, "Error", f"Failed to delete TM entry:\n{e}")
        return False
    if deleted is False:
        forget_cached_matches(window, tm_id, source, target)
        QMessageBox.warning(window, "Not Found",
                            "The entry could not be found in the database.\n"
                            "It may have already been changed or deleted.")
        return False

    if hasattr(window, 'log'):
        window.log(f"🗑️ Deleted TM entry from '{tm_name}'")
    forget_cached_matches(window, tm_id, source, target)
    return True
