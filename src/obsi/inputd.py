#!/usr/bin/env python3
"""Own a physical keyboard, filter Alt+F12, and forward through uinput."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Linux input key codes. Keeping the filter independent from python-evdev makes
# it unit-testable on development machines.
EV_SYN = 0
EV_KEY = 1
KEY_LEFTALT = 56
KEY_RIGHTALT = 100
KEY_F12 = 88


@dataclass
class FilterResult:
    forward: list[tuple[int, int, int]] = field(default_factory=list)
    synthetic: list[tuple[int, int, int]] = field(default_factory=list)
    trigger: bool = False


class HotkeyFilter:
    def __init__(self) -> None:
        self.alt_down: set[int] = set()
        self.synthetic_released: set[int] = set()
        self.chord_active = False

    def process(self, event_type: int, code: int, value: int) -> FilterResult:
        result = FilterResult()
        event = (event_type, code, value)
        if event_type == EV_SYN:
            return result
        if event_type != EV_KEY:
            result.forward.append(event)
            return result

        if code in (KEY_LEFTALT, KEY_RIGHTALT):
            if value in (1, 2):
                self.alt_down.add(code)
                if code not in self.synthetic_released:
                    result.forward.append(event)
            else:
                self.alt_down.discard(code)
                if code in self.synthetic_released:
                    self.synthetic_released.discard(code)
                else:
                    result.forward.append(event)
                if not self.alt_down:
                    self.chord_active = False
            return result

        if code == KEY_F12 and self.alt_down:
            if value == 1 and not self.chord_active:
                self.chord_active = True
                result.trigger = True
                for alt_code in sorted(self.alt_down):
                    result.synthetic.append((EV_KEY, alt_code, 0))
                    self.synthetic_released.add(alt_code)
            return result

        if code == KEY_F12 and self.chord_active:
            return result

        result.forward.append(event)
        return result


def read_active(path: Path) -> str | None:
    try:
        machine = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if machine and all(c.isalnum() or c in "_-" for c in machine):
        return machine
    return None


def trigger_hibernate(machine: str, command: str) -> None:
    subprocess.Popen(
        [command, "hibernate", machine],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/etc/obsi/inputd.json"))
    parser.add_argument("--active-file", type=Path, default=Path("/run/obsi/active-machine"))
    parser.add_argument("--workspace-command", default="/usr/bin/obsi-workspace")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="obsi-inputd: %(message)s")

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        device_path = config["keyboard"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        logging.error("cannot load %s: %s", args.config, exc)
        return 1

    try:
        from evdev import InputDevice, UInput, ecodes
    except ImportError:
        logging.error("python3-evdev is required")
        return 1

    device = InputDevice(device_path)
    ui = UInput.from_device(device, name="OBSI Guest Keyboard", phys="obsi/input0")
    filter_state = HotkeyFilter()
    device.grab()
    logging.info("grabbed %s; forwarding as %s", device.path, ui.device)
    try:
        for event in device.read_loop():
            result = filter_state.process(event.type, event.code, event.value)
            for event_type, code, value in result.forward + result.synthetic:
                ui.write(event_type, code, value)
            if result.forward or result.synthetic:
                ui.syn()
            if result.trigger:
                machine = read_active(args.active_file)
                if machine:
                    logging.info("Alt+F12: hibernating %s", machine)
                    trigger_hibernate(machine, args.workspace_command)
                else:
                    logging.info("Alt+F12 ignored: no active workspace")
    finally:
        device.ungrab()
        ui.close()
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
