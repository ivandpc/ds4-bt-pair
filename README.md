# ds4-bt-pair

Buttonless Bluetooth pairing for the Sony DualShock 4 on Linux, including a
workaround for the `hid-playstation` duplicate-MAC bug that makes the
controller connect for only a few seconds.

Tested with:

- DualShock 4 CUH-ZCT1x (`054c:05c4`)
- BlueZ 5.87 / `bluetoothctl`
- Linux 6.x with `hid-playstation` and `uhid`
- A generic USB BR/EDR adapter

The DS4 v2 (`054c:09cc`) is matched by the USB finder; the same feature-report
protocol is expected to work, but it was not tested here.

## Why the obvious approaches fail

1. **Host-initiated connect times out.** A DS4 that is not in pairing mode does
   not page-scan. `bluetoothctl connect <mac>` ends with
   `br-connection-page-timeout`.
2. **USB feature report `0x14` makes the controller page the host.** Sending
   `0x14 0x01` over USB puts it into pairing/connect mode without PS+Share, and
   it pages the Bluetooth address stored in its pairing info.
3. **Linux refuses the BT HID instance while USB is attached.** The kernel's
   `hid-playstation` driver keeps a global list of controller MAC addresses. If
   the DS4 is also on USB, the BT probe fails with:

   ```
   playstation 0005:054C:05C4.0013: Duplicate device found for MAC address aa:bb:cc:dd:ee:ff.
   playstation 0005:054C:05C4.0013: Failed to create dualshock4.
   playstation 0005:054C:05C4.0013: probe with driver playstation failed with error -17
   ```

   The Bluetooth link may stay up for a while, but no input device is
   registered, and the link eventually drops. That is the "works for about ten
   seconds" symptom.
4. **BlueZ's sixaxis input plugin needs an agent.** On USB add it sets the PNP
   ID and requests cable-pairing authorization. Without an agent it logs
   `Authentication attempt without agent`, removes the temporary device, and
   the input server later rejects the connection:

   ```
   profiles/input/server.c:confirm_event_cb() Refusing connection from aa:bb:cc:dd:ee:ff: unknown device
   ```

   The plugin also writes the adapter's Bluetooth address into the controller
   via feature report `0x13` (with a zero link key; BlueZ then does SSP and
   stores the real key) and marks the device as cable-paired, which lowers the
   HIDP security requirement.

The fix is a three-part sequence:

- register an agent so the sixaxis plugin can cable-pair the device,
- trigger `0x14 0x01` while the adapter radio is **off** (so the controller
  cannot connect early),
- unplug USB, then turn the radio on. The BT HID probe now runs with no USB
  twin.

## Requirements

- Linux with BlueZ (`bluetoothctl`), `hid-playstation`, and `uhid`.
- Python 3.
- Read/write access to `/dev/hidraw*` (desktop udev ACL; otherwise add a udev
  rule or the `input` group).
- Permission to power the adapter off/on via BlueZ. This is usually granted to
  the local seat; if `bluetoothctl power off` is denied, adjust polkit or run
  the command as a user with permission.
- The controller must have enough battery to stay on when the USB cable is
  removed.

No root is required for the normal desktop case. The script does not write link
keys; BlueZ and the controller handle them via SSP.

## Usage

```
python3 ds4_bt_pair.py
```

What happens:

1. The script starts a temporary `bluetoothctl --agent auto` (auto-accept; see
   Security).
2. If the DS4 is already plugged in, it asks you to unplug it, then plug it
   back in, so the sixaxis plugin runs with the agent present.
3. It waits for the USB node, reads the pairing info (`0x12`), and waits for
   `CablePairing: yes`.
4. It powers the Bluetooth adapter off.
5. It sends `0x14 0x01` over USB and prints **UNPLUG THE USB CABLE NOW**.
6. Unplug the cable and keep it unplugged. The script keeps re-sending `0x14`
   while waiting.
7. Once the USB node disappears, it powers the adapter back on.
8. The controller connects, the BT HID device registers, and the script prints
   `bluetoothctl info`, the input event devices, and the battery state.

If the controller is already plugged in and fully set up, the replug step is
still safe; it only re-runs the sixaxis plugin (which may skip with
`device ... already known, skipping`).

Options:

```
--adapter MAC     use a specific adapter (default: first controller from bluetoothctl list)
--timeout N       seconds to wait for the unplug (default: 45)
--no-agent        do not start the temporary auto agent (use an existing agent)
```

If your desktop's Bluetooth applet is running, it may intercept the
authorization request and show a prompt. Either accept it, or stop the applet
for the setup and use the script's auto agent.

### Manual equivalent

If you prefer not to run the script:

1. Start an agent: `bluetoothctl --agent auto` and keep it running.
2. Plug in the DS4 and wait for the sixaxis plugin
   (`journalctl -u bluetooth -f`).
3. `bluetoothctl power off`
4. `python3 ds4_hid.py trigger`
5. Unplug the USB cable.
6. `bluetoothctl power on`

`ds4_hid.py` also supports `info` and `unpair` for debugging.

## Verification

```
bluetoothctl info <controller-MAC>
ls -l /dev/input/js*
journalctl -k | grep -E 'playstation|BLUETOOTH HID'
```

A successful registration looks like:

```
playstation 0005:054C:05C4.0013: hidraw9: BLUETOOTH HID v1.00 Gamepad [Wireless Controller] on 11:22:33:44:55:66
input: Wireless Controller as /devices/virtual/misc/uhid/...
input: Wireless Controller Motion Sensors ...
input: Wireless Controller Touchpad ...
playstation 0005:054C:05C4.0013: Registered DualShock4 controller hw_version=0x00005414 fw_version=0x00002029
```

`ServicesResolved: false` can appear even when everything works: the device
store already has the HID UUIDs, so no SDP query is needed. Trust the kernel
registration and `/dev/input/js*`, not that property.

## After pairing

- The bond and cable-pairing flag are stored by BlueZ
  (`/var/lib/bluetooth/...`). You do not need to repeat this unless you unpair
  or change hosts.
- The controller reconnects when turned on (PS button) as long as the adapter
  is powered and connectable.
- Plugging USB in while the BT link is active makes the kernel reject the USB
  HID instance with the same duplicate-MAC error. Charging still works, and
  the BT input remains. Disconnect Bluetooth first if you want wired mode.
- To rerun the script, disconnect the controller and replug USB first. While a
  BT link is active the USB HID node may not exist (it was rejected as a
  duplicate), and the script exits with `already connected over Bluetooth`.
- If the controller turns off when unplugged, charge it. The script prints the
  battery state.

## Troubleshooting

- `Refusing connection ... unknown device` or
  `Authentication attempt without agent`:
  the sixaxis plugin did not complete cable pairing. Start the agent first,
  then unplug/replug the DS4 and rerun.
- `Duplicate device found ... -17`:
  USB was still attached when the BT HID probed. The radio must be off during
  the trigger, and page scan must only be enabled after the USB node is gone.
- `br-connection-page-timeout`:
  host-initiated connect to a DS4 that is not paging. Use `0x14 0x01`.
- `power off` denied:
  polkit restriction. Grant your user adapter control, or run
  `bluetoothctl power off` manually before step 5 and `bluetoothctl power on`
  after unplugging.
- The controller is connected but no input:
  check `journalctl -k` for the duplicate/probe error, check that the BT node
  is `0005:054C:05C4` (not the USB `0003:` node), and check the battery.
- The script powered the adapter off and something else powered it back on:
  disable other Bluetooth managers or auto-power during the procedure. The
  radio-off window is only a few seconds.

## Protocol

Feature reports on the DS4 (report ID + data; HIDIOCGFEATURE/HIDIOCSFEATURE
length includes the report ID):

| Report | Direction | Data | Meaning |
|--------|-----------|------|---------|
| `0x12` | GET | 15 bytes | `[controller MAC LE][08 25 00][host MAC LE]` |
| `0x13` | SET | 22 bytes | `[host MAC LE][16-byte link key]` |
| `0x14` | SET | 16 bytes | `[cmd][15 x 00]`; `0x01` = pair/connect, `0x02` = unpair |
| `0x81` | GET | 6 bytes | controller MAC (verification) |

Example `0x12` with placeholder addresses:

```
12 ff ee dd cc bb aa 08 25 00 66 55 44 33 22 11
   AA:BB:CC:DD:EE:FF          11:22:33:44:55:66
```

The MAC bytes are little-endian. The host address is the adapter's address;
the sixaxis plugin writes it with `0x13` when it differs, using a zero key, and
SSP establishes the real key. `DS4.write_pairing()` is a low-level helper for
that report; it is not used by the pairing flow and needs a matching key in
BlueZ to be useful.

`0x14 0x02` (unpair) is exposed as `ds4_hid.py unpair`; it does not clear
BlueZ's stored bond, so remove the BlueZ device as well if you want a clean
slate.

## Security

The script starts `bluetoothctl --agent auto` for the duration of the setup.
That agent auto-accepts authorization and service requests without
confirmation. It is convenient for this local, physical-cable setup, but do not
leave it running. The script terminates it in its `finally` block. If you
prefer, use your desktop's agent and approve the cable-pairing prompt manually
(`--no-agent`).

The repository does not contain link keys or device-specific secrets. The MAC
addresses in the examples are placeholders.

## References

- DS4 reverse engineering, part 3 (pairing reports):
  https://blog.the.al/2023/01/03/ds4-reverse-engineering-part-3.html
- Unpairing a DualShock 4 and setting a new BDADDR:
  https://eleccelerator.com/unpairing-a-dualshock-4-and-setting-a-new-bdaddr/
- `ds4pairer` (host MAC write): https://github.com/paulstraw/ds4pairer
- `ds4-tools`: https://github.com/dualshock-tools/ds4-tools
- DS4 HID report descriptor: https://github.com/DJm00n/ControllersInfo
- BlueZ 5.87: `plugins/sixaxis.c`, `profiles/input/server.c`,
  `profiles/input/device.c`, `src/adapter.c`, `src/device.c`

## Files

- `ds4_hid.py` - USB feature-report library and small CLI (`info`, `trigger`,
  `unpair`).
- `ds4_bt_pair.py` - end-to-end pairing script (agent, radio-off gate, unplug
  detection, verification).
- `.gitignore`
