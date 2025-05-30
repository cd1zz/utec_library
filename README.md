# U-tec Smart Lock Python Library

> **Note**: 99% of the hard work was done by [maeneak/utecio](https://github.com/maeneak/utecio). This project builds upon their excellent foundation.

A Python library for interacting with U-tec smart locks via Bluetooth Low Energy (BLE) with Home Assistant MQTT integration support.

## Why This Project?

This project was specifically built to run on a **Raspberry Pi positioned within BLE range of U-tec smart locks** to provide reliable, up-to-date status reporting to Home Assistant. 

**The Problem**: U-tec's WiFi bridges are notoriously unreliable and often fail to report accurate lock status, leaving Home Assistant with stale or incorrect data.

**The Solution**: This library bypasses the unreliable WiFi bridges entirely by:
- Running directly on a Raspberry Pi with Bluetooth capability
- Polling lock status via BLE at configurable intervals (default: 5 minutes)
- Publishing real-time status updates to Home Assistant via MQTT
- Providing immediate status changes when locks are operated

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

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

1. Copy `.env_sample` to `.env`:
```bash
cp .env_sample .env
```

2. Edit `.env` with your credentials:
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
python main.py
```

The bridge will:
1. Connect to U-tec cloud to discover your locks
2. Set up Home Assistant MQTT discovery
3. Poll lock status every 5 minutes (configurable)
4. Publish state updates to Home Assistant

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

- **Lock Entity**: Lock/unlock control
- **Battery Sensor**: Battery percentage 
- **Lock Mode Sensor**: Current operation mode
- **Autolock Timer**: Configured autolock time
- **Mute Status**: Sound enabled/disabled
- **Signal Strength**: BLE connection quality (when available)

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

## Supported Devices

The library automatically detects your U-tec lock model and capabilities. Supported models include:

- **UL1-BT** - Ultraloq UL1 with Bluetooth
- **Latch-5-NFC** - Latch 5 with NFC capability  
- **Latch-5-F** - Latch 5 with fingerprint reader
- **Bolt-NFC** - Bolt lock with NFC
- **LEVER** - Lever-style smart lock
- **U-Bolt** / **U-Bolt-WiFi** / **U-Bolt-ZWave** - U-Bolt series
- **U-Bolt-PRO** - Professional U-Bolt variant
- **SmartLockByBle** / **UL3-2ND** / **UL300** - UL3 series locks

Each model automatically enables appropriate features:
- Bluetooth connectivity and encryption (Static, MD5, ECC)
- Autolock functionality where supported
- Wake-up receiver support for extended range
- Battery monitoring and alerts
- Work modes (Normal, Passage, Lockout)
- Audio muting capabilities

## Troubleshooting

**Connection Issues:**
- Ensure your locks are within Bluetooth range
- Check that your U-tec credentials are correct
- Verify MQTT broker connectivity

**Lock Not Responding:**
- Try restarting the bridge
- Check lock battery level
- Ensure lock is not in use by another device

**Home Assistant Discovery:**
- Verify MQTT integration is enabled in HA
- Check MQTT broker logs for connection issues
- Restart Home Assistant if devices don't appear

## Logging

The application logs to both console and `utec_ha_bridge.log`. For verbose debugging, check the log file for detailed BLE communication traces.

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