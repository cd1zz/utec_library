# Setup Guide

## Hardware

Any Raspberry Pi with Bluetooth works. Tested on:
- **Raspberry Pi 5** (BT 5.0) — works great
- **Raspberry Pi Zero 2 W** (BT 4.2) — works great, cheapest option (~$15)

Place the Pi within ~30 feet of your U-tec locks with minimal walls between them. BLE doesn't penetrate exterior walls or metal doors well.

## Operating System

**Use Raspberry Pi OS Bookworm (Debian 12).** Flash it with the Raspberry Pi Imager.

> **Warning:** Debian 13 (Trixie) ships with BlueZ 5.82 and installs Bleak 3.0+, which causes BLE connection timeouts with U-tec locks. Scanning works but connections silently fail. Stick with Bookworm.

## Installation

### 1. System dependencies

```bash
sudo apt-get install -y python3-dev gcc git
```

### 2. Clone and install

```bash
git clone https://github.com/cd1zz/utec_library.git
cd utec_library
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 3. Configure

Copy the sample config and edit it:

```bash
cp .env_sample .env
nano .env
```

Required settings:
- `UTEC_EMAIL` — your U-tec app email
- `UTEC_PASSWORD` — your U-tec app password
- `MQTT_HOST` — your Home Assistant IP
- `MQTT_USERNAME` / `MQTT_PASSWORD` — if your MQTT broker requires auth

### 4. Test

```bash
.venv/bin/python main.py --test-discovery    # verify U-tec account works
.venv/bin/python main.py --test-mqtt         # verify MQTT broker works
.venv/bin/python main.py --verbose           # run with debug logging
```

### 5. Install as a service

```bash
sudo tee /etc/systemd/system/utec_library.service > /dev/null << EOF
[Unit]
Description=Utec Library Python Service
After=network.target bluetooth.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/utec_library
ExecStart=$HOME/utec_library/.venv/bin/python $HOME/utec_library/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable utec_library.service
sudo systemctl start utec_library.service
```

Check logs:

```bash
sudo journalctl -u utec_library.service -f
```

## Multi-Bridge Setup

If some locks are out of BLE range from a single Pi, deploy multiple Pis and use `LOCK_FILTER` to assign locks to each bridge.

### Example: two Pis

**Pi 1** (living room) — `.env`:
```
LOCK_FILTER=Front Door,Office door
```

**Pi 2** (back patio) — `.env`:
```
LOCK_FILTER=Back Patio
```

Both Pis use the same U-tec credentials and MQTT broker. Each only manages its assigned locks — no BLE contention, no MQTT conflicts, no duplicate commands.

`LOCK_FILTER` accepts a comma-separated list of lock names (as they appear in the U-tec app) or MAC addresses. Leave it empty or unset to manage all locks.

## Troubleshooting

### Bluetooth not working

```bash
# Check if Bluetooth is blocked
sudo rfkill list bluetooth

# Unblock if needed
sudo rfkill unblock bluetooth
sudo hciconfig hci0 up
```

### Connection timeouts

- Verify you're running **Debian 12 Bookworm**, not Trixie: `cat /etc/os-release`
- Verify **Bleak version is 0.22.x**, not 3.x: `.venv/bin/pip show bleak`
- Move the Pi closer to the lock — RSSI should be better than -75 dBm
- Check for USB 3.0 devices nearby — they interfere with 2.4GHz BLE

### Check lock status

```bash
sudo journalctl -u utec_library.service --since "5 minutes ago" --no-pager | grep -E "(RSSI|established|LOCK_STATUS|Battery|Lock:|Connection attempt|Status update completed|ERROR|Device found)"
```
