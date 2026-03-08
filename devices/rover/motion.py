#!/usr/bin/env python3
"""
Robot motion library: move(cm) and rotate(degrees).
Includes example routines at the bottom.
"""

import math
import random
import time
import threading
import lgpio

# ---- Config ----
SPEED = 40              # duty cycle 0-100
WHEEL_DIAMETER_CM = 6.8
WHEELBASE_CM = 19.2     # distance between left and right wheels — adjust this!
ENCODER_SIGNALS = 150   # per revolution
QUAD_MULTIPLIER = 4
COUNTS_PER_REV = ENCODER_SIGNALS * QUAD_MULTIPLIER
TIMEOUT_S = 10.0
PWM_FREQ = 1000
GPIO_CHIP = 4           # Pi 5 = 4, Pi 4 = 0

# Closed-loop control tuning (duty-cycle units)
PID_KP = 0.08
PID_KI = 0.00
PID_KD = 0.01
SYNC_KP = 0.04
MIN_DUTY = 18
MAX_DUTY = 45
I_CLAMP = 800.0

# L298N pins
IN1 = 23  # right motor +
IN2 = 24  # right motor -
IN3 = 16  # left motor +
IN4 = 25  # left motor -

# Encoder pins
LEFT_A = 17
LEFT_B = 27
RIGHT_A = 22
RIGHT_B = 10

# ---- Encoder (fast polling thread) ----
class Encoder:
    def __init__(self, chip, pin_a, pin_b):
        self.chip = chip
        self.pin_a = pin_a
        self.pin_b = pin_b
        self.count = 0
        self._running = True

        lgpio.gpio_claim_input(chip, pin_a, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(chip, pin_b, lgpio.SET_PULL_UP)

        self.last_a = lgpio.gpio_read(chip, pin_a)
        self.last_b = lgpio.gpio_read(chip, pin_b)

        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self):
        chip = self.chip
        pa, pb = self.pin_a, self.pin_b
        read = lgpio.gpio_read
        last_a, last_b = self.last_a, self.last_b
        _sleep = time.sleep

        while self._running:
            a = read(chip, pa)
            b = read(chip, pb)
            if a != last_a:
                self.count += 1 if a ^ last_b else -1
                last_a = a
            if b != last_b:
                self.count += -1 if b ^ last_a else 1
                last_b = b
            self.last_a = last_a
            self.last_b = last_b
            _sleep(0.0001)  # 100us — prevents 100% CPU while still tracking fast enough

    def read(self):
        return self.count

    def reset(self):
        self.count = 0

    def stop(self):
        self._running = False
        self._thread.join()

# ---- Hardware setup ----
h = lgpio.gpiochip_open(GPIO_CHIP)

for pin in (IN1, IN2, IN3, IN4):
    lgpio.gpio_claim_output(h, pin, 0)

enc_l = Encoder(h, LEFT_A, LEFT_B)
enc_r = Encoder(h, RIGHT_A, RIGHT_B)

def _set_pwm(pin, duty):
    lgpio.tx_pwm(h, pin, PWM_FREQ, duty if duty > 0 else 0)

def _stop_all():
    for pin in (IN1, IN2, IN3, IN4):
        lgpio.tx_pwm(h, pin, PWM_FREQ, 0)

def drive(left_duty, right_duty):
    ld = float(left_duty)
    rd = float(right_duty)

    if ld > 100:
        ld = 100
    elif ld < -100:
        ld = -100

    if rd > 100:
        rd = 100
    elif rd < -100:
        rd = -100

    def _drive_side(forward_pin, reverse_pin, duty):
        if duty > 0:
            _set_pwm(reverse_pin, 0)
            _set_pwm(forward_pin, duty)
        elif duty < 0:
            _set_pwm(forward_pin, 0)
            _set_pwm(reverse_pin, -duty)
        else:
            _set_pwm(forward_pin, 0)
            _set_pwm(reverse_pin, 0)

    _drive_side(IN3, IN4, ld)
    _drive_side(IN1, IN2, rd)

def _cm_to_counts(cm):
    return abs(cm) / (math.pi * WHEEL_DIAMETER_CM) * COUNTS_PER_REV

def _run_motors(left_pin, right_pin, target_counts, label, speed):
    """Drive both motors until each reaches target_counts, then stop."""
    enc_l.reset()
    enc_r.reset()

    print(f"{label}  (target {target_counts:.0f} counts)")

    def _clamp(d):
        if d < 0:
            return 0
        if d > MAX_DUTY:
            return MAX_DUTY
        return d

    # Start from requested speed but clamp and enforce a minimum duty to overcome stiction.
    base_left = float(speed)
    base_right = float(speed)
    base_left = max(MIN_DUTY, min(MAX_DUTY, base_left))
    base_right = max(MIN_DUTY, min(MAX_DUTY, base_right))

    _set_pwm(left_pin, base_left)
    _set_pwm(right_pin, base_right)

    l_done = r_done = False
    start = time.monotonic()

    # Per-wheel PID state
    li = ri = 0.0
    l_prev_e = r_prev_e = 0.0
    last_t = start

    while not (l_done and r_done):
        if time.monotonic() - start > TIMEOUT_S:
            print("  Timeout!")
            break

        now = time.monotonic()
        dt = now - last_t
        if dt <= 0:
            dt = 1e-3
        last_t = now

        lc = abs(enc_l.read())
        rc = abs(enc_r.read())

        le = float(target_counts - lc)
        re = float(target_counts - rc)

        if not l_done and lc >= target_counts:
            _set_pwm(left_pin, 0)
            l_done = True
        if not r_done and rc >= target_counts:
            _set_pwm(right_pin, 0)
            r_done = True

        # Closed-loop update for whichever wheels are still running.
        if not (l_done and r_done):
            # Optional left-right sync correction to keep both sides tracking similarly.
            sync = float(lc - rc)

            l_duty = 0.0
            r_duty = 0.0

            if not l_done:
                li += le * dt
                if li > I_CLAMP:
                    li = I_CLAMP
                elif li < -I_CLAMP:
                    li = -I_CLAMP
                ld = (le - l_prev_e) / dt
                l_prev_e = le
                l_duty = base_left + (PID_KP * le) + (PID_KI * li) + (PID_KD * ld) - (SYNC_KP * sync)
                l_duty = _clamp(l_duty)
                if l_duty > 0 and l_duty < MIN_DUTY:
                    l_duty = MIN_DUTY
                _set_pwm(left_pin, l_duty)

            if not r_done:
                ri += re * dt
                if ri > I_CLAMP:
                    ri = I_CLAMP
                elif ri < -I_CLAMP:
                    ri = -I_CLAMP
                rd = (re - r_prev_e) / dt
                r_prev_e = re
                r_duty = base_right + (PID_KP * re) + (PID_KI * ri) + (PID_KD * rd) + (SYNC_KP * sync)
                r_duty = _clamp(r_duty)
                if r_duty > 0 and r_duty < MIN_DUTY:
                    r_duty = MIN_DUTY
                _set_pwm(right_pin, r_duty)

        print(f"\r  L:{lc:6.0f}/{target_counts:.0f}  R:{rc:6.0f}/{target_counts:.0f}", end="", flush=True)
        time.sleep(0.005)

    _stop_all()
    print(f"\n  Final -> L:{enc_l.read()}  R:{enc_r.read()}")
    time.sleep(0.2)

# ---- Public API ----

def move(cm, speed=SPEED):
    """Move forward (positive) or backward (negative) by cm centimetres."""
    if cm == 0:
        return
    direction = 1 if cm > 0 else -1
    target = _cm_to_counts(cm)

    if direction > 0:
        pin_r, pin_l = IN1, IN3
    else:
        pin_r, pin_l = IN2, IN4

    label = f"{'Forward' if direction > 0 else 'Backward'} {abs(cm):.1f} cm @ {speed}%"
    _run_motors(pin_l, pin_r, target, label, speed)

def rotate(degrees, speed=SPEED):
    """Rotate in place: positive = clockwise, negative = counter-clockwise.
    Each wheel travels an arc of (degrees/360) * pi * wheelbase."""
    if degrees == 0:
        return
    arc_cm = abs(degrees) / 1000.0 * math.pi * WHEELBASE_CM
    target = _cm_to_counts(arc_cm)

    if degrees > 0:
        # Clockwise: left forward, right backward
        pin_l, pin_r = IN3, IN2
    else:
        # Counter-clockwise: left backward, right forward
        pin_l, pin_r = IN4, IN1

    label = f"Rotate {'CW' if degrees > 0 else 'CCW'} {abs(degrees):.1f}° @ {speed}%"
    _run_motors(pin_l, pin_r, target, label, speed)

def stop():
    _stop_all()

def cleanup():
    _stop_all()
    enc_l.stop()
    enc_r.stop()
    lgpio.gpiochip_close(h)

def say_no(cycles=1, speed=40):
    print("=== Ponder ===")
    for _ in range(cycles):
        move(10, speed)
        time.sleep(1)
        move(-10, speed)
        rotate(-20, speed)
        rotate(40, speed)
        rotate(-20, speed)
        time.sleep(2)
    print("Ponder done!\n")

_excitement_idx = 0

def excitement(speed=100):
    print("=== Spin ===")
    move(15, speed)

    routines = [
        [360, -40, 40, -120, 120, -360],
        [60, -60, 120, -120, 180, -180],
        [720, -180, 180, -90, 90, -720],
        [45, -45, 90, -90, 135, -135],
    ]

    global _excitement_idx
    seq = routines[_excitement_idx % len(routines)]
    _excitement_idx += 1
    for deg in seq:
        rotate(deg, speed)

    move(-15, speed)
    print("Spin done!\n")

def pass_food(speed=40):
    move(23, speed)
    rotate(-90, speed)
    move(15, speed)
    time.sleep(5)
    move(-15, speed)
    rotate(90, speed)
    move(-23, speed)

def act_sad(cycles=2, speed=30):
    print("=== Sad ===")
    for _ in range(cycles):
        move(4, 30)
        time.sleep(0.4)
        rotate(-12, speed)
        time.sleep(0.3)
        rotate(24, speed)
        time.sleep(0.3)
        rotate(-12, speed)
        time.sleep(0.4)
        move(-5, 17)
        time.sleep(0.6)
    print("Sad done!\n")