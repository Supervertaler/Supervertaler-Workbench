"""Selecting a saved model in a Settings model list.

Moved out of Supervertaler.py unchanged (Nov 2026), so the Settings code in
modules/main_window/ can use it too.
"""


def _select_saved_model(combo, model_id):
    """Select the saved model in a Settings model list whose entries read
    "<model id> (<label>)". The match is exact: a substring test found
    "anthropic/claude-sonnet-5" inside "anthropic/claude-sonnet-5.5". A
    saved model that has left the list is added as "<model id> (your
    current model)" and selected, because saving Settings writes back
    whatever the list shows: falling back to the first entry switched
    the user to another model without a word."""
    for i in range(combo.count()):
        if (combo.itemText(i).split() or [''])[0] == model_id:
            combo.setCurrentIndex(i)
            return
    if model_id:
        combo.addItem(f"{model_id} (your current model)")
        combo.setCurrentIndex(combo.count() - 1)
