"""U-tec Library for interacting with U-tec smart locks via Bluetooth.

This library provides classes and utilities for interacting with U-tec smart locks,
including authentication, device discovery, and lock control.
"""

__version__ = "1.0.0"
__author__ = "Craig Freyman"
__license__ = "MIT"

# Re-export core classes for easy import
from .api.client import UtecClient
from .ble.lock import UtecBleLock
from .factory import DeviceFactory, DeviceCategory
from .config import UtecConfig, LogLevel, BLERetryStrategy
from .events import EventEmitter, EventDispatcher, EventType, event_handler

# Initialize the configuration
from .config import config

# Set up main classes as public exports
__all__ = [
    "UtecClient",
    "UtecBleLock",
    "DeviceFactory",
    "DeviceCategory",
    "UtecConfig",
    "LogLevel",
    "BLERetryStrategy",
    "EventEmitter",
    "EventDispatcher",
    "EventType",
    "event_handler",
    "config",
    "setup",
    "get_logger",
    "discover_devices"
]


def setup(**kwargs):
    """Set up the U-tec library with the given configuration.
    
    This is a convenience function that configures the library
    and returns the configuration instance.
    
    Args:
        **kwargs: Configuration options to set.
        
    Returns:
        The configuration instance.
    """
    return config.configure(**kwargs)


def get_logger(name=None):
    """Get a logger for a specific component.
    
    Args:
        name: Name of the component.
        
    Returns:
        Logger instance.
    """
    return config.get_logger(name)


async def discover_devices(email, password, include_non_ble=False):
    """Discover U-tec devices.
    
    This is a convenience function that creates a client,
    authenticates, and returns the discovered devices.
    
    Args:
        email: U-tec account email.
        password: U-tec account password.
        include_non_ble: Whether to include non-BLE devices.
        
    Returns:
        List of discovered devices.
    """
    # Use async context manager to ensure proper session cleanup
    async with UtecClient(email, password) as client:
        if not await client.connect():
            return []
        
        if include_non_ble:
            await client.sync_devices()
            return client.devices
        else:
            return await client.get_ble_devices()


# Register the built-in device classes
DeviceFactory.register("UL1-BT", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("Latch-5-NFC", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("Latch-5-F", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("Bolt-NFC", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("LEVER", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("U-Bolt", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("U-Bolt-WiFi", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("U-Bolt-ZWave", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("SmartLockByBle", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("UL3-2ND", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("UL300", UtecBleLock, DeviceCategory.LOCK)
DeviceFactory.register("U-Bolt-PRO", UtecBleLock, DeviceCategory.LOCK)

# Register a default lock class
DeviceFactory.register_default(UtecBleLock, DeviceCategory.LOCK)


# Example library usage:
#
# import asyncio
# import utec
# 
# # Set up the library with custom configuration
# utec.setup(
#     log_level=utec.LogLevel.DEBUG,
#     ble_max_retries=5
# )
# 
# # Get a logger
# logger = utec.get_logger("example")
# 
# # Define an event handler
# @utec.event_handler(utec.EventType.DEVICE_UNLOCKED)
# def on_device_unlocked(event):
#     print(f"Device unlocked: {event.source.name}")
# 
# async def main():
#     # Discover devices
#     devices = await utec.discover_devices("user@example.com", "password")
#     
#     if not devices:
#         print("No devices found")
#         return
#     
#     # Use the first device
#     lock = devices[0]
#     
#     # Create an event emitter for the device
#     emitter = utec.EventEmitter(lock)
#     
#     # Unlock the lock
#     await lock.async_unlock()
#     
#     # Emit the event manually (the library would normally do this)
#     emitter.emit(utec.EventType.DEVICE_UNLOCKED)
# 
# if __name__ == "__main__":
#     asyncio.run(main())