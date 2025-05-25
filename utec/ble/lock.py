"""Lock implementation for the U-tec library with enhanced timeout handling."""
import logging
import asyncio
from typing import Any, Dict, Optional

from ..abstract import BaseLock
from ..config import config
from ..utils.enums import BLECommandCode, DeviceLockWorkMode
from ..utils.data import to_byte_array, decode_password
from .device import UtecBleDevice, UtecBleRequest

# Get a logger for this module
logger = config.get_logger("ble.lock")


class UtecBleLock(UtecBleDevice, BaseLock):
    """U-tec BLE lock implementation with timeout protection."""

    def __init__(
        self,
        uid: str,
        password: str,
        mac_uuid: str,
        device_name: str,
        wurx_uuid: Optional[str] = None,
        device_model: str = "",
        operation_timeout: float = 45.0,
    ):
        """Initialize a U-tec BLE lock.
        
        Args:
            uid: User ID.
            password: User password.
            mac_uuid: Device MAC address.
            device_name: Device name.
            wurx_uuid: Wake-up receiver UUID.
            device_model: Device model.
            operation_timeout: Timeout for lock operations in seconds.
        """
        super().__init__(
            uid=uid,
            password=password,
            mac_uuid=mac_uuid,
            wurx_uuid=wurx_uuid,
            device_name=device_name,
            device_model=device_model,
            connection_timeout=operation_timeout,
            operation_timeout=15.0,  # Individual request timeout
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

    async def async_unlock(self, update: bool = True, timeout: float = 30.0) -> bool:
        """Unlock the lock with timeout protection.
        
        Args:
            update: Whether to update the lock status after unlocking.
            timeout: Operation timeout in seconds.
            
        Returns:
            True if the operation was successful.
        """
        logger.info(f"Unlocking {self.name} (timeout: {timeout}s)...")
        
        try:
            if update:
                self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
            self.add_request(UtecBleRequest(BLECommandCode.UNLOCK), priority=True)

            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.info(f"Successfully unlocked {self.name}")
            else:
                logger.error(f"Failed to unlock {self.name}")
                
            return success
            
        except asyncio.TimeoutError:
            logger.error(f"Unlock operation for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"Unlock operation for {self.name} failed: {e}")
            return False

    async def async_lock(self, update: bool = True, timeout: float = 30.0) -> bool:
        """Lock the lock with timeout protection.
        
        Args:
            update: Whether to update the lock status after locking.
            timeout: Operation timeout in seconds.
            
        Returns:
            True if the operation was successful.
        """
        logger.info(f"Locking {self.name} (timeout: {timeout}s)...")
        
        try:
            if update:
                self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
            self.add_request(UtecBleRequest(BLECommandCode.BOLT_LOCK), priority=True)

            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.info(f"Successfully locked {self.name}")
            else:
                logger.error(f"Failed to lock {self.name}")
                
            return success
            
        except asyncio.TimeoutError:
            logger.error(f"Lock operation for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"Lock operation for {self.name} failed: {e}")
            return False

    async def async_reboot(self, timeout: float = 30.0) -> bool:
        """Reboot the lock with timeout protection.
        
        Args:
            timeout: Operation timeout in seconds.
            
        Returns:
            True if the reboot was successful.
        """
        logger.info(f"Rebooting {self.name} (timeout: {timeout}s)...")
        
        try:
            self.add_request(UtecBleRequest(BLECommandCode.REBOOT))
            
            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.info(f"Successfully rebooted {self.name}")
            else:
                logger.error(f"Failed to reboot {self.name}")
                
            return success
            
        except asyncio.TimeoutError:
            logger.error(f"Reboot operation for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"Reboot operation for {self.name} failed: {e}")
            return False

    async def async_set_workmode(self, mode: DeviceLockWorkMode, timeout: float = 30.0) -> bool:
        """Set the lock work mode with timeout protection.
        
        Args:
            mode: Lock work mode to set.
            timeout: Operation timeout in seconds.
            
        Returns:
            True if the operation was successful.
        """
        logger.info(f"Setting work mode for {self.name} to {mode.name} (timeout: {timeout}s)...")
        
        try:
            self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
            if self.capabilities.bt264:
                self.add_request(
                    UtecBleRequest(BLECommandCode.SET_LOCK_STATUS, data=bytes([mode.value]))
                )
            else:
                self.add_request(
                    UtecBleRequest(BLECommandCode.SET_WORK_MODE, data=bytes([mode.value]))
                )

            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.info(f"Successfully set work mode for {self.name} to {mode.name}")
            else:
                logger.error(f"Failed to set work mode for {self.name}")
                
            return success
            
        except asyncio.TimeoutError:
            logger.error(f"Set work mode operation for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"Set work mode operation for {self.name} failed: {e}")
            return False

    async def async_set_autolock(self, seconds: int, timeout: float = 30.0) -> bool:
        """Set the autolock time in seconds with timeout protection.
        
        Args:
            seconds: Autolock time in seconds.
            timeout: Operation timeout in seconds.
            
        Returns:
            True if the operation was successful.
        """
        logger.info(f"Setting autolock time for {self.name} to {seconds} seconds (timeout: {timeout}s)...")
        
        try:
            if self.capabilities.autolock:
                self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
                self.add_request(
                    UtecBleRequest(
                        BLECommandCode.SET_AUTOLOCK,
                        data=to_byte_array(seconds, 2) + bytes([0]),
                    )
                )
                
                success = await asyncio.wait_for(
                    self.send_requests(),
                    timeout=timeout
                )
                
                if success:
                    logger.info(f"Successfully set autolock time for {self.name} to {seconds}s")
                else:
                    logger.error(f"Failed to set autolock time for {self.name}")
                    
                return success
            else:
                logger.warning(f"Autolock not supported for {self.name}")
                return False
                
        except asyncio.TimeoutError:
            logger.error(f"Set autolock operation for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"Set autolock operation for {self.name} failed: {e}")
            return False

    async def async_update_status(self, timeout: Optional[float] = None) -> None:
        """Update the lock status with timeout protection.
        
        Args:
            timeout: Operation timeout in seconds. If None, uses device default.
        """
        if timeout is None:
            timeout = self.connection_timeout
            
        logger.debug(f"Updating status for {self.name} (timeout: {timeout}s)...")
        
        try:
            # Build request queue based on device capabilities
            self.add_request(UtecBleRequest(BLECommandCode.ADMIN_LOGIN))
            self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
            
            if not self.capabilities.bt264:
                self.add_request(UtecBleRequest(BLECommandCode.GET_LOCK_STATUS))
                self.add_request(UtecBleRequest(BLECommandCode.GET_BATTERY))
                self.add_request(UtecBleRequest(BLECommandCode.GET_MUTE))

            if self.capabilities.autolock:
                self.add_request(UtecBleRequest(BLECommandCode.GET_AUTOLOCK))

            # Send all requests with timeout
            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.debug(f"Status update completed successfully for {self.name}")
            else:
                logger.warning(f"Status update failed for {self.name}")
                
        except asyncio.TimeoutError:
            logger.error(f"Status update for {self.name} timed out after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"Status update for {self.name} failed: {e}")
            raise

    async def async_health_check(self, timeout: float = 10.0) -> bool:
        """Perform a quick health check on the lock.
        
        Args:
            timeout: Health check timeout in seconds.
            
        Returns:
            True if the lock is responding and healthy.
        """
        logger.debug(f"Performing health check for {self.name} (timeout: {timeout}s)...")
        
        try:
            # Just try to get basic status - quickest operation
            self.add_request(UtecBleRequest(BLECommandCode.LOCK_STATUS))
            
            success = await asyncio.wait_for(
                self.send_requests(),
                timeout=timeout
            )
            
            if success:
                logger.debug(f"Health check passed for {self.name}")
            else:
                logger.debug(f"Health check failed for {self.name}")
                
            return success
            
        except asyncio.TimeoutError:
            logger.debug(f"Health check for {self.name} timed out after {timeout}s")
            return False
        except Exception as e:
            logger.debug(f"Health check for {self.name} failed: {e}")
            return False

    def get_status_summary(self) -> Dict[str, Any]:
        """Get a summary of the current lock status.
        
        Returns:
            Dictionary containing lock status information.
        """
        return {
            'name': self.name,
            'mac_uuid': self.mac_uuid,
            'lock_status': self.lock_status,
            'lock_mode': self.lock_mode,
            'bolt_status': self.bolt_status,
            'battery': self.battery,
            'autolock_time': self.autolock_time,
            'mute': self.mute,
            'serial_number': self.sn,
            'model': self.model,
            'is_busy': self.is_busy,
        }