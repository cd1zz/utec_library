"""Abstract base classes for the U-tec library.

This module defines the interfaces that are implemented by the concrete classes
in the library. Custom implementations can extend these base classes to ensure
compatibility with the rest of the library.
"""
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, List, Dict, Optional, Protocol, TypeVar
import datetime
import asyncio
from bleak.backends.device import BLEDevice


# Type definitions for improved type checking
DeviceId = str
UserId = str
Password = str
T = TypeVar('T')


class BLEDeviceCallback(Protocol):
    """Protocol for BLE device callback functions."""
    
    async def __call__(self, address: str) -> Optional[BLEDevice]: ...


class ErrorCallback(Protocol):
    """Protocol for error callback functions."""
    
    def __call__(self, device_id: str, exception: Exception) -> None: ...


class BaseUtecClient(ABC):
    """Base class for U-tec API clients."""
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the U-tec service and authenticate."""
        pass
    
    @abstractmethod
    async def sync_devices(self) -> bool:
        """Sync all devices, addresses, and rooms."""
        pass
    
    @abstractmethod
    async def get_ble_devices(self, sync: bool = True) -> List[Any]:
        """Get all BLE-capable devices."""
        pass
    
    @abstractmethod
    async def get_json(self) -> List[Dict[str, Any]]:
        """Get raw JSON data for all devices."""
        pass


class BaseDeviceCapabilities(ABC):
    """Base class for device capabilities."""
    
    model: str = ""
    
    @abstractmethod
    def __init__(self) -> None:
        """Initialize device capabilities."""
        pass
    
    @classmethod
    @abstractmethod
    def get_supported_features(cls) -> List[str]:
        """Get a list of supported features for this device model."""
        pass


class BaseBleDevice(ABC):
    """Base class for BLE devices."""
    
    @abstractmethod
    def __init__(
        self,
        uid: UserId,
        password: Password,
        mac_uuid: DeviceId,
        device_name: str,
        wurx_uuid: Optional[DeviceId] = None,
        device_model: str = "",
        async_bledevice_callback: Optional[BLEDeviceCallback] = None,
        error_callback: Optional[ErrorCallback] = None,
    ) -> None:
        """Initialize a BLE device."""
        pass
    
    @classmethod
    @abstractmethod
    def from_json(cls, json_config: Dict[str, Any]) -> "BaseBleDevice":
        """Create a BLE device from JSON data."""
        pass
    
    @abstractmethod
    async def async_update_status(self) -> None:
        """Update the device status."""
        pass
    
    @abstractmethod
    async def send_requests(self) -> bool:
        """Send queued requests to the device."""
        pass
    
    @abstractmethod
    async def async_wakeup_device(self) -> None:
        """Attempt to wake up the device using the wake-up receiver."""
        pass


class BaseBleRequest(ABC):
    """Base class for BLE requests."""
    
    @abstractmethod
    def __init__(
        self,
        command: Any,
        device: Optional[BaseBleDevice] = None,
        data: bytes = bytes(),
        auth_required: bool = False,
    ) -> None:
        """Initialize a BLE request."""
        pass
    
    @property
    @abstractmethod
    def package(self) -> bytearray:
        """Get the request package."""
        pass
    
    @abstractmethod
    def encrypted_package(self, aes_key: bytes) -> bytearray:
        """Get the encrypted request package."""
        pass
    
    @abstractmethod
    async def _get_response(self, client: Any) -> None:
        """Get the response from the device."""
        pass


class BaseLock(BaseBleDevice):
    """Base class for lock devices."""
    
    @abstractmethod
    async def async_unlock(self, update: bool = True) -> None:
        """Unlock the lock."""
        pass
    
    @abstractmethod
    async def async_lock(self, update: bool = True) -> None:
        """Lock the lock."""
        pass
    
    @abstractmethod
    async def async_reboot(self) -> bool:
        """Reboot the lock."""
        pass
    
    @abstractmethod
    async def async_set_workmode(self, mode: Any) -> None:
        """Set the lock work mode."""
        pass
    
    @abstractmethod
    async def async_set_autolock(self, seconds: int) -> None:
        """Set the autolock time in seconds."""
        pass