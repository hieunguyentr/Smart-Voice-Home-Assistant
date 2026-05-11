# Simulation / Results

This section summarizes testing, results, current working behavior, and known issues.

## Testing Performed

| Test Area | Result |
| --- | --- |
| Raspberry Pi first boot | Completed |
| SSH remote control | Completed |
| RealVNC remote desktop | Completed |
| Serial voice sensor setup | Completed |
| Sensor command detection | Completed |
| Vosk speech recognition | Installed and tested |
| USB microphone debugging | Completed |
| OpenAI / GPT integration | Integrated |
| OLED status display code | Implemented |
| GPIO status setup | Implemented in runtime |
| Local AI stack experiments | Tested with low-RAM constraints |

## Current Working Features

- The assistant can wait for `hello robot`.
- The assistant can enter an active voice session.
- The assistant can cancel/reset the session.
- The OLED can display live state information.
- The code can reopen the UART sensor after serial errors.
- The project can be operated remotely from a PC using SSH.

## Problems Found

- Vosk was fast but sensitive to background noise.
- Microphone configuration required debugging before reliable input.
- Raspberry Pi CPU/heat became a concern during speech processing tests.
- Raspberry Pi 4 hardware was lost during the project, requiring migration to a Raspberry Pi 3.
- Raspberry Pi 3 has much lower RAM, so local AI tools must run carefully and sequentially.

## Lessons Learned

- Hardware bring-up should be tested one part at a time.
- Serial pin mapping must be verified before software testing.
- Remote access saves time once the Raspberry Pi is headless.
- Voice AI projects need both software debugging and physical audio testing.
- Low-RAM hardware changes the design tradeoff between local AI and cloud AI.

## Evidence

Screenshots and audio artifacts are stored in:

- [Screenshots](../../media/screenshots/)
- [Audio](../../media/audio/)
