#!/usr/bin/env python3
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import wave
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

from openai import OpenAI

try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None


SYSTEM_INSTRUCTIONS = (
    "You are the voice brain for a Raspberry Pi home assistant device inside a home. "
    "Reply only in English using very short spoken sentences suitable for speech output whenever possible. "
    "Prefer the shortest helpful answer by default, and only give a longer answer if the user explicitly asks for more detail or explanation. "
    "This robot can control three devices: a fan, a red light, and a green light. "
    "The input is an automatic speech transcript and may contain recognition mistakes. "
    "Silently correct obvious transcription errors when the intended meaning is clear from context, "
    "for example 'turn on lim' should be treated as 'turn on light'. "
    "If the meaning is unclear, ask the user to repeat it in a short natural way, such as Could you repeat that? or Could you say that again? "
    "If the user asks to turn on or off a light but does not specify a color, ask whether they want the red light or the green light. "
    "When the user asks for an action, your reply can be short but a little more natural and expressive, "
    "for example Sure, I can turn on that light or I will turn on the light for you. "
    "Do not use markdown or bullet points."
)

DEVICE_PLANNER_INSTRUCTIONS = (
    "You are a device action planner for a Raspberry Pi home assistant. "
    "You must decide whether the user is asking to control devices. "
    "The only controllable devices are fan, red_light, and green_light. "
    "The only supported actions are on, off, and blink. "
    "Optional timing fields are duration_seconds and delay_seconds. "
    "If the user says all or everything, that means all three devices. "
    "If the user says light without a color, ask whether they want red or green. "
    "If the request is not a device-control request, return intent not_device. "
    "Return only valid JSON with this shape: "
    "{\"intent\":\"device_control|clarify|not_device\","
    "\"reply\":\"short spoken reply\","
    "\"targets\":[\"fan\"|\"red_light\"|\"green_light\"|\"all\"],"
    "\"action\":\"on|off|blink|null\","
    "\"duration_seconds\":null_or_integer,"
    "\"delay_seconds\":null_or_integer}. "
    "Do not use markdown. Do not add extra text."
)

TTS_INSTRUCTIONS = (
    "Voice Affect: Energetic and animated; dynamic with variations in pitch and tone. "
    "Tone: Excited and enthusiastic, conveying an upbeat and thrilling atmosphere. "
    "Pacing: Rapid delivery when describing the game or key moments to convey intensity and build excitement. "
    "Use slightly slower pacing during dramatic pauses to let key points sink in. "
    "Emotion: Intensely focused and excited, with positive energy. "
    "Personality: Relatable and engaging. "
    "Pauses: Use short, purposeful pauses after key moments in the game."
)

DAVID_TTS_INSTRUCTIONS = (
    "Voice Affect: Happy, playful, and upbeat. "
    "Tone: Cheerful and friendly, like a comedian telling a fun story. "
    "Pacing: Smooth and lively. "
    "Emotion: Warm, amused, and lighthearted. "
    "Personality: Charming, funny, and easy to listen to."
)

PI_ASSISTANT_HOME = Path(os.environ.get("PI_ASSISTANT_HOME", str(Path.home())))
PICOCLAW = Path(os.environ.get("PICOCLAW_BIN", str(PI_ASSISTANT_HOME / ".local" / "opt" / "picoclaw" / "picoclaw")))
PICOCLAW_BASE = Path(os.environ.get("PICOCLAW_AGENTS_DIR", str(PI_ASSISTANT_HOME / "picoclaw_agents")))
AI_NEWS_URL = "https://www.artificialintelligence-news.com/"
NEWS_USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

AGENTS = {
    "rose": {
        "name": "Rose Carter",
        "role_label": "AI assistant",
        "mode": "native",
        "voice": "coral",
        "aliases": ["rose carter", "rose"],
        "system_instructions": "Your name is Rose Carter. " + SYSTEM_INSTRUCTIONS,
        "tts_instructions": TTS_INSTRUCTIONS,
    },
    "david": {
        "name": "David Miller",
        "role_label": "comedian",
        "mode": "picoclaw",
        "home": PICOCLAW_BASE / "story_home",
        "voice": "ash",
        "aliases": ["david miller", "david"],
        "prompt": (
            "You are David Miller. You are a cheerful comedian and funny storyteller. "
            "Only tell funny stories, jokes, playful banter, and light entertainment. "
            "If the user asks for device control, serious analysis, or political stories, "
            "reply briefly that David only does funny stories."
        ),
        "tts_instructions": DAVID_TTS_INSTRUCTIONS,
    },
    "maya": {
        "name": "Maya Chen",
        "role_label": "AI news reader",
        "mode": "news",
        "voice": "sage",
        "aliases": ["maya chen", "maya", "ai news", "news"],
        "tts_instructions": (
            "Voice Affect: Clear, calm, and informative. "
            "Tone: Professional and concise, like a news reader. "
            "Pacing: Steady and easy to follow. "
            "Emotion: Neutral but engaged."
        ),
    },
    "anna": {
        "name": "Andrew Lee",
        "role_label": "translator",
        "mode": "native",
        "voice": "ash",
        "aliases": ["andrew lee", "andrew", "anna lee", "anna", "translator", "translate"],
        "system_instructions": (
            "Your name is Andrew Lee. "
            "You are a speech repeater and translator. "
            "Your job is to repeat what the user said in natural English. "
            "If the user speaks in another language, translate it into short, natural English. "
            "If the user already speaks English, restate it clearly in English. "
            "Do not explain the translation. Do not add commentary. "
            "Reply with only the translated or restated English sentence."
        ),
        "tts_instructions": (
            "Voice Affect: Clear, steady, and friendly. "
            "Tone: Warm, helpful, and easy to understand, with a male speaking style. "
            "Pacing: Smooth and steady. "
            "Emotion: Calm and neutral."
        ),
    },
}

AGENT_SELECTION_PROMPT = (
    "Which AI agent would you like to choose? You can choose Rose Carter, the AI assistant; "
    "David Miller, the comedian; Maya Chen, the AI news reader; or Andrew Lee, the translator. "
    "Say the full name, or say number one, number two, number three, or number four."
)



class DeviceController:
    def __init__(self, logger):
        self._log = logger
        self._lock = threading.Lock()
        self._gpio_ready = False
        self._timers = {}
        self._blink_stop = {}
        self._devices = {
            "fan": {
                "label": "fan",
                "pin": int(os.environ.get("PI_FAN_PIN", "16")),
                "active_low": os.environ.get("PI_FAN_ACTIVE_LOW", "0") == "1",
            },
            "red_light": {
                "label": "red light",
                "pin": int(os.environ.get("PI_RED_LIGHT_PIN", "20")),
                "active_low": os.environ.get("PI_RED_LIGHT_ACTIVE_LOW", "0") == "1",
            },
            "green_light": {
                "label": "green light",
                "pin": int(os.environ.get("PI_GREEN_LIGHT_PIN", "21")),
                "active_low": os.environ.get("PI_GREEN_LIGHT_ACTIVE_LOW", "0") == "1",
            },
        }
        self._state = {name: False for name in self._devices}
        self._mode = {name: "off" for name in self._devices}
        self._setup_gpio()

    def _setup_gpio(self):
        if GPIO is None:
            self._log("[device] RPi.GPIO unavailable")
            return
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for cfg in self._devices.values():
            initial = GPIO.HIGH if cfg["active_low"] else GPIO.LOW
            GPIO.setup(cfg["pin"], GPIO.OUT, initial=initial)
        self._gpio_ready = True
        self._log("[device] gpio ready: fan=16 red=20 green=21")

    def _output_level(self, device_name, on):
        cfg = self._devices[device_name]
        if cfg["active_low"]:
            return GPIO.LOW if on else GPIO.HIGH
        return GPIO.HIGH if on else GPIO.LOW

    def _cancel_timer_locked(self, device_name):
        timer = self._timers.pop(device_name, None)
        if timer is not None:
            timer.cancel()

    def _stop_blink_locked(self, device_name):
        stop_event = self._blink_stop.pop(device_name, None)
        if stop_event is not None:
            stop_event.set()

    def _cancel_activity_locked(self, device_name):
        self._cancel_timer_locked(device_name)
        self._stop_blink_locked(device_name)

    def _set_device_locked(self, device_name, on, mode=None, log_change=True):
        if self._gpio_ready:
            GPIO.output(self._devices[device_name]["pin"], self._output_level(device_name, on))
        self._state[device_name] = on
        self._mode[device_name] = mode or ("on" if on else "off")
        if log_change:
            self._log(f"[device] {self._devices[device_name]['label']} -> {self._mode[device_name]}")

    def _auto_turn_off(self, device_name):
        with self._lock:
            self._timers.pop(device_name, None)
            self._stop_blink_locked(device_name)
            self._set_device_locked(device_name, False, mode="off")

    def _blink_worker(self, device_name, stop_event, interval=0.5):
        current = False
        while not stop_event.is_set():
            current = not current
            with self._lock:
                if self._blink_stop.get(device_name) is not stop_event:
                    return
                self._set_device_locked(device_name, current, mode="blinking", log_change=False)
            if stop_event.wait(interval):
                break

    def _start_blink_locked(self, device_name, duration=None):
        stop_event = threading.Event()
        self._blink_stop[device_name] = stop_event
        self._mode[device_name] = "blinking"
        self._log(f"[device] {self._devices[device_name]['label']} -> blinking")
        worker = threading.Thread(target=self._blink_worker, args=(device_name, stop_event), daemon=True)
        worker.start()
        if duration:
            timer = threading.Timer(duration, self._auto_turn_off, args=(device_name,))
            timer.daemon = True
            timer.start()
            self._timers[device_name] = timer

    def _apply_action_locked(self, devices, action, duration=None, blink=False):
        for device_name in devices:
            self._cancel_activity_locked(device_name)
            if blink and action == "on":
                self._start_blink_locked(device_name, duration=duration)
            else:
                self._set_device_locked(device_name, action == "on")
                if action == "on" and duration:
                    timer = threading.Timer(duration, self._auto_turn_off, args=(device_name,))
                    timer.daemon = True
                    timer.start()
                    self._timers[device_name] = timer

    def _run_scheduled_action(self, device_name, action, duration=None, blink=False):
        with self._lock:
            self._timers.pop(device_name, None)
            self._apply_action_locked([device_name], action, duration=duration, blink=blink)

    def apply(self, devices, action, duration=None, delay=None, blink=False):
        applied = []
        with self._lock:
            if delay:
                for device_name in devices:
                    self._cancel_activity_locked(device_name)
                    timer = threading.Timer(delay, self._run_scheduled_action, args=(device_name, action, duration, blink))
                    timer.daemon = True
                    timer.start()
                    self._timers[device_name] = timer
                    self._mode[device_name] = f"scheduled {action}"
                    self._state[device_name] = False
                    applied.append(device_name)
            else:
                self._apply_action_locked(devices, action, duration=duration, blink=blink)
                applied.extend(devices)
        return applied

    def get_state_snapshot(self):
        with self._lock:
            return {
                name: {
                    "on": self._state[name],
                    "mode": self._mode[name],
                }
                for name in self._devices
            }

    def cleanup(self):
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            for stop_event in self._blink_stop.values():
                stop_event.set()
            self._blink_stop.clear()
        if self._gpio_ready:
            GPIO.cleanup([cfg["pin"] for cfg in self._devices.values()])
            self._gpio_ready = False


class PiVoiceRuntimeOpenAI:
    def __init__(self):
        self.transcribe_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
        self.text_model = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4.1-nano")
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
        self.tts_voice = os.environ.get("OPENAI_TTS_VOICE", "coral")
        self.tts_speed = float(os.environ.get("OPENAI_TTS_SPEED", "1.1"))
        self.tts_gain = float(os.environ.get("PI_TTS_GAIN", "2.5"))
        self.capture_device = os.environ.get("PI_MIC_DEVICE", "plughw:Microphone,0")
        self.record_seconds = int(os.environ.get("PI_RECORD_SECONDS", "5"))
        self.playback_device = os.environ.get("PI_SPEAKER_DEVICE", "plughw:CARD=Headphones,DEV=0")
        self.conversation_timeout = int(os.environ.get("PI_CONVERSATION_TIMEOUT", "25"))
        self.max_output_tokens = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "1000"))
        self.max_history_turns = int(os.environ.get("PI_MAX_HISTORY_TURNS", "3"))
        self.preferred_wifi_ssid = os.environ.get("PI_PREFERRED_WIFI_SSID", "")
        self.preferred_wifi_password = os.environ.get("PI_PREFERRED_WIFI_PASSWORD", "")
        self.fallback_wifi_ssid = os.environ.get("PI_FALLBACK_WIFI_SSID", "")
        self._last_wifi_attempt = 0.0
        self._wifi_retry_seconds = int(os.environ.get("PI_WIFI_RETRY_SECONDS", "30"))
        self._ignored_phrases = {
            "uh", "um", "hmm", "huh", "mm", "mmm", "ah", "er", "uhh", "umm", "hm",
        }
        self._end_phrases = {
            "goodbye", "goodbye robot", "bye", "bye robot", "stop listening", "go idle", "go to sleep",
        }
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._worker = None
        self._session_id = 0
        self._record_proc = None
        self._play_proc = None
        self._history = []
        self._status_callback = None
        self._transcript_callback = None
        self._agent_callback = None
        self._pending_device_intent = None
        self._pending_confirmation_action = None
        self._awaiting_agent_switch = False
        self.current_agent = "rose"
        self._news_last_items = []
        self._news_used_links = set()
        self.device_controller = DeviceController(self._log)

    def _log(self, message):
        print(message, flush=True)

    def set_status_callback(self, callback):
        self._status_callback = callback

    def set_transcript_callback(self, callback):
        self._transcript_callback = callback

    def set_agent_callback(self, callback):
        self._agent_callback = callback
        self._update_agent_display()

    def _update_status(self, status, check_internet=False):
        if self._status_callback is not None:
            try:
                self._status_callback(status, check_internet)
            except Exception:
                pass

    def _update_transcript(self, transcript):
        if self._transcript_callback is not None:
            try:
                self._transcript_callback(transcript)
            except Exception:
                pass

    def _update_agent_display(self):
        if self._agent_callback is not None:
            try:
                agent = AGENTS[self.current_agent]
                self._agent_callback(agent["name"], agent["role_label"])
            except Exception:
                pass

    def _listen_once(self, session_id, seconds=None):
        listen_seconds = seconds if seconds is not None else self.record_seconds
        self._log("[step] recording microphone")
        self._update_status("listening.....", check_internet=True)
        wav_path = self._record_utterance(session_id, listen_seconds)
        if self._cancelled(session_id):
            Path(wav_path).unlink(missing_ok=True) if wav_path else None
            self._log("[step] reset won before transcription")
            return None
        if not wav_path:
            self._log("[step] no audio captured")
            return None
        try:
            if self._cancelled(session_id):
                self._log("[step] reset won before transcription")
                return None
            self._log("[step] sending audio to openai transcription")
            self._update_status("transcripting....", check_internet=True)
            transcript = self._transcribe_openai(wav_path, session_id)
        finally:
            Path(wav_path).unlink(missing_ok=True)
        if transcript:
            self._log(f"[transcript] {transcript}")
            self._update_transcript(transcript)
        return transcript

    def _speak_text(self, text, session_id, agent_key=None):
        self._log(f"[reply] {text}")
        self._log("[step] generating speech")
        self._update_status("agent speaking...", check_internet=True)
        tts_path = self._synthesize_speech(text, session_id, agent_key=agent_key)
        if not tts_path or self._cancelled(session_id):
            self._log("[step] tts failed or cancelled -> idle")
            self._update_status("idle", check_internet=True)
            return False
        try:
            self._log("[step] playing audio")
            self._play_audio(tts_path, session_id)
        finally:
            Path(tts_path).unlink(missing_ok=True)
        return not self._cancelled(session_id)

    def _play_cached_prompt(self, text, wav_path, session_id):
        self._log(f"[reply] {text}")
        if not wav_path.exists():
            return self._speak_text(text, session_id, agent_key="rose")
        self._log(f"[tts] using cached prompt={wav_path.name}")
        self._update_status("agent speaking...", check_internet=True)
        self._log("[step] playing audio")
        self._play_audio(str(wav_path), session_id)
        return not self._cancelled(session_id)

    def is_active(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def start_session(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                self._log("[session] already active")
                return False
            self._cancel_event.clear()
            self._session_id += 1
            session_id = self._session_id
            self._history = []
            self._pending_device_intent = None
            self._pending_confirmation_action = None
            self._awaiting_agent_switch = False
            self._news_last_items = []
            self._news_used_links = set()
            worker = threading.Thread(target=self._run_session, args=(session_id,), daemon=True)
            self._worker = worker
            self._log("[session] started")
            self._log("[memory] cleared for new wake session")
            worker.start()
            return True

    def cancel_session(self):
        self._log("[session] stopping active audio/openai work")
        self._update_status("idle", check_internet=True)
        self._cancel_event.set()
        with self._lock:
            self._session_id += 1
            self._worker = None
            self._history = []
            self._pending_device_intent = None
            self._pending_confirmation_action = None
            self._awaiting_agent_switch = False
            self._news_last_items = []
            self._news_used_links = set()
        self._terminate_proc("_record_proc")
        self._terminate_proc("_play_proc")
        self._log("[memory] cleared by reset")
        self._log("[idle] sensor-only mode")

    def shutdown(self):
        self.cancel_session()
        self.device_controller.cleanup()

    def prewarm_tts(self):
        return None

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

    def _current_wifi_ssid(self):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", "wlan0"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            ssid = ""
            for line in result.stdout.splitlines():
                if line.startswith("GENERAL.CONNECTION:"):
                    ssid = line.split(":", 1)[1].strip()
                    break
            if ssid:
                return ssid
        except Exception:
            pass
        return ""

    def _try_wifi_profile(self, ssid):
        if not ssid:
            return False
        try:
            result = subprocess.run(
                ["nmcli", "con", "up", "id", ssid],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            self._log(f"[wifi] failed to activate {ssid}: {exc}")
            return False
        if result.returncode == 0:
            self._log(f"[wifi] connected to {ssid}")
            return True
        return False

    def _try_wifi_connect(self, ssid, password):
        if not ssid or not password:
            return False
        try:
            result = subprocess.run(
                ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            self._log(f"[wifi] failed to connect to {ssid}: {exc}")
            return False
        if result.returncode == 0:
            self._log(f"[wifi] connected to {ssid}")
            return True
        return False

    def _ensure_preferred_wifi(self, force=False):
        current_ssid = self._current_wifi_ssid()
        if current_ssid == self.preferred_wifi_ssid:
            return current_ssid
        now = time.monotonic()
        if not force and now - self._last_wifi_attempt < self._wifi_retry_seconds:
            return current_ssid
        self._last_wifi_attempt = now
        self._log(f"[wifi] current network: {current_ssid or 'offline'}")
        if self._try_wifi_profile(self.preferred_wifi_ssid):
            return self.preferred_wifi_ssid
        if self._try_wifi_connect(self.preferred_wifi_ssid, self.preferred_wifi_password):
            return self.preferred_wifi_ssid
        if self.fallback_wifi_ssid and self.fallback_wifi_ssid != self.preferred_wifi_ssid:
            self._log(f"[wifi] hotspot failed, trying fallback: {self.fallback_wifi_ssid}")
            if self._try_wifi_profile(self.fallback_wifi_ssid):
                return self.fallback_wifi_ssid
        return self._current_wifi_ssid()

    def ensure_preferred_wifi_on_startup(self):
        return self._ensure_preferred_wifi(force=True)

    def _client(self):
        self._ensure_preferred_wifi()
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            key_file = Path(os.environ.get("OPENAI_API_KEY_FILE", str(PI_ASSISTANT_HOME / ".openai_key")))
            if key_file.exists():
                api_key = key_file.read_text().strip()
        if not api_key:
            return None
        return OpenAI(api_key=api_key, timeout=90.0)

    def _build_picoclaw_env(self, agent_key):
        self._ensure_preferred_wifi()
        env = os.environ.copy()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            key_file = Path(os.environ.get("OPENAI_API_KEY_FILE", str(PI_ASSISTANT_HOME / ".openai_key")))
            if key_file.exists():
                api_key = key_file.read_text().strip()
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        env["HOME"] = str(AGENTS[agent_key]["home"])
        env["GOMAXPROCS"] = env.get("GOMAXPROCS", "1")
        return env

    def _strip_ansi(self, text):
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)

    def _extract_picoclaw_reply(self, output):
        cleaned = self._strip_ansi(output).replace("\r", "")
        if "🦞" in cleaned:
            return cleaned.split("🦞", 1)[1].strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def _switch_agent(self, agent_key):
        self.current_agent = agent_key
        self._awaiting_agent_switch = False
        self._pending_device_intent = None
        self._pending_confirmation_action = None
        self._news_last_items = []
        self._news_used_links = set()
        self._update_agent_display()

    def _detect_agent_name(self, normalized):
        for key, agent in AGENTS.items():
            for alias in agent.get("aliases", []) + [key]:
                if re.search(rf"\b{re.escape(alias)}\b", normalized):
                    return key
            if re.search(rf"\b{re.escape(key)}\b", normalized):
                return key
        return None

    def _best_phrase_similarity(self, normalized, phrase):
        tokens = normalized.split()
        phrase_tokens = phrase.split()
        variants = {normalized}
        for window_len in {max(1, len(phrase_tokens) - 1), len(phrase_tokens), len(phrase_tokens) + 1, len(phrase_tokens) + 2}:
            if window_len > len(tokens):
                continue
            for index in range(len(tokens) - window_len + 1):
                variants.add(" ".join(tokens[index:index + window_len]))
        return max(SequenceMatcher(None, phrase, variant).ratio() for variant in variants)

    def _guess_agent_choice(self, normalized):
        if not normalized:
            return None, 0.0, None
        candidate_phrases = {
            "rose": ["rose carter", "rose", "number one", "agent one", "option one", "choice one"],
            "david": ["david miller", "david", "number two", "agent two", "option two", "choice two"],
            "maya": ["maya chen", "maya", "ai news", "number three", "agent three", "option three", "choice three"],
            "anna": ["andrew lee", "andrew", "translator", "number four", "agent four", "option four", "choice four", "anna lee", "anna"],
        }
        scores = []
        for key, phrases in candidate_phrases.items():
            for phrase in phrases:
                scores.append((self._best_phrase_similarity(normalized, phrase), key, phrase))
        scores.sort(reverse=True)
        if not scores:
            return None, 0.0, None
        best_score, best_key, best_phrase = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0
        numeric_hint = bool(re.search(r"\b(number|one|two|three|four|1|2|3|4)\b", normalized))
        threshold = 0.72 if numeric_hint else 0.78
        if best_score < threshold:
            return None, best_score, best_phrase
        if best_score - second_score < 0.06:
            return None, best_score, best_phrase
        return best_key, best_score, best_phrase

    def _detect_agent_choice(self, normalized, allow_fuzzy=False):
        chosen = self._detect_agent_name(normalized)
        if chosen:
            return chosen
        if re.search(r"\b(number one|one|1)\b", normalized):
            return "rose"
        if re.search(r"\b(number two|two|2)\b", normalized):
            return "david"
        if re.search(r"\b(number three|three|3)\b", normalized):
            return "maya"
        if re.search(r"\b(number four|four|4)\b", normalized):
            return "anna"
        if allow_fuzzy:
            guessed, score, phrase = self._guess_agent_choice(normalized)
            if guessed:
                self._log(f"[agent] guessed {AGENTS[guessed]['name']} from '{normalized}' via '{phrase}' ({score:.2f})")
                return guessed
        return None

    def _normalize_text(self, text):
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-z0-9' ]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    def _handle_agent_switch_command(self, transcript):
        normalized = self._normalize_text(transcript)

        if self._awaiting_agent_switch:
            chosen = self._detect_agent_choice(normalized, allow_fuzzy=True)
            if chosen:
                self._switch_agent(chosen)
                return self._handled_local_reply(
                    transcript,
                    f"Switched to {AGENTS[chosen]['name']}. {AGENTS[chosen]['name']} is your {AGENTS[chosen]['role_label']}.",
                )
            if normalized in {"cancel", "never mind", "nevermind"}:
                self._awaiting_agent_switch = False
                return self._handled_local_reply(transcript, "Okay, staying with the current agent.")
            return self._handled_local_reply(transcript, AGENT_SELECTION_PROMPT)

        if "switch agent" in normalized or "change agent" in normalized:
            chosen = self._detect_agent_choice(normalized, allow_fuzzy=True)
            if chosen:
                self._switch_agent(chosen)
                return self._handled_local_reply(
                    transcript,
                    f"Switched to {AGENTS[chosen]['name']}. {AGENTS[chosen]['name']} is your {AGENTS[chosen]['role_label']}.",
                )
            self._awaiting_agent_switch = True
            return self._handled_local_reply(transcript, AGENT_SELECTION_PROMPT)

        return None

    def _parse_duration_seconds(self, normalized):
        match = re.search(r"\bfor (\d+)\s*(second|seconds|sec|secs)\b", normalized)
        if match:
            return int(match.group(1))
        return self._parse_named_seconds(normalized, prefix="for")

    def _parse_delay_seconds(self, normalized):
        match = re.search(r"\bin (\d+)\s*(second|seconds|sec|secs)\b", normalized)
        if match:
            return int(match.group(1))
        return self._parse_named_seconds(normalized, prefix="in")

    def _parse_named_seconds(self, normalized, prefix):
        word_map = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "fifteen": 15,
            "twenty": 20,
            "thirty": 30,
            "sixty": 60,
        }
        match = re.search(
            rf"\b{prefix} (one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|sixty)\s*"
            r"(second|seconds|sec|secs)\b",
            normalized,
        )
        if match:
            return word_map[match.group(1)]
        return None

    def _detect_device_action(self, normalized):
        if re.search(r"\b(turn|switch|power)\b.*\bon\b", normalized):
            return "on"
        if re.search(r"\b(turn|switch|power)\b.*\boff\b", normalized):
            return "off"
        action_patterns = {
            "on": ["start", "enable"],
            "off": ["stop", "disable"],
        }
        for action, patterns in action_patterns.items():
            if any(pattern in normalized for pattern in patterns):
                return action
        if normalized.endswith(" on"):
            return "on"
        if normalized.endswith(" off"):
            return "off"
        return None

    def _detect_blink(self, normalized):
        return bool(re.search(r"\b(blink|blinking|flash|flashing)\b", normalized))

    def _is_device_related(self, normalized):
        keywords = (
            "fan",
            "light",
            "lights",
            "led",
            "red",
            "green",
            "relay",
            "all",
            "everything",
        )
        return any(keyword in normalized for keyword in keywords)

    def _detect_devices(self, normalized):
        devices = set()
        if re.search(r"\b(all|everything)\b", normalized):
            devices.update({"fan", "red_light", "green_light"})

        if "fan" in normalized or "relay" in normalized:
            devices.add("fan")

        has_red = bool(re.search(r"\bred\b", normalized))
        has_green = bool(re.search(r"\bgreen\b", normalized))
        mentions_light = bool(re.search(r"\b(light|lights|led|leds)\b", normalized))
        mentions_both = "both" in normalized or "all lights" in normalized

        if has_red:
            devices.add("red_light")
        if has_green:
            devices.add("green_light")
        if mentions_both and mentions_light:
            devices.update({"red_light", "green_light"})
        if re.search(r"\bred and green\b", normalized) or re.search(r"\bgreen and red\b", normalized):
            devices.update({"red_light", "green_light"})
        return devices

    def _device_clarification(self, normalized, action):
        if not action:
            return None
        if re.search(r"\b(light|led)\b", normalized) and not re.search(r"\b(red|green|both)\b", normalized):
            return f"Do you want red or green light {'on' if action == 'on' else 'off'}?"
        return None

    def _set_pending_device_intent(self, action, duration=None, delay=None, blink=False):
        self._pending_device_intent = {
            "action": action,
            "duration": duration,
            "delay": delay,
            "blink": blink,
        }

    def _clear_pending_device_intent(self):
        self._pending_device_intent = None

    def _set_pending_confirmation_action(self, action_plan):
        self._pending_confirmation_action = action_plan

    def _clear_pending_confirmation_action(self):
        self._pending_confirmation_action = None

    def _matches_spoken_choice(self, normalized, phrases, threshold=0.76):
        if not normalized:
            return False
        if normalized in phrases:
            return True
        tokens = normalized.split()
        candidates = {normalized}
        for phrase in phrases:
            phrase_tokens = phrase.split()
            for window_len in {max(1, len(phrase_tokens) - 1), len(phrase_tokens), len(phrase_tokens) + 1}:
                if window_len > len(tokens):
                    continue
                for index in range(len(tokens) - window_len + 1):
                    candidates.add(" ".join(tokens[index:index + window_len]))
        best = 0.0
        for phrase in phrases:
            for candidate in candidates:
                best = max(best, SequenceMatcher(None, phrase, candidate).ratio())
        return best >= threshold

    def _is_affirmative(self, normalized):
        phrases = {
            "yes",
            "yeah",
            "yep",
            "yes sir",
            "yes please",
            "please",
            "please yes",
            "do it",
            "okay",
            "ok",
            "sure",
            "please do",
            "correct",
            "that's right",
            "right",
            "yee",
            "yea",
            "ya",
        }
        return self._matches_spoken_choice(normalized, phrases, threshold=0.74)

    def _is_negative(self, normalized):
        phrases = {
            "no",
            "nope",
            "nah",
            "no thanks",
            "don't",
            "do not",
            "wrong",
            "not that",
            "no thank you",
            "stop",
        }
        return self._matches_spoken_choice(normalized, phrases, threshold=0.76)

    def _build_action_plan(self, devices, action, duration=None, delay=None, blink=False):
        return {
            "devices": list(devices),
            "action": action,
            "duration": duration,
            "delay": delay,
            "blink": blink,
        }

    def _device_state_for_planner(self):
        snapshot = self.device_controller.get_state_snapshot()
        parts = []
        for name in ("fan", "red_light", "green_light"):
            info = snapshot[name]
            parts.append(f"{name}={info['mode']}")
        return ", ".join(parts)

    def _extract_json_object(self, text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    def _normalize_planner_targets(self, raw_targets):
        if raw_targets is None:
            return []
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        if not isinstance(raw_targets, list):
            return []
        devices = []
        for item in raw_targets:
            if not isinstance(item, str):
                continue
            key = item.strip().lower()
            if key in {"all", "everything"}:
                for name in ("fan", "red_light", "green_light"):
                    if name not in devices:
                        devices.append(name)
                continue
            if key in {"all_lights", "lights", "both_lights"}:
                for name in ("red_light", "green_light"):
                    if name not in devices:
                        devices.append(name)
                continue
            alias_map = {
                "fan": "fan",
                "relay": "fan",
                "red": "red_light",
                "red light": "red_light",
                "red_light": "red_light",
                "green": "green_light",
                "green light": "green_light",
                "green_light": "green_light",
            }
            mapped = alias_map.get(key)
            if mapped and mapped not in devices:
                devices.append(mapped)
        return devices

    def _coerce_optional_int(self, value):
        if value in (None, "", False):
            return None
        try:
            coerced = int(value)
        except Exception:
            return None
        if coerced < 0:
            return None
        return coerced

    def _plan_device_command_with_ai(self, transcript, session_id):
        client = self._client()
        if client is None:
            return None
        planner_input = (
            f"Current device state: {self._device_state_for_planner()}\n"
            f"User transcript: {transcript}"
        )
        try:
            response = client.responses.create(
                model=self.text_model,
                instructions=DEVICE_PLANNER_INSTRUCTIONS,
                input=planner_input,
                max_output_tokens=220,
                timeout=25.0,
            )
        except Exception as exc:
            self._log(f"[device] ai planner failed: {exc}")
            return None
        if self._cancelled(session_id):
            return None
        raw = (getattr(response, "output_text", "") or "").strip()
        if not raw:
            pieces = []
            for item in getattr(response, "output", []) or []:
                for content in getattr(item, "content", []) or []:
                    text = getattr(content, "text", "") or ""
                    if text:
                        pieces.append(text)
            raw = " ".join(pieces).strip()
        data = self._extract_json_object(raw)
        if not isinstance(data, dict):
            return None

        intent = str(data.get("intent", "")).strip().lower()
        reply = str(data.get("reply", "")).strip()
        if intent == "not_device":
            return None
        if intent == "clarify":
            action = str(data.get("action", "")).strip().lower() or None
            duration = self._coerce_optional_int(data.get("duration_seconds"))
            delay = self._coerce_optional_int(data.get("delay_seconds"))
            if action in {"blink", "on", "off"} and "red" in reply.lower() and "green" in reply.lower():
                self._set_pending_device_intent(action if action != "blink" else "on", duration=duration, delay=delay, blink=(action == "blink"))
            if reply:
                self._append_history("user", transcript)
                self._append_history("assistant", reply)
                return {"reply": reply, "handled": True}
            return None
        if intent != "device_control":
            return None

        action = str(data.get("action", "")).strip().lower()
        if action not in {"on", "off", "blink"}:
            return None
        devices = self._normalize_planner_targets(data.get("targets"))
        if not devices:
            return None
        duration = self._coerce_optional_int(data.get("duration_seconds"))
        delay = self._coerce_optional_int(data.get("delay_seconds"))
        ordered_devices = [name for name in ("fan", "red_light", "green_light") if name in devices]
        blink = action == "blink"
        plan_action = "on" if blink else action
        action_plan = self._build_action_plan(
            ordered_devices,
            plan_action,
            duration=duration,
            delay=delay,
            blink=blink,
        )
        final_reply = reply or self._build_device_reply(
            ordered_devices,
            plan_action,
            duration,
            delay=delay,
            blink=blink,
        )
        self._append_history("user", transcript)
        self._append_history("assistant", final_reply)
        return {"reply": final_reply, "handled": True, "post_action": action_plan}

    def _extract_action_plan_from_text(self, text):
        normalized = self._normalize_text(text)
        action = self._detect_device_action(normalized)
        blink = self._detect_blink(normalized)
        if blink and not action:
            action = "on"
        if not action or not self._is_device_related(normalized):
            return None
        devices = self._detect_devices(normalized)
        if not devices:
            return None
        ordered_devices = [name for name in ("fan", "red_light", "green_light") if name in devices]
        return self._build_action_plan(
            ordered_devices,
            action,
            duration=self._parse_duration_seconds(normalized),
            delay=self._parse_delay_seconds(normalized),
            blink=blink,
        )

    def _resolve_pending_confirmation_action(self, transcript):
        if not self._pending_confirmation_action:
            return None
        normalized = self._normalize_text(transcript)
        if self._is_affirmative(normalized):
            action_plan = self._pending_confirmation_action
            reply = self._build_device_reply(
                action_plan["devices"],
                action_plan["action"],
                action_plan.get("duration"),
                delay=action_plan.get("delay"),
                blink=action_plan.get("blink", False),
            )
            self._clear_pending_confirmation_action()
            self._append_history("user", transcript)
            self._append_history("assistant", reply)
            return {"reply": reply, "handled": True, "post_action": action_plan}
        if self._is_negative(normalized):
            self._clear_pending_confirmation_action()
            return self._handled_local_reply(transcript, "Okay, please say the command again.")
        return None

    def _resolve_pending_device_intent(self, transcript):
        if not self._pending_device_intent:
            return None
        normalized = self._normalize_text(transcript)
        devices = set()
        if re.search(r"\bred\b", normalized):
            devices.add("red_light")
        if re.search(r"\bgreen\b", normalized):
            devices.add("green_light")
        if "both" in normalized:
            devices.update({"red_light", "green_light"})
        if not devices:
            return None

        pending = self._pending_device_intent
        ordered_devices = [name for name in ("red_light", "green_light") if name in devices]
        reply = self._build_device_reply(
            ordered_devices,
            pending["action"],
            pending["duration"],
            delay=pending["delay"],
            blink=pending["blink"],
        )
        self._clear_pending_device_intent()
        self._append_history("user", transcript)
        self._append_history("assistant", reply)
        return {
            "reply": reply,
            "handled": True,
            "post_action": {
                "devices": ordered_devices,
                "action": pending["action"],
                "duration": pending["duration"],
                "delay": pending["delay"],
                "blink": pending["blink"],
            },
        }

    def _join_device_labels(self, devices):
        labels = [self.device_controller._devices[name]["label"] for name in devices]
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return ", ".join(labels[:-1]) + f", and {labels[-1]}"

    def _format_state_value(self, device_name, info):
        label = self.device_controller._devices[device_name]["label"]
        mode = info["mode"]
        if mode == "blinking":
            return f"{label} is blinking"
        if mode.startswith("scheduled "):
            return f"{label} is {mode}"
        return f"{label} is {'on' if info['on'] else 'off'}"

    def _build_state_summary(self, snapshot):
        ordered = ("fan", "red_light", "green_light")
        parts = [self._format_state_value(name, snapshot[name]) for name in ordered]
        return "Right now the " + ", the ".join(parts[:-1]) + ", and the " + parts[-1] + "."

    def _build_single_state_reply(self, device_name, is_on):
        label = self.device_controller._devices[device_name]["label"]
        return f"Yes, the {label} is on." if is_on else f"No, the {label} is off."

    def _build_mode_reply(self, device_name, info):
        label = self.device_controller._devices[device_name]["label"]
        mode = info["mode"]
        if mode == "blinking":
            return f"The {label} is blinking."
        if mode.startswith("scheduled "):
            return f"The {label} is {mode}."
        return self._build_single_state_reply(device_name, info["on"])

    def _is_state_query(self, normalized):
        patterns = (
            "what is on",
            "what's on",
            "what devices are on",
            "which device is on",
            "which devices are on",
            "status",
            "current state",
            "right now",
            "is the ",
            "are the ",
        )
        return any(pattern in normalized for pattern in patterns)

    def _handle_state_query(self, transcript):
        normalized = self._normalize_text(transcript)
        if not self._is_state_query(normalized):
            return None

        snapshot = self.device_controller.get_state_snapshot()

        if re.search(r"\b(light|led)\b", normalized) and not re.search(r"\b(red|green|both)\b", normalized):
            if normalized.startswith("is the") or normalized.startswith("are the"):
                return self._handled_local_reply(transcript, "Do you want the red light or the green light?")

        if "fan" in normalized:
            return self._handled_local_reply(transcript, self._build_mode_reply("fan", snapshot["fan"]))
        if re.search(r"\bred\b", normalized):
            return self._handled_local_reply(transcript, self._build_mode_reply("red_light", snapshot["red_light"]))
        if re.search(r"\bgreen\b", normalized):
            return self._handled_local_reply(transcript, self._build_mode_reply("green_light", snapshot["green_light"]))

        if "what is on" in normalized or "what's on" in normalized or "what devices are on" in normalized:
            on_devices = [
                self.device_controller._devices[name]["label"]
                for name in ("fan", "red_light", "green_light")
                if snapshot[name]["on"] or snapshot[name]["mode"] == "blinking"
            ]
            if not on_devices:
                return self._handled_local_reply(transcript, "Right now everything is off.")
            if len(on_devices) == 1:
                return self._handled_local_reply(transcript, f"Right now only the {on_devices[0]} is on.")
            if len(on_devices) == 2:
                return self._handled_local_reply(
                    transcript,
                    f"Right now the {on_devices[0]} and the {on_devices[1]} are on.",
                )
            return self._handled_local_reply(transcript, "Right now the fan, the red light, and the green light are on.")

        if (
            "status" in normalized
            or "current state" in normalized
            or "which device is on" in normalized
            or "which devices are on" in normalized
            or "right now" in normalized
        ):
            return self._handled_local_reply(transcript, self._build_state_summary(snapshot))

        return None

    def _build_device_reply(self, devices, action, duration, delay=None, blink=False):
        labels = self._join_device_labels(devices)
        if blink:
            if delay and duration:
                return f"Sure, I will make the {labels} blink in {delay} seconds for {duration} seconds."
            if delay:
                return f"Sure, I will make the {labels} blink in {delay} seconds."
            if duration:
                return f"Sure, I will make the {labels} blink for {duration} seconds."
            return f"Sure, I will make the {labels} blink."
        if delay and action == "on" and duration:
            return f"Sure, I will turn on the {labels} in {delay} seconds for {duration} seconds."
        if delay and action == "off":
            return f"Okay, I will turn off the {labels} in {delay} seconds."
        if delay and action == "on":
            return f"Sure, I will turn on the {labels} in {delay} seconds."
        if action == "on" and duration:
            return f"Sure, I turned on the {labels} for {duration} seconds."
        if action == "on":
            return f"Sure, I turned on the {labels}."
        return f"Okay, I turned off the {labels}."

    def _execute_device_action(self, action_plan):
        if not action_plan:
            return
        self.device_controller.apply(
            action_plan["devices"],
            action_plan["action"],
            duration=action_plan.get("duration"),
            delay=action_plan.get("delay"),
            blink=action_plan.get("blink", False),
        )

    def _handle_device_command(self, transcript):
        confirmation_result = self._resolve_pending_confirmation_action(transcript)
        if confirmation_result and confirmation_result.get("handled"):
            return confirmation_result
        pending_result = self._resolve_pending_device_intent(transcript)
        if pending_result and pending_result.get("handled"):
            return pending_result

        normalized = self._normalize_text(transcript)
        action = self._detect_device_action(normalized)
        blink = self._detect_blink(normalized)
        if blink and not action:
            action = "on"
        if not action and self._is_device_related(normalized):
            if self._parse_duration_seconds(normalized) is not None or self._parse_delay_seconds(normalized) is not None:
                action = "on"
        if not action:
            return None
        if not self._is_device_related(normalized):
            return None

        devices = self._detect_devices(normalized)
        if not devices:
            clarification = self._device_clarification(normalized, action)
            if clarification:
                duration = self._parse_duration_seconds(normalized)
                delay = self._parse_delay_seconds(normalized)
                self._set_pending_device_intent(action, duration=duration, delay=delay, blink=blink)
                return {"reply": clarification, "handled": True}
            return None

        clarification = self._device_clarification(normalized, action)
        if clarification and not ({"red_light", "green_light"} & devices):
            duration = self._parse_duration_seconds(normalized)
            delay = self._parse_delay_seconds(normalized)
            self._set_pending_device_intent(action, duration=duration, delay=delay, blink=blink)
            return {"reply": clarification, "handled": True}

        duration = self._parse_duration_seconds(normalized)
        delay = self._parse_delay_seconds(normalized)
        ordered_devices = [name for name in ("fan", "red_light", "green_light") if name in devices]
        reply = self._build_device_reply(ordered_devices, action, duration, delay=delay, blink=blink)
        self._clear_pending_device_intent()
        self._append_history("user", transcript)
        self._append_history("assistant", reply)
        return {
            "reply": reply,
            "handled": True,
            "post_action": {
                "devices": ordered_devices,
                "action": action,
                "duration": duration,
                "delay": delay,
                "blink": blink,
            },
        }

    def _capture_confirmation_from_reply(self, reply):
        stripped = reply.strip()
        if not stripped.lower().startswith("did you mean "):
            return
        action_plan = self._extract_action_plan_from_text(stripped)
        if action_plan:
            self._set_pending_confirmation_action(action_plan)
        else:
            self._clear_pending_confirmation_action()

    def _is_end_phrase(self, transcript):
        normalized = self._normalize_text(transcript)
        return normalized in self._end_phrases

    def _is_valid_followup(self, transcript):
        normalized = self._normalize_text(transcript)
        if not normalized:
            return False
        if normalized in self._ignored_phrases:
            return False
        words = [word for word in normalized.split() if word]
        if not words:
            return False
        if len(normalized) < 3:
            return False
        if all(len(word) == 1 for word in words):
            return False
        return True

    def _run_session(self, session_id):
        try:
            if self._cancelled(session_id):
                self._log("[session] cancelled before start")
                return
            deadline = time.monotonic() + self.conversation_timeout
            self._log(f"[conversation] active for {self.conversation_timeout}s")
            while not self._cancelled(session_id):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._log("[conversation] timeout -> idle")
                    break

                listen_seconds = max(1, min(self.record_seconds, int(remaining)))
                self._log(f"[conversation] waiting for speech ({int(remaining)}s left)")
                transcript = self._listen_once(session_id, seconds=listen_seconds)

                if self._cancelled(session_id):
                    break
                if not transcript:
                    self._log("[step] empty transcript ignored")
                    self._update_status("listening.....", check_internet=True)
                    continue
                if self._is_end_phrase(transcript):
                    self._log("[conversation] goodbye -> idle")
                    break
                if not self._is_valid_followup(transcript):
                    self._log(f"[conversation] ignored short/junk transcript: {transcript}")
                    self._update_status("listening.....", check_internet=True)
                    continue

                self._log(f"[transcript] {transcript}")
                self._update_transcript(transcript)
                deadline = time.monotonic() + self.conversation_timeout
                if self._cancelled(session_id):
                    self._log("[step] reset won before text step")
                    break
                reply = None
                post_action = None

                agent_switch_result = self._handle_agent_switch_command(transcript)
                if agent_switch_result and agent_switch_result.get("handled"):
                    reply = agent_switch_result.get("reply")
                    self._log("[agent] handled change-agent command")

                if not reply:
                    self._log("[step] sending transcript to openai")
                    self._update_status("send to AI.....", check_internet=True)

                if not reply and self.current_agent == "rose":
                    local_result = self._handle_state_query(transcript)
                    if local_result and local_result.get("handled"):
                        reply = local_result.get("reply")
                        self._log("[device] answered state query locally")
                    else:
                        device_result = self._plan_device_command_with_ai(transcript, session_id)
                        if device_result and device_result.get("handled"):
                            reply = device_result.get("reply")
                            post_action = device_result.get("post_action")
                            self._log("[device] handled command via ai planner")
                        else:
                            device_result = self._handle_device_command(transcript)
                        if device_result and device_result.get("handled") and not reply:
                            reply = device_result.get("reply")
                            post_action = device_result.get("post_action")
                            self._log("[device] handled command locally")
                        if not reply:
                            reply = self._generate_reply(transcript, session_id)
                            if reply:
                                self._capture_confirmation_from_reply(reply)
                elif not reply:
                    reply = self._generate_reply(transcript, session_id)
                    if reply:
                        self._clear_pending_confirmation_action()
                if self._cancelled(session_id):
                    self._log("[step] openai reply cancelled -> idle")
                    break
                if not reply:
                    self._log("[step] openai reply missing -> idle")
                    self._update_status("idle", check_internet=True)
                    break

                self._log(f"[reply] {reply}")
                self._log("[step] generating speech")
                self._update_status("agent speaking...", check_internet=True)
                tts_path = self._synthesize_speech(reply, session_id)
                if not tts_path or self._cancelled(session_id):
                    self._log("[step] tts failed or cancelled -> idle")
                    self._update_status("idle", check_internet=True)
                    break
                try:
                    self._log("[step] playing audio")
                    self._play_audio(tts_path, session_id)
                finally:
                    Path(tts_path).unlink(missing_ok=True)

                if self._cancelled(session_id):
                    break
                if post_action:
                    self._execute_device_action(post_action)
                deadline = time.monotonic() + self.conversation_timeout
                self._log(f"[conversation] follow-up window reset to {self.conversation_timeout}s")
                self._update_status("idle", check_internet=True)

            if not self._cancelled(session_id):
                self._log("[session] completed -> idle")
                self._update_status("idle", check_internet=True)
        finally:
            self._clear_worker(session_id)

    def _cancelled(self, session_id):
        return self._cancel_event.is_set() or session_id != self._session_id

    def _record_utterance(self, session_id, seconds=None):
        fd, wav_path = tempfile.mkstemp(prefix="pi_voice_in_", suffix=".wav")
        os.close(fd)
        duration = seconds if seconds is not None else self.record_seconds
        cmd = [
            "arecord", "-D", self.capture_device, "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(duration), wav_path,
        ]
        self._log(f"[record] using device={self.capture_device} seconds={duration}")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set_proc("_record_proc", proc)
        try:
            while proc.poll() is None:
                if self._cancelled(session_id):
                    self._terminate_proc("_record_proc")
                    self._log("[step] recording cancelled")
                    return None
                time.sleep(0.1)
        finally:
            self._set_proc("_record_proc", None)

        if proc.returncode != 0:
            Path(wav_path).unlink(missing_ok=True)
            self._log(f"[error] recorder exited with code {proc.returncode}")
            return None
        if not Path(wav_path).exists() or Path(wav_path).stat().st_size < 4096:
            Path(wav_path).unlink(missing_ok=True)
            return None
        return wav_path

    def _transcribe_openai(self, wav_path, session_id):
        client = self._client()
        if client is None:
            self._log("[error] openai key missing")
            return None
        try:
            with open(wav_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    file=audio_file,
                    model=self.transcribe_model,
                    language="en",
                    response_format="text",
                    timeout=60.0,
                )
        except Exception as exc:
            self._log(f"[error] openai transcription failed: {exc}")
            return None
        if self._cancelled(session_id):
            return None
        if isinstance(transcript, str):
            return transcript.strip() or None
        text = getattr(transcript, "text", "") or ""
        return text.strip() or None

    def _build_messages(self, transcript):
        messages = []
        for item in self._history:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": transcript})
        return messages

    def _append_history(self, role, content):
        if not content:
            return
        self._history.append({"role": role, "content": content})
        max_messages = self.max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def _handled_local_reply(self, transcript, reply):
        self._append_history("user", transcript)
        self._append_history("assistant", reply)
        return {"reply": reply, "handled": True}

    def _generate_picoclaw_reply(self, transcript):
        agent_key = self.current_agent
        env = self._build_picoclaw_env(agent_key)
        full_message = AGENTS[agent_key]["prompt"] + "\n\nUser request: " + transcript
        cmd = [
            str(PICOCLAW),
            "--no-color",
            "agent",
            "-s",
            f"agent:{agent_key}",
            "-m",
            full_message,
        ]
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=90.0)
        except Exception as exc:
            self._log(f"[error] picoclaw agent failed: {exc}")
            return None
        output = (result.stdout or "") + (result.stderr or "")
        reply = self._extract_picoclaw_reply(output).strip()
        return reply or None

    def _fetch_url_text(self, url):
        self._ensure_preferred_wifi()
        req = Request(url, headers={"User-Agent": NEWS_USER_AGENT})
        with urlopen(req, timeout=20) as response:
            return response.read().decode("utf-8", "ignore")

    def _extract_meta_content(self, html, name):
        patterns = [
            rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]+)"',
            rf'<meta\s+property="{re.escape(name)}"\s+content="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                return unescape(match.group(1)).strip()
        return None

    def _strip_html_text(self, html):
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _extract_article_detail(self, html):
        paragraphs = []
        for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", html, re.I | re.S):
            text = self._strip_html_text(match.group(1))
            if len(text) < 60:
                continue
            if text.lower().startswith(("ai news", "subscribe", "read more")):
                continue
            paragraphs.append(text)
            if len(paragraphs) >= 3:
                break
        if not paragraphs:
            return None
        detail = " ".join(paragraphs)
        words = detail.split()
        if len(words) > 80:
            detail = " ".join(words[:80]).rstrip(",;:.!?") + "."
        return detail

    def _fetch_ai_news_links(self):
        try:
            homepage = self._fetch_url_text(AI_NEWS_URL)
        except Exception as exc:
            self._log(f"[error] ai news homepage failed: {exc}")
            return []
        links = []
        for match in re.finditer(r'href="(https://www\.artificialintelligence-news\.com/news/[^"#?]+/)"', homepage):
            url = match.group(1)
            if url not in links:
                links.append(url)
            if len(links) >= 12:
                break
        return links

    def _fetch_ai_news_article(self, article_url):
        try:
            article_html = self._fetch_url_text(article_url)
        except Exception as exc:
            self._log(f"[error] ai news article failed: {exc}")
            return None
        title = self._extract_meta_content(article_html, "og:title")
        if not title:
            title_match = re.search(r"<title>([^<]+)</title>", article_html, re.I)
            title = unescape(title_match.group(1)).strip() if title_match else "Latest AI news"
        summary = self._extract_meta_content(article_html, "description") or self._extract_meta_content(article_html, "og:description")
        if not summary:
            summary = "I found the latest AI article, but I could not read a clean summary."
        detail = self._extract_article_detail(article_html) or summary
        return {"title": title, "summary": summary, "detail": detail, "url": article_url}

    def _parse_news_count(self, normalized):
        max_count = 3
        digit_match = re.search(r"\b([1-9])\b", normalized)
        if digit_match:
            return min(max_count, max(1, int(digit_match.group(1))))
        word_map = {
            "one": 1,
            "two": 2,
            "three": 3,
        }
        for word, value in word_map.items():
            if re.search(rf"\b{word}\b", normalized):
                return value
        if any(term in normalized for term in ("multiple", "several", "few")):
            return 3
        return 1

    def _is_news_more_request(self, normalized):
        phrases = (
            "more detail",
            "more details",
            "read more",
            "tell me more",
            "more about that",
            "more about the news",
            "more on that",
            "details",
        )
        return any(phrase in normalized for phrase in phrases)

    def _is_news_another_request(self, normalized):
        phrases = (
            "another news",
            "another one",
            "different news",
            "next news",
            "more news",
            "other news",
            "random news",
        )
        return any(phrase in normalized for phrase in phrases)

    def _select_ai_news_items(self, count=1, force_new=False):
        links = self._fetch_ai_news_links()
        if not links:
            self._log("[error] ai news article link not found")
            return []
        available = [link for link in links if force_new is False or link not in self._news_used_links]
        if len(available) < count:
            self._news_used_links.clear()
            available = list(links)
        random.shuffle(available)
        picked = available[:count]
        items = []
        for link in picked:
            article = self._fetch_ai_news_article(link)
            if article:
                items.append(article)
                self._news_used_links.add(link)
        return items

    def _generate_ai_news_reply(self, transcript):
        normalized = self._normalize_text(transcript)
        if self._is_news_more_request(normalized):
            if not self._news_last_items:
                return "Ask me for AI news first."
            item = self._news_last_items[0]
            title = item["title"].strip().rstrip(".")
            detail = item.get("detail") or item["summary"]
            return f"More on {title}. {detail}"

        count = self._parse_news_count(normalized)
        force_new = self._is_news_another_request(normalized) or "random" in normalized
        items = self._select_ai_news_items(count=count, force_new=force_new)
        if not items:
            return "I could not reach AI News right now."
        self._news_last_items = items
        if len(items) == 1:
            item = items[0]
            title = item["title"].strip().rstrip(".")
            summary = item["summary"].strip()
            return f"AI news. {title}. Summary: {summary}"

        parts = []
        for idx, item in enumerate(items, start=1):
            title = item["title"].strip().rstrip(".")
            summary = item["summary"].strip()
            parts.append(f"{idx}. {title}. {summary}")
        return "Here are a few AI news updates. " + " ".join(parts)

    def _generate_reply(self, transcript, session_id):
        agent_mode = AGENTS[self.current_agent]["mode"]
        if agent_mode == "picoclaw":
            reply = self._generate_picoclaw_reply(transcript)
            if reply:
                self._append_history("user", transcript)
                self._append_history("assistant", reply)
            return reply
        if agent_mode == "news":
            reply = self._generate_ai_news_reply(transcript)
            if reply:
                self._append_history("user", transcript)
                self._append_history("assistant", reply)
            return reply

        client = self._client()
        if client is None:
            self._log("[error] openai key missing")
            return None
        try:
            response = client.responses.create(
                model=self.text_model,
                instructions=AGENTS[self.current_agent]["system_instructions"],
                input=self._build_messages(transcript),
                max_output_tokens=self.max_output_tokens,
                timeout=45.0,
            )
        except Exception as exc:
            self._log(f"[error] openai text failed: {exc}")
            return None
        if self._cancelled(session_id):
            return None

        reply = (getattr(response, "output_text", "") or "").strip()
        if not reply:
            pieces = []
            for item in getattr(response, "output", []) or []:
                for content in getattr(item, "content", []) or []:
                    text = getattr(content, "text", "") or ""
                    if text:
                        pieces.append(text)
            reply = " ".join(pieces).strip()

        reply = reply or None
        if reply:
            self._append_history("user", transcript)
            self._append_history("assistant", reply)
        return reply

    def _boost_audio(self, wav_path):
        if self.tts_gain <= 1.0:
            return wav_path
        boosted_path = f"{wav_path}.boost.wav"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-filter:a", f"volume={self.tts_gain}", boosted_path,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            Path(boosted_path).replace(wav_path)
            self._log(f"[tts] boosted volume x{self.tts_gain}")
        except Exception as exc:
            Path(boosted_path).unlink(missing_ok=True)
            self._log(f"[warn] volume boost skipped: {exc}")
        return wav_path

    def _synthesize_speech_openai(self, text, wav_path, session_id, voice=None, instructions=None):
        client = self._client()
        if client is None:
            self._log("[error] openai key missing")
            return None
        try:
            response = client.audio.speech.create(
                model=self.tts_model,
                voice=voice or self.tts_voice,
                input=text,
                instructions=instructions or TTS_INSTRUCTIONS,
                response_format="wav",
                speed=self.tts_speed,
                timeout=120.0,
            )
            response.write_to_file(wav_path)
        except Exception as exc:
            self._log(f"[error] openai tts failed: {exc}")
            Path(wav_path).unlink(missing_ok=True)
            return None
        if self._cancelled(session_id):
            Path(wav_path).unlink(missing_ok=True)
            return None
        return self._boost_audio(wav_path)

    def _synthesize_speech(self, text, session_id, agent_key=None):
        fd, wav_path = tempfile.mkstemp(prefix="pi_voice_out_", suffix=".wav")
        os.close(fd)
        agent = AGENTS[agent_key or self.current_agent]
        voice = agent.get("voice", self.tts_voice)
        instructions = agent.get("tts_instructions", TTS_INSTRUCTIONS)
        self._log(f"[tts] using openai voice={voice}")
        openai_wav = self._synthesize_speech_openai(text, wav_path, session_id, voice=voice, instructions=instructions)
        if openai_wav:
            return openai_wav
        Path(wav_path).unlink(missing_ok=True)
        return None

    def _wav_duration_seconds(self, wav_path):
        try:
            with wave.open(str(wav_path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                if rate <= 0:
                    return None
                return frames / float(rate)
        except Exception:
            return None

    def _play_audio(self, wav_path, session_id):
        self._log(f"[playback] using device={self.playback_device}")
        wav_duration = self._wav_duration_seconds(wav_path)
        playback_deadline = None
        if wav_duration is not None:
            playback_deadline = time.monotonic() + max(5.0, wav_duration + 3.0)
        proc = subprocess.Popen(
            ["aplay", "-D", self.playback_device, "-q", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._set_proc("_play_proc", proc)
        try:
            while proc.poll() is None:
                if self._cancelled(session_id):
                    self._terminate_proc("_play_proc")
                    self._log("[step] playback cancelled")
                    return
                if playback_deadline is not None and time.monotonic() > playback_deadline:
                    self._terminate_proc("_play_proc")
                    self._log("[warn] playback timed out; continuing")
                    return
                time.sleep(0.1)
        finally:
            if proc.stderr is not None:
                err = proc.stderr.read().strip()
                if err:
                    self._log(f"[error] aplay failed: {err[:200]}")
            self._set_proc("_play_proc", None)
        self._log("[step] playback finished")
