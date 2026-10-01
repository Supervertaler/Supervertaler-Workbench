"""
Segment arbitration dialog (issue #242, tier 3)
===============================================

Grid → right-click a segment → 🎭 Arbitrate This Segment…

Two different AI models debate one contested translation under the Duet
protocol (``modules/duet.py``, ARBITRATION_PROTOCOL): the source, its
neighbours, the project's prompt, the glossary terms and TM matches for the
segment, and any review comments on it are attached. When they agree – or
at the round limit – one writes the final translation, with anything still
disputed listed for the translator. Nothing changes until the translator
clicks "Use this translation" (undoable with Ctrl+Z). The transcript goes to
the project's ``reports/arbitration/`` folder. Deliberately one segment at a
time: never run wholesale.
"""

import os
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from modules import duet
from modules.cross_review_dialog import load_settings, project_instructions, project_terms
from modules.duet_dialog import DuetWorker, _html, available_providers, default_model, make_ask, model_picker


def segment_material(app, segment) -> dict:
    """What the two models check the translation against."""
    proj = getattr(app, 'current_project', None)
    segs = list(app._segments_in_document_order()) if hasattr(app, '_segments_in_document_order') else \
        list(getattr(proj, 'segments', []) or [])
    idx = next((i for i, s in enumerate(segs) if s.id == segment.id), -1)
    context = [((s.source or '').strip(), (s.target or '').strip())
               for s in segs[max(idx - 2, 0):idx] + segs[idx + 1:idx + 3]
               if idx >= 0 and (s.source or '').strip()]
    _name, instructions = project_instructions(app)
    terms = project_terms(app)
    try:
        from modules.unified_prompt_manager_qt import filter_relevant_glossary_terms
        terms = filter_relevant_glossary_terms(terms, segment.source)
    except Exception:
        terms = [t for t in terms if t['source_term'].lower() in (segment.source or '').lower()]
    term_pairs = [(t['source_term'], t['target_term'] + (" (forbidden – do not use)" if t.get('forbidden') else ""))
                  for t in terms[:40]]
    tm = []
    try:
        tm_ids = app.tm_metadata_mgr.get_active_tm_ids(proj.id)
        if tm_ids:
            for m in app.db_manager.search_fuzzy_matches(
                    segment.source, tm_ids=tm_ids, threshold=0.7, max_results=3,
                    source_lang=proj.source_lang, target_lang=proj.target_lang) or []:
                pct = int(m.get('match_pct') or round(float(m.get('similarity') or 0) * 100))
                tm.append((m['source_text'], m['target_text'], pct))
    except Exception:
        pass
    notes = getattr(segment, 'proofreading_notes', None) or {}
    comments = [f"{k}: {v}" for k, v in notes.items() if str(v).strip()]
    return {"context": context, "instructions": instructions, "terms": term_pairs, "tm": tm,
            "comments": comments}


class ArbitrationDialog(QDialog):
    """Have two models settle the translation of one segment."""

    def __init__(self, parent, app, segment):
        super().__init__(parent)
        self.app, self.segment = app, segment
        self.project = getattr(app, 'current_project', None)
        self._settings = app.load_llm_settings() if hasattr(app, 'load_llm_settings') else {}
        self.worker: Optional[DuetWorker] = None
        self.result: Optional[duet.DuetResult] = None
        self.transcript_path: Optional[str] = None
        self.applied = False
        self.material = segment_material(app, segment)
        self.setWindowTitle(f"Arbitrate segment {segment.id}")
        self.resize(900, 780)
        self._build()
        self._update_estimate()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        intro = QLabel(
            "Two different AI models debate this translation. Each must check the other's claims "
            "against the source, the project's prompt, glossary and TM, and keep a register of open "
            "issues. When they agree, or at the round limit, one writes the final translation, with "
            "anything still disputed listed for you. Nothing changes until you click "
            "<b>Use this translation</b>.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        seg_box = QGroupBox(f"Segment {self.segment.id}")
        sf = QFormLayout(seg_box)
        for label, text in (("Source:", self.segment.source), ("Translation:", self.segment.target)):
            lab = QLabel(text or "")
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            sf.addRow(label, lab)
        self.question_edit = QPlainTextEdit()
        self.question_edit.setPlaceholderText("Optional: what is contested, e.g. which term is right here.")
        self.question_edit.setPlainText("\n".join(self.material["comments"]))
        self.question_edit.setMaximumHeight(70)
        sf.addRow("What's contested:", self.question_edit)
        v.addWidget(seg_box)

        keys = self.app.load_api_keys() if hasattr(self.app, 'load_api_keys') else {}
        enabled = self.app.load_provider_enabled_states() if hasattr(self.app, 'load_provider_enabled_states') else {}
        self.providers = available_providers(keys, enabled)
        model_for = lambda p: default_model(self.app, self._settings, p)
        models = QGroupBox("Models")
        form = QFormLayout(models)
        translator = self._settings.get('provider', '')
        w_a, self.combo_a, self.model_a = model_picker(self.providers, model_for, preferred=translator)
        reviewer = load_settings(self.app)
        w_b, self.combo_b, self.model_b = model_picker(self.providers, model_for,
                                                       preferred=reviewer.get('provider', ''),
                                                       fallback_index=1, avoid=self.combo_a.currentData() or "")
        if reviewer.get('model') and self.combo_b.currentData() == reviewer.get('provider'):
            self.model_b.setText(reviewer['model'])
        form.addRow("Model A (opens):", w_a)
        form.addRow("Model B (writes the result):", w_b)
        opts = QHBoxLayout()
        self.rounds_spin = QSpinBox(); self.rounds_spin.setRange(1, 6); self.rounds_spin.setValue(3)
        self.tokens_spin = QSpinBox(); self.tokens_spin.setRange(300, 8000)
        self.tokens_spin.setSingleStep(100); self.tokens_spin.setValue(1500)
        opts.addWidget(QLabel("Round limit:")); opts.addWidget(self.rounds_spin)
        opts.addWidget(QLabel("Max output tokens per turn:")); opts.addWidget(self.tokens_spin)
        opts.addStretch()
        ow = QWidget(); ow.setLayout(opts); opts.setContentsMargins(0, 0, 0, 0)
        form.addRow(ow)
        v.addWidget(models)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        v.addWidget(self.estimate_label)
        for wdg in (self.rounds_spin, self.tokens_spin):
            wdg.valueChanged.connect(self._update_estimate)
        for wdg in (self.model_a, self.model_b):
            wdg.textChanged.connect(self._update_estimate)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start")
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        buttons.addWidget(self.start_btn); buttons.addWidget(self.stop_btn)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        v.addLayout(buttons)

        split = QSplitter(Qt.Orientation.Vertical)
        self.transcript_view = QPlainTextEdit(); self.transcript_view.setReadOnly(True)
        self.transcript_view.setPlaceholderText("The debate appears here turn by turn.")
        split.addWidget(self.transcript_view)
        result_box = QWidget(); rv = QVBoxLayout(result_box); rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("<b>Final translation</b> (you can edit it before using it):"))
        self.result_edit = QPlainTextEdit()
        self.result_edit.setMaximumHeight(110)
        rv.addWidget(self.result_edit)
        self.unresolved_label = QLabel(""); self.unresolved_label.setWordWrap(True)
        self.unresolved_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rv.addWidget(self.unresolved_label)
        row = QHBoxLayout()
        self.use_btn = QPushButton("✔ Use this translation")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self.use_translation)
        self.open_transcript_btn = QPushButton("📄 Open transcript")
        self.open_transcript_btn.setEnabled(False)
        self.open_transcript_btn.clicked.connect(self._open_transcript)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        row.addWidget(self.use_btn); row.addWidget(self.open_transcript_btn); row.addStretch(); row.addWidget(close_btn)
        rv.addLayout(row)
        split.addWidget(result_box)
        split.setSizes([360, 220])
        v.addWidget(split, 1)

        if not self.providers:
            self.status.setText("No AI provider has an API key yet – add one in Settings → 🤖 AI Settings.")
            self.start_btn.setEnabled(False)

    # ------------------------------------------------------------ helpers
    def _brief(self) -> str:
        m = self.material
        return duet.build_segment_brief(
            self.segment.source, self.segment.target,
            source_lang=getattr(self.project, 'source_lang', ''),
            target_lang=getattr(self.project, 'target_lang', ''),
            question=self.question_edit.toPlainText(), context=m["context"],
            instructions=m["instructions"], terms=m["terms"], tm=m["tm"])

    def _participants(self, with_clients=False) -> List[duet.Participant]:
        out = []
        for combo, edit in ((self.combo_a, self.model_a), (self.combo_b, self.model_b)):
            provider, model = combo.currentData() or "", edit.text().strip()
            ask = make_ask(self.app, self._settings, provider, model) if with_clients else None
            out.append(duet.Participant(f"{combo.currentText()} ({model})", provider, model, ask))
        return out

    def _update_estimate(self):
        try:
            from modules.llm_pricing import estimate_cost
            from modules.cost_estimate import currency_settings, format_cost
            brief_chars = len(self._brief())
            a, b = self._participants()
            est = duet.estimate_cost(a, b, brief_chars, self.rounds_spin.value(), self.tokens_spin.value(),
                                     "A", "B", price=estimate_cost, avg_output_tokens=500,
                                     protocol=duet.ARBITRATION_PROTOCOL)
            gs = self.app._load_general_settings_from_file() if hasattr(self.app, '_load_general_settings_from_file') else {}
            self.estimate_label.setText(
                f"If all {self.rounds_spin.value()} rounds run: up to {est['turns']} calls, "
                f"~{est['input_tokens']:,} input and ~{est['output_tokens']:,} output tokens, "
                f"about <b>{format_cost(est['cost'], *currency_settings(gs))}</b>.")
        except Exception as exc:
            self.estimate_label.setText(f"(No estimate: {exc})")

    def _transcript_file(self) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        project_path = getattr(self.app, 'project_file_path', None)
        if project_path:
            folder = os.path.join(os.path.dirname(os.path.abspath(project_path)), "reports", "arbitration")
        else:
            base = getattr(self.app, 'user_data_path', None) or os.path.expanduser("~")
            folder = os.path.join(str(base), "workbench", "arbitration")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"Segment {self.segment.id} – {stamp}.md")

    # ---------------------------------------------------------------- run
    def start(self):
        a, b = self._participants()
        if not a.model or not b.model:
            QMessageBox.warning(self, "Arbitrate segment", "Enter a model for both A and B.")
            return
        if (a.provider, a.model) == (b.provider, b.model):
            reply = QMessageBox.question(
                self, "Arbitrate segment",
                "Both models are the same. Arbitration works best with two different models. Start anyway?")
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            a, b = self._participants(with_clients=True)
        except Exception as exc:
            QMessageBox.warning(self, "Arbitrate segment", f"Could not set up the models:\n\n{exc}")
            return
        self.transcript_path = self._transcript_file()
        self.transcript_view.clear()
        self.result_edit.clear()
        self.unresolved_label.clear()
        self.use_btn.setEnabled(False)
        self.open_transcript_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText(f"Round 1 – waiting for {a.label}…")
        self.worker = DuetWorker(duet.run_duet, dict(
            a=a, b=b, brief=self._brief(), max_rounds=self.rounds_spin.value(),
            max_tokens=self.tokens_spin.value(), opener="A", synthesiser="B",
            transcript_path=self.transcript_path, protocol=duet.ARBITRATION_PROTOCOL,
            synthesis=duet.ARBITRATION_SYNTHESIS, title=f"Arbitration of segment {self.segment.id}",
            synthesis_tokens=2000))
        self.worker.turn_done.connect(self._on_turn)
        self.worker.retrying.connect(lambda n, e: self.status.setText(f"API error ({e}); retry {n} of 2…"))
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("Stopping after the current turn…")
            self.stop_btn.setEnabled(False)

    def _on_turn(self, turn):
        title = (f"=== Final translation – {turn.label} ===" if turn.speaker == "synthesis"
                 else f"=== Round {turn.round} – {turn.label} ===")
        self.transcript_view.appendPlainText(f"{title}\n{turn.text.strip()}\n")
        if turn.speaker != "synthesis":
            nxt = "writing the result" if turn.verdict == "AGREED" else "next turn"
            self.status.setText(f"Round {turn.round}: {turn.label} – {turn.verdict or 'no verdict'}; {nxt}…")

    def _on_finished(self, result):
        self.result = result
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if result.stopped:
            self.status.setText("Stopped. The transcript so far is saved.")
            return
        final = result.deliverable.strip()
        same = final == (self.segment.target or "").strip()
        outcome = (f"Both models agreed after {result.rounds} round(s)" if result.consensus
                   else f"No agreement after {result.rounds} round(s)")
        self.status.setText(outcome + ("; they kept the current translation." if same else "."))
        self.result_edit.setPlainText(final)
        unresolved = result.unresolved.strip()
        self.unresolved_label.setText(
            f"<b>Unresolved (for you to decide):</b><br>{_html(unresolved)}" if unresolved
            and unresolved.lower().rstrip('.') != 'none' else "<b>Unresolved:</b> none.")
        self.use_btn.setEnabled(bool(final) and not same)

    def _on_failed(self, message):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText(f"The arbitration stopped: {message}. The transcript so far is saved.")

    def _open_transcript(self):
        if self.transcript_path and os.path.exists(self.transcript_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.transcript_path))

    def use_translation(self) -> bool:
        text = self.result_edit.toPlainText().strip()
        segs = getattr(self.project, 'segments', None) or []
        index = next((i for i, s in enumerate(segs) if s is self.segment), -1)
        if not text or index < 0 or not hasattr(self.app, '_set_target_from_tool'):
            return False
        self.app._set_target_from_tool(index, text)
        self.applied = True
        self.use_btn.setEnabled(False)
        self.status.setText("The translation was updated (Ctrl+Z undoes it). Its review comments are "
                            "still there: delete them in the Proofreading comments tab once settled.")
        if self.transcript_path:
            try:
                with open(self.transcript_path, "a", encoding="utf-8") as f:
                    f.write(f"\n_Used as the translation of segment {self.segment.id}:_\n\n{text}\n")
            except OSError:
                pass
        return True

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.stop()
            return
        super().reject()
