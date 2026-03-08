#!/usr/bin/env python3
"""
Test sequences for the RGB LED controller.

Usage:
  python3 test_led.py              # run all tests
  python3 test_led.py red          # single color
  python3 test_led.py rgb          # red, green, blue cycle
  python3 test_led.py sine         # sine wave color sweep
  python3 test_led.py rainbow      # smooth rainbow cycle
"""

import math
import sys

from LED_control import LEDController

# --- Static color tests ---

RED = [(255, 0, 0, 2000)]
GREEN = [(0, 255, 0, 2000)]
BLUE = [(0, 0, 255, 2000)]

RGB_CYCLE = [
    (255, 0, 0, 1000),
    (0, 255, 0, 1000),
    (0, 0, 255, 1000),
]


def make_sine_sequence(duration_s=5, step_ms=30):
    """Sweep R/G/B as offset sine waves for smooth color blending."""
    steps = int(duration_s * 1000 / step_ms)
    seq = []
    for i in range(steps):
        t = i / steps * 2 * math.pi
        r = int((math.sin(t) + 1) / 2 * 255)
        g = int((math.sin(t + 2 * math.pi / 3) + 1) / 2 * 255)
        b = int((math.sin(t + 4 * math.pi / 3) + 1) / 2 * 255)
        seq.append((r, g, b, step_ms))
    return seq


def make_rainbow_sequence(duration_s=5, step_ms=30):
    """Cycle through hue 0-360 at full saturation/value."""
    steps = int(duration_s * 1000 / step_ms)
    seq = []
    for i in range(steps):
        hue = (i / steps) * 360
        r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
        seq.append((r, g, b, step_ms))
    return seq


def hsv_to_rgb(h, s, v):
    """Convert HSV (h: 0-360, s: 0-1, v: 0-1) to RGB (0-255)."""
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r1, g1, b1 = c, x, 0
    elif h < 120:
        r1, g1, b1 = x, c, 0
    elif h < 180:
        r1, g1, b1 = 0, c, x
    elif h < 240:
        r1, g1, b1 = 0, x, c
    elif h < 300:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x
    return int((r1 + m) * 255), int((g1 + m) * 255), int((b1 + m) * 255)


TESTS = {
    "red": ("Red for 2s", RED),
    "green": ("Green for 2s", GREEN),
    "blue": ("Blue for 2s", BLUE),
    "rgb": ("R -> G -> B cycle", RGB_CYCLE),
    "sine": ("Sine wave color sweep (5s)", None),
    "rainbow": ("Rainbow hue cycle (5s)", None),
}


def run_test(led, name):
    if name == "sine":
        desc, _ = TESTS[name]
        seq = make_sine_sequence()
    elif name == "rainbow":
        desc, _ = TESTS[name]
        seq = make_rainbow_sequence()
    else:
        desc, seq = TESTS[name]
    print(f"  [{name}] {desc}")
    led.play(seq)


def main():
    led = LEDController()
    try:
        if len(sys.argv) > 1:
            names = sys.argv[1:]
        else:
            names = list(TESTS.keys())

        print("LED Test Suite")
        print("-" * 30)
        for name in names:
            if name not in TESTS:
                print(f"  Unknown test: {name}")
                continue
            run_test(led, name)
        print("Done.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        led.cleanup()


if __name__ == "__main__":
    main()
