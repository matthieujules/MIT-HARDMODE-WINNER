#!/usr/bin/env python3
"""
Shared motion utilities for smooth arm control.

All transitions — pose-to-pose, idle-to-animation, animation-to-animation —
go through interpolate_to() so the arm never teleports between positions.
"""

import time


def _ease_in_out(t: float) -> float:
    """Hermite ease-in-out: smooth start and stop."""
    return t * t * (3.0 - 2.0 * t)


def get_current_positions(robot) -> dict[str, float]:
    """Read current joint positions from the robot."""
    obs = robot.get_observation()
    return {k: float(v) for k, v in obs.items()}


def max_joint_delta(current: dict[str, float], target: dict[str, float]) -> float:
    """Compute the largest joint angle difference between two poses."""
    deltas = []
    for k in target:
        if k in current:
            deltas.append(abs(float(target[k]) - float(current[k])))
    return max(deltas) if deltas else 0.0


def smart_duration(current: dict[str, float], target: dict[str, float],
                   min_s: float = 0.4, max_s: float = 3.0,
                   deg_per_sec: float = 80.0) -> float:
    """Scale transition duration based on how far the joints need to travel.

    Small moves (~5°) get min_s.  Large moves (~180°) get max_s.
    """
    delta = max_joint_delta(current, target)
    return min(max_s, max(min_s, delta / deg_per_sec))


def interpolate_to(robot, target: dict[str, float], duration_s: float | None = None,
                   fps: int = 30, min_s: float = 0.4, max_s: float = 3.0) -> None:
    """Smoothly interpolate from current position to target.

    Args:
        robot: Connected lerobot robot instance.
        target: Target joint positions (e.g. {"shoulder_pan.pos": 10.0, ...}).
        duration_s: Fixed duration. If None, auto-scales based on joint distance.
        fps: Interpolation frame rate.
        min_s: Minimum duration when auto-scaling.
        max_s: Maximum duration when auto-scaling.
    """
    current = get_current_positions(robot)

    if duration_s is None:
        duration_s = smart_duration(current, target, min_s=min_s, max_s=max_s)

    n_frames = max(1, int(duration_s * fps))
    interval = duration_s / n_frames

    for i in range(1, n_frames + 1):
        t = _ease_in_out(i / n_frames)

        frame = {}
        for key in target:
            start_val = current.get(key, float(target[key]))
            end_val = float(target[key])
            frame[key] = start_val + (end_val - start_val) * t

        frame_start = time.perf_counter()
        robot.send_action(frame)
        sleep_time = interval - (time.perf_counter() - frame_start)
        if sleep_time > 0:
            time.sleep(sleep_time)
