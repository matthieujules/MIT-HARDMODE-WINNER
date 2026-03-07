# Mirror

The visual mirror device. It faces the user, captures a face-to-face camera image, generates screen visuals from control-plane instructions, and displays them on an attached LCD. It tilts on a single servo axis for physical expression.

## Sensors

| Sensor | Details |
|--------|---------|
| USB Camera | Face-to-face with the user. Frames sent to laptop for Claude Vision (mood, expression, appearance analysis). |

## Actions

| Action | Description | Example |
|--------|-------------|---------|
| `display_image` | Generate and display a visual response on the LCD | `display_image("show a calm blue smile")` |
| `tilt` | Tilt the mirror face to a specific angle | `tilt(120)` |
| `nod` | Nod up and down (agreement) | `nod("slow")` |
| `look_up` | Tilt up to face the user | `look_up()` |
| `look_down` | Tilt down (shy/thinking) | `look_down()` |

## Notes

- Has no speaker and no microphone.
- Visual output is handled by the attached LCD screen.
- Single tilt servo on PCA9685 I2C board.

## Mirror LCD Runtime

The mirror folder now includes a display runtime that can:

- accept a natural-language instruction from the control plane
- capture a fresh camera frame from the Pi camera
- turn that request into a display plan
- generate a mirror-facing visual response
- render the result fullscreen on the attached LCD over the Pi's display output

### Files

- `main.py` — entry point for local testing and device boot
- `camera.py` — Pi camera capture with placeholder fallback
- `planner.py` — turns text into a structured display plan, optionally via Cerebras
- `image_generation.py` — image API integration plus local fallback renderer
- `display.py` — fullscreen LCD display or headless save-only mode

### Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r devices/mirror/requirements.txt
python3 devices/mirror/main.py "show a calm blue smile"
python3 devices/mirror/main.py --skip-camera "put a green check mark on screen"
python3 devices/mirror/main.py --loop
```

### Environment

- `OPENAI_API_KEY` or `MIRROR_IMAGE_API_KEY` enables live image generation
- `MIRROR_IMAGE_BASE_URL` optionally points to a compatible image endpoint
- `CEREBRAS_API_KEY` optionally enables structured visual planning through Cerebras
- `MIRROR_SAVE_ONLY=1` saves output images without opening a fullscreen window
