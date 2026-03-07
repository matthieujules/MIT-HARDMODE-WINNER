# Rover

A small mobile coaster on wheels that can drive around and pull a basket. The helpful little robot butler of the system.

## Sensors

| Sensor | Details |
|--------|---------|
| Wheel Encoders | Quadrature encoders on left and right wheels for dead-reckoning position tracking. |

## Actions

| Action | Description | Example |
|--------|-------------|---------|
| `drive_to` | Drive in a direction at a given speed | `drive_to("kitchen", 50, 3000)` |
| `stop` | Stop all motors immediately | `stop()` |
| `return_home` | Drive back to starting position | `return_home()` |

## Notes

- Differential drive with PWM motor board.
- No camera, no mic, no speaker.
- Buzzer TBD (currently disabled).
