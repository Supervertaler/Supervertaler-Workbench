"""Language lists for the import dialogs' language combo boxes.

Moved out of Supervertaler.py unchanged (Nov 2026), so the main window's
mixins in modules/main_window/ can use them too.
"""


def _import_language_pairs():
    """(English-name, code) pairs for the import dialogs — the full canonical
    Workbench set (sorted by name). Replaces an old hardcoded 12-language list
    that silently dropped any other language (e.g. Slovak) to English."""
    from modules import language_codes as _lc
    return _lc.available_pairs()


def _select_lang_in_combo(combo, value):
    """Select the combo entry for a language `value` (code, name or region
    variant, matched via base_code). If it is not in the list, add and select it,
    so a project's language is NEVER silently left on item 0 (the old
    'imports as English' bug)."""
    from modules import language_codes as _lc
    want = _lc.base_code(value)
    if not want:
        return
    # An exact locale first, so a zh-TW project picks Chinese (Traditional),
    # not the Simplified entry that shares its base code.
    for i in range(combo.count()):
        if _lc.same_language(combo.itemData(i), value, region=True):
            combo.setCurrentIndex(i)
            return
    for i in range(combo.count()):
        if _lc.base_code(combo.itemData(i)) == want:
            combo.setCurrentIndex(i)
            return
    combo.addItem(_lc.english_name(value) or str(value), want)
    combo.setCurrentIndex(combo.count() - 1)
