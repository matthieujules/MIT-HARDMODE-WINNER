# Lamp

An expressive desk lamp with an RGB LED and a 4-axis servo head (pan, tilt, roll, body lean). Communicates entirely through light and motion — no speech, no camera, no mic.

## Sensors

None.

## Actions

| Action | Description | Example |
|--------|-------------|---------|
| `set_color` | Set LED to an RGB color | `set_color(255, 180, 50)` |
| `set_brightness` | Set overall brightness (0-100) | `set_brightness(70)` |
| `set_scene` | Apply a named lighting scene | `set_scene("focus")` |
| `look_at` | Turn head toward a direction | `look_at(pan=45, tilt=100)` |
| `nod` | Nod head up and down (yes) | `nod("fast")` |
| `shake` | Shake head side to side (no) | `shake("normal")` |
| `emote` | Express emotion via light + motion | `emote("curious")` |
| `perk_up` | Quick upward motion (surprise/attention) | `perk_up()` |
| `droop` | Slow downward lean (sad/sleepy) | `droop()` |
| `reset_position` | Return to neutral position | `reset_position()` |

## Notes

- 4 servo axes: head pan, head tilt, head roll, body lean (PCA9685 I2C).
- RGB LED on GPIO PWM pins.
- Scenes available: `focus`, `relax`, `energy`, `dim`, `alert`, `off`.
