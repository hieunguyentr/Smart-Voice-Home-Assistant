# Progress Reports

The original weekly status report is included here:

[WEEKLY_PROJECT_STATUS_5-7.pdf](WEEKLY_PROJECT_STATUS_5-7.pdf)

Useful commands from development are included here:

[useful-commands.txt](useful-commands.txt)

## Milestone Summary

| Week / Date | Progress |
| --- | --- |
| Week 2, 1/20 | Prepared Raspberry Pi materials, flashed microSD card, completed first boot setup, and reached Raspberry Pi desktop. |
| Week 3, 2/05 | Set up local network control through PuTTY and RealVNC so the Raspberry Pi could be controlled from a PC. |
| Recognition sensor setup | Enabled serial port, identified Raspberry Pi UART pins, and connected the voice recognition sensor. |
| Command detection | Used the sensor documentation and command list to detect spoken commands over serial data. |
| Device planning | Planned fan, lamp, relay, LED, and possible PWM-based device control. |
| Week 5, 2/19 | Installed Vosk, PyAudio, pyttsx3, espeak, and the `vosk-model-small-en-us-0.15` speech model. |
| Vosk testing | Verified Vosk worked, but found it was sensitive to background noise and could heat the CPU. |
| Week 8, 3/20 | Debugged USB lavalier microphone setup and confirmed Raspberry Pi microphone detection. |
| Week 9, 3/26 | Connected Vosk, GPT, and the voice sensor into a functional assistant flow. |
| Week 10, 4/1 | Reorganized project goals and explored OpenClaw as a customer-facing AI assistant feature. |
| Local AI stack | Installed and tested tools for offline speech processing, local language models, and text-to-speech. |
| Week 12, 4/7 | Migrated from lost Raspberry Pi 4 hardware to Raspberry Pi 3 and adjusted for low-RAM limitations. |
| Current status | Device can listen for `hello robot`, begin a voice interaction session, respond with speech output, control connected hardware, and show status on OLED. |

## Final Status

The project is healthy as a prototype and documentation project. Remaining work includes final presentation slides, final demo video, exact BOM pricing, and a final physical enclosure/CAD design.
