# Raspberry Pi thin-client setup

The Pi runs only the eco-free thin client (Python 3 + Tkinter). All eco /
EPICS logic stays on the PC. The two talk over one serial line (Bluetooth
RFCOMM in the field; TCP only for local testing).

## What goes on the Pi

Nothing eco. Only:
- `python3` and `python3-tk` (`sudo apt install python3-tk`)
- the `manual_control/` bundle package, built on the PC with:
  ```
  python -m eco.manual_control.remote.make_pi_bundle /tmp/pi
  ```
  then copy `/tmp/pi/manual_control/` to the Pi (e.g. `~/manual_control_pi/manual_control/`).

Run it:
```
cd ~/manual_control_pi
python3 -m manual_control.remote.pi_client --serial /dev/rfcomm0     # Bluetooth
python3 -m manual_control.remote.pi_client --tcp 192.168.x.y 8791    # testing only
```

On the PC, run the server against the fake beamline or real bernina:
```
python -m eco.manual_control.remote.serve --fake    --serial /dev/rfcomm0
python -m eco.manual_control.remote.serve --bernina --serial /dev/rfcomm0
```

## The link, by Pi model

- **Bluetooth (works on Pi 3B, the wireless choice here):** the Pi 3B has
  built-in BT. Pair once, then bind an RFCOMM channel so `/dev/rfcomm0`
  appears on both ends. Sketch:
  - PC: `sudo rfcomm bind 0 <PI_BT_MAC> 1`
  - Pi: run an RFCOMM listener bound to `/dev/rfcomm0`
  - point both `--serial` at `/dev/rfcomm0`.
- **USB — provisioning always works:** power the Pi, copy the bundle,
  `apt install python3-tk`, enable autostart. Good for setup regardless of
  model.
- **USB as the *data link*:** depends on the model.
  - **Pi 3B cannot be a USB serial/ethernet gadget** (its USB is host-only;
    micro-USB is power only). For a *wired* link on a 3B use a USB-TTL
    serial cable from a PC USB port to the Pi GPIO UART (GND/TXD/RXD):
    PC sees `/dev/ttyUSB0`, Pi uses `/dev/serial0`.
  - **Pi Zero 2 W / Pi 4 / Pi 5** can do USB-gadget serial: Pi side
    `/dev/ttyGS0`, PC side typically `/dev/ttyACM0` — plug in one USB cable,
    no wires. (These also have Wi-Fi/BT.)

## Autostart on the touchscreen

Copy `manual-control-client.service` to `/etc/systemd/system/`, edit the
`WorkingDirectory` / `--serial` device / user, then:
```
sudo systemctl enable --now manual-control-client
```

## Updating

- New components / values / tree content: nothing on the Pi — the PC streams it.
- New GUI *widgets/layout*: rebuild the bundle on the PC and re-copy
  `manual_control/` to the Pi (or `git pull` if you keep it in a repo there).

---

# Pi Zero 2 W pendant build (touchscreen + joystick + encoder)

## Link: Bluetooth (default) — keeps the USB port free to charge

Data goes over Bluetooth RFCOMM so the single USB cable is only ever for
power/charging (see Battery & charging). Both ends expose `/dev/rfcomm0`.

PC (once): pair with the Pi, then bind a channel:
```
bluetoothctl            # scan on; pair <PI_MAC>; trust <PI_MAC>
sudo rfcomm bind 0 <PI_MAC> 1     # -> /dev/rfcomm0 on the PC
```
Pi (once): advertise an RFCOMM serial service on channel 1 and bind it to
`/dev/rfcomm0` (e.g. an `rfcomm watch hci0 1` unit, or `sdptool add SP`
plus `rfcomm listen`). Make the Pi discoverable/pairable on first setup.

Then both sides use `--serial /dev/rfcomm0`. The autostart unit already does.

### USB-gadget serial (alternative link)

If you'd rather link over the cable (no Bluetooth): on the Pi add
`dtoverlay=dwc2` to `config.txt` and `dwc2`,`g_serial` to
`/etc/modules-load.d/gadget.conf`, reboot → Pi `/dev/ttyGS0`, PC
`/dev/ttyACM0`. Note: then the cable is busy with data and the battery
charges via the PiSugar's own port instead.

## GPIO wiring (BCM numbering — VERIFY free pins against your 4" SPI
## display's overlay before soldering; the display owns SPI0 + a few GPIO)

| Signal | Pin |
|---|---|
| Rotary encoder A / B / push | GPIO 5 / 6 / 13 |
| Thumbstick push button | GPIO 19 |
| Thumbstick X / Y (analog) | ADS1115 A0 / A1 |
| ADS1115 + PiSugar | I²C: GPIO 2 (SDA) / 3 (SCL), 3V3, GND |

Defaults live in `manual_control/remote/pi_hardware.py:DEFAULT_CONFIG` — edit
there if you use other pins.

## Python packages on the Pi

```
sudo apt install python3-tk python3-gpiozero
pip3 install adafruit-circuitpython-ads1x15
```
(Only needed for the physical controls. Without them the GUI still runs by
touch/keyboard — HardwareInput reports itself unavailable.)

## Run

```
cd ~/manual_control_pi
python3 -m manual_control.remote.pi_app --serial /dev/rfcomm0 --fullscreen --idle-blank 60
```
Autostart on the touchscreen: install `manual-control-client.service` (see
above) — it already points at `pi_app --serial /dev/rfcomm0 --fullscreen
--idle-blank 60`.

## PC side

Run the server against the gadget serial. For testing:
```
python -m eco.manual_control.remote.serve --fake --serial /dev/ttyACM0
```
For the real beamline, start it from inside your eco session so there is no
cold import (see `eco-pendant-serve@.service` header for the snippet).
Optional auto-start on plug-in: `99-eco-pendant.rules` + `eco-pendant-serve@.service`.

## Battery & charging

Battery: **PiSugar 3+ (5000 mAh)** (drop-in for the 1200 mAh PiSugar 3 —
same pogo-pin mount, thicker; the enclosure `depth_in` is set to 36 mm for
it). Idle backlight blanking (`--idle-blank`, on by default in the unit)
turns off the display's backlight — ~half the draw — after 60 s of no
input, and any touch/knob/stick wakes it instantly.

Estimated runtime: **~18–24 h** in normal intermittent use with blanking
(mostly-idle duty), ~10–12 h if the screen is kept on continuously. For a
guaranteed 24 h+ of *continuous-display* use you'd need ~10,000 mAh — an
external UPS/pack rather than a PiSugar (heavier). Confirm with a USB power
meter once assembled; the display backlight is the main variable.

Charging: because the data link is **Bluetooth**, the USB port is free —
plug the single cable from the computer (or any 5 V charger) into the
**PiSugar's USB-C** and it charges while running. (If you switch the link
to USB-gadget serial instead, that cable carries data and cannot also
charge — you'd charge separately.)

## Housing

Printable enclosure in `eco/manual_control/hardware/`:
- `pendant.scad` — parametric OpenSCAD master (proper round holes, screw
  posts). Render: `openscad -o tray.stl -D 'part="tray"' pendant.scad` and
  `... -D 'part="faceplate"' ...`.
- `generate_stl.py` — emits ready `pendant_tray.stl` / `pendant_faceplate.stl`
  directly (no OpenSCAD needed): `python3 generate_stl.py .`

Both are a **first article**: print the faceplate alone first and check the
display window + joystick/encoder cutouts against your actual parts, then
adjust the dimension variables at the top of the file before printing the
tray.
