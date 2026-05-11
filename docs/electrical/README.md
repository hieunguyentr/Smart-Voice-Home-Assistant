# Electrical

This section documents the main electrical and wiring decisions.

## Raspberry Pi Serial Connection

The voice recognition sensor uses Raspberry Pi UART communication.

| Raspberry Pi Pin | GPIO | Function | Purpose |
| --- | --- | --- | --- |
| Pin 8 | GPIO14 / TXD | Transmit | Sends serial commands to the voice recognition module |
| Pin 10 | GPIO15 / RXD | Receive | Receives command data from the voice recognition module |

Serial communication was enabled through Raspberry Pi configuration.

## Voice Command IDs

The main Python script uses these command IDs:

| Command | ID | Behavior |
| --- | --- | --- |
| `hello robot` | `2` | Starts the AI voice session when inactive |
| `reset` | `82` | Cancels the current AI voice session |

## OLED Display

The OLED display is controlled with the `luma.oled` library over I2C.

The display shows:

- Active agent name
- Agent role
- Current network name
- Current system status
- Recent user transcript

## GPIO Indicators

The runtime reports GPIO readiness for:

| Device | GPIO |
| --- | --- |
| Fan | GPIO16 |
| Red indicator | GPIO20 |
| Green indicator | GPIO21 |

These outputs provide physical feedback and device-control capability.

## Safety Notes

- Isolate low-voltage Raspberry Pi control wiring from higher-voltage loads.
- Use relay modules or driver circuits for loads that cannot be powered directly from GPIO.
- Confirm pin numbers before applying power.
- Test each device independently before running the full assistant loop.
