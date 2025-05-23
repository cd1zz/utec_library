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

This approach ensures your Home Assistant always has current, accurate lock status without depending on U-tec's problematic cloud infrastructure.

## Features

- **BLE Communication**: Direct Bluetooth communication with U-tec smart locks
- **Raspberry Pi Optimized**: Designed to run continuously on Raspberry Pi hardware
- **Reliable Status Updates**: Periodic polling ensures Home Assistant always has current lock status
- **Home Assistant Integration**: Native MQTT discovery and state publishing
- **No WiFi Bridge Dependency**: Bypasses unreliable U-tec WiFi bridges completely
- **Device Support**: Wide range of U-tec lock models
- **Event System**: Comprehensive event handling for device state changes
- **Async/Await**: Modern Python async support for non-blocking operations
- **Configuration Management**: Centralized configuration with logging support
- **Retry Logic**: Robust connection handling with configurable retry strategies

## Supported Device Models

- UL1-BT (Ultraloq UL-1)
- Latch-5-NFC
- Latch-5-F
- Bolt-NFC
- LEVER
- U-Bolt, U-Bolt-WiFi, U-Bolt-ZWave
- SmartLockByBle (UL3)
- UL3-2ND
- UL300
- U-Bolt-PRO

## Raspberry Pi Setup

### Hardware Requirements

- Raspberry Pi 3B+ or newer (with built-in Bluetooth)
- OR Raspberry Pi with USB Bluetooth adapter
- MicroSD card (16GB+ recommended)
- Power supply
- Position within 30-50 feet of your U-tec locks

### Installation on Raspberry Pi

1. **Flash Raspberry Pi OS** to your SD card
2. **Enable SSH and configure WiFi** (if using headless setup)
3. **Update the system**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

4. **Install Python dependencies**:
   ```bash
   sudo apt install python3-pip python3-venv bluetooth bluez -y
   ```

5. **Clone and set up the project**:
   ```bash
   git clone <your-repo-url>
   cd utec-homeassistant
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

6. **Configure environment**:
   ```bash
   cp .env.example .env
   nano .env  # Edit with your credentials
   ```

7. **Test the setup**:
   ```bash
   python main.py
   ```

### Running as a Service

Create a systemd service for automatic startup:

```bash
sudo nano /etc/systemd/system/utec-ha.service
```

```ini
[Unit]
Description=U-tec Home Assistant Integration
After=network.target bluetooth.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/utec-homeassistant
Environment=PATH=/home/pi/utec-homeassistant/venv/bin
ExecStart=/home/pi/utec-homeassistant/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable utec-ha.service
sudo systemctl start utec-ha.service
```

Check status:
```bash
sudo systemctl status utec-ha.service
sudo journalctl -u utec-ha.service -f  # Follow logs
```

### Environment Setup

Create a `.env` file in your project root:

```env
# Required
UTEC_EMAIL=your-utec-account@email.com
UTEC_PASSWORD=your-utec-password
MQTT_HOST=your-mqtt-broker-ip

# Optional
MQTT_PORT=1883
MQTT_USERNAME=your-mqtt-username
MQTT_PASSWORD=your-mqtt-password
MQTT_CLIENT_ID=utec_ha_client
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_DEVICE_PREFIX=utec
UPDATE_INTERVAL=300
```

## Quick Start

### Basic Usage

```python
import asyncio
import utec

async def main():
    # Configure the library
    utec.setup(
        log_level=utec.LogLevel.INFO,
        ble_max_retries=5
    )
    
    # Discover devices
    locks = await utec.discover_devices(
        email="your-email@example.com",
        password="your-password"
    )
    
    if locks:
        lock = locks[0]
        
        # Update status
        await lock.async_update_status()
        print(f"Battery: {lock.battery}%")
        print(f"Lock Status: {lock.lock_status}")
        
        # Control the lock
        await lock.async_unlock()
        await asyncio.sleep(5)
        await lock.async_lock()

if __name__ == "__main__":
    asyncio.run(main())
```

### Home Assistant Integration

```python
import asyncio
from utec.integrations.ha_mqtt import HomeAssistantMQTTIntegration, HAMQTTConfig

async def main():
    # Configure MQTT
    mqtt_config = HAMQTTConfig(
        broker_host="192.168.1.100",
        username="mqtt_user",
        password="mqtt_pass"
    )
    
    # Create integration
    ha_mqtt = HomeAssistantMQTTIntegration(mqtt_config)
    
    # Connect and set up devices
    if await ha_mqtt.connect():
        locks = await utec.discover_devices("email", "password")
        
        for lock in locks:
            # Set up Home Assistant discovery
            ha_mqtt.setup_lock_discovery(lock)
            
            # Publish initial state
            await lock.async_update_status()
            ha_mqtt.publish_lock_state(lock)

if __name__ == "__main__":
    asyncio.run(main())
```

### Running the Main Integration Script

The main script is designed to run continuously on your Raspberry Pi:

```bash
python main.py
```

This will:
1. **Discover all U-tec devices** associated with your account
2. **Set up Home Assistant MQTT discovery** (auto-creates entities)
3. **Continuously monitor device states** every 5 minutes (configurable)
4. **Publish real-time updates** to Home Assistant via MQTT
5. **Handle lock/unlock commands** from Home Assistant
6. **Provide detailed logging** of all operations

**Key Benefits**:
- ✅ Always up-to-date lock status in Home Assistant
- ✅ No dependency on unreliable U-tec WiFi bridges
- ✅ Direct BLE communication for maximum reliability
- ✅ Configurable polling intervals to balance battery life and responsiveness
- ✅ Automatic reconnection and error handling

## Configuration Options

### Library Configuration

```python
utec.setup(
    log_level=utec.LogLevel.DEBUG,           # Logging level
    ble_max_retries=5,                       # BLE connection retries
    ble_connection_timeout=30.0,             # Connection timeout (seconds)
    ble_retry_strategy=utec.BLERetryStrategy.EXPONENTIAL,
    api_timeout=300,                         # API timeout (seconds)
    enable_extended_logging=True             # Extended debug logging
)
```

### MQTT Configuration

```python
mqtt_config = HAMQTTConfig(
    broker_host="localhost",
    broker_port=1883,
    username=None,                           # Optional
    password=None,                           # Optional
    discovery_prefix="homeassistant",        # HA discovery prefix
    device_prefix="utec",                    # Device topic prefix
    qos=1,                                   # MQTT QoS level
    retain=True,                             # Retain messages
    keepalive=60                             # Connection keepalive
)
```

## Event System

Subscribe to device events:

```python
from utec import EventType, event_handler

@event_handler(EventType.DEVICE_UNLOCKED)
def on_unlock(event):
    print(f"Device {event.source.name} was unlocked!")

@event_handler(EventType.DEVICE_BATTERY_UPDATED)
def on_battery_change(event):
    battery_level = event.data.get('level')
    print(f"Battery level: {battery_level}%")
```

## Device Operations

### Lock Control

```python
# Basic operations
await lock.async_unlock()
await lock.async_lock()
await lock.async_reboot()

# Status update
await lock.async_update_status()

# Configuration
await lock.async_set_autolock(300)  # 5 minutes
await lock.async_set_workmode(utec.DeviceLockWorkMode.PASSAGE)
```

### Device Properties

```python
print(f"Name: {lock.name}")
print(f"Model: {lock.model}")
print(f"MAC: {lock.mac_uuid}")
print(f"Battery: {lock.battery}")
print(f"Lock Status: {lock.lock_status}")
print(f"Lock Mode: {lock.lock_mode}")
print(f"Autolock Time: {lock.autolock_time}s")
print(f"Mute: {lock.mute}")
```

## Home Assistant Entities

The MQTT integration automatically creates these entities:

- **Lock**: `lock.utec_device_name_lock`
- **Battery**: `sensor.utec_device_name_battery`
- **Lock Mode**: `sensor.utec_device_name_lock_mode`
- **Autolock Time**: `sensor.utec_device_name_autolock_time`
- **Mute Status**: `binary_sensor.utec_device_name_mute`
- **Signal Strength**: `sensor.utec_device_name_signal` (if available)

## Troubleshooting

### Common Issues

1. **Device Not Found**
   - Ensure Raspberry Pi is within BLE range (30-50 feet) of locks
   - Check if Bluetooth is enabled: `sudo systemctl status bluetooth`
   - Verify device is powered and has sufficient battery
   - Check if wake-up receiver (wurx_uuid) is configured

2. **Authentication Errors**
   - Verify U-tec account credentials in `.env` file
   - Check if device is associated with your account
   - Ensure account has admin access to the locks

3. **Connection Timeouts**
   - Increase `ble_connection_timeout` and `ble_max_retries` in configuration
   - Check for Bluetooth interference from other devices
   - Ensure lock battery is sufficient (low battery affects BLE range)
   - Consider moving Raspberry Pi closer to locks

4. **MQTT Issues**
   - Verify MQTT broker connectivity: `ping your-mqtt-broker-ip`
   - Check username/password if authentication is required
   - Ensure Home Assistant MQTT integration is enabled
   - Verify firewall settings on MQTT broker

5. **Raspberry Pi Specific Issues**
   - Check Bluetooth hardware: `hciconfig`
   - Restart Bluetooth service: `sudo systemctl restart bluetooth`
   - Check system logs: `sudo journalctl -u utec-ha.service`
   - Monitor system resources: `htop` (ensure sufficient RAM/CPU)

### Debugging

Enable debug logging:

```python
utec.setup(
    log_level=utec.LogLevel.DEBUG,
    enable_extended_logging=True
)
```

## Architecture

```
utec/
├── abstract.py          # Base classes and interfaces
├── config.py           # Configuration management
├── events.py           # Event system
├── factory.py          # Device factory pattern
├── exceptions.py       # Custom exceptions
├── api/
│   └── client.py       # U-tec API client
├── ble/
│   ├── device.py       # BLE device base class
│   └── lock.py         # Lock-specific implementation
├── integrations/
│   └── ha_mqtt.py      # Home Assistant MQTT
├── models/
│   └── capabilities.py # Device capabilities
└── utils/
    ├── constants.py    # Constants
    ├── enums.py        # Enumerations
    ├── data.py         # Data utilities
    └── logging.py      # Logging utilities
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Follow KISS, YAGNI, and SOLID principles
4. Add tests for new functionality
5. Submit a pull request

## Credits

This project is built upon the excellent work done by [maeneak/utecio](https://github.com/maeneak/utecio). The core BLE communication, device protocols, and encryption handling are based on their implementation.

## License

MIT License - see the original [utecio project](https://github.com/maeneak/utecio) for licensing details.

## Disclaimer

This is an unofficial library. U-tec is a trademark of their respective owners. Use at your own risk.