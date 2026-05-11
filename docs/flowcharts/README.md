# Flow Charts

This section explains the final system flow for the Smart Voice Home Assistant based on the current code in `code/voice_test_openai.py` and `code/home_assistant_ai/pi_voice_runtime_openai.py`.

## Final Code Flow

```mermaid
flowchart TD
    A["Boot script<br/>Create voice sensor, runtime, and OLED"] --> B["Idle loop<br/>Refresh OLED and read command ID"]
    B --> C{"Voice sensor command?"}

    C -->|"Hello robot<br/>ID 2"| D{"AI session active?"}
    D -->|"No"| E["Start AI session<br/>Worker thread begins"]
    D -->|"Yes"| B

    C -->|"Reset<br/>ID 82"| R["Cancel session<br/>Stop audio/OpenAI work<br/>OLED returns idle"]
    R --> B

    C -->|"Serial error"| U["Reconnect UART sensor"]
    U --> B

    C -->|"No command"| K["Keep sensor awake<br/>Send wake command every 5 seconds"]
    K --> B

    E --> F["Ask about agent switch<br/>Rose, David, Maya, or Andrew"]
    F --> G["Record microphone audio"]
    G --> H["OpenAI transcription"]
    H --> I{"Transcript result?"}

    I -->|"Empty or junk"| G
    I -->|"Goodbye, timeout, or reset"| B
    I -->|"Valid speech"| J{"Request type?"}

    J -->|"Normal question"| L["Generate AI reply"]
    J -->|"Device command"| M["Plan device action<br/>Fan, red light, green light"]
    J -->|"Agent mode"| N["Generate selected agent reply"]

    L --> O["Generate TTS audio"]
    M --> O
    N --> O

    O --> P["Speak response"]
    P --> Q{"Device action planned?"}
    Q -->|"Yes"| S["Apply GPIO action<br/>on, off, blink, delay, duration"]
    Q -->|"No"| T["Wait for follow-up"]
    S --> T
    T --> G
```

Source file: [`final-code-flow.mmd`](final-code-flow.mmd)

## Flow Summary

- `voice_test_openai.py` runs continuously on the Raspberry Pi.
- The DFRobot voice recognition sensor reports command IDs.
- Command ID `2` starts a new AI session when no session is active.
- Command ID `82` cancels the active session and returns the OLED to idle.
- `PiVoiceRuntimeOpenAI` runs the conversation in a worker thread so the main loop can keep reading the sensor.
- At session start, the runtime asks whether the user wants to change agent.
- During the conversation window, the runtime records audio, transcribes with OpenAI, routes the transcript, generates a reply, speaks with TTS, and optionally applies GPIO device actions.
- The session ends on reset, timeout, goodbye phrase, missing reply, or cancellation.

## Device Outputs

| Device | GPIO | Supported actions |
| --- | --- | --- |
| Fan | GPIO16 | on, off, blink, delay, duration |
| Red light | GPIO20 | on, off, blink, delay, duration |
| Green light | GPIO21 | on, off, blink, delay, duration |
