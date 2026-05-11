# Code

This folder contains the main Python script for the Raspberry Pi voice assistant.

## Main File

```text
voice_test_openai.py
```

## What the Script Does

- Imports the DFRobot voice recognition sensor library.
- Starts the `PiVoiceRuntimeOpenAI` assistant runtime.
- Initializes the OLED status display when available.
- Registers callbacks so the runtime can update OLED status, transcript, agent name, and agent role.
- Configures the voice sensor volume, mute state, and wake timing.
- Waits for command IDs from the sensor.
- Starts a voice session when `hello robot` is detected.
- Cancels the current session when `reset` is detected.
- Reconnects the UART sensor after serial glitches.

## Raspberry Pi Run Command

From the Raspberry Pi home folder:

```bash
source ~/.profile
cd /home/hieunguyentr
python3 -u voice_test_openai.py
```

## Remote SSH Example

From the development PC:

```powershell
ssh <pi-user>@<raspberry-pi-ip>
```

## Dependencies

The script expects these Raspberry Pi-side dependencies or local project files:

- `DFRobot_DF2301Q`
- `pi_voice_runtime_openai`
- `pyserial`
- `luma.oled`
- OpenAI runtime environment variables loaded from the user's shell profile

## Important Note

API keys and local environment files should not be committed to GitHub. Keep secrets in shell profile files or local `.env` files that are ignored by Git.
