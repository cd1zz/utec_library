"""Lock implementation for the U-tec library."""
import logging
from typing import Any, Dict, Optional

from ..abstract import BaseLock
from ..config import config
from ..utils.enums import BLECommandCode, DeviceLockWorkMode
from ..utils.data import to_byte_array, decode_password
from .device import UtecBleDevice, UtecBleRequest

# Get a logger for this module
logger = config.get_logger("ble.lock")


class UtecBleLock(UtecBleDevice, BaseLock):
    """U-tec BLE lock implementation."""

    def __init__(
        self,
        uid: str,
        password: str,
        mac_uuid: str,
        device_name: str,
        wurx_uuid: Optional[str] = None,
        device_model: str = "",
    ):
        """Initialize a U-tec BLE lock.
        
        Args:
            uid: User ID.
            password: User password.
            mac_uuid: Device MAC address.
            device_name: Device name.
            wurx_uuid: Wake-up receiver UUID.
            device_model: Device model.
        """
        super().__init__(
            uid=uid,
            password=password,
            mac_uuid=mac_uuid,
            wurx_uuid=wurx_uuid,
            device_name=device_name,
            device_model=device_model,
        )

    @classmethod
    def from_json(cls, json_config: Dict[str, Any]) -> "UtecBleLock":
        """Create a lock from JSON data.
        
        Args:
            json_config: JSON configuration data.
            
        Returns:
            A new lock instance.
        """
        try:
            # Extract required values with safe defaults
            name = json_config.get("name", "Unknown")
            
            # Safely access nested values
            user_data = json_config.get("user", {})
            uid = str(user_data.get("uid", "0"))
            password = decode_password(user_data.get("password", 0))
            
            mac_uuid = json_config.get("uuid", "")
            device_model = json_config.get("model", "")
            
            # Create the device
            new_device = cls(
                device_name=name,
                uid=uid,
                password=password,
                mac_uuid=mac_uuid,
                device_model=device_model,
            )
            
            # Extract optional values
            params = json_config.get("params", {})
            if params.get("extend_ble"):
                new_device.wurx_uuid = params["extend_ble"]
                
            new_device.sn = params.get("serialnumber", "")
            new_device.model = device_model
            new_device.config = json_config
            
            logger.debug(f"Successfully created lock: {name}")
            return new_device
            
        except Exception as e:
            logger.error(f"Error creating lock from JSON: {str(e)}")
            raise

    async def async_unlock(self, update: bool = True) -> None:
        """Unlock the lock.
        
        Args:
            update: Whether to update the lock status after unlocking.
        """
        logger.info(f"Unlocking {self.name}...")
        
        if update:
            self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
        self.add_request(UtecBleRequest(BLECommandCode.UNLOCK), priority=True)

        await self.send_requests()

    async def async_lock(self, update: bool = True) -> None:
        """Lock the lock.
        
        Args:
            update: Whether to update the lock status after locking.
        """
        logger.info(f"Locking {self.name}...")
        
        if update:
            self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
        self.add_request(UtecBleRequest(BLECommandCode.BOLT_LOCK), priority=True)

        await self.send_requests()

    async def async_reboot(self) -> bool:
        """Reboot the lock.
        
        Returns:
            True if the reboot was successful.
        """
        logger.info(f"Rebooting {self.name}...")
        
        self.add_request(UtecBleRequest(BLECommandCode.REBOOT))
        return await self.send_requests()

    async def async_set_workmode(self, mode: DeviceLockWorkMode) -> None:
        """Set the lock work mode.
        
        Args:
            mode: Lock work mode to set.
        """
        logger.info(f"Setting work mode for {self.name} to {mode.name}...")
        
        #self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
        if self.capabilities.bt264:
            self.add_request(
                UtecBleRequest(BLECommandCode.SET_LOCK_STATUS, data=bytes([mode.value]))
            )
        else:
            self.add_request(
                UtecBleRequest(BLECommandCode.SET_WORK_MODE, data=bytes([mode.value]))
            )

        await self.send_requests()

    async def async_set_autolock(self, seconds: int) -> None:
        """Set the autolock time in seconds.
        
        Args:
            seconds: Autolock time in seconds.
        """
        logger.info(f"Setting autolock time for {self.name} to {seconds} seconds...")
        
        if self.capabilities.autolock:
            #self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
            self.add_request(
                UtecBleRequest(
                    BLECommandCode.SET_AUTOLOCK,
                    data=to_byte_array(seconds, 2) + bytes([0]),
                )
            )
        await self.send_requests()

    async def async_update_status(self) -> None:
        """Update the lock status."""
        logger.info(f"Updating status for {self.name}...")
        
        #self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
        self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
        if not self.capabilities.bt264:
            self.add_request(UtecBleRequest(BLECommandCode.GET_LOCK_STATUS))
            self.add_request(UtecBleRequest(BLECommandCode.GET_BATTERY))
            self.add_request(UtecBleRequest(BLECommandCode.GET_MUTE))

        if self.capabilities.autolock:
            self.add_request(UtecBleRequest(BLECommandCode.GET_AUTOLOCK))

        await self.send_requests()
        logger.info(f"Status update completed for {self.name}")