#!/usr/bin/env python3
import argparse
import glob
import os
import re
import signal
import subprocess
import sys
import time

from ds4_hid import DS4, find_ds4_bt, find_ds4_usb

DEFAULT_UNPLUG_TIMEOUT = 45


def run(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def bt(*args):
    return run("bluetoothctl", *args)


def ts():
    return time.strftime("%H:%M:%S")


def log(msg):
    print(f"{ts()} {msg}", flush=True)


def adapter_address(explicit=None):
    if explicit:
        return explicit.upper()
    out = bt("list")
    match = re.search(r"Controller ([0-9A-Fa-f:]{17})", out)
    if not match:
        raise RuntimeError("no Bluetooth adapter found")
    return match.group(1).upper()


def powered():
    return "Powered: yes" in bt("show")


def set_power(on):
    bt("power", "on" if on else "off")


def wait_for(cond, timeout, message):
    end = time.time() + timeout
    while time.time() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(message)


def wait_powered(on, timeout=8):
    wait_for(lambda: powered() == on, timeout,
             f"adapter did not power {'on' if on else 'off'}")


def start_agent():
    proc = subprocess.Popen(
        ["bash", "-c", "exec tail -f /dev/null | exec bluetoothctl --agent auto"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(2)
    return proc


def stop_agent(proc):
    if not proc:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def read_pairing_info(path):
    last = None
    for _ in range(50):
        try:
            with DS4(path) as d:
                return d.get_pairing_info()
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"could not read pairing info: {last}")


def wait_cable_pairing(mac, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if "CablePairing: yes" in bt("info", mac):
            return True
        time.sleep(0.25)
    return False


def wait_usb_unplug(timeout):
    end = time.time() + timeout
    while time.time() < end:
        if find_ds4_usb() is None:
            return True
        time.sleep(0.1)
    return False


def wait_bt_hid(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        path = find_ds4_bt()
        if path:
            return path
        time.sleep(0.2)
    return None


def input_names():
    found = []
    for path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(path) as f:
                name = f.read().strip()
        except OSError:
            continue
        if "Wireless Controller" in name:
            found.append((path.split("/")[-3], name))
    return found


def battery(mac):
    base = f"/sys/class/power_supply/ps-controller-battery-{mac.lower()}"
    try:
        with open(base + "/capacity") as f:
            capacity = f.read().strip()
        with open(base + "/status") as f:
            status = f.read().strip()
        return capacity, status
    except OSError:
        return None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pair a DualShock 4 over Bluetooth without PS+Share.")
    parser.add_argument("--adapter", help="adapter MAC (default: first controller)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_UNPLUG_TIMEOUT,
                        help="seconds to wait for unplug (default: 45)")
    parser.add_argument("--no-agent", action="store_true",
                        help="use an existing BlueZ agent instead of starting one")
    return parser.parse_args()


def main():
    args = parse_args()
    adapter = adapter_address(args.adapter)
    log(f"adapter {adapter}")

    if find_ds4_usb() is None and find_ds4_bt():
        log("DS4 is already connected over Bluetooth; disconnect it and replug USB to re-pair")
        return 1

    agent = None
    if not args.no_agent:
        agent = start_agent()
        log("temporary auto-accept agent started")

    powered_off = False
    try:
        if find_ds4_usb():
            log("DS4 is already on USB; unplug it now, then plug it back in")
            if not wait_usb_unplug(60):
                raise RuntimeError("DS4 was not unplugged")
            log("now plug the DS4 back in")
        else:
            log("plug the DS4 in via USB now")

        path = wait_for(find_ds4_usb, 60, "DS4 did not appear on USB")
        log(f"DS4 USB at {path}")
        time.sleep(2)

        ctrl, host = read_pairing_info(path)
        log(f"controller {ctrl}; stored host {host}")

        if wait_cable_pairing(ctrl):
            log("cable pairing detected")
            _, host = read_pairing_info(path)
            if host.upper() != adapter:
                log(f"warning: stored host {host} differs from adapter {adapter}")
        else:
            log("warning: cable pairing not detected; connection may be refused")

        log("powering adapter off")
        set_power(False)
        powered_off = True
        wait_powered(False)

        with DS4(path) as d:
            d.trigger_pairing()
        log("sent 0x14 0x01")
        log(">>> UNPLUG THE USB CABLE NOW <<<")

        end = time.time() + args.timeout
        last_trigger = time.time()
        while time.time() < end:
            current = find_ds4_usb()
            if current is None:
                break
            path = current
            if time.time() - last_trigger >= 1.5:
                try:
                    with DS4(path) as d:
                        d.trigger_pairing()
                    last_trigger = time.time()
                except OSError:
                    pass
            time.sleep(0.1)
        else:
            raise RuntimeError("timed out waiting for USB unplug")

        log("USB gone; powering adapter on")
        set_power(True)
        powered_off = False
        wait_powered(True)

        hid = wait_bt_hid(30)
        if not hid:
            raise RuntimeError("Bluetooth HID device did not appear")

        log(f"Bluetooth HID device: {hid}")
        for event, name in input_names():
            log(f"input: {event} {name}")
        print(bt("info", ctrl))
        state = battery(ctrl)
        if state:
            log(f"battery: {state[0]}% ({state[1]})")
        log("success")
        return 0
    except Exception as exc:
        log(f"error: {exc}")
        return 1
    finally:
        if powered_off:
            log("restoring adapter power")
            set_power(True)
        stop_agent(agent)


if __name__ == "__main__":
    sys.exit(main())
