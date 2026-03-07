# Mirror

The primary conversational device. A smart mirror that faces the user, sees them, and talks to them. It tilts on a single servo axis for physical expression (nodding, looking away shyly, etc.).

## Sensors

| Sensor | Details |
|--------|---------|
| USB Camera | Face-to-face with the user. Frames sent to laptop for Claude Vision (mood, expression, appearance analysis). |

## Actions

| Action | Description | Example |
|--------|-------------|---------|
| `speak` | Say something via TTS | `speak("Good morning, you look well-rested")` |
| `tilt` | Tilt the mirror face to a specific angle | `tilt(120)` |
| `nod` | Nod up and down (agreement) | `nod("slow")` |
| `look_up` | Tilt up to face the user | `look_up()` |
| `look_down` | Tilt down (shy/thinking) | `look_down()` |

## Notes

- Has a speaker but no microphone (global mic handles all voice input).
- Single tilt servo on PCA9685 I2C board.
