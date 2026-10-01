"""
Voice Dictation Module for Supervertaler
Uses OpenAI Whisper for multilingual speech recognition
Supports English, Dutch, and 90+ other languages
"""

import sys
import sounddevice as sd
import numpy as np
import tempfile
import wave
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTextEdit, QGroupBox, QMessageBox, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from modules.shortcut_display import format_shortcut_for_display
from modules.platform_helpers import hide_subprocess_console_windows


class RecordingThread(QThread):
    """Background thread for audio recording"""
    finished = pyqtSignal(str)  # Emits path to recorded file
    error = pyqtSignal(str)

    def __init__(self, duration=30, sample_rate=16000):
        super().__init__()
        self.duration = duration
        self.sample_rate = sample_rate
        self.is_recording = False

    def run(self):
        """Record audio in background"""
        try:
            self.is_recording = True

            # Record audio
            recording = sd.rec(
                int(self.duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32'
            )

            # Wait for recording to complete
            sd.wait()

            if not self.is_recording:
                # Recording was stopped early
                sd.stop()

            # Convert to int16
            audio_data = np.int16(recording * 32767)

            # Save to temporary WAV file with explicit directory
            import os
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"voice_recording_{os.getpid()}.wav")

            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())

            self.finished.emit(temp_path)

        except Exception as e:
            import traceback
            self.error.emit(f"{str(e)}\n{traceback.format_exc()}")

    def stop(self):
        """Stop recording"""
        self.is_recording = False
        sd.stop()


class TranscriptionThread(QThread):
    """Background thread for transcription"""
    finished = pyqtSignal(str)  # Emits transcribed text
    error = pyqtSignal(str)
    progress = pyqtSignal(str)  # Progress updates

    def __init__(self, audio_path, model_name="base", language=None):
        super().__init__()
        self.audio_path = audio_path
        self.model_name = model_name
        self.language = language

    def run(self):
        """Transcribe audio in background"""
        try:
            import os

            try:
                from faster_whisper import WhisperModel  # Now a core dep
            except ImportError:
                msg = (
                    "faster-whisper is not installed in this environment.\n\n"
                    "Re-install Supervertaler:\n"
                    "  pip install --upgrade supervertaler"
                )
                self.error.emit(msg)
                return

            # Verify file exists
            if not os.path.exists(self.audio_path):
                self.error.emit(f"Audio file not found: {self.audio_path}")
                return

            self.progress.emit("Loading Whisper model...")

            # Load model – faster-whisper / CTranslate2 backend on CPU with
            # int8 quantisation: ~4× faster than openai-whisper, lower RAM,
            # no ffmpeg subprocess.
            model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

            self.progress.emit("Transcribing audio...")

            # Transcribe – wrapper kept defensively (no-op if nothing spawns).
            with hide_subprocess_console_windows():
                lang = self.language or None
                segments, _info = model.transcribe(
                    self.audio_path,
                    language=lang,
                    beam_size=5,
                    vad_filter=False,
                )
                text = "".join(seg.text for seg in segments)

            # Clean up temp file
            try:
                Path(self.audio_path).unlink()
            except:
                pass

            self.finished.emit(text.strip())

        except Exception as e:
            import traceback
            self.error.emit(f"{str(e)}\n\nFull error:\n{traceback.format_exc()}")


class VoiceDictationWidget(QWidget):
    """
    Voice Dictation Widget using Whisper

    Features:
    - Push-to-record button
    - Multilingual support (100+ languages)
    - Multiple model sizes (tiny, base, small, medium, large)
    - Copy to clipboard functionality
    """

    MODELS = {
        "tiny": "Tiny (fastest, ~1GB RAM)",
        "base": "Base (good balance, ~1GB RAM)",
        "small": "Small (better quality, ~2GB RAM)",
        "medium": "Medium (high quality, ~5GB RAM)",
        "large": "Large (best quality, ~10GB RAM)"
    }

    LANGUAGES = {
        "auto": "Auto-detect",
        "en": "English",
        "nl": "Dutch",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "pl": "Polish",
        "ru": "Russian",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        # Add more as needed
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recording_thread = None
        self.transcription_thread = None
        self.init_ui()
        self.setup_shortcuts()

    def init_ui(self):
        """Initialize the user interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Title
        title = QLabel("🎤 Voice Dictation")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #1976D2;")
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Record your voice and get instant transcription in 100+ languages.\n"
            "Powered by OpenAI Whisper."
        )
        desc.setStyleSheet("color: #666; padding: 5px; background-color: #E3F2FD; border-radius: 3px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Settings group
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout()

        # Model selection
        settings_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        for key, label in self.MODELS.items():
            self.model_combo.addItem(label, key)
        self.model_combo.setCurrentIndex(1)  # Default to "base"
        settings_layout.addWidget(self.model_combo)

        # Language selection
        settings_layout.addWidget(QLabel("Language:"))
        self.language_combo = QComboBox()
        for key, label in self.LANGUAGES.items():
            self.language_combo.addItem(label, key)
        settings_layout.addWidget(self.language_combo)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # Recording controls
        controls_layout = QHBoxLayout()

        self.record_btn = QPushButton("🎙️ Start Recording")
        self.record_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #BDBDBD;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.record_btn.clicked.connect(self.toggle_recording)
        controls_layout.addWidget(self.record_btn)

        self.copy_btn = QPushButton("📋 Copy to Clipboard")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        controls_layout.addWidget(self.copy_btn)

        self.clear_btn = QPushButton("🗑️ Clear")
        self.clear_btn.clicked.connect(self.clear_text)
        controls_layout.addWidget(self.clear_btn)

        layout.addLayout(controls_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.status_label)

        # Transcription output
        output_group = QGroupBox("Transcription")
        output_layout = QVBoxLayout()

        self.transcription_text = QTextEdit()
        self.transcription_text.setPlaceholderText("Transcribed text will appear here...")
        self.transcription_text.setMinimumHeight(200)
        output_layout.addWidget(self.transcription_text)

        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        # Help text
        help_text = QLabel(
            "💡 Tip: Use 'base' model for quick transcription, 'medium' or 'large' for best quality. "
            "First use downloads the model (~1-10GB depending on size)."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #666; font-size: 10px; padding: 5px;")
        layout.addWidget(help_text)

        # Keyboard shortcuts info
        shortcuts_text = QLabel(
            f"⌨️ Shortcuts: F9 = Start/Stop Recording | Esc = Cancel | {format_shortcut_for_display('Ctrl+C')} = Copy"
        )
        shortcuts_text.setWordWrap(True)
        shortcuts_text.setStyleSheet(
            "color: #1976D2; font-size: 10px; font-weight: bold; "
            "padding: 8px; background-color: #E3F2FD; border-radius: 3px; margin-top: 5px;"
        )
        layout.addWidget(shortcuts_text)

    def setup_shortcuts(self):
        """Setup keyboard shortcuts"""
        # F9 - Start/Stop recording
        self.shortcut_record = QShortcut(QKeySequence("F9"), self)
        self.shortcut_record.activated.connect(self.toggle_recording)

        # Esc - Cancel recording
        self.shortcut_cancel = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.shortcut_cancel.activated.connect(self.cancel_recording)

        # Ctrl+C - Copy to clipboard
        self.shortcut_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        self.shortcut_copy.activated.connect(self.copy_to_clipboard)

    def cancel_recording(self):
        """Cancel ongoing recording"""
        if self.recording_thread and self.recording_thread.is_recording:
            self.stop_recording()
            self.status_label.setText("⚠️ Recording cancelled")
            self.status_label.setStyleSheet("color: #FF9800;")

    def toggle_recording(self):
        """Start or stop recording"""
        if self.recording_thread and self.recording_thread.is_recording:
            # Stop recording
            self.stop_recording()
        else:
            # Start recording
            self.start_recording()

    def start_recording(self):
        """Start recording audio"""
        self.status_label.setText("🔴 Recording... (max 30 seconds)")
        self.status_label.setStyleSheet("color: #D32F2F; font-weight: bold;")
        self.record_btn.setText("⏹️ Stop Recording")
        self.record_btn.setStyleSheet("""
            QPushButton {
                background-color: #D32F2F;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #C62828;
            }
            QPushButton:focus {
                outline: none;
            }
        """)

        # Disable controls
        self.model_combo.setEnabled(False)
        self.language_combo.setEnabled(False)

        # Start recording thread
        self.recording_thread = RecordingThread()
        self.recording_thread.finished.connect(self.on_recording_finished)
        self.recording_thread.error.connect(self.on_recording_error)
        self.recording_thread.start()

    def stop_recording(self):
        """Stop recording audio"""
        if self.recording_thread:
            self.recording_thread.stop()
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #FF9800; font-weight: bold;")

    def on_recording_finished(self, audio_path):
        """Handle recording completion"""
        # Reset button
        self.record_btn.setText("🎙️ Start Recording")
        self.record_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:focus {
                outline: none;
            }
        """)

        # Start transcription
        self.transcribe_audio(audio_path)

    def on_recording_error(self, error_msg):
        """Handle recording error"""
        self.status_label.setText(f"❌ Recording error: {error_msg}")
        self.status_label.setStyleSheet("color: #D32F2F;")
        self.record_btn.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.language_combo.setEnabled(True)

    def transcribe_audio(self, audio_path):
        """Transcribe recorded audio"""
        model_name = self.model_combo.currentData()
        language = self.language_combo.currentData()
        if language == "auto":
            language = None

        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.record_btn.setEnabled(False)

        # Start transcription thread
        self.transcription_thread = TranscriptionThread(audio_path, model_name, language)
        self.transcription_thread.finished.connect(self.on_transcription_finished)
        self.transcription_thread.error.connect(self.on_transcription_error)
        self.transcription_thread.progress.connect(self.on_transcription_progress)
        self.transcription_thread.start()

    def on_transcription_progress(self, message):
        """Update progress message"""
        self.status_label.setText(message)

    def on_transcription_finished(self, text):
        """Handle transcription completion"""
        self.progress_bar.setVisible(False)
        self.status_label.setText("✅ Transcription complete")
        self.status_label.setStyleSheet("color: #388E3C; font-weight: bold;")

        # Add text to output
        current_text = self.transcription_text.toPlainText()
        if current_text:
            self.transcription_text.setPlainText(current_text + "\n\n" + text)
        else:
            self.transcription_text.setPlainText(text)

        # Re-enable controls
        self.record_btn.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def on_transcription_error(self, error_msg):
        """Handle transcription error"""
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"❌ Transcription error: {error_msg}")
        self.status_label.setStyleSheet("color: #D32F2F;")

        QMessageBox.critical(self, "Error", f"Transcription failed:\n{error_msg}")

        self.record_btn.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.language_combo.setEnabled(True)

    def copy_to_clipboard(self):
        """Copy transcription to clipboard"""
        from PyQt6.QtWidgets import QApplication
        text = self.transcription_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status_label.setText("✅ Copied to clipboard")
            self.status_label.setStyleSheet("color: #388E3C;")

    def clear_text(self):
        """Clear transcription text"""
        self.transcription_text.clear()
        self.copy_btn.setEnabled(False)
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")


# Standalone test application
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = VoiceDictationWidget()
    window.setWindowTitle("Voice Dictation - Supervertaler")
    window.resize(600, 700)
    window.show()
    sys.exit(app.exec())
