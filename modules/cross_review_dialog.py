"""
Cross-model review dialog (issue #242, tier 2)
==============================================

QA → Proofreading → 🔀 Cross-model Review…, and optionally straight after an
AI batch translation.

A second AI model checks the translations against their source and against
the instructions the translating model followed (the project's prompt) and
the terms of the project's glossaries. Its flags become proofreading comments
keyed ``"XR · <model>"`` (see ``modules/cross_review.py``); a segment that
now passes loses an earlier flag from the same reviewer. A Markdown record of
the review goes to the project's ``reports/cross-review/`` folder.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from modules import cross_review
from modules.duet_dialog import available_providers, default_model, make_ask, model_picker
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton

SETTINGS_KEY = "cross_review"
DONE_STATUSES = ("confirmed", "proofread", "approved")


def load_settings(app) -> Dict:
    try:
        return dict(app._load_general_settings_from_file().get(SETTINGS_KEY) or {})
    except Exception:
        return {}


def save_settings(app, values: Dict) -> None:
    try:
        general = app._load_general_settings_from_file()
        merged = dict(general.get(SETTINGS_KEY) or {})
        merged.update(values)
        general[SETTINGS_KEY] = merged
        app.save_general_settings(general)
    except Exception:
        pass


def project_instructions(app) -> Tuple[str, str]:
    """``(name, text)`` of the prompt the project translates with: the custom
    prompt plus its attached prompts, as the translating model got them."""
    lib = getattr(getattr(app, 'prompt_manager_qt', None), 'library', None)
    if lib is None:
        return "", ""
    parts = []
    if getattr(lib, 'active_primary_prompt', None):
        parts.append(lib.active_primary_prompt)
    attached = [a for a in (getattr(lib, 'attached_prompts', None) or []) if a]
    parts.extend(attached)
    path = getattr(lib, 'active_primary_prompt_path', None) or ""
    name = Path(path.replace("[EXTERNAL] ", "")).stem if path else ""
    if attached:
        name = f"{name or 'no custom prompt'} + {len(attached)} attached"
    return name, "\n\n".join(parts)


def project_terms(app, limit: int = 5000) -> List[Dict]:
    """The terms of the glossaries switched on for the project – the approved
    terminology, whether or not a glossary is also injected into AI prompts.
    Forbidden terms are kept (and marked), so the reviewer can flag them."""
    mgr = getattr(app, 'termbase_mgr', None)
    proj = getattr(app, 'current_project', None)
    if not (mgr and proj is not None and getattr(proj, 'id', None)):
        return []
    out = []
    try:
        for tb_id in mgr.get_active_termbase_ids(proj.id) or []:
            for t in mgr.get_terms(tb_id) or []:
                s, tt = (t.get('source_term') or '').strip(), (t.get('target_term') or '').strip()
                if s and tt:
                    out.append({'source_term': s, 'target_term': tt, 'forbidden': bool(t.get('forbidden'))})
                if len(out) >= limit:
                    return out
    except Exception:
        pass
    return out


def apply_answers(segments_by_id: Dict, answers: Dict, key: str) -> List:
    """Store flags under ``key``; drop an earlier flag under ``key`` from a
    segment that now passes. Returns the segments that changed."""
    changed = []
    for seg_id, (verdict, flag) in answers.items():
        seg = segments_by_id.get(seg_id)
        if seg is None:
            continue
        notes = seg.proofreading_notes if isinstance(getattr(seg, 'proofreading_notes', None), dict) else {}
        if verdict == "flag" and flag:
            if notes.get(key) == flag:
                continue
            notes[key] = flag
        elif key in notes:
            del notes[key]
        else:
            continue
        seg.proofreading_notes = notes
        changed.append(seg)
    return changed


class ReviewWorker(QThread):
    batch_done = pyqtSignal(object, int, int)
    retrying = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, kwargs: Dict):
        super().__init__()
        self._kwargs, self._stop = kwargs, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            result = cross_review.run_review(
                on_batch=lambda answers, done, total: self.batch_done.emit(answers, done, total),
                should_stop=lambda: self._stop,
                on_retry=lambda n, e: self.retrying.emit(n, str(e)),
                **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class CrossReviewDialog(QDialog):
    """Have a second model review translations.

    ``rows``: ``(row, segment)`` pairs to review (after a batch translation);
    None lets the user choose. ``translator``: ``(provider, model)`` that
    translated them, if known. ``autostart`` starts the review at once.
    """

    def __init__(self, parent, app, rows: Optional[Sequence[Tuple[int, object]]] = None,
                 translator: Optional[Tuple[str, str]] = None, autostart: bool = False):
        super().__init__(parent)
        self.app = app
        self.project = getattr(app, 'current_project', None)
        self.fixed_rows = list(rows) if rows is not None else None
        self._settings = app.load_llm_settings() if hasattr(app, 'load_llm_settings') else {}
        # Who translated: known after a batch translation, otherwise the model
        # set in AI Settings is the best guess
        self.translator_known = translator is not None
        if translator is None:
            provider = self._settings.get('provider', '')
            translator = (provider, default_model(app, self._settings, provider) if provider else "")
        self.translator = translator
        self.saved = load_settings(app)
        self.worker: Optional[ReviewWorker] = None
        self.result: Optional[cross_review.ReviewResult] = None
        self.report_path: Optional[str] = None
        self.setWindowTitle("Cross-model review")
        self.resize(760, 640)
        self._build()
        self._update_estimate()
        if autostart and self.start_btn.isEnabled():
            self.start()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        intro = QLabel(
            "A second AI model checks the translations against their source and against the "
            "instructions the translating model followed. It flags problems but never changes a "
            "translation. Its flags appear in the <b>Proofreading comments</b> tab as <b>XR</b>, "
            "apart from ordinary proofreading and from the translator's own ⟦TC⟧ comments. "
            "A different model works best: it doesn't share the translator's blind spots.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        segments = list(self.project.segments) if self.project else []
        translated = [s for s in segments if (s.target or '').strip()]
        scope = QGroupBox("Segments")
        sv = QVBoxLayout(scope)
        self.scope_group = QButtonGroup(self)
        if self.fixed_rows is not None:
            n = sum(1 for _r, s in self.fixed_rows if (s.target or '').strip())
            sv.addWidget(QLabel(f"The {n} segment(s) just translated."))
        else:
            selected_rows = set()
            table = getattr(self.app, 'table', None)
            if table is not None:
                selected_rows = {i.row() for i in table.selectedItems()}
            id_by_row = {}
            if selected_rows and hasattr(self.app, '_rows_by_segment_id'):
                id_by_row = {row: sid for sid, row in self.app._rows_by_segment_id().items()}
            selected_ids = {id_by_row[r] for r in selected_rows if r in id_by_row}
            self._scopes = [
                ("Translations not yet confirmed",
                 [s for s in translated if s.status not in DONE_STATUSES]),
                ("All translations", translated),
                ("Selected segments", [s for s in translated if s.id in selected_ids]),
            ]
            for i, (label, segs) in enumerate(self._scopes):
                rb = CheckmarkRadioButton(f"{label} ({len(segs)})")
                rb.setEnabled(bool(segs))
                self.scope_group.addButton(rb, i)
                sv.addWidget(rb)
            first = next((i for i, (_l, segs) in enumerate(self._scopes) if segs), 0)
            self.scope_group.button(first).setChecked(True)
            self.scope_group.idClicked.connect(lambda _i: self._update_estimate())
        v.addWidget(scope)

        keys = self.app.load_api_keys() if hasattr(self.app, 'load_api_keys') else {}
        enabled = self.app.load_provider_enabled_states() if hasattr(self.app, 'load_provider_enabled_states') else {}
        self.providers = available_providers(keys, enabled)
        models = QGroupBox("Models")
        form = QFormLayout(models)
        t_provider, t_model = self.translator
        t_label = dict(self.providers).get(t_provider, t_provider or "?")
        form.addRow("Translated by:" if self.translator_known else "Translating model (AI Settings):",
                    QLabel(f"{t_label} ({t_model or '?'})"))
        preferred = self.saved.get('provider', '')
        w, self.reviewer_combo, self.reviewer_model = model_picker(
            self.providers, lambda p: default_model(self.app, self._settings, p),
            preferred=preferred, avoid=t_provider)
        saved_model = self.saved.get('model')
        if saved_model and self.reviewer_combo.currentData() == preferred:
            self.reviewer_model.setText(saved_model)
        form.addRow("Reviewed by:", w)
        self.same_model_label = QLabel("")
        self.same_model_label.setWordWrap(True)
        self.same_model_label.setVisible(False)
        form.addRow(self.same_model_label)
        v.addWidget(models)

        context = QGroupBox("The reviewer also gets")
        cv = QVBoxLayout(context)
        self.prompt_name, self.instructions = project_instructions(self.app)
        self.use_prompt = CheckmarkCheckBox(
            f"The project's translation prompt: {self.prompt_name}" if self.instructions
            else "The project's translation prompt (none set – the default prompt was used)")
        self.use_prompt.setEnabled(bool(self.instructions))
        self.use_prompt.setChecked(bool(self.instructions) and self.saved.get('use_prompt', True))
        self.terms = project_terms(self.app)
        self.use_glossary = CheckmarkCheckBox(
            f"The glossary terms found in the segments ({len(self.terms)} terms in the project's glossaries)"
            if self.terms else "Glossary terms (no glossary is switched on for this project)")
        self.use_glossary.setEnabled(bool(self.terms))
        self.use_glossary.setChecked(bool(self.terms) and self.saved.get('use_glossary', True))
        cv.addWidget(self.use_prompt)
        cv.addWidget(self.use_glossary)
        v.addWidget(context)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        v.addWidget(self.estimate_label)
        for cb in (self.use_prompt, self.use_glossary):
            cb.toggled.connect(lambda _c: self._update_estimate())
        self.reviewer_model.textChanged.connect(lambda _t: self._update_estimate())

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        v.addStretch()

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start review")
        self.start_btn.setDefault(True)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.report_btn = QPushButton("📄 Open report")
        self.report_btn.setEnabled(False)
        self.close_btn = QPushButton("Close")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.report_btn.clicked.connect(self._open_report)
        self.close_btn.clicked.connect(self.reject)
        for b in (self.start_btn, self.stop_btn, self.report_btn):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.close_btn)
        v.addLayout(buttons)

        if not self.providers:
            self.status.setText("No AI provider has an API key yet – add one in Settings → 🤖 AI Settings.")
            self.start_btn.setEnabled(False)
        elif not self._segments():
            self.status.setText("There are no translations to review.")
            self.start_btn.setEnabled(False)

    # ------------------------------------------------------------ helpers
    def _segments(self) -> List:
        if self.fixed_rows is not None:
            segs = [s for _r, s in self.fixed_rows]
        elif getattr(self, '_scopes', None):
            segs = self._scopes[max(self.scope_group.checkedId(), 0)][1]
        else:
            segs = []
        return [s for s in segs if (s.target or '').strip() and (s.source or '').strip()]

    def _items(self) -> List[cross_review.Item]:
        return [cross_review.Item(s.id, s.source, s.target) for s in self._segments()]

    def _reviewer(self) -> Tuple[str, str, str]:
        provider = self.reviewer_combo.currentData() or ""
        model = self.reviewer_model.text().strip()
        return provider, model, f"{self.reviewer_combo.currentText()} ({model})"

    def _update_estimate(self):
        provider, model, _label = self._reviewer()
        same = (provider, model) == tuple(self.translator)
        self.same_model_label.setText(
            "⚠️ This is the model that translated. A different model catches more." if same else "")
        self.same_model_label.setVisible(same)
        try:
            from modules.llm_pricing import estimate_cost
            from modules.cost_estimate import currency_settings, format_cost
            items = self._items()
            terms_chars = sum(len(t.get('source_term') or '') + len(t.get('target_term') or '') + 6
                              for t in self.terms) if self.use_glossary.isChecked() else 0
            est = cross_review.estimate(items, self.instructions if self.use_prompt.isChecked() else "",
                                        terms_chars)
            cost = estimate_cost(provider, model, est['input_tokens'], est['output_tokens'])
            gs = self.app._load_general_settings_from_file() if hasattr(self.app, '_load_general_settings_from_file') else {}
            self.estimate_label.setText(
                f"{len(items)} segment(s) in {est['calls']} call(s): ~{est['input_tokens']:,} input and "
                f"~{est['output_tokens']:,} output tokens, about <b>{format_cost(cost, *currency_settings(gs))}</b>. "
                f"The prompt is sent again with every batch of 20 segments.")
        except Exception as exc:
            self.estimate_label.setText(f"(No estimate: {exc})")

    def _report_file(self) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        project_path = getattr(self.app, 'project_file_path', None)
        if project_path:
            folder = os.path.join(os.path.dirname(os.path.abspath(project_path)), "reports", "cross-review")
        else:
            base = getattr(self.app, 'user_data_path', None) or os.path.expanduser("~")
            folder = os.path.join(str(base), "workbench", "cross-review")
        os.makedirs(folder, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", getattr(self.project, 'name', '') or "project").strip(" .")
        return os.path.join(folder, f"{name or 'project'} – {stamp}.md")

    # ---------------------------------------------------------------- run
    def start(self):
        provider, model, label = self._reviewer()
        if not model:
            QMessageBox.warning(self, "Cross-model review", "Enter the reviewer's model.")
            return
        try:
            ask = make_ask(self.app, self._settings, provider, model)
        except Exception as exc:
            QMessageBox.warning(self, "Cross-model review", f"Could not set up the reviewer:\n\n{exc}")
            return
        values = {'provider': provider, 'model': model}
        for key, box in (('use_prompt', self.use_prompt), ('use_glossary', self.use_glossary)):
            if box.isEnabled():  # a greyed-out box says nothing about the user's choice
                values[key] = box.isChecked()
        save_settings(self.app, values)
        self.items = self._items()
        self.key = cross_review.note_key(cross_review.XR, model)
        self.reviewer_label = label
        self._by_id = {s.id: s for s in self._segments()}
        self._rows = self.app._rows_by_segment_id() if hasattr(self.app, '_rows_by_segment_id') else {}
        filter_terms = None
        try:
            from modules.unified_prompt_manager_qt import filter_relevant_glossary_terms as filter_terms
        except Exception:
            pass
        self.progress.setRange(0, len(self.items))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.close_btn.setEnabled(False)
        self.status.setText(f"Reviewing with {label}…")
        self.worker = ReviewWorker(dict(
            items=self.items, ask=ask,
            source_lang=getattr(self.project, 'source_lang', ''),
            target_lang=getattr(self.project, 'target_lang', ''),
            instructions=self.instructions if self.use_prompt.isChecked() else "",
            terms=self.terms if self.use_glossary.isChecked() else [],
            filter_terms=filter_terms))
        self.worker.batch_done.connect(self._on_batch)
        self.worker.retrying.connect(lambda n, e: self.status.setText(f"API error ({e}); retry {n} of 2…"))
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.status.setText("Stopping after the current batch…")

    def _on_batch(self, answers, done, total):
        changed = apply_answers(self._by_id, answers, self.key)
        for seg in changed:
            row = self._rows.get(seg.id)
            if row is not None and hasattr(self.app, '_update_status_cell'):
                try:
                    self.app._update_status_cell(row, seg)
                except Exception:
                    pass
        if changed:
            self.app.project_modified = True
        self.progress.setValue(done)
        flagged = sum(1 for v, _f in answers.values() if v == "flag")
        self.status.setText(f"Reviewed {done} of {total} segment(s)… ({flagged} flagged in the last batch)")

    def _finish(self, result: Optional[cross_review.ReviewResult], error: str = ""):
        self.stop_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        if result is not None:
            try:
                self.report_path = self._report_file()
                t_provider, t_model = self.translator
                t_label = dict(self.providers).get(t_provider, t_provider)
                cross_review.write_report(
                    self.report_path, result, self.items, reviewer=self.reviewer_label,
                    translator=(f"{t_label} ({t_model})" + ("" if self.translator_known else ", per AI Settings"))
                    if t_model else "",
                    prompt_name=self.prompt_name if self.use_prompt.isChecked() else "",
                    project_name=getattr(self.project, 'name', ''))
                self.report_btn.setEnabled(True)
            except OSError as exc:
                self.report_path = None
                error = error or f"(the report could not be written: {exc})"
        # Show the XR flags in the Proofreading comments tab
        for hook, args in (('_set_proofreading_origin_filter', (cross_review.XR,)),
                           ('update_window_title', ())):
            fn = getattr(self.app, hook, None)
            if callable(fn):
                try:
                    fn(*args)
                except Exception:
                    pass
        if hasattr(self.app, 'log') and result is not None:
            self.app.log(f"🔀 Cross-model review by {self.reviewer_label}: {len(result.flags)} flagged, "
                         f"{len(result.passed)} passed" + (f", {len(result.missing)} not answered"
                                                           if result.missing else ""))
        return error

    def _on_finished(self, result):
        self.result = result
        self._finish(result)
        parts = [f"{len(result.flags)} flagged", f"{len(result.passed)} passed"]
        if result.missing:
            parts.append(f"{len(result.missing)} not answered by the reviewer")
        head = "Stopped. " if result.stopped else "Done: "
        self.status.setText(head + ", ".join(parts) + ". The flags are in the Proofreading comments "
                            "tab, shown as XR. Nothing in your translations was changed.")

    def _on_failed(self, message):
        self.status.setText(f"The review stopped: {message}. Flags received so far are kept.")
        self._finish(None, message)

    def _open_report(self):
        if self.report_path and os.path.exists(self.report_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report_path))

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.stop()
            return
        super().reject()
