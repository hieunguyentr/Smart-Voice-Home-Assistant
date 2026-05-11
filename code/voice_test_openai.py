import atexit
import os
import serial
import socket
import subprocess
import sys
import time

sys.path.insert(0, "/home/hieunguyentr/.venvs/luma-oled/lib/python3.13/site-packages")
sys.path.insert(0, "/home/hieunguyentr/home_assistant_ai")

from DFRobot_DF2301Q import *
from pi_voice_runtime_openai import PiVoiceRuntimeOpenAI

try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306
except Exception:
    i2c = None
    canvas = None
    ssd1306 = None


HELLO_ROBOT_CMDID = 2
RESET_CMDID = 82


def _unique_items(items):
    seen = set()
    unique = []
    for item in items:
        item = str(item).strip()
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _speaker_card_candidates():
    candidates = []
    configured_card = os.environ.get("PI_SPEAKER_CARD", "")
    if configured_card:
        candidates.append(configured_card)

    playback_device = os.environ.get("PI_SPEAKER_DEVICE", "plughw:CARD=Headphones,DEV=0")
    if "CARD=" in playback_device:
        candidates.append(playback_device.split("CARD=", 1)[1].split(",", 1)[0])

    try:
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        for line in result.stdout.splitlines():
            if not line.startswith("card "):
                continue
            card_number = line.split(":", 1)[0].replace("card", "").strip()
            if any(name in line.lower() for name in ("usb", "speaker", "headphones")):
                candidates.append(card_number)
    except Exception:
        pass

    candidates.extend(["Headphones", "1", "0", "2", "3"])
    return _unique_items(candidates)


def set_speaker_volume():
    volume = os.environ.get("PI_SPEAKER_VOLUME", "100%")
    mixer = os.environ.get("PI_SPEAKER_MIXER", "").strip()
    mixers = [mixer] if mixer else ["PCM", "Master", "Speaker"]
    last_error = ""

    for card in _speaker_card_candidates():
        for mixer_name in mixers:
            try:
                result = subprocess.run(
                    ["amixer", "-c", card, "sset", mixer_name, volume, "unmute"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except Exception as exc:
                last_error = str(exc)
                continue
            if result.returncode == 0:
                print(f"[audio] speaker volume set: card={card} mixer={mixer_name} volume={volume}", flush=True)
                return True
            last_error = (result.stderr or result.stdout or "").strip()

    print(f"[audio] speaker volume not changed: {last_error[:160]}", flush=True)
    return False


class OLEDStatusDisplay:
    def __init__(self, agent_name, agent_role):
        self.agent_name = agent_name
        self.agent_role = agent_role
        self.network_name = 'checking'
        self.status = 'booting...'
        self.transcript = ''
        self.device = None
        if not (i2c and canvas and ssd1306):
            return
        try:
            serial_if = i2c(port=1, address=0x3C)
            self.device = ssd1306(serial_if, width=128, height=64)
            if hasattr(self.device, 'show'):
                self.device.show()
        except Exception as exc:
            print(f'[oled] unavailable: {exc}', flush=True)
            self.device = None

    def _scroll_text(self, text, width=21):
        text = (text or '').strip()
        if len(text) <= width:
            return text
        padded = text + '   '
        start = int(time.monotonic() * 3) % len(padded)
        looped = padded + padded
        return looped[start:start + width]

    def _check_network_name(self):
        try:
            result = subprocess.run(
                ["iwgetid", "-r"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            ssid = result.stdout.strip()
            if ssid:
                self.network_name = ssid
                return
        except Exception:
            pass
        try:
            sock = socket.create_connection(('api.openai.com', 443), timeout=1.5)
            sock.close()
            self.network_name = 'connected'
        except OSError:
            self.network_name = 'offline'

    def set_status(self, status, check_internet=False):
        self.status = status
        if check_internet:
            self._check_network_name()
        self.render()

    def set_transcript(self, transcript):
        self.transcript = transcript.strip()
        self.render()

    def set_agent(self, agent_name, agent_role):
        self.agent_name = agent_name
        self.agent_role = agent_role
        self.render()

    def render(self):
        if self.device is None:
            return
        try:
            with canvas(self.device) as draw:
                draw.text((0, 0), f'agent: {self.agent_name}'[:21], fill='white')
                draw.text((0, 16), f'job: {self.agent_role}'[:21], fill='white')
                draw.text((0, 28), f'network: {self.network_name}'[:21], fill='white')
                draw.text((0, 40), f'doing: {self.status}'[:21], fill='white')
                draw.text((0, 52), self._scroll_text(self.transcript, 21), fill='white')
        except Exception as exc:
            print(f'[oled] render failed: {exc}', flush=True)


def main():
    voice = DFRobot_DF2301Q_UART()
    runtime = PiVoiceRuntimeOpenAI()
    oled = OLEDStatusDisplay('Rose Carter', 'AI assistant')
    set_speaker_volume()
    runtime.set_status_callback(lambda status, net=False: oled.set_status(status, check_internet=net))
    runtime.set_transcript_callback(oled.set_transcript)
    runtime.set_agent_callback(oled.set_agent)
    atexit.register(runtime.shutdown)
    runtime.prewarm_tts()

    time.sleep(1)

    voice.setting_CMD(DF2301Q_UART_MSG_CMD_SET_MUTE, 0)
    time.sleep(0.2)
    voice.setting_CMD(DF2301Q_UART_MSG_CMD_SET_VOLUME, 4)
    time.sleep(0.2)
    voice.setting_CMD(DF2301Q_UART_MSG_CMD_SET_WAKE_TIME, 255)
    time.sleep(0.2)
    voice.setting_CMD(DF2301Q_UART_MSG_CMD_SET_ENTERWAKEUP, 0)
    time.sleep(0.2)

    print('listening.....', flush=True)
    oled.set_status('idle', check_internet=True)

    last_wakeup = time.monotonic()
    last_oled_refresh = 0.0

    while True:
        now = time.monotonic()
        if now - last_oled_refresh >= 0.25:
            oled.render()
            last_oled_refresh = now
        try:
            cmd_id = voice.get_CMDID()
        except serial.SerialException:
            print('[uart] serial glitch, reopening voice sensor', flush=True)
            oled.set_status('uart reconnect')
            time.sleep(0.5)
            voice = DFRobot_DF2301Q_UART()
            time.sleep(0.5)
            continue
        except Exception as exc:
            print(f'[uart] sensor read error: {exc}', flush=True)
            oled.set_status('uart error')
            time.sleep(0.5)
            continue

        if cmd_id == HELLO_ROBOT_CMDID and not runtime.is_active():
            print('hello robot', flush=True)
            oled.set_status('listening.....', check_internet=True)
            runtime.start_session()
        elif cmd_id == HELLO_ROBOT_CMDID and runtime.is_active():
            print('[sensor] hello robot reset active session', flush=True)
            runtime.cancel_session()
            time.sleep(0.2)
            oled.set_status('listening.....', check_internet=True)
            runtime.start_session()
        elif cmd_id == RESET_CMDID:
            print('reset', flush=True)
            runtime.cancel_session()
            oled.set_status('idle', check_internet=True)

        if time.monotonic() - last_wakeup >= 5:
            voice.setting_CMD(DF2301Q_UART_MSG_CMD_SET_ENTERWAKEUP, 0)
            last_wakeup = time.monotonic()

        time.sleep(0.05)


if __name__ == '__main__':
    main()
