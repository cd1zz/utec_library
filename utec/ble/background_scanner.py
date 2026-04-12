"""
Background BLE scanner for continuous device monitoring.

This module provides a background scanner that continuously monitors BLE advertisements
to maintain a real-time registry of available devices, eliminating the need for
blocking discovery scans.
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple, Callable, Any
from threading import Lock

from bleak import BleakScanner, BLEDevice
from bleak.assigned_numbers import AdvertisementDataType
from bleak.backends.scanner import AdvertisementData

try:
    from bleak.args.bluez import OrPattern, BlueZScannerArgs
except ImportError:
    from bleak.backends.bluezdbus.advertisement_monitor import OrPattern
    from bleak.backends.bluezdbus.scanner import BlueZScannerArgs

from ..config import config
from ..utils.logging import get_logger
from ..utils.enums import DeviceServiceUUID

logger = get_logger(__name__)

PASSIVE_SCANNER_ARGS = BlueZScannerArgs(
    or_patterns=[
        OrPattern(0, AdvertisementDataType.FLAGS, b"\x06"),
        OrPattern(0, AdvertisementDataType.FLAGS, b"\x1a"),
    ]
)


class DeviceInfo:
    """Information about a discovered BLE device."""

    def __init__(self, device: BLEDevice, advertisement_data: AdvertisementData):
        self.device = device
        self.advertisement_data = advertisement_data
        self.rssi = advertisement_data.rssi
        self.last_seen = time.time()
        self.available = True
        self.lock_serial: Optional[str] = None
        self.update_count = 0

    def update(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """Update device information with new advertisement."""
        self.device = device
        self.advertisement_data = advertisement_data
        self.rssi = advertisement_data.rssi
        self.last_seen = time.time()
        self.available = True
        self.update_count += 1

    def is_available(self, timeout: float) -> bool:
        """Check if device is still available based on timeout."""
        return (time.time() - self.last_seen) < timeout


class BleBackgroundScanner:
    """
    Background BLE scanner that maintains a registry of available devices.

    Uses Bleak's detection callback mode to continuously monitor BLE advertisements
    without blocking operations.
    """

    def __init__(self):
        self._scanner: Optional[BleakScanner] = None
        self._device_registry: Dict[str, DeviceInfo] = {}
        self._registry_lock = Lock()
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None
        self._start_time = None
        self._scan_count = 0
        self._detection_callbacks: list[Callable] = []

        # Metrics
        self._metrics = {
            "devices_discovered": 0,
            "advertisements_received": 0,
            "registry_hits": 0,
            "registry_misses": 0,
            "cleanups_performed": 0,
        }

    def add_detection_callback(
        self, callback: Callable[[BLEDevice, AdvertisementData], None]
    ):
        """Add a callback to be notified of device detections."""
        self._detection_callbacks.append(callback)

    def remove_detection_callback(
        self, callback: Callable[[BLEDevice, AdvertisementData], None]
    ):
        """Remove a detection callback."""
        if callback in self._detection_callbacks:
            self._detection_callbacks.remove(callback)

    async def start(self):
        """Start the background scanner."""
        if self._running:
            logger.warning("Background scanner already running")
            return

        logger.info("Starting background BLE scanner")
        self._running = True
        self._start_time = time.time()

        # Create scanner with detection callback
        # No service_uuids filter — some U-tec locks only advertise manufacturer
        # data (0x0969) or device name, not the lock service UUID.
        # Device validation happens in _is_utec_lock() instead.
        self._scanner = BleakScanner(
            detection_callback=self._detection_callback,
            scanning_mode="passive" if config.ble_scan_passive else "active",
        )

        try:
            await self._scanner.start()
            logger.info("Background scanner started successfully")

            # Start cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        except Exception as e:
            logger.error(f"Failed to start background scanner: {e}")
            self._running = False
            raise

    async def stop(self):
        """Stop the background scanner."""
        if not self._running:
            return

        logger.info("Stopping background BLE scanner")
        self._running = False

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Stop scanner
        if self._scanner:
            try:
                await self._scanner.stop()
            except Exception as e:
                logger.error(f"Error stopping scanner: {e}")
            self._scanner = None

        logger.info(f"Background scanner stopped. Metrics: {self.get_metrics()}")

    def _detection_callback(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ):
        """Handle device detection from scanner."""
        self._scan_count += 1
        self._metrics["advertisements_received"] += 1

        with self._registry_lock:
            if device.address in self._device_registry:
                # Update existing device
                device_info = self._device_registry[device.address]
                device_info.update(device, advertisement_data)

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"Updated device {device.address} ({device.name}), "
                        f"RSSI: {advertisement_data.rssi}, "
                        f"Updates: {device_info.update_count}"
                    )
            else:
                # New device discovered
                device_info = DeviceInfo(device, advertisement_data)
                self._device_registry[device.address] = device_info
                self._metrics["devices_discovered"] += 1

                # Check if this is a U-tec lock
                if self._is_utec_lock(device, advertisement_data):
                    device_info.lock_serial = self._extract_serial(
                        device, advertisement_data
                    )
                    logger.info(
                        f"Discovered U-tec lock: {device.address} ({device.name}), "
                        f"Serial: {device_info.lock_serial}, RSSI: {advertisement_data.rssi}"
                    )
                else:
                    logger.debug(f"Discovered device: {device.address} ({device.name})")

        # Notify callbacks
        for callback in self._detection_callbacks:
            try:
                callback(device, advertisement_data)
            except Exception as e:
                logger.error(f"Error in detection callback: {e}")

    def _is_utec_lock(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ) -> bool:
        """Check if device is a U-tec lock based on name or service UUIDs."""
        # Check device name
        if device.name and any(
            name in device.name.upper() for name in ["UTEC", "U-BOLT", "ULTRALOQ"]
        ):
            return True

        # Check manufacturer data (U-tec uses specific patterns)
        if advertisement_data.manufacturer_data:
            # U-tec locks often use manufacturer ID 0x0969
            if 0x0969 in advertisement_data.manufacturer_data:
                return True

        return False

    def _extract_serial(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ) -> Optional[str]:
        """Extract serial number from device name or advertisement data."""
        if device.name:
            # U-tec locks often include serial in name
            # Format: "U-Bolt-XXXXXXXXXXXX" or similar
            parts = device.name.split("-")
            if len(parts) >= 3:
                return parts[-1]
        return None

    async def _cleanup_loop(self):
        """Periodically clean up stale devices from registry."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Run cleanup every 30 seconds
                self._cleanup_stale_devices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    def _cleanup_stale_devices(self):
        """Remove devices that haven't been seen recently."""
        timeout = config.ble_device_timeout
        now = time.time()
        stale_devices = []

        with self._registry_lock:
            for address, device_info in self._device_registry.items():
                if not device_info.is_available(timeout):
                    stale_devices.append(address)
                    device_info.available = False

            # Remove stale devices
            for address in stale_devices:
                device_info = self._device_registry.pop(address)
                logger.info(
                    f"Removed stale device: {address} ({device_info.device.name}), "
                    f"Last seen: {int(now - device_info.last_seen)}s ago"
                )

        if stale_devices:
            self._metrics["cleanups_performed"] += 1
            logger.debug(f"Cleaned up {len(stale_devices)} stale devices")

    def get_device(self, address: str) -> Optional[Tuple[BLEDevice, AdvertisementData]]:
        """
        Get device and advertisement data by address.

        Returns None if device not found or not recently seen.
        """
        with self._registry_lock:
            device_info = self._device_registry.get(address)

            if device_info and device_info.is_available(config.ble_device_timeout):
                self._metrics["registry_hits"] += 1
                return device_info.device, device_info.advertisement_data
            else:
                self._metrics["registry_misses"] += 1
                return None

    def get_device_by_serial(
        self, serial: str
    ) -> Optional[Tuple[BLEDevice, AdvertisementData]]:
        """Get U-tec lock device by serial number."""
        with self._registry_lock:
            for device_info in self._device_registry.values():
                if device_info.lock_serial == serial and device_info.is_available(
                    config.ble_device_timeout
                ):
                    self._metrics["registry_hits"] += 1
                    return device_info.device, device_info.advertisement_data

        self._metrics["registry_misses"] += 1
        return None

    def get_all_devices(self) -> Dict[str, DeviceInfo]:
        """Get all devices in registry (including stale ones)."""
        with self._registry_lock:
            return self._device_registry.copy()

    def get_available_devices(self) -> Dict[str, DeviceInfo]:
        """Get only currently available devices."""
        timeout = config.ble_device_timeout
        with self._registry_lock:
            return {
                addr: info
                for addr, info in self._device_registry.items()
                if info.is_available(timeout)
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Get scanner metrics."""
        uptime = int(time.time() - self._start_time) if self._start_time else 0

        with self._registry_lock:
            active_devices = sum(
                1
                for info in self._device_registry.values()
                if info.is_available(config.ble_device_timeout)
            )

        return {
            **self._metrics,
            "uptime_seconds": uptime,
            "scan_count": self._scan_count,
            "active_devices": active_devices,
            "total_devices": len(self._device_registry),
            "running": self._running,
        }


# Global scanner instance
_background_scanner: Optional[BleBackgroundScanner] = None


def get_background_scanner() -> Optional[BleBackgroundScanner]:
    """Get the global background scanner instance."""
    return _background_scanner


def set_background_scanner(scanner: Optional[BleBackgroundScanner]):
    """Set the global background scanner instance."""
    global _background_scanner
    _background_scanner = scanner
