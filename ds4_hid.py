#!/usr/bin/env python3
import fcntl
import glob
import os
import sys

IOC_RW = 0xC0004800
USB_BUS = "0003"
BT_BUS = "0005"
DS4_IDS = ("0000054C:000005C4", "0000054C:000009CC")


def gfeature(n):
    return IOC_RW | 0x07 | (n << 16)


def sfeature(n):
    return IOC_RW | 0x06 | (n << 16)


def _hidraw_for_bus(bus):
    for uevent in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            with open(uevent) as f:
                fields = dict(
                    line.split("=", 1)
                    for line in f.read().splitlines()
                    if "=" in line
                )
        except OSError:
            continue
        hid_id = fields.get("HID_ID", "").upper()
        if not hid_id.startswith(bus + ":"):
            continue
        if any(i in hid_id for i in DS4_IDS):
            node = os.path.basename(os.path.dirname(os.path.dirname(uevent)))
            return "/dev/" + node
    return None


def find_ds4_usb():
    return _hidraw_for_bus(USB_BUS)


def find_ds4_bt():
    return _hidraw_for_bus(BT_BUS)


def mac_canon(b):
    return ":".join("{:02X}".format(c) for c in reversed(b))


def mac_le(mac):
    return bytes.fromhex(mac.replace(":", ""))[::-1]


def hexs(b):
    return b.hex(" ")


class DS4:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def get_feature(self, report_id, size):
        buf = bytearray(size + 1)
        buf[0] = report_id
        fcntl.ioctl(self.fd, gfeature(size + 1), buf)
        return bytes(buf)

    def set_feature(self, report_id, payload):
        buf = bytearray(1 + len(payload))
        buf[0] = report_id
        buf[1:] = payload
        fcntl.ioctl(self.fd, sfeature(len(buf)), buf)
        return bytes(buf)

    def get_pairing_info(self):
        buf = self.get_feature(0x12, 15)
        return mac_canon(buf[1:7]), mac_canon(buf[10:16])

    def write_pairing(self, host_mac, link_key):
        key = bytes(link_key)
        if len(key) != 16:
            raise ValueError("link key must be 16 bytes")
        self.set_feature(0x13, mac_le(host_mac) + key)

    def trigger_pairing(self):
        self.set_feature(0x14, b"\x01" + b"\x00" * 15)

    def unpair(self):
        self.set_feature(0x14, b"\x02" + b"\x00" * 15)


def main():
    path = find_ds4_usb()
    if not path:
        sys.exit("DS4 not found on USB (054c:05c4 or 054c:09cc)")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    with DS4(path) as d:
        if cmd == "info":
            mac = d.get_feature(0x81, 6)
            ctrl, host = d.get_pairing_info()
            print("device:       ", path)
            print("0x81 raw:     ", hexs(mac))
            print("controller:   ", mac_canon(mac[1:7]))
            print("0x12 ctrl:    ", ctrl)
            print("0x12 host:    ", host)
        elif cmd == "trigger":
            d.trigger_pairing()
            print("sent 0x14 0x01")
        elif cmd == "unpair":
            d.unpair()
            print("sent 0x14 0x02")
        else:
            sys.exit("commands: info | trigger | unpair")


if __name__ == "__main__":
    main()
