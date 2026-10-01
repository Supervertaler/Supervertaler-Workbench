"""
Lightweight Voice Dictation for Supervertaler
Minimal version for integration into target editors
"""

# Note: Heavy imports (whisper, sounddevice, numpy) are loaded lazily in run()
# to avoid slow startup. These libraries add 5+ seconds of import time.
import tempfile
import wave
import os
import sys
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from modules.platform_helpers import hide_subprocess_console_windows


def ensure_ffmpeg_available():
    """
    Ensure FFmpeg is available for Whisper
    Returns True if FFmpeg is found, False otherwise
    """
    import shutil

    # Check if ffmpeg is already in system PATH
    if shutil.which('ffmpeg'):
        return True

    # Check for bundled ffmpeg (for .exe distributions)
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        bundle_dir = Path(sys._MEIPASS)
    else:
        # Running as script
        bundle_dir = Path(__file__).parent.parent

    bundled_ffmpeg = bundle_dir / 'binaries' / 'ffmpeg.exe'
    if bundled_ffmpeg.exists():
        # Add bundled ffmpeg directory to PATH
        os.environ['PATH'] = str(bundled_ffmpeg.parent) + os.pathsep + os.environ['PATH']
        return True

    return False


class QuickDictationThread(QThread):
    """
    Quick voice dictation thread - records and transcribes in one go
    Minimal UI, fast operation
    """
    transcription_ready = pyqtSignal(str)  # Emits transcribed text
    status_update = pyqtSignal(str)  # Status messages
    error_occurred = pyqtSignal(str)  # Errors
    model_loading_started = pyqtSignal(str)  # Model name being loaded/downloaded
    model_loading_finished = pyqtSignal()  # Model loaded successfully

    def __init__(self, model_name="base", language="auto", duration=10, use_api: bool = False, api_key: str | None = None, mic_device: str | None = None, initial_prompt: str | None = None, replacements: list | None = None,
                 engine: str = "faster_whisper", user_data_path: str | None = None):
        super().__init__()
        self.model_name = model_name
        # "faster_whisper" (default), "api", or "parakeet" (issue #198:
        # NVIDIA Parakeet V3 via onnx-asr, model in <user_data>/voice-models)
        self.engine = "api" if use_api else engine
        self.user_data_path = user_data_path
        self.language = None if language == "auto" else language
        self.duration = duration  # Max recording duration
        self.use_api = use_api
        self.api_key = api_key
        # Saved device *name* from dictation_settings (None ⇒ OS default).
        # Resolved to a sounddevice index inside run() via
        # modules.mic_devices, so we always honour the *current* device
        # mapping rather than a stale index from __init__ time.
        self.mic_device = mic_device
        # Vocabulary biasing (v1.10.26). ``initial_prompt`` is passed
        # through to faster-whisper's transcribe() / OpenAI API's
        # transcriptions.create() to bias the decoder toward known
        # brand and technical terms (e.g. "Supervertaler" stops
        # mistranscribing as "Supervertile"). ``replacements`` is a
        # list of {"heard": str, "meant": str} dicts applied as a
        # post-process regex pass for stubborn cases the biasing
        # doesn't catch. Both are built by
        # modules.voice_vocabulary.build_initial_prompt() /
        # post_process_transcript(); pass None to skip both.
        self.initial_prompt = initial_prompt
        self.replacements = replacements
        self.sample_rate = 16000
        self.is_recording = False
        self.stop_requested = False
        self.recording_stream = None

    def stop_recording(self):
        """Stop recording early (called from main thread)"""
        self.stop_requested = True

    def run(self):
        """Record and transcribe audio"""
        try:
            # Lazy import heavy libraries to avoid slow startup
            import sounddevice as sd
            import numpy as np

            # Local Whisper needs FFmpeg; the API and Parakeet (which reads
            # the WAV itself) don't.
            if self.engine == "faster_whisper":
                if not ensure_ffmpeg_available():
                    self.error_occurred.emit(
                        "FFmpeg not found. Local Whisper requires FFmpeg.\n\n"
                        "Install FFmpeg (PowerShell as Admin):\n"
                        "winget install FFmpeg  (or)  choco install ffmpeg"
                    )
                    return

            # Step 1: Record audio
            self.status_update.emit("🔴 Recording... (Press F9 or click Stop to finish)")
            self.is_recording = True
            self.stop_requested = False

            # Resolve the saved mic-device name to a current index.
            # ``None`` → sd uses the OS default input. Done here (not
            # in __init__) so we honour the live device mapping –
            # devices the user plugged in after launching Workbench
            # are picked up without a restart.
            #
            # extra_settings: when the user explicitly picked a WASAPI
            # device (as opposed to "System default"), enable WASAPI's
            # auto sample-rate conversion. Without it, WASAPI rejects
            # our 16 kHz capture because shared-mode WASAPI forces the
            # OS mixer rate (typically 48 kHz). MME devices and the
            # default-fallback path don't need this.
            try:
                from modules.mic_devices import (
                    resolve_device_index, wasapi_autoconvert_settings,
                )
                device_idx = resolve_device_index(self.mic_device)
                extra = wasapi_autoconvert_settings(device_idx)
            except Exception:
                device_idx = None
                extra = None

            # Start recording
            recording = sd.rec(
                int(self.duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32',
                device=device_idx,
                extra_settings=extra,
            )

            # Wait for recording to complete OR manual stop
            import time
            elapsed = 0
            check_interval = 0.1  # Check every 100ms
            while elapsed < self.duration and not self.stop_requested:
                time.sleep(check_interval)
                elapsed += check_interval

            # Stop recording
            sd.stop()
            self.is_recording = False
            self.status_update.emit(f"🛑 Recording stopped ({elapsed:.1f}s recorded)")

            # Calculate actual recorded samples
            actual_samples = int(min(elapsed, self.duration) * self.sample_rate)
            recording = recording[:actual_samples]  # Trim to actual length
            self.status_update.emit(f"📊 Processing {actual_samples} audio samples...")

            # Convert to int16
            audio_data = np.int16(recording * 32767)

            # Save to temp WAV
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"sv_dictation_{os.getpid()}.wav")
            self.status_update.emit(f"💾 Saving audio to {temp_path}")

            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())

            self.status_update.emit(f"✓ Audio saved ({len(audio_data)} bytes)")

            # Step 2: Transcribe
            self.status_update.emit("⏳ Transcribing...")

            if self.use_api:
                if not self.api_key:
                    self.error_occurred.emit(
                        "OpenAI API key missing.\n\n"
                        "Set your OpenAI API key in Settings → AI Settings, or switch to Local Whisper (offline)."
                    )
                    try:
                        Path(temp_path).unlink()
                    except:
                        pass
                    return

                self.status_update.emit("🎤 Using OpenAI Whisper API (fast & accurate)")
                text = self._transcribe_with_api(temp_path)
            elif self.engine == "parakeet":
                text = self._transcribe_with_parakeet(temp_path)
            else:
                text = self._transcribe_with_local(temp_path)

            # Clean up temp file
            try:
                Path(temp_path).unlink()
            except:
                pass

            # Emit result
            if text:
                self.transcription_ready.emit(text)
                self.status_update.emit("✅ Done")
            else:
                self.error_occurred.emit("No speech detected")

        except Exception as e:
            self.is_recording = False
            import traceback
            error_details = traceback.format_exc()
            self.error_occurred.emit(f"Error: {str(e)}\n\nTraceback:\n{error_details}")

    def _transcribe_with_api(self, audio_path: str) -> str:
        """Transcribe using OpenAI Whisper API (no local Whisper required).

        v1.10.26: passes ``self.initial_prompt`` as the ``prompt``
        field to bias the decoder toward known brand / technical
        vocabulary. Applies ``self.replacements`` (default + user)
        as a post-process pass on the returned text.
        """
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key)
            with open(audio_path, "rb") as audio_file:
                kwargs = {"model": "whisper-1", "file": audio_file}
                if self.language:
                    kwargs["language"] = self.language
                if self.initial_prompt:
                    kwargs["prompt"] = self.initial_prompt
                response = client.audio.transcriptions.create(**kwargs)

            text = (response.text or "").strip()
            return self._apply_replacements(text)
        except Exception as e:
            self.error_occurred.emit(f"OpenAI API error: {e}")
            return ""

    def _transcribe_with_local(self, audio_path: str) -> str:
        """Transcribe using local faster-whisper (now a core dependency)."""
        try:
            # Lazy import to avoid loading the C++ engine until needed.
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                msg = (
                    "faster-whisper is not installed in this environment.\n\n"
                    "Re-install Supervertaler:\n"
                    "  pip install --upgrade supervertaler"
                )
                self.error_occurred.emit(msg)
                return ""

            # faster-whisper auto-downloads its CTranslate2 model the first
            # time you instantiate WhisperModel(model_name) – no separate
            # cache-existence probe needed (the constructor handles it).
            self.model_loading_started.emit(self.model_name)
            self.status_update.emit(
                f"⏳ Loading {self.model_name} model "
                "(may download on first use)..."
            )

            # int8 on CPU: ~4× faster than openai-whisper at near-equal quality.
            model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self.model_loading_finished.emit()

            # Transcribe – wrapper kept defensively (no-op if nothing spawns).
            self.status_update.emit("⏳ Transcribing audio...")
            with hide_subprocess_console_windows():
                lang = self.language or None
                transcribe_kwargs = dict(
                    language=lang,
                    beam_size=5,
                    vad_filter=False,
                )
                # v1.10.26: vocabulary biasing. ``initial_prompt`` is
                # passed straight through to faster-whisper, which
                # tokenises it into the decoder's preceding-context
                # so words appearing in the prompt get a small
                # probability boost throughout transcription. Fixes
                # the "Supervertile" mistranscription class.
                if self.initial_prompt:
                    transcribe_kwargs["initial_prompt"] = self.initial_prompt
                segments, _info = model.transcribe(audio_path, **transcribe_kwargs)
                text = "".join(seg.text for seg in segments)

            return self._apply_replacements(text.strip())
        except Exception as e:
            self.error_occurred.emit(f"Local transcription error: {e}")
            return ""

    def _transcribe_with_parakeet(self, audio_path: str) -> str:
        """Transcribe with NVIDIA Parakeet V3 (issue #198). The model stays
        loaded between dictations. Parakeet detects the language itself and
        has no vocabulary prompt, so only the replacement table applies."""
        try:
            from modules.voice_engines import parakeet
            if not parakeet.is_available():
                self.error_occurred.emit(
                    "Parakeet needs the onnx-asr and onnxruntime packages:\n\n"
                    "  pip install --upgrade supervertaler")
                return ""
            if not parakeet.is_installed(self.user_data_path or ""):
                self.error_occurred.emit(
                    "The Parakeet V3 model isn't downloaded yet.\n\n"
                    "Download it in the Voice tab → Dictation, or switch the engine "
                    "back to faster-whisper.")
                return ""
            if not parakeet._loaded:
                self.model_loading_started.emit(parakeet.PARAKEET_V3.name)
                self.status_update.emit("⏳ Loading Parakeet V3...")
                parakeet.load(self.user_data_path)
                self.model_loading_finished.emit()
            self.status_update.emit("⏳ Transcribing audio...")
            text = parakeet.transcribe(audio_path, self.user_data_path)
            return self._apply_replacements(text)
        except Exception as e:
            self.error_occurred.emit(f"Parakeet transcription error: {e}")
            return ""

    def _apply_replacements(self, text: str) -> str:
        """Apply the default + user-supplied replacement table to
        the transcript. Wraps :func:`modules.voice_vocabulary.
        post_process_transcript` with a try/except so a buggy user
        replacement entry can't bring down the whole dictation
        path – worst case we return the raw text.
        """
        if not text:
            return text
        try:
            from modules.voice_vocabulary import post_process_transcript
            return post_process_transcript(text, self.replacements)
        except Exception as e:
            print(f"[VoiceDictation] replacement post-process error: {e}")
            return text

    def stop(self):
        """Stop recording"""
        if self.is_recording:
            self.is_recording = False
            try:
                import sounddevice as sd
                sd.stop()
            except:
                pass
