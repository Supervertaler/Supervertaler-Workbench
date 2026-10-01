"""
Duet review dialog (issue #242, tier 1)
=======================================

Prompt Manager → right-click a prompt → 🎭 Duet review…

Two AI models review the prompt together under the debate protocol in
``modules/duet.py``, with the project's languages, a source sample, confirmed
translations / TM pairs and glossary terms attached. The transcript is written
into the project's ``reports/duet/`` folder as the review runs, and the agreed
prompt can be saved to the library as a new version next to the original –
the original is never overwritten.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from modules import duet
from modules.styled_widgets import CheckmarkRadioButton

# Providers a Duet can use, in the order offered: (settings key, label)
PROVIDERS = [
    ("claude", "Claude"), ("openai", "OpenAI"), ("gemini", "Gemini"),
    ("mistral", "Mistral"), ("deepseek", "DeepSeek"), ("openrouter", "OpenRouter"),
    ("custom_openai", "Custom endpoint"), ("ollama", "Ollama (local)"),
]


def available_providers(api_keys: Dict, enabled: Dict) -> List[Tuple[str, str]]:
    """The providers that have a key (or need none) and aren't switched off."""
    out = []
    for key, label in PROVIDERS:
        if enabled.get(f"llm_{key}", True) is False:
            continue
        has_key = bool(api_keys.get(key) or (key == "gemini" and api_keys.get("google")))
        if has_key or key in ("ollama", "custom_openai"):
            out.append((key, label))
    return out


def build_material(project, segments, sample_size: int = 30, pairs: Optional[List[Tuple[str, str]]] = None,
                   terms: Optional[List[Tuple[str, str]]] = None, max_chars: int = 20000) -> str:
    """What both reviewers get to check claims against (the Workbench side of
    the MCP server's get_prompt_context): languages, a source sample,
    confirmed translations / TM pairs and glossary terms."""
    parts = []
    if project is not None:
        parts.append(f"## Project\n- Name: {getattr(project, 'name', '')}\n"
                     f"- Source language: {getattr(project, 'source_lang', '')}\n"
                     f"- Target language: {getattr(project, 'target_lang', '')}")
    sample = [s.source.strip() for s in segments if (getattr(s, 'source', '') or '').strip()][:sample_size]
    if sample:
        parts.append("## Source sample (first segments of the document)\n"
                     + "\n".join(f"{i}. {t}" for i, t in enumerate(sample, 1)))
    if pairs:
        parts.append("## Validated translations (confirmed segments and TM matches)\n"
                     + "\n".join(f"- {s}  →  {t}" for s, t in pairs))
    if terms:
        parts.append("## Glossary terms\n" + "\n".join(f"- {s} = {t}" for s, t in terms))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n[… shortened to keep the review affordable]"
    return text


def count_points(unresolved: str) -> int:
    """Number of points in an Unresolved section (list items; 0 for "None.")."""
    text = (unresolved or "").strip()
    if not text or text.lower().rstrip('.') == 'none':
        return 0
    items = [l for l in text.splitlines() if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", l)]
    return len(items) or 1


def _yaml_safe(text: str) -> str:
    """One line without double quotes: save_prompt writes ``field: "value"``."""
    return re.sub(r"\s+", " ", (text or "").replace('"', "'")).strip()


class DuetWorker(QThread):
    turn_done = pyqtSignal(object)
    retrying = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, run: Callable[..., "duet.DuetResult"], kwargs: Dict):
        super().__init__()
        self._run, self._kwargs, self._stop = run, kwargs, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            result = self._run(on_turn=self.turn_done.emit,
                               should_stop=lambda: self._stop,
                               on_retry=lambda n, e: self.retrying.emit(n, str(e)),
                               **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DuetDialog(QDialog):
    """Run a Duet review of one prompt-library prompt."""

    def __init__(self, parent, app, library, prompt_path: str,
                 on_saved: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.app, self.library, self.prompt_path = app, library, prompt_path
        self.on_saved = on_saved
        self.prompt = dict(library.prompts.get(prompt_path, {}))
        self.prompt_name = self.prompt.get('name') or Path(prompt_path).stem
        self.worker: Optional[DuetWorker] = None
        self.result: Optional[duet.DuetResult] = None
        self.transcript_path: Optional[str] = None
        self.saved_path: Optional[str] = None
        self.setWindowTitle(f"Duet review – {self.prompt_name}")
        self.resize(980, 780)
        self._build()
        self._update_estimate()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        intro = QLabel(
            "Two AI models review this prompt together. Each turn must check the other's "
            "claims against the attached project material, keep a register of open issues, "
            "and end with a verdict. When both agree – or at the round limit – one of them "
            "writes the improved prompt, with anything still disputed listed for you to decide. "
            "The result is saved as a <b>new version</b>; the original is never changed.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        settings = self.app.load_llm_settings() if hasattr(self.app, 'load_llm_settings') else {}
        keys = self.app.load_api_keys() if hasattr(self.app, 'load_api_keys') else {}
        enabled = self.app.load_provider_enabled_states() if hasattr(self.app, 'load_provider_enabled_states') else {}
        self._settings = settings
        self.providers = available_providers(keys, enabled)

        models = QGroupBox("Models")
        form = QFormLayout(models)
        self.combo_a, self.model_a = self._model_row(form, "Model A:", "claude")
        self.combo_b, self.model_b = self._model_row(form, "Model B:", "openai")
        v.addWidget(models)

        opts = QHBoxLayout()
        self.rounds_spin = QSpinBox(); self.rounds_spin.setRange(1, 8); self.rounds_spin.setValue(4)
        self.tokens_spin = QSpinBox(); self.tokens_spin.setRange(500, 32000)
        self.tokens_spin.setSingleStep(500); self.tokens_spin.setValue(4000)
        self.sample_spin = QSpinBox(); self.sample_spin.setRange(0, 200); self.sample_spin.setValue(30)
        opts.addWidget(QLabel("Round limit:")); opts.addWidget(self.rounds_spin)
        opts.addWidget(QLabel("Max output tokens per turn:")); opts.addWidget(self.tokens_spin)
        opts.addWidget(QLabel("Source sample (segments):")); opts.addWidget(self.sample_spin)
        opts.addStretch()
        v.addLayout(opts)

        roles = QHBoxLayout()
        self.open_group, self.synth_group = QButtonGroup(self), QButtonGroup(self)
        for title, group in (("Opens:", self.open_group), ("Writes the result:", self.synth_group)):
            roles.addWidget(QLabel(title))
            for i, name in enumerate(("A", "B")):
                rb = CheckmarkRadioButton(f"Model {name}")
                rb.setChecked(i == 0)
                group.addButton(rb, i)
                roles.addWidget(rb)
            roles.addSpacing(20)
        roles.addStretch()
        v.addLayout(roles)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        v.addWidget(self.estimate_label)
        for w in (self.rounds_spin, self.tokens_spin, self.sample_spin):
            w.valueChanged.connect(self._update_estimate)
        for w in (self.model_a, self.model_b):
            w.textChanged.connect(self._update_estimate)
        for g in (self.open_group, self.synth_group):
            g.idClicked.connect(lambda _id: self._update_estimate())

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start review")
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        buttons.addWidget(self.start_btn); buttons.addWidget(self.stop_btn)
        self.status = QLabel("")
        buttons.addWidget(self.status, 1)
        v.addLayout(buttons)

        split = QSplitter(Qt.Orientation.Vertical)
        mono = QFont("Consolas"); mono.setStyleHint(QFont.StyleHint.Monospace)
        self.transcript_view = QPlainTextEdit(); self.transcript_view.setReadOnly(True)
        self.transcript_view.setPlaceholderText("The review appears here turn by turn.")
        split.addWidget(self.transcript_view)
        result_box = QWidget(); rv = QVBoxLayout(result_box); rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("<b>Improved prompt</b> (you can edit it before saving):"))
        self.deliverable_edit = QPlainTextEdit(); self.deliverable_edit.setFont(mono)
        rv.addWidget(self.deliverable_edit)
        self.unresolved_label = QLabel(""); self.unresolved_label.setWordWrap(True)
        self.unresolved_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rv.addWidget(self.unresolved_label)
        row = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save as new version")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_new_version)
        self.open_transcript_btn = QPushButton("📄 Open transcript")
        self.open_transcript_btn.setEnabled(False)
        self.open_transcript_btn.clicked.connect(self._open_transcript)
        row.addWidget(self.save_btn); row.addWidget(self.open_transcript_btn); row.addStretch()
        rv.addLayout(row)
        split.addWidget(result_box)
        split.setSizes([480, 260])
        v.addWidget(split, 1)

        if len(self.providers) < 1:
            self.status.setText("No AI provider has an API key yet – add one in Settings → 🤖 AI Settings.")
            self.start_btn.setEnabled(False)

    def _model_row(self, form, label, preferred):
        combo, edit = QComboBox(), QLineEdit()
        for key, name in self.providers:
            combo.addItem(name, key)
        idx = combo.findData(preferred)
        if idx < 0 and combo.count() > 1 and preferred == "openai":
            idx = 1  # a second, different provider for model B
        combo.setCurrentIndex(max(idx, 0))

        def fill():
            key = combo.currentData()
            edit.setText(self._model_for(key) if key else "")
        combo.currentIndexChanged.connect(fill)
        fill()
        row = QHBoxLayout(); row.addWidget(combo); row.addWidget(edit, 1)
        w = QWidget(); w.setLayout(row); row.setContentsMargins(0, 0, 0, 0)
        form.addRow(label, w)
        return combo, edit

    def _model_for(self, provider):
        if hasattr(self.app, '_resolve_provider_model'):
            try:
                from modules.llm_clients import LLMClient
                default = LLMClient.DEFAULT_MODELS.get(provider, "")
                return self.app._resolve_provider_model(self._settings, provider, default) or default
            except Exception:
                pass
        return self._settings.get(f"{provider}_model", "")

    # ------------------------------------------------------- material/cost
    def _segments(self):
        proj = getattr(self.app, 'current_project', None)
        if not proj:
            return []
        if hasattr(self.app, '_segments_in_document_order'):
            return self.app._segments_in_document_order()
        return list(proj.segments)

    def _material(self) -> str:
        """Looked up once (TM and glossary queries aren't free); only the
        sample size changes afterwards."""
        proj = getattr(self.app, 'current_project', None)
        segments = self._segments()
        if not hasattr(self, '_pairs'):
            self._pairs = [(s.source.strip(), s.target.strip()) for s in segments
                           if (s.target or '').strip() and getattr(s, 'status', '') in
                           ('confirmed', 'proofread', 'approved', 'tm_100', 'cm', 'pm')][:30]
            if not self._pairs and proj is not None:
                self._pairs = self._tm_pairs(segments[:10])
            self._glossary = self._terms()
        return build_material(proj, segments, self.sample_spin.value(), self._pairs, self._glossary)

    def _tm_pairs(self, segments) -> List[Tuple[str, str]]:
        """Best TM match for the first segments, from the project's Read TMs."""
        app, proj = self.app, getattr(self.app, 'current_project', None)
        try:
            tm_ids = app.tm_metadata_mgr.get_active_tm_ids(proj.id)
            if not tm_ids:
                return []
            out = []
            for seg in segments:
                found = app.db_manager.search_fuzzy_matches(
                    seg.source, tm_ids=tm_ids, threshold=0.75, max_results=1,
                    source_lang=proj.source_lang, target_lang=proj.target_lang)
                if found:
                    out.append((found[0]['source_text'], found[0]['target_text']))
            return out
        except Exception:
            return []

    def _terms(self) -> List[Tuple[str, str]]:
        app, proj = self.app, getattr(self.app, 'current_project', None)
        mgr = getattr(app, 'termbase_mgr', None)
        if not (mgr and proj is not None and getattr(proj, 'id', None)):
            return []
        try:
            out = []
            for tb_id in mgr.get_active_termbase_ids(proj.id) or []:
                for t in mgr.get_terms(tb_id) or []:
                    if t.get('forbidden'):
                        continue
                    s, tt = (t.get('source_term') or '').strip(), (t.get('target_term') or '').strip()
                    if s and tt:
                        out.append((s, tt))
                    if len(out) >= 150:
                        return out
            return out
        except Exception:
            return []

    def _brief(self) -> str:
        return duet.build_brief(self.prompt_name, self.prompt.get('content', ''), self._material())

    def _participants(self, with_clients=False):
        out = []
        for combo, edit, name in ((self.combo_a, self.model_a, "A"), (self.combo_b, self.model_b, "B")):
            provider, model = combo.currentData() or "", edit.text().strip()
            label = f"{combo.currentText()} ({model})"
            ask = self._make_ask(provider, model) if with_clients else None
            out.append(duet.Participant(label, provider, model, ask))
        return out

    def _make_ask(self, provider, model):
        client = self.app.create_llm_client(provider, model, self.app.load_api_keys(), self._settings)

        def ask(system, prompt, max_tokens):
            return client.translate_with_usage(text="", custom_prompt=prompt, system_prompt=system,
                                               max_tokens=max_tokens, skip_cleaning=True)
        return ask

    def _update_estimate(self):
        try:
            from modules.llm_pricing import estimate_cost
            from modules.cost_estimate import currency_settings, format_cost
            brief_chars = len(self._brief())
            a, b = self._participants()
            est = duet.estimate_cost(a, b, brief_chars, self.rounds_spin.value(), self.tokens_spin.value(),
                                     "AB"[max(self.open_group.checkedId(), 0)],
                                     "AB"[max(self.synth_group.checkedId(), 0)], price=estimate_cost)
            gs = self.app._load_general_settings_from_file() if hasattr(self.app, '_load_general_settings_from_file') else {}
            cost = format_cost(est['cost'], *currency_settings(gs))
            self.estimate_label.setText(
                f"About <b>{brief_chars:,}</b> characters of prompt and material go to each model, every turn. "
                f"If all {self.rounds_spin.value()} rounds run: up to {est['turns']} calls, "
                f"~{est['input_tokens']:,} input and ~{est['output_tokens']:,} output tokens, "
                f"about <b>{cost}</b>. The whole review is re-sent every turn, so the cost grows "
                f"quickly with more rounds; attach an extract (a smaller sample), not a whole document.")
        except Exception as exc:
            self.estimate_label.setText(f"(No estimate: {exc})")

    # ---------------------------------------------------------------- run
    def _transcript_file(self) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", self.prompt_name).strip(" .") or "prompt"
        project_path = getattr(self.app, 'project_file_path', None)
        if project_path:
            folder = os.path.join(os.path.dirname(os.path.abspath(project_path)), "reports", "duet")
        else:
            base = getattr(self.app, 'user_data_path', None) or os.path.expanduser("~")
            folder = os.path.join(str(base), "workbench", "duet")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{safe} – {stamp}.md")

    def start(self):
        a, b = self._participants()
        if a.provider == b.provider and a.model == b.model:
            reply = QMessageBox.question(
                self, "Duet review",
                "Both models are the same. A Duet works best with two different models, "
                "which catch different problems. Start anyway?")
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            a, b = self._participants(with_clients=True)
        except Exception as exc:
            QMessageBox.warning(self, "Duet review", f"Could not set up the models:\n\n{exc}")
            return
        self.transcript_path = self._transcript_file()
        brief = self._brief()
        self.transcript_view.clear()
        self.deliverable_edit.clear()
        self.unresolved_label.clear()
        self.save_btn.setEnabled(False)
        self.open_transcript_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText(f"Round 1 – waiting for {a.label if self.open_group.checkedId() != 1 else b.label}…")
        self.worker = DuetWorker(duet.run_duet, dict(
            a=a, b=b, brief=brief, max_rounds=self.rounds_spin.value(),
            max_tokens=self.tokens_spin.value(),
            opener="AB"[max(self.open_group.checkedId(), 0)],
            synthesiser="AB"[max(self.synth_group.checkedId(), 0)],
            transcript_path=self.transcript_path))
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
        title = (f"=== Synthesis – {turn.label} ===" if turn.speaker == "synthesis"
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
        outcome = (f"Both models agreed after {result.rounds} round(s)." if result.consensus
                   else f"No agreement after {result.rounds} round(s); the disagreements are listed below.")
        self.status.setText(outcome)
        self.deliverable_edit.setPlainText(result.deliverable)
        unresolved = result.unresolved.strip()
        self.unresolved_label.setText(
            f"<b>Unresolved (for you to decide):</b><br>{_html(unresolved)}" if unresolved
            and unresolved.lower().rstrip('.') != 'none' else "<b>Unresolved:</b> none.")
        self.save_btn.setEnabled(bool(result.deliverable.strip()))

    def _on_failed(self, message):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText(f"The review stopped: {message}. The transcript so far is saved.")

    def _open_transcript(self):
        if self.transcript_path and os.path.exists(self.transcript_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.transcript_path))

    # --------------------------------------------------------------- save
    def save_new_version(self) -> Optional[str]:
        content = self.deliverable_edit.toPlainText().strip()
        if not content or not self.result:
            return None
        folder = str(Path(self.prompt_path).parent)
        folder = "" if folder == "." else folder
        existing = [Path(p).stem for p in self.library.prompts
                    if str(Path(p).parent) in (folder or ".",)]
        name = duet.next_version_name(Path(self.prompt_path).stem, existing)
        new_path = str(Path(folder) / f"{name}.md") if folder else f"{name}.md"
        a, b = self._participants()
        outcome = ("agreed" if self.result.consensus
                   else f"no agreement after {self.result.rounds} rounds")
        n_unresolved = count_points(self.result.unresolved)
        description = _yaml_safe(
            f"Duet review of {Path(self.prompt_path).stem} by {a.label} and {b.label} on "
            f"{datetime.now():%Y-%m-%d} ({outcome}; {n_unresolved} unresolved point(s)). "
            f"Transcript: {self.transcript_path}")
        data = {k: v for k, v in self.prompt.items() if not k.startswith('_')}
        data.update(name=name, content=content, description=description, default=False,
                    read_only=False, quicklauncher=False, quicklauncher_grid=False, quick_run=False,
                    created=datetime.now().strftime('%Y-%m-%d'),
                    modified=datetime.now().strftime('%Y-%m-%d'))
        if not self.library.save_prompt(new_path, data):
            QMessageBox.warning(self, "Duet review", "Could not save the new version.")
            return None
        self.saved_path = new_path
        if self.transcript_path:
            try:
                with open(self.transcript_path, "a", encoding="utf-8") as f:
                    f.write(f"\n_Saved to the prompt library as {new_path}._\n")
            except OSError:
                pass
        self.status.setText(f"Saved as “{name}”. The original prompt is unchanged.")
        self.save_btn.setEnabled(False)
        if self.on_saved:
            self.on_saved(new_path)
        return new_path


def _html(text: str) -> str:
    import html
    return html.escape(text).replace("\n", "<br>")
