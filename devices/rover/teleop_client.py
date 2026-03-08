#!/usr/bin/env python3
import argparse
import asyncio
import json
import time

try:
    import curses
except Exception as e:
    raise SystemExit(f"curses not available: {e}")

try:
    import websockets
except Exception as e:
    raise SystemExit(
        "Missing dependency 'websockets' on this machine.\n"
        "Install with: python3 -m pip install websockets\n"
        f"Original error: {e}"
    )


async def teleop(ws_url: str, step_cm: float, step_deg: float, speed: int) -> None:
    current_speed = int(speed)

    async with websockets.connect(ws_url) as ws:
        def send(obj: dict) -> None:
            asyncio.create_task(ws.send(json.dumps(obj)))

        stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.nodelay(True)

        last_send = 0.0
        left = 0.0
        right = 0.0
        last_key_ts = 0.0
        send_hz = 15.0

        def draw(status: str) -> None:
            stdscr.erase()
            stdscr.addstr(0, 0, "Rover Teleop (WebSocket)")
            stdscr.addstr(2, 0, f"URL: {ws_url}")
            stdscr.addstr(3, 0, f"Step: {step_cm} cm, {step_deg} deg")
            stdscr.addstr(4, 0, f"Speed: {current_speed}")
            stdscr.addstr(5, 0, f"Drive: L {left:.0f}  R {right:.0f}")
            stdscr.addstr(6, 0, "Controls:")
            stdscr.addstr(7, 2, "Arrow Up/Down: forward/back")
            stdscr.addstr(8, 2, "Arrow Left/Right: turn left/right")
            stdscr.addstr(9, 2, "Space: STOP motors")
            stdscr.addstr(10, 2, "1/2/3: speed 30/50/80")
            stdscr.addstr(11, 2, "q: quit")
            stdscr.addstr(13, 0, f"Status: {status}")
            stdscr.refresh()

        async def recv_loop() -> None:
            nonlocal last_send
            try:
                async for msg in ws:
                    draw(f"recv: {msg}")
            except Exception as e:
                draw(f"disconnected: {e}")

        recv_task = asyncio.create_task(recv_loop())

        try:
            draw("connected")

            while True:
                k = stdscr.getch()
                now = time.monotonic()

                if k == -1:
                    # If no keypresses for a bit, stop driving.
                    if (now - last_key_ts) > 0.20 and (left != 0.0 or right != 0.0):
                        left = 0.0
                        right = 0.0
                        send({"type": "stop"})
                        draw("sent: stop (idle)")

                    if (now - last_send) >= (1.0 / send_hz) and (left != 0.0 or right != 0.0):
                        send({"type": "drive", "left": left, "right": right})
                        last_send = now
                    await asyncio.sleep(0.01)
                    continue

                if k in (ord("q"), ord("Q")):
                    break

                if k == ord("1"):
                    current_speed = 30
                    draw("speed set to 30")
                    continue
                if k == ord("2"):
                    current_speed = 50
                    draw("speed set to 50")
                    continue
                if k == ord("3"):
                    current_speed = 80
                    draw("speed set to 80")
                    continue

                if k == curses.KEY_UP:
                    left = float(current_speed)
                    right = float(current_speed)
                    last_key_ts = now
                    send({"type": "drive", "left": left, "right": right})
                    last_send = now
                    draw("drive: forward")
                elif k == curses.KEY_DOWN:
                    left = -float(current_speed)
                    right = -float(current_speed)
                    last_key_ts = now
                    send({"type": "drive", "left": left, "right": right})
                    last_send = now
                    draw("drive: backward")
                elif k == curses.KEY_LEFT:
                    left = -float(current_speed)
                    right = float(current_speed)
                    last_key_ts = now
                    send({"type": "drive", "left": left, "right": right})
                    last_send = now
                    draw("drive: left")
                elif k == curses.KEY_RIGHT:
                    left = float(current_speed)
                    right = -float(current_speed)
                    last_key_ts = now
                    send({"type": "drive", "left": left, "right": right})
                    last_send = now
                    draw("drive: right")
                elif k == ord(" "):
                    send({"type": "stop"})
                    last_send = now
                    left = 0.0
                    right = 0.0
                    draw("sent: stop")

        finally:
            try:
                await ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass
            try:
                recv_task.cancel()
            except Exception:
                pass
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.endwin()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="WebSocket URL, e.g. ws://100.97.253.17:8765")
    p.add_argument("--step-cm", type=float, default=4.0)
    p.add_argument("--step-deg", type=float, default=15.0)
    p.add_argument("--speed", type=int, default=40)
    args = p.parse_args()

    asyncio.run(teleop(args.url, args.step_cm, args.step_deg, args.speed))


if __name__ == "__main__":
    main()
