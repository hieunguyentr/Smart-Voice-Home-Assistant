#!/usr/bin/env python3
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from openai import OpenAI


SYSTEM_INSTRUCTIONS = (
    "You are a helpful home robot assistant. "
    "Reply in short, direct spoken sentences. "
    "Do not use markdown, bullets, or long explanations."
)


class PiVoiceRuntime:
    def __init__(self):
        self.base_dir = Path("/home/hieunguyentr")
        self.whisper_cli = self.base_dir / "whisper.cpp" / "build" / "bin" / "whisper-cli"
        self.whisper_model = self.base_dir / "whisper.cpp" / "models" / "ggml-tiny.en.bin"
        self.piper_bin = self._find_first([
            self.base_dir / ".local" / "opt" / "piper" / "piper" / "piper",
            self.base_dir / ".local" / "bin" / "piper",
        ])
        self.piper_voice = self._find_first([
            self.base_dir / ".local" / "share" / "piper" / "voices" / "bobby" / "en_US-bobby-medium.onnx",
            self.base_dir / ".local" / "share" / "piper" / "voices" / "carl" / "en_US-carl-medium.onnx",
            self.base_dir / ".local" / "share" / "piper" / "voices" / "patrick" / "en_US-patrick-medium.onnx",
            self.base_dir / ".local" / "share" / "piper" / "voices" / "donald-trump" / "en_US-trump-high.onnx",
            self.base_dir / ".local" / "share" / "piper" / "voices" / "george-carlin" / "en_US-carlin-high.onnx",
        ])
        self.text_model = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini")
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.tts_voice = os.environ.get("OPENAI_TTS_VOICE", "alloy")
        self.capture_device = os.environ.get("PI_MIC_DEVICE", "plughw:Microphone,0")
        self.record_seconds = int(os.environ.get("PI_RECORD_SECONDS", "5"))
        self.playback_device = os.environ.get("PI_SPEAKER_DEVICE", "plughw:CARD=Headphones,DEV=0")
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._worker = None
        self._session_id = 0
        self._record_proc = None
        self._transcribe_proc = None
        self._play_proc = None

    def _log(self, message):
        print(message, flush=True)

    def _find_first(self, paths):
        for path in paths:
            if Path(path).exists():
                return Path(path)
        return None

    def is_active(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def start_session(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                self._log('[session] already active')
                return False
            self._cancel_event.clear()
            self._session_id += 1
            session_id = self._session_id
            worker = threading.Thread(target=self._run_session, args=(session_id,), daemon=True)
            self._worker = worker
        self._log('[session] started')
        worker.start()
        return True

    def cancel_session(self):
        self._log('[session] cancel requested')
        self._cancel_event.set()
        with self._lock:
            self._session_id += 1
            self._worker = None
        self._terminate_proc("_record_proc")
        self._terminate_proc("_transcribe_proc")
        self._terminate_proc("_play_proc")
        self._log('[session] reset to idle')

    def shutdown(self):
        self.cancel_session()

    def _terminate_proc(self, attr_name):
        with self._lock:
            proc = getattr(self, attr_name)
            setattr(self, attr_name, None)
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _set_proc(self, attr_name, proc):
        with self._lock:
            setattr(self, attr_name, proc)

    def _clear_worker(self, session_id):
        with self._lock:
            if self._session_id == session_id and self._worker is threading.current_thread():
                self._worker = None

    def _client(self):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        return OpenAI(api_key=api_key, timeout=60.0)

    def _run_session(self, session_id):
        temp_paths = []
        try:
            if self._cancelled(session_id):
                self._log('[session] cancelled before start')
                return

            self._log('[step] recording microphone')
            wav_path = self._record_utterance(session_id)
            if not wav_path:
                self._log('[step] no audio captured -> idle')
                return
            temp_paths.append(wav_path)

            self._log('[step] transcribing audio')
            transcript = self._transcribe(wav_path, session_id)
            if not transcript or self._cancelled(session_id):
                self._log('[step] empty transcript or cancelled -> idle')
                return
            self._log(f'[transcript] {transcript}')

            self._log('[step] sending transcript to openai')
            reply = self._generate_reply(transcript, session_id)
            if not reply or self._cancelled(session_id):
                self._log('[step] openai reply missing or cancelled -> idle')
                return
            self._log(f'[reply] {reply}')

            self._log('[step] generating speech')
            tts_path = self._synthesize_speech(reply, session_id)
            if not tts_path or self._cancelled(session_id):
                self._log('[step] tts failed or cancelled -> idle')
                return
            temp_paths.append(tts_path)

            self._log('[step] playing audio')
            self._play_audio(tts_path, session_id)
            if not self._cancelled(session_id):
                self._log('[session] completed -> idle')
        finally:
            self._clear_worker(session_id)
            for path in temp_paths:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _cancelled(self, session_id):
        return self._cancel_event.is_set() or session_id != self._session_id

    def _record_utterance(self, session_id):
        fd, wav_path = tempfile.mkstemp(prefix="pi_voice_in_", suffix=".wav")
        os.close(fd)
        cmd = [
            "arecord",
            "-D", self.capture_device,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-d", str(self.record_seconds),
            wav_path,
        ]
        self._log(f"[record] using device={self.capture_device} seconds={self.record_seconds}")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_proc("_record_proc", proc)
        try:
            while proc.poll() is None:
                if self._cancelled(session_id):
                    self._terminate_proc("_record_proc")
                    self._log('[step] recording cancelled')
                    return None
                time.sleep(0.1)
        finally:
            self._set_proc("_record_proc", None)

        if proc.returncode != 0:
            Path(wav_path).unlink(missing_ok=True)
            self._log(f'[error] recorder exited with code {proc.returncode}')
            return None
        if not Path(wav_path).exists() or Path(wav_path).stat().st_size < 4096:
            Path(wav_path).unlink(missing_ok=True)
            return None
        return wav_path

    def _transcribe(self, wav_path, session_id):
        if not self.whisper_cli.exists() or not self.whisper_model.exists():
            self._log('[error] whisper.cpp binary or model missing')
            return None
        cmd = [
            str(self.whisper_cli),
            "-m", str(self.whisper_model),
            "-l", "en",
            "-t", "2",
            "-f", str(wav_path),
            "-nt",
            "-np",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._set_proc("_transcribe_proc", proc)
        try:
            while proc.poll() is None:
                if self._cancelled(session_id):
                    self._terminate_proc("_transcribe_proc")
                    self._log('[step] transcription cancelled')
                    return None
                time.sleep(0.1)
            stdout, stderr = proc.communicate(timeout=1)
        finally:
            self._set_proc("_transcribe_proc", None)

        if proc.returncode != 0:
            self._log(f'[error] whisper failed: {stderr.strip()[:200]}')
            return None
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        transcript = " ".join(lines).strip()
        return transcript or None

    def _generate_reply(self, transcript, session_id):
        client = self._client()
        if client is None:
            self._log('[error] openai key missing')
            self._speak_local_fallback('openai key missing', session_id)
            return None
        try:
            response = client.responses.create(
                model=self.text_model,
                instructions=SYSTEM_INSTRUCTIONS,
                input=transcript,
                max_output_tokens=120,
                temperature=0.3,
                timeout=45.0,
            )
        except Exception as exc:
            self._log(f'[error] openai text failed: {exc}')
            self._speak_local_fallback('internet unavailable', session_id)
            return None
        if self._cancelled(session_id):
            return None
        reply = (getattr(response, "output_text", "") or "").strip()
        return reply or None

    def _synthesize_speech(self, text, session_id):
        client = self._client()
        if client is None:
            self._log('[error] openai key missing for tts')
            self._speak_local_fallback('openai key missing', session_id)
            return None
        fd, wav_path = tempfile.mkstemp(prefix="pi_voice_out_", suffix=".wav")
        os.close(fd)
        try:
            audio = client.audio.speech.create(
                model=self.tts_model,
                voice=self.tts_voice,
                input=text,
                response_format="wav",
                timeout=45.0,
            )
            if self._cancelled(session_id):
                Path(wav_path).unlink(missing_ok=True)
                return None
            audio.write_to_file(wav_path)
            return wav_path
        except Exception as exc:
            Path(wav_path).unlink(missing_ok=True)
            self._log(f'[error] openai tts failed: {exc}')
            self._speak_local_fallback('internet unavailable', session_id)
            return None

    def _play_audio(self, wav_path, session_id):
        self._log(f"[playback] using device={self.playback_device}")
        proc = subprocess.Popen(["aplay", "-D", self.playback_device, "-q", str(wav_path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self._set_proc("_play_proc", proc)
        try:
            while proc.poll() is None:
                if self._cancelled(session_id):
                    self._terminate_proc("_play_proc")
                    self._log('[step] playback cancelled')
                    return
                time.sleep(0.1)
        finally:
            if proc.stderr is not None:
                err = proc.stderr.read().strip()
                if err:
                    self._log(f"[error] aplay failed: {err[:200]}")
            self._set_proc("_play_proc", None)

    def _speak_local_fallback(self, text, session_id):
        if self._cancelled(session_id):
            return
        if not self.piper_bin or not self.piper_voice:
            self._log(f'[fallback] {text}')
            return
        fd, wav_path = tempfile.mkstemp(prefix="pi_voice_fallback_", suffix=".wav")
        os.close(fd)
        try:
            synth = subprocess.run(
                [str(self.piper_bin), "-q", "-m", str(self.piper_voice), "-f", wav_path],
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            if synth.returncode != 0 or self._cancelled(session_id):
                Path(wav_path).unlink(missing_ok=True)
                self._log(f'[fallback] {text}')
                return
            self._play_audio(wav_path, session_id)
        except Exception:
            self._log(f'[fallback] {text}')
        finally:
            Path(wav_path).unlink(missing_ok=True)
