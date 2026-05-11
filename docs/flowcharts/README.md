# Flow Charts

This section explains the main system flow for the Smart Voice Home Assistant.

## System Flow

![System flowchart](system-flowchart.png)

The original editable diagram is included as:

```text
system-flowchart.drawio
```

## Wake Word Flow

```mermaid
flowchart TD
    Idle[Idle listening mode] --> Sensor[Read voice sensor command ID]
    Sensor -->|Command ID 2| ActiveCheck{Session active?}
    ActiveCheck -->|No| Start[Start AI session]
    ActiveCheck -->|Yes| Ignore[Ignore duplicate wake command]
    Sensor -->|Command ID 82| Cancel[Cancel current session]
    Start --> Listen[Listen to user speech]
    Listen --> Respond[Generate and speak response]
    Respond --> Idle
    Cancel --> Idle
```

## GPT Response Flow

```mermaid
flowchart LR
    Speech[User speech] --> Runtime[PiVoiceRuntimeOpenAI]
    Runtime --> Status[OLED and GPIO status callbacks]
    Runtime --> AI[OpenAI response]
    AI --> TTS[Text-to-speech]
    TTS --> Speaker[Speaker output]
```

## Notes

- The dedicated sensor handles wake and reset commands.
- The Python runtime handles the active conversation session.
- OLED and GPIO output give the user live feedback about the assistant state.
