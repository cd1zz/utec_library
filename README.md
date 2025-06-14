# U-tec Smart Lock Python Library

> **Note**: 99% of the hard work was done by [maeneak/utecio](https://github.com/maeneak/utecio). This project builds upon their excellent foundation.

A Python library for interacting with U-tec smart locks via Bluetooth Low Energy (BLE) with complete Home Assistant MQTT integration support.

## Why This Project?

This project was specifically built to run on a **Raspberry Pi positioned within BLE range of U-tec smart locks** to provide reliable, up-to-date status reporting to Home Assistant. 

**The Problem**: U-tec's WiFi bridges are notoriously unreliable and often fail to report accurate lock status, leaving Home Assistant with stale or incorrect data.

**The Solution**: This library bypasses the unreliable WiFi bridges entirely by:
- Running directly on a Raspberry Pi with Bluetooth capability
- Polling lock status via BLE at configurable intervals (default: 5 minutes)
- Publishing real-time status updates to Home Assistant via MQTT
- Providing immediate status changes when locks are operated
- **Full remote control** via MQTT commands from Home Assistant

## Main Bridge Script

### `main.py` - Complete Home Assistant Integration
**Full-featured bridge with monitoring and control**

- ✅ Polls lock status every 5 minutes (configurable)
- ✅ Publishes status updates to Home Assistant via MQTT
- ✅ Auto-discovers locks from your U-tec account
- ✅ Sets up Home Assistant device discovery
- ✅ Listens for MQTT lock/unlock commands from Home Assistant
- ✅ Executes lock/unlock operations via Bluetooth
- ✅ Immediate status updates after commands
- ✅ Bridge management commands
- ✅ Comprehensive CLI options for testing and deployment

**Features complete Home Assistant integration with dashboard control and status monitoring.**

## Command Line Options

The bridge script supports extensive command line options for flexible deployment:

```bash
# Basic usage
python main.py

# Configuration options
python main.py --config-file /etc/utec/production.env
python main.py --mqtt-host 192.168.1.100 --update-interval 120
python main.py --dry-run --verbose  # Safe testing mode

# Testing options
python main.py --test-discovery     # Test device discovery
python main.py --test-mqtt         # Test MQTT connection

# Get help
python main.py --help
```

### Available Options:
- `--verbose` / `--debug` - Enable debug logging
- `--config-file PATH` - Use custom configuration file
- `--mqtt-host HOST` / `--mqtt-port PORT` - Override MQTT settings
- `--update-interval SECONDS` - Change status update frequency
- `--dry-run` - Test mode (no actual lock commands executed)
- `--test-discovery` - Test U-tec account and device discovery
- `--test-mqtt` - Test MQTT broker connectivity

## U-Bolt-PRO Support

This library includes **enhanced support for U-Bolt-PRO devices** with critical fixes that were necessary for proper operation:

### Fixed Issues for U-Bolt-PRO:
- **Authentication Protocol**: U-Bolt-PRO requires user credentials (UID/password) embedded in each lock/unlock command, not separate admin login
- **Device Capability Detection**: Proper bt264 protocol detection for newer U-Bolt-PRO firmware

### Technical Improvements:
- **Command Data Embedding**: Lock/unlock commands now properly include 4-byte UID and length-encoded password
- **Status Command Filtering**: bt264-capable devices (like U-Bolt-PRO) now use optimized status commands
- **Connection Per Operation**: Follows BLE best practices for battery-powered devices

## Testing Script

A comprehensive testing script (`test.py`) is included for validating lock functionality:

### Features:
- **Device Discovery Testing**: Validates U-tec cloud authentication and device enumeration
- **Multi-Lock Support**: Test any of your registered U-tec locks
- **Safe Operations**: Pre/post status checks with bolt jam detection
- **Status Interpretation**: Human-readable display of all lock parameters
- **Confirmation Prompts**: Safety confirmations for lock/unlock operations

### Usage:
```bash
python test.py
```

The test script will:
1. Test login credentials and discover all available locks
2. Allow selection of specific lock for testing
3. Provide interactive menu for status checks and lock operations
4. Perform safety validations before executing commands
5. Verify operation success with post-command status checks

## Features

- **BLE Communication**: Direct Bluetooth Low Energy communication with U-tec locks
- **Home Assistant Integration**: Automatic MQTT discovery and state publishing  
- **Multiple Lock Support**: Handle multiple locks from single client
- **Persistent Authentication**: Token caching with automatic renewal
- **Event System**: Subscribe to lock events (unlock, lock, status changes)
- **Device Factory**: Extensible device registration system
- **Comprehensive Logging**: Configurable debug information
- **Error Handling**: Robust exception handling and retry logic
- **Async/Await Support**: Full asynchronous operation
- **Remote Control**: MQTT command listening for Home Assistant control
- **Flexible Configuration**: Environment variables with CLI overrides
- **Safe Testing**: Dry-run mode for configuration validation

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

1. Create a `.env` file in the project root:
```env
# Required
UTEC_EMAIL=your_email@example.com
UTEC_PASSWORD=your_password
MQTT_HOST=your_home_assistant_ip

# Optional
MQTT_PORT=1883
MQTT_USERNAME=your_mqtt_user
MQTT_PASSWORD=your_mqtt_password
UPDATE_INTERVAL=300
```

### Running the Bridge

```bash
# Basic usage with .env configuration
python main.py

# Production deployment with custom config
python main.py --config-file /etc/utec/bridge.env

# Development testing
python main.py --dry-run --verbose --update-interval 60

# Network troubleshooting
python main.py --mqtt-host 192.168.1.100 --test-mqtt
```

The bridge will:
1. Connect to U-tec cloud to discover your locks
2. Set up Home Assistant MQTT discovery
3. Poll lock status every 5 minutes (configurable)
4. Publish state updates to Home Assistant
5. Listen for MQTT commands from Home Assistant
6. Execute lock/unlock operations via Bluetooth
7. Provide immediate status feedback

### Testing Your Setup

Before deploying the bridge, test your locks and configuration:

```bash
# Test device discovery and authentication
python main.py --test-discovery

# Test MQTT connectivity
python main.py --test-mqtt

# Safe testing with actual locks (no commands executed)
python main.py --dry-run --verbose

# Full lock functionality testing
python test.py
```

## Library Usage

### Basic Lock Control

```python
import asyncio
import utec

async def main():
    # Configure library (optional)
    utec.setup(
        log_level=utec.LogLevel.INFO,
        ble_max_retries=3
    )
    
    # Discover locks
    locks = await utec.discover_devices("email@example.com", "password")
    
    if not locks:
        print("No locks found")
        return
    
    # Control a lock
    lock = locks[0]
    await lock.async_unlock()
    await lock.async_lock()
    
    # Update status and read properties
    await lock.async_update_status()
    print(f"Lock: {lock.name}")
    print(f"Model: {lock.model}")
    print(f"Battery: {lock.battery}% ({utec.BATTERY_LEVEL[lock.battery]})")
    print(f"Lock Status: {lock.lock_status}")
    print(f"Lock Mode: {lock.lock_mode} ({utec.LOCK_MODE[lock.lock_mode]})")
    
    # Advanced control
    await lock.async_set_workmode(utec.DeviceLockWorkMode.PASSAGE)
    await lock.async_set_autolock(300)  # 5 minutes

asyncio.run(main())
```

### Event System

```python
import utec

# Set up event handler
@utec.event_handler(utec.EventType.DEVICE_UNLOCKED)
def on_device_unlocked(event):
    print(f"Device unlocked: {event.source.name}")

# Create event emitter for a device
emitter = utec.EventEmitter(lock)

# Emit events manually
emitter.emit(utec.EventType.DEVICE_UNLOCKED)
```

### MQTT Integration

```python
from utec.integrations.ha_mqtt import UtecMQTTClient

# Setup MQTT client
mqtt_client = UtecMQTTClient(
    broker_host="192.168.1.100",
    username="mqtt_user",
    password="mqtt_pass"
)

if mqtt_client.connect():
    # Auto-discovery in Home Assistant
    mqtt_client.setup_lock_discovery(lock)
    
    # Publish current state
    mqtt_client.update_lock_state(lock)
```

### Custom Device Registration

```python
from utec import DeviceFactory, DeviceCategory
from utec.ble.lock import UtecBleLock

# Register custom device model
class MyCustomLock(UtecBleLock):
    model = "MY-CUSTOM-LOCK"

DeviceFactory.register("MY-CUSTOM-LOCK", MyCustomLock, DeviceCategory.LOCK)
```

## Home Assistant Integration

Once the bridge is running, your U-tec locks will automatically appear in Home Assistant with:

- **Lock Entity**: Lock/unlock control from dashboard
- **Battery Sensor**: Battery percentage 
- **Lock Mode Sensor**: Current operation mode
- **Autolock Timer**: Configured autolock time
- **Mute Status**: Sound enabled/disabled
- **Signal Strength**: BLE connection quality (when available)

### Remote Control

Control locks directly from your Home Assistant dashboard:

- Click lock/unlock buttons in the dashboard
- Commands are sent via MQTT to the bridge running on your Raspberry Pi
- The bridge executes the command via Bluetooth and reports back the result
- Status updates are immediate after commands

### MQTT Topics

The bridge uses these MQTT topics:

**Status Publishing:**
- `utec/{device_id}/lock/state` - Lock state (LOCKED/UNLOCKED)
- `utec/{device_id}/battery/state` - Battery percentage
- `utec/{device_id}/lock_mode/state` - Lock mode
- `utec/{device_id}/autolock/state` - Autolock time
- `utec/{device_id}/mute/state` - Mute status

**Commands:**
- `utec/{device_id}/lock/command` - Send LOCK/UNLOCK commands
- `utec/bridge/command` - Bridge management (UPDATE_STATUS, STATUS)

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `UTEC_EMAIL` | Yes | - | Your U-tec account email |
| `UTEC_PASSWORD` | Yes | - | Your U-tec account password |
| `MQTT_HOST` | Yes | - | Home Assistant/MQTT broker IP |
| `MQTT_PORT` | No | 1883 | MQTT broker port |
| `MQTT_USERNAME` | No | - | MQTT username |
| `MQTT_PASSWORD` | No | - | MQTT password |
| `UPDATE_INTERVAL` | No | 300 | Status update interval (seconds) |

**Note**: CLI arguments override environment variables, allowing flexible configuration without editing files.

## Supported Devices

The library automatically detects your U-tec lock model and capabilities. Supported models include:

- **UL1-BT** - Ultraloq UL1 with Bluetooth
- **Latch-5-NFC** - Latch 5 with NFC capability  
- **Latch-5-F** - Latch 5 with fingerprint reader
- **Bolt-NFC** - Bolt lock with NFC
- **LEVER** - Lever-style smart lock
- **U-Bolt** / **U-Bolt-WiFi** / **U-Bolt-ZWave** - U-Bolt series
- **U-Bolt-PRO** - Professional U-Bolt variant *(enhanced support)*
- **SmartLockByBle** / **UL3-2ND** / **UL300** - UL3 series locks

Each model automatically enables appropriate features:
- Bluetooth connectivity and encryption (Static, MD5, ECC)
- Autolock functionality where supported
- Wake-up receiver support for extended range
- Battery monitoring and alerts
- Work modes (Normal, Passage, Lockout)
- Audio muting capabilities

### Device-Specific Notes

**U-Bolt-PRO**: Requires enhanced authentication protocol with embedded credentials. The library automatically detects PRO models and uses the appropriate command structure.

## Deployment

### Raspberry Pi Service Setup

For production deployment, run the bridge as a systemd service:

1. **Copy the script to the Pi:**
```bash
cp main.py /home/pi/utec-bridge/
cp -r utec/ /home/pi/utec-bridge/
cp requirements.txt /home/pi/utec-bridge/
```

2. **Create systemd service file:**
```bash
sudo nano /etc/systemd/system/utec-bridge.service
```

3. **Service configuration:**
```ini
[Unit]
Description=U-tec Home Assistant Bridge
After=network.target bluetooth.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/utec-bridge
ExecStart=/home/pi/utec-bridge/venv/bin/python main.py --config-file /etc/utec/bridge.env
Restart=always
RestartSec=10
EnvironmentFile=/etc/utec/bridge.env

[Install]
WantedBy=multi-user.target
```

4. **Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable utec-bridge
sudo systemctl start utec-bridge
```

### Configuration Examples

**Production service:**
```bash
# /etc/utec/bridge.env
UTEC_EMAIL=user@example.com
UTEC_PASSWORD=secure_password
MQTT_HOST=192.168.1.100
MQTT_USERNAME=utec_bridge
MQTT_PASSWORD=mqtt_password
UPDATE_INTERVAL=300
```

**Development testing:**
```bash
python main.py --config-file dev.env --dry-run --verbose --update-interval 60
```

**Network troubleshooting:**
```bash
python main.py --mqtt-host 192.168.1.50 --mqtt-port 1884 --test-mqtt --verbose
```

## Troubleshooting

**Connection Issues:**
- Ensure your locks are within Bluetooth range of your Raspberry Pi
- Check that your U-tec credentials are correct: `python main.py --test-discovery`
- Verify MQTT broker connectivity: `python main.py --test-mqtt`
- Use dry-run mode to test configuration: `python main.py --dry-run --verbose`

**Lock Not Responding:**
- Try restarting the bridge
- Check lock battery level
- Ensure lock is not in use by another device
- Run the test script to isolate the issue: `python test.py`

**Commands Not Working:**
- Check MQTT command topics in Home Assistant Developer Tools
- Look for "Lock {name} is busy" messages (normal during operations)
- Ensure device IDs match between discovery and commands
- Test in dry-run mode: `python main.py --dry-run --verbose`

**U-Bolt-PRO Specific Issues:**
- Verify device model is correctly detected as "U-Bolt-PRO"
- Check that bt264 capability is enabled
- Ensure UID and password are properly configured
- Test with the included test script for detailed diagnostics

**Home Assistant Discovery:**
- Verify MQTT integration is enabled in HA
- Check MQTT broker logs for connection issues
- Restart Home Assistant if devices don't appear
- Ensure device names don't have special characters

## Logging

The bridge script logs to:
- Console output (stdout)
- `utec_ha_bridge.log` file

For verbose debugging:
```bash
python main.py --verbose
```

The test script logs to `lock_test.log` for troubleshooting specific device issues.

## Architecture

- **API Client** (`utec/api/`): U-tec cloud authentication and device discovery with token persistence
- **BLE Communication** (`utec/ble/`): Direct Bluetooth communication with comprehensive device support
- **MQTT Integration** (`utec/integrations/`): Home Assistant discovery and state management
- **Device Models** (`utec/models/`): Lock capabilities and device definitions for all supported models
- **Event System** (`utec/events.py`): Pub/sub event handling for device state changes
- **Configuration** (`utec/config.py`): Centralized configuration with logging and BLE retry strategies
- **Factory Pattern** (`utec/factory.py`): Extensible device creation and registration system
- **Utilities** (`utec/utils/`): Constants, enums, data conversion, and logging helpers

## Configuration Options

The library supports extensive configuration through the `utec.setup()` function:

```python
utec.setup(
    log_level=utec.LogLevel.DEBUG,           # Logging verbosity
    ble_max_retries=5,                       # BLE connection retry limit
    ble_connection_timeout=30.0,             # BLE connection timeout
    ble_retry_strategy=utec.BLERetryStrategy.EXPONENTIAL,
    api_timeout=300,                         # API request timeout
    enable_extended_logging=True             # Extended debug logging
)
```

## Contributing

When adding support for new device models:

1. Add device capabilities to `utec/models/capabilities.py`
2. Update the `known_devices` dictionary
3. Test with the included test script
4. Consider device-specific protocol requirements (see U-Bolt-PRO implementation)

For issues or improvements, please ensure testing with multiple device models when possible.

## License

This project builds upon the excellent work done by [maeneak/utecio](https://github.com/maeneak/utecio). Please refer to their repository for the original license terms.