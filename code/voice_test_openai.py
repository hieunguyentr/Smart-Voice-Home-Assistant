import atexit
import serial
import socket
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LUMA_SITE_PACKAGES = os.environ.get("PI_LUMA_SITE_PACKAGES")

if LUMA_SITE_PACKAGES:
    sys.path.insert(0, LUMA_SITE_PACKAGES)
sys.path.insert(0, str(BASE_DIR / "home_assistant_ai"))

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
            print('[sensor] hello robot ignored because session is active', flush=True)
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
