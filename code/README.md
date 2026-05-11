# Code

This folder contains the Raspberry Pi application code copied from the working device and cleaned for a public GitHub repository.

## Folder Contents

| Path | Purpose |
| --- | --- |
| `voice_test_openai.py` | Main runner for the voice sensor, OLED display, and AI runtime. |
| `DFRobot_DF2301Q.py` | DFRobot DF2301Q voice recognition sensor library. |
| `home_assistant_ai/pi_voice_runtime_openai.py` | Main OpenAI runtime with device control, OLED callbacks, TTS, and agent logic. |
| `home_assistant_ai/pi_voice_runtime.py` | Earlier local Whisper/Piper runtime implementation. |
| `home_assistant_ai/gravity_voice_sensor.py` | Small serial test/diagnostic helper for the voice sensor. |
| `requirements.txt` | Python package requirements for the public project code. |
| `systemd/voice_test_openai.service` | Example service template for running the assistant at boot. |

## Fresh Raspberry Pi OS Setup

On a freshly formatted Raspberry Pi OS card, install the system packages first:

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-dev build-essential \
  python3-rpi.gpio python3-smbus i2c-tools alsa-utils ffmpeg
```

These provide GPIO access, I2C/OLED support, microphone/speaker command-line tools, and audio processing tools.

Then install the Python dependencies from this folder:

```bash
python3 -m pip install -r requirements.txt
```

If Raspberry Pi OS blocks system-wide pip installs, use a virtual environment:

```bash
python3 -m venv ~/.venvs/smart-voice-home-assistant
source ~/.venvs/smart-voice-home-assistant/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

PicoClaw is not installed through `requirements.txt`. The runtime calls the PicoClaw executable with `subprocess`, so PicoClaw must exist at `PICOCLAW_BIN` or the default `~/.local/opt/picoclaw/picoclaw` path.

## Environment Variables

Keep all private values outside GitHub. Do not commit API keys, Wi-Fi passwords, or local machine secrets.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API key loaded from your shell or private environment file. |
| `OPENAI_API_KEY_FILE` | Optional path to a local file containing the OpenAI key. |
| `PI_ASSISTANT_HOME` | Base home directory for local models, voices, and cached prompt audio. |
| `PI_LUMA_SITE_PACKAGES` | Optional custom path if `luma.oled` is installed in a separate virtual environment. |
| `PI_PREFERRED_WIFI_SSID` | Optional preferred Wi-Fi SSID. |
| `PI_PREFERRED_WIFI_PASSWORD` | Optional preferred Wi-Fi password. |
| `PI_FALLBACK_WIFI_SSID` | Optional fallback Wi-Fi profile name. |
| `PI_FAN_PIN` | GPIO pin for fan output. Default: `16`. |
| `PI_RED_LIGHT_PIN` | GPIO pin for red indicator. Default: `20`. |
| `PI_GREEN_LIGHT_PIN` | GPIO pin for green indicator. Default: `21`. |

## Run Manually

From this folder on the Raspberry Pi:

```bash
python3 -u voice_test_openai.py
```

## Run at Boot

The service file is a template. Before installing it, change `User`, `WorkingDirectory`, `PI_ASSISTANT_HOME`, `EnvironmentFile`, and `ExecStart` so they match your Raspberry Pi.

Private key file example:

```bash
mkdir -p ~/.config/smart-voice-home-assistant
nano ~/.config/smart-voice-home-assistant/openai.env
```

Example private file content:

```bash
OPENAI_API_KEY=<your-openai-api-key>
```

Then install the service after editing paths:

```bash
sudo cp systemd/voice_test_openai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now voice_test_openai.service
```
