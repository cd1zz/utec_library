"""BLE device implementation with comprehensive debug logging for hang detection."""
import datetime
import asyncio
import hashlib
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional, Dict, List, Union, cast

from ecdsa import SECP128r1, SigningKey
from ecdsa.ellipticcurve import Point

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection, BleakNotFoundError, get_device
from bleak.backends.characteristic import BleakGATTCharacteristic
from Crypto.Cipher import AES

# Import from abstract base classes
from ..abstract import BaseBleDevice, BaseBleRequest, BLEDeviceCallback, ErrorCallback

# Import from configuration
from ..config import config, LogLevel

# Create a logger for this module
logger = config.get_logger("ble.device")

# We'll need to define or import these from other modules
from ..utils.constants import LOCK_MODE, BOLT_STATUS, BATTERY_LEVEL, CRC8Table
from ..utils.enums import BleResponseCode, BLECommandCode, DeviceServiceUUID, DeviceKeyUUID
from ..utils.data import decode_password, bytes_to_int2
from ..models.capabilities import DeviceDefinition, GenericLock, known_devices


class OperationTimeout:
    """Context manager for operation timeouts with debug logging."""
    
    def __init__(self, operation_name: str, timeout_seconds: float = 30.0, device_mac: str = ""):
        self.operation_name = operation_name
        self.timeout_seconds = timeout_seconds
        self.device_mac = device_mac
        self.start_time = None
        self.task = None
        
    async def __aenter__(self):
        self.start_time = time.time()
        logger.debug(f"[{self.device_mac}] Starting operation: {self.operation_name} (timeout: {self.timeout_seconds}s)")
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start_time if self.start_time else 0
        if exc_type is asyncio.TimeoutError:
            logger.error(f"[{self.device_mac}] TIMEOUT in {self.operation_name} after {elapsed:.2f}s")
        elif exc_type:
            logger.error(f"[{self.device_mac}] ERROR in {self.operation_name} after {elapsed:.2f}s: {exc_val}")
        else:
            logger.debug(f"[{self.device_mac}] Completed {self.operation_name} in {elapsed:.2f}s")


class UtecBleNotFoundError(Exception):
    """Exception raised when a BLE device is not found."""
    pass


class UtecBleError(Exception):
    """Exception raised for general BLE errors."""
    pass


class UtecBleDeviceError(Exception):
    """Exception raised for device-specific errors."""
    pass


class UtecBleDeviceBusyError(Exception):
    """Exception raised when a device is busy."""
    pass


class UtecBleDevice(BaseBleDevice):
    """Base class for U-tec BLE devices with enhanced debug logging."""

    def __init__(
        self,
        uid: str,
        password: str,
        mac_uuid: str,
        device_name: str,
        wurx_uuid: Optional[str] = None,
        device_model: str = "",
        async_bledevice_callback: Optional[BLEDeviceCallback] = None,
        error_callback: Optional[ErrorCallback] = None,
    ):
        """Initialize a U-tec BLE device."""
        self.mac_uuid = mac_uuid
        self.wurx_uuid = wurx_uuid
        self.uid = uid
        self.password: str = password
        self.name = device_name
        self.model: str = device_model
        self.capabilities: DeviceDefinition = known_devices.get(
            device_model, GenericLock()
        )
        self._requests: List[UtecBleRequest] = []
        self.config: Dict[str, Any] = {}
        self.async_bledevice_callback = async_bledevice_callback
        self.error_callback = error_callback
        self.lock_status: int = -1
        self.lock_mode: int = -1
        self.autolock_time: int = -1
        self.battery: int = -1
        self.mute: bool = False
        self.bolt_status: int = -1
        self.sn: str = ""
        self.calendar: Optional[datetime.datetime] = None
        self.is_busy = False
        self.device_time_offset: Optional[datetime.timedelta] = None
        
        # Get BLE configuration from the central config
        self._scanner_lock = asyncio.Lock()
        self._last_scan_time = 0
        self._scan_cache = {}
        self._scan_cache_ttl = config.ble_scan_cache_ttl
        
        # Debug tracking
        self._operation_start_time = None
        self._current_operation = None
        
        logger.debug(f"[{self.mac_uuid}] Initialized device: {device_name} (model: {device_model})")

    @classmethod
    def from_json(cls, json_config: Dict[str, Any]) -> "UtecBleDevice":
        """Create a BLE device from JSON data."""
        try:
            logger.debug(f"Creating device from JSON config: {json_config.get('name', 'Unknown')}")
            
            # Extract required values with safe defaults
            name = json_config.get("name", "Unknown")
            user_data = json_config.get("user", {})
            uid = str(user_data.get("uid", "0"))
            password = decode_password(user_data.get("password", 0))
            mac_uuid = json_config.get("uuid", "")
            device_model = json_config.get("model", "")
            
            logger.debug(f"[{mac_uuid}] Device details - UID: {uid}, Model: {device_model}")
            
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
                logger.debug(f"[{mac_uuid}] Wake-up UUID: {new_device.wurx_uuid}")
            
            new_device.sn = params.get("serialnumber", "")
            new_device.config = json_config
            
            logger.debug(f"[{mac_uuid}] Successfully created device from JSON")
            return new_device
        except Exception as e:
            logger.error(f"Error creating device from JSON: {e}", exc_info=True)
            raise

    async def async_update_status(self) -> None:
        """Update the device status."""
        pass

    def error(self, e: Exception, note: str = "") -> Exception:
        """Handle an error with debug logging."""
        error_msg = f"[{self.mac_uuid}] ERROR"
        if self._current_operation:
            error_msg += f" during {self._current_operation}"
        if self._operation_start_time:
            elapsed = time.time() - self._operation_start_time
            error_msg += f" after {elapsed:.2f}s"
        error_msg += f": {e}"
        
        if note:
            error_msg += f" ({note})"
            try:
                e.add_note(note)
            except AttributeError:
                pass

        if self.error_callback:
            self.error_callback(self.mac_uuid, e)

        logger.error(error_msg, exc_info=True)
        return e

    def debug(self, msg: str, *args: Any) -> None:
        """Log a debug message."""
        if logger.level <= LogLevel.DEBUG.value:
            formatted_msg = f"[{self.mac_uuid}] {msg}"
            if args:
                logger.debug(formatted_msg, *args)
            else:
                logger.debug(formatted_msg)

    def add_request(self, request: "UtecBleRequest", priority: bool = False) -> None:
        """Add a request to the queue."""
        request.device = self
        if priority:
            self._requests.insert(0, request)
            logger.debug(f"[{self.mac_uuid}] Added priority request: {request.command.name}")
        else:
            self._requests.append(request)
            logger.debug(f"[{self.mac_uuid}] Added request: {request.command.name}")
        
        logger.debug(f"[{self.mac_uuid}] Request queue size: {len(self._requests)}")

    async def send_requests(self) -> bool:
        """Send all queued requests to the device with comprehensive logging."""
        self._current_operation = "send_requests"
        self._operation_start_time = time.time()
        
        client: Optional[BleakClient] = None
        try:
            logger.info(f"[{self.mac_uuid}] Starting to send {len(self._requests)} request(s)")
            
            if len(self._requests) < 1:
                raise self.error(
                    UtecBleError(f"No commands to send to {self.name}({self.mac_uuid})")
                )

            # Set busy flag
            logger.debug(f"[{self.mac_uuid}] Setting device busy flag")
            self.is_busy = True
            
            # Device discovery phase
            async with OperationTimeout("device_discovery", 15.0, self.mac_uuid):
                self._current_operation = "device_discovery"
                logger.debug(f"[{self.mac_uuid}] Starting device discovery")
                
                device = await self._get_bledevice(self.mac_uuid)
                if not device:
                    logger.warning(f"[{self.mac_uuid}] Device not found, attempting wakeup")
                    
                    if self.wurx_uuid:
                        async with OperationTimeout("device_wakeup", 10.0, self.mac_uuid):
                            self._current_operation = "device_wakeup"
                            await self.async_wakeup_device()
                        
                        # Try again after wakeup
                        logger.debug(f"[{self.mac_uuid}] Retrying device discovery after wakeup")
                        device = await self._get_bledevice(self.mac_uuid)
                        
                        if not device:
                            raise self.error(
                                UtecBleNotFoundError(
                                    f"Device {self.name}({self.mac_uuid}) not found after wakeup"
                                )
                            )
                    else:
                        raise self.error(
                            UtecBleNotFoundError(
                                f"Device {self.name}({self.mac_uuid}) not found and no wakeup available"
                            )
                        )
                
                logger.info(f"[{self.mac_uuid}] Device found: {device.name} (RSSI: {device.rssi})")
            
            # Connection establishment phase
            async with OperationTimeout("connection_establishment", 20.0, self.mac_uuid):
                self._current_operation = "connection_establishment"
                logger.debug(f"[{self.mac_uuid}] Establishing BLE connection")
                
                try:
                    client = await establish_connection(
                        client_class=BleakClient,
                        device=device,
                        name=self.mac_uuid,
                        max_attempts=config.ble_max_retries,
                        ble_device_callback=self._brc_get_lock_device,
                    )
                    logger.info(f"[{self.mac_uuid}] BLE connection established successfully")
                    
                except (BleakNotFoundError, BleakError) as e:
                    logger.error(f"[{self.mac_uuid}] Connection failed: {str(e)}")
                    raise self.error(
                        UtecBleNotFoundError(
                            f"Could not connect to device {self.name}({self.mac_uuid}): {str(e)}"
                        )
                    ) from None

            # Key exchange phase
            async with OperationTimeout("key_exchange", 15.0, self.mac_uuid):
                self._current_operation = "key_exchange"
                logger.debug(f"[{self.mac_uuid}] Starting key exchange")
                
                try:
                    aes_key = await UtecBleDeviceKey.get_shared_key(
                        client=client, device=self
                    )
                    logger.debug(f"[{self.mac_uuid}] Key exchange completed successfully")
                    
                except Exception as e:
                    logger.error(f"[{self.mac_uuid}] Key exchange failed: {str(e)}")
                    raise self.error(
                        UtecBleDeviceError(
                            f"Could not retrieve shared key for {self.name}({self.mac_uuid}): {str(e)}"
                        )
                    ) from None

            # Request processing phase
            request_count = len(self._requests)
            logger.info(f"[{self.mac_uuid}] Processing {request_count} requests")
            
            for i, request in enumerate(self._requests[:], 1):
                if not request.sent or not request.response.completed:
                    async with OperationTimeout(f"request_{request.command.name}", 10.0, self.mac_uuid):
                        self._current_operation = f"request_{request.command.name}"
                        logger.debug(f"[{self.mac_uuid}] Processing request {i}/{request_count}: {request.command.name}")
                        
                        request.aes_key = aes_key
                        request.device = self
                        request.sent = True
                        
                        try:
                            await request._get_response(client)
                            self._requests.remove(request)
                            logger.debug(f"[{self.mac_uuid}] Request {request.command.name} completed successfully")
                            
                        except Exception as e:
                            logger.error(f"[{self.mac_uuid}] Request {request.command.name} failed: {str(e)}")
                            raise self.error(
                                UtecBleDeviceError(
                                    f"Command {request.command.name} failed for {self.name}({self.mac_uuid}): {str(e)}"
                                )
                            ) from None

            logger.info(f"[{self.mac_uuid}] All requests completed successfully")
            return True
            
        except Exception as e:
            elapsed = time.time() - self._operation_start_time if self._operation_start_time else 0
            logger.error(f"[{self.mac_uuid}] Operation failed after {elapsed:.2f}s in phase '{self._current_operation}': {str(e)}")
            return False
            
        finally:
            # Cleanup phase
            logger.debug(f"[{self.mac_uuid}] Starting cleanup")
            
            self._requests.clear()
            
            if client:
                try:
                    logger.debug(f"[{self.mac_uuid}] Disconnecting BLE client")
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    logger.debug(f"[{self.mac_uuid}] BLE client disconnected")
                except asyncio.TimeoutError:
                    logger.warning(f"[{self.mac_uuid}] Disconnect timeout - client may still be connected")
                except Exception as e:
                    logger.warning(f"[{self.mac_uuid}] Error during disconnect: {str(e)}")
            
            logger.debug(f"[{self.mac_uuid}] Clearing device busy flag")
            self.is_busy = False
            
            self._current_operation = None
            self._operation_start_time = None
            
            total_elapsed = time.time() - self._operation_start_time if self._operation_start_time else 0
            logger.debug(f"[{self.mac_uuid}] Cleanup completed, total operation time: {total_elapsed:.2f}s")

    async def _get_bledevice(self, address: str) -> Optional[BLEDevice]:
        """Get BLE device with enhanced logging."""
        if not address:
            logger.warning(f"[{self.mac_uuid}] Empty address provided to _get_bledevice")
            return None
            
        # Normalize address format
        address = address.upper()
        logger.debug(f"[{self.mac_uuid}] Looking for device: {address}")
        
        # Check cache first
        current_time = time.time()
        if address in self._scan_cache:
            cache_time, device = self._scan_cache[address]
            if current_time - cache_time < self._scan_cache_ttl:
                logger.debug(f"[{self.mac_uuid}] Using cached device (age: {current_time - cache_time:.1f}s)")
                return device
            else:
                logger.debug(f"[{self.mac_uuid}] Cache expired (age: {current_time - cache_time:.1f}s)")
        
        # If callback is provided, use it first
        if self.async_bledevice_callback:
            logger.debug(f"[{self.mac_uuid}] Trying device callback")
            try:
                device = await asyncio.wait_for(
                    self.async_bledevice_callback(address), 
                    timeout=5.0
                )
                if device:
                    logger.debug(f"[{self.mac_uuid}] Device found via callback")
                    self._scan_cache[address] = (current_time, device)
                    return device
                else:
                    logger.debug(f"[{self.mac_uuid}] Device callback returned None")
            except asyncio.TimeoutError:
                logger.warning(f"[{self.mac_uuid}] Device callback timed out")
            except Exception as e:
                logger.warning(f"[{self.mac_uuid}] Device callback error: {str(e)}")
        
        # Use our own scanning method
        async with self._scanner_lock:
            logger.debug(f"[{self.mac_uuid}] Acquired scanner lock")
            
            # If we recently did a scan, wait a bit
            if current_time - self._last_scan_time < 2:
                wait_time = 2 - (current_time - self._last_scan_time)
                logger.debug(f"[{self.mac_uuid}] Waiting {wait_time:.1f}s for Bluetooth to settle")
                await asyncio.sleep(wait_time)
            
            # Try discovery method first
            try:
                logger.debug(f"[{self.mac_uuid}] Starting BLE discovery scan (timeout: {config.ble_scan_timeout}s)")
                scan_start = time.time()
                
                all_devices = await asyncio.wait_for(
                    BleakScanner.discover(timeout=config.ble_scan_timeout),
                    timeout=config.ble_scan_timeout + 5.0
                )
                
                scan_elapsed = time.time() - scan_start
                logger.debug(f"[{self.mac_uuid}] Discovery scan completed in {scan_elapsed:.2f}s, found {len(all_devices)} devices")
                
                # Look through discovered devices
                for device in all_devices:
                    if device.address.upper() == address.upper():
                        logger.info(f"[{self.mac_uuid}] Device found in discovery: {device.name} (RSSI: {device.rssi})")
                        
                        # Update cache
                        self._scan_cache[address] = (current_time, device)
                        self._last_scan_time = current_time
                        
                        return device
                
                logger.debug(f"[{self.mac_uuid}] Device not found in discovery results")
                
                # If device not found via discovery, try direct method as fallback
                logger.debug(f"[{self.mac_uuid}] Trying direct device lookup")
                try:
                    device = await asyncio.wait_for(get_device(address), timeout=3.0)
                    if device:
                        logger.info(f"[{self.mac_uuid}] Device found via direct lookup")
                        
                        # Update cache
                        self._scan_cache[address] = (current_time, device)
                        self._last_scan_time = current_time
                        
                        return device
                except asyncio.TimeoutError:
                    logger.warning(f"[{self.mac_uuid}] Direct device lookup timed out")
                except Exception as e:
                    logger.debug(f"[{self.mac_uuid}] Direct device lookup failed: {e}")
            
            except asyncio.TimeoutError:
                logger.error(f"[{self.mac_uuid}] BLE discovery scan timed out")
            except Exception as e:
                logger.error(f"[{self.mac_uuid}] Error during device search: {e}")
                
                # For the specific "operation in progress" error, wait longer
                if "Operation already in progress" in str(e):
                    logger.warning(f"[{self.mac_uuid}] BLE scan in progress, waiting 10s...")
                    await asyncio.sleep(10)
                    
                    # Try one more time after waiting
                    try:
                        device = await asyncio.wait_for(get_device(address), timeout=3.0)
                        if device:
                            logger.info(f"[{self.mac_uuid}] Device found after retry")
                            self._scan_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            return device
                    except Exception:
                        logger.debug(f"[{self.mac_uuid}] Retry after wait also failed")
            
            self._last_scan_time = current_time
            logger.warning(f"[{self.mac_uuid}] Device not found after all attempts")
            return None

    async def _brc_get_lock_device(self) -> Optional[BLEDevice]:
        """Callback for bleak_retry_connector to get the lock device."""
        logger.debug(f"[{self.mac_uuid}] BRC callback requested lock device")
        return await self._get_bledevice(self.mac_uuid)

    async def _brc_get_wurx_device(self) -> Optional[BLEDevice]:
        """Callback for bleak_retry_connector to get the wurx device."""
        logger.debug(f"[{self.mac_uuid}] BRC callback requested wurx device")
        return await self._get_bledevice(self.wurx_uuid) if self.wurx_uuid else None

    async def async_wakeup_device(self) -> None:
        """Attempt to wake up the device using the wake-up receiver."""
        if not self.wurx_uuid:
            logger.debug(f"[{self.mac_uuid}] No wake-up UUID available")
            return
            
        logger.info(f"[{self.mac_uuid}] Attempting to wake up device via {self.wurx_uuid}")
        
        try:
            device = await asyncio.wait_for(
                self._get_bledevice(self.wurx_uuid), 
                timeout=8.0
            )
            if not device:
                raise BleakNotFoundError(f"Wake-up receiver {self.wurx_uuid} not found")
                
            logger.debug(f"[{self.mac_uuid}] Connecting to wake-up receiver")
            wclient: BleakClient = await asyncio.wait_for(
                establish_connection(
                    client_class=BleakClient,
                    device=device,
                    name=self.wurx_uuid,
                    max_attempts=2,
                    ble_device_callback=self._brc_get_wurx_device,
                ),
                timeout=10.0
            )
            
            logger.debug(f"[{self.mac_uuid}] Wake-up receiver connected, sending wake signal")
            
            # Wait for the wake-up signal to take effect
            await asyncio.sleep(2)
            
            logger.debug(f"[{self.mac_uuid}] Disconnecting from wake-up receiver")
            await asyncio.wait_for(wclient.disconnect(), timeout=3.0)
            
            logger.info(f"[{self.mac_uuid}] Wake-up sequence completed")
            
        except asyncio.TimeoutError:
            logger.error(f"[{self.mac_uuid}] Wake-up operation timed out")
        except Exception as e:
            logger.warning(f"[{self.mac_uuid}] Wake-up attempt failed: {str(e)}")


class UtecBleRequest(BaseBleRequest):
    """Request to send to a U-tec BLE device with debug logging."""

    def __init__(
        self,
        command: BLECommandCode,
        device: Optional[UtecBleDevice] = None,
        data: bytes = bytes(),
        auth_required: bool = False,
    ):
        """Initialize a BLE request."""
        self.command = command
        self.device = device
        self.uuid = DeviceServiceUUID.DATA.value
        self.response: Optional[UtecBleResponse] = None
        self.aes_key: Optional[bytes] = None
        self.sent = False
        self.data = data
        self.auth_required = auth_required

        self.buffer = bytearray(5120)
        self.buffer[0] = 0x7F
        byte_array = bytearray(int.to_bytes(2, 2, "little"))
        self.buffer[1] = byte_array[0]
        self.buffer[2] = byte_array[1]
        self.buffer[3] = command.value
        self._write_pos = 4

        if auth_required and device:
            self._append_auth(device.uid, device.password)
        if data:
            self._append_data(data)
        self._append_length()
        self._append_crc()
        
        if device:
            logger.debug(f"[{device.mac_uuid}] Created request: {command.name} (auth: {auth_required}, data: {len(data)} bytes)")

    def _append_data(self, data: bytes) -> None:
        """Append data to the request."""
        data_len = len(data)
        self.buffer[self._write_pos : self._write_pos + data_len] = data
        self._write_pos += data_len

    def _append_auth(self, uid: str, password: str = "") -> None:
        """Append authentication data to the request."""
        if uid:
            byte_array = bytearray(int(uid).to_bytes(4, "little"))
            self.buffer[self._write_pos : self._write_pos + 4] = byte_array
            self._write_pos += 4
        if password:
            byte_array = bytearray(int(password).to_bytes(4, "little"))
            byte_array[3] = (len(password) << 4) | byte_array[3]
            self.buffer[self._write_pos : self._write_pos + 4] = byte_array[:4]
            self._write_pos += 4

    def _append_length(self) -> None:
        """Append length to the request."""
        byte_array = bytearray(int(self._write_pos - 2).to_bytes(2, "little"))
        self.buffer[1] = byte_array[0]
        self.buffer[2] = byte_array[1]

    def _append_crc(self) -> None:
        """Append CRC to the request."""
        b = 0
        for i2 in range(3, self._write_pos):
            m_index = (b ^ self.buffer[i2]) & 0xFF
            b = CRC8Table[m_index]

        self.buffer[self._write_pos] = b
        self._write_pos += 1

    @property
    def package(self) -> bytearray:
        """Get the request package."""
        return self.buffer[: self._write_pos]

    def encrypted_package(self, aes_key: bytes) -> bytearray:
        """Get the encrypted request package."""
        if self.device:
            logger.debug(f"[{self.device.mac_uuid}] Encrypting package for {self.command.name} ({self._write_pos} bytes)")
        
        bArr2 = bytearray(self._write_pos)
        bArr2[: self._write_pos] = self.buffer[: self._write_pos]
        num_chunks = (self._write_pos // 16) + (1 if self._write_pos % 16 > 0 else 0)
        pkg = bytearray(num_chunks * 16)

        i2 = 0
        while i2 < num_chunks:
            i3 = i2 + 1
            if i3 < num_chunks:
                bArr = bArr2[i2 * 16 : (i2 + 1) * 16]
            else:
                i4 = self._write_pos - ((num_chunks - 1) * 16)
                bArr = bArr2[i2 * 16 : i2 * 16 + i4]

            initialValue = bytearray(16)
            encrypt_buffer = bytearray(16)
            encrypt_buffer[: len(bArr)] = bArr
            cipher = AES.new(aes_key, AES.MODE_CBC, initialValue)
            encrypt = cipher.encrypt(encrypt_buffer)
            if encrypt is None:
                encrypt = bytearray(16)

            pkg[i2 * 16 : (i2 + 1) * 16] = encrypt
            i2 = i3
        
        if self.device:
            logger.debug(f"[{self.device.mac_uuid}] Package encrypted to {len(pkg)} bytes ({num_chunks} chunks)")
        
        return pkg

    async def _get_response(self, client: BleakClient) -> None:
        """Get the response from the device with timeout and logging."""
        if not self.device or not self.aes_key:
            raise ValueError("Device and AES key must be set before getting response")
        
        mac_uuid = self.device.mac_uuid
        logger.debug(f"[{mac_uuid}] Starting response handling for {self.command.name}")
        
        self.response = UtecBleResponse(self, self.device)
        
        try:
            # Start notifications
            logger.debug(f"[{mac_uuid}] Starting notifications on {self.uuid}")
            await asyncio.wait_for(
                client.start_notify(self.uuid, self.response._receive_write_response),
                timeout=5.0
            )
            
            # Send the encrypted package
            encrypted_pkg = self.encrypted_package(self.aes_key)
            logger.debug(f"[{mac_uuid}] Sending encrypted package ({len(encrypted_pkg)} bytes)")
            
            await asyncio.wait_for(
                client.write_gatt_char(self.uuid, encrypted_pkg),
                timeout=5.0
            )
            
            logger.debug(f"[{mac_uuid}] Package sent, waiting for response...")
            
            # Wait for response with timeout
            await asyncio.wait_for(
                self.response.response_completed.wait(),
                timeout=8.0
            )
            
            logger.debug(f"[{mac_uuid}] Response received for {self.command.name}")
            
        except asyncio.TimeoutError as e:
            logger.error(f"[{mac_uuid}] Timeout waiting for response to {self.command.name}")
            if self.device:
                raise self.device.error(e, f"Timeout in {self.command.name}")
            else:
                raise
        except Exception as e:
            logger.error(f"[{mac_uuid}] Error in response handling for {self.command.name}: {str(e)}")
            if self.device:
                raise self.device.error(e, f"Error in {self.command.name}")
            else:
                raise
        finally:
            try:
                logger.debug(f"[{mac_uuid}] Stopping notifications")
                await asyncio.wait_for(
                    client.stop_notify(self.uuid),
                    timeout=3.0
                )
            except Exception as e:
                logger.warning(f"[{mac_uuid}] Error stopping notifications: {str(e)}")


class UtecBleResponse:
    """Response from a U-tec BLE device with enhanced logging."""

    def __init__(self, request: UtecBleRequest, device: UtecBleDevice):
        """Initialize a BLE response."""
        self.buffer = bytearray()
        self.request = request
        self.response_completed = asyncio.Event()
        self.device = device
        self.completed = False
        self._response_start_time = time.time()
        
        logger.debug(f"[{device.mac_uuid}] Response handler created for {request.command.name}")

    async def _receive_write_response(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Receive a write response from the device with logging."""
        try:
            elapsed = time.time() - self._response_start_time
            logger.debug(f"[{self.device.mac_uuid}] Received response chunk: {len(data)} bytes (after {elapsed:.2f}s)")
            
            if not self.request.aes_key:
                raise ValueError("AES key must be set before receiving response")
                
            self._append(data, bytearray(self.request.aes_key))
            
            if self.completed and self.is_valid:
                logger.debug(f"[{self.device.mac_uuid}] Response complete and valid, processing...")
                await self._read_response()
                self.response_completed.set()
                logger.debug(f"[{self.device.mac_uuid}] Response processing completed")
            elif self.completed:
                logger.warning(f"[{self.device.mac_uuid}] Response complete but invalid")
            else:
                logger.debug(f"[{self.device.mac_uuid}] Response incomplete, waiting for more data...")
                
        except Exception as e:
            logger.error(f"[{self.device.mac_uuid}] Error receiving write response: {str(e)}", exc_info=True)
            try:
                e.add_note(f"({self.device.mac_uuid}) Error receiving write response.")
            except AttributeError:
                pass
            raise self.device.error(e)

    def reset(self) -> None:
        """Reset the response."""
        logger.debug(f"[{self.device.mac_uuid}] Resetting response buffer")
        self.buffer = bytearray(0)
        self.completed = False

    def _append(self, barr: bytearray, aes_key: bytearray) -> None:
        """Append data to the response with logging."""
        logger.debug(f"[{self.device.mac_uuid}] Decrypting {len(barr)} bytes")
        
        f495iv = bytearray(16)
        cipher = AES.new(aes_key, AES.MODE_CBC, f495iv)
        output = cipher.decrypt(barr)

        if (self.length > 0 and self.buffer[0] == 0x7F) or output[0] == 0x7F:
            self.buffer += output
            logger.debug(f"[{self.device.mac_uuid}] Buffer now {len(self.buffer)} bytes")
            
        # Update completed flag
        was_completed = self.completed
        self.completed = True if self.length > 3 and self.length >= self.package_len else False
        
        if not was_completed and self.completed:
            logger.debug(f"[{self.device.mac_uuid}] Response marked as completed")

    def _parameter(self, index: int) -> Optional[bytearray]:
        """Get a parameter from the response."""
        data_len = self.data_len
        if data_len < 3:
            return None

        param_size = (data_len - 2) - index
        bArr2 = bytearray([0] * param_size)
        bArr2[:] = self.buffer[index + 4 : index + 4 + param_size]

        return bytearray(bArr2)

    @property
    def is_valid(self) -> bool:
        """Check if the response is valid."""
        cmd = self.command
        is_valid = (
            True
            if (self.completed and cmd and isinstance(cmd, BleResponseCode))
            else False
        )
        if not is_valid:
            logger.debug(f"[{self.device.mac_uuid}] Response validation failed - completed: {self.completed}, cmd: {cmd}")
        return is_valid

    @property
    def length(self) -> int:
        """Get the response length."""
        return len(self.buffer)

    @property
    def data_len(self) -> int:
        """Get the data length."""
        return (
            int.from_bytes(self.buffer[1:3], byteorder="little")
            if self.length > 3
            else 0
        )

    @property
    def package_len(self) -> int:
        """Get the package length."""
        return self.data_len + 4 if self.length > 3 else 0

    @property
    def package(self) -> bytearray:
        """Get the response package."""
        return self.buffer[: self.package_len - 1]

    @property
    def command(self) -> Union[BleResponseCode, None]:
        """Get the response command."""
        return BleResponseCode(self.buffer[3]) if self.completed else None

    @property
    def success(self) -> bool:
        """Check if the response was successful."""
        return True if self.completed and self.buffer[4] == 0 else False

    @property
    def data(self) -> bytearray:
        """Get the response data."""
        if self.is_valid:
            return self.buffer[5 : self.data_len + 5]
        else:
            return bytearray()

    async def _read_response(self) -> None:
        """Read the response and update the device state with logging."""
        try:
            success_str = "Success" if self.success else "Failed"
            logger.info(f"[{self.device.mac_uuid}] Response {self.command.name}: {success_str}")
            logger.debug(f"[{self.device.mac_uuid}] Raw response: {self.package.hex()}")

            if self.command == BleResponseCode.GET_LOCK_STATUS:
                self.device.lock_mode = int(self.data[0])
                self.device.bolt_status = int(self.data[1])
                logger.info(f"[{self.device.mac_uuid}] Lock status - Mode: {self.device.lock_mode} ({LOCK_MODE[self.device.lock_mode]}), Bolt: {self.device.bolt_status} ({BOLT_STATUS[self.device.bolt_status]})")

            elif self.command == BleResponseCode.SET_LOCK_STATUS:
                self.device.lock_mode = self.data[0]
                logger.info(f"[{self.device.mac_uuid}] Work mode set to: {self.device.lock_mode}")

            elif self.command == BleResponseCode.GET_BATTERY:
                self.device.battery = int(self.data[0])
                logger.info(f"[{self.device.mac_uuid}] Battery level: {self.device.battery} ({BATTERY_LEVEL[self.device.battery]})")

            elif self.command == BleResponseCode.GET_AUTOLOCK:
                self.device.autolock_time = bytes_to_int2(self.data[:2])
                logger.info(f"[{self.device.mac_uuid}] Autolock time: {self.device.autolock_time}s")

            elif self.command == BleResponseCode.SET_AUTOLOCK:
                if self.success:
                    self.device.autolock_time = bytes_to_int2(self.data[:2])
                    logger.info(f"[{self.device.mac_uuid}] Autolock time set to: {self.device.autolock_time}s")

            elif self.command == BleResponseCode.GET_SN:
                self.device.sn = self.data.decode("ISO8859-1")
                logger.info(f"[{self.device.mac_uuid}] Serial number: {self.device.sn}")

            elif self.command == BleResponseCode.GET_MUTE:
                self.device.mute = bool(self.data[0])
                logger.info(f"[{self.device.mac_uuid}] Mute status: {self.device.mute}")

            elif self.command == BleResponseCode.SET_WORK_MODE:
                if self.success:
                    self.device.lock_mode = self.data[0]
                    logger.info(f"[{self.device.mac_uuid}] Work mode set to: {self.device.lock_mode}")

            elif self.command == BleResponseCode.UNLOCK:
                logger.info(f"[{self.device.mac_uuid}] {self.device.name} - UNLOCKED")

            elif self.command == BleResponseCode.BOLT_LOCK:
                logger.info(f"[{self.device.mac_uuid}] {self.device.name} - BOLT LOCKED")

            elif self.command == BleResponseCode.LOCK_STATUS:
                self.device.lock_status = int(self.data[0])
                self.device.bolt_status = int(self.data[1])
                logger.info(f"[{self.device.mac_uuid}] Status - Lock: {self.device.lock_status}, Bolt: {self.device.bolt_status}")
                
                if self.length > 16:
                    self.device.battery = int(self.data[2])
                    self.device.lock_mode = int(self.data[3])
                    self.device.mute = bool(self.data[4])
                    logger.info(f"[{self.device.mac_uuid}] Extended status - Battery: {self.device.battery}, Mode: {self.device.lock_mode}, Mute: {self.device.mute}")

            logger.debug(f"[{self.device.mac_uuid}] Command processing completed: {self.command.name}")

        except Exception as e:
            error_msg = f"Error updating lock data"
            if hasattr(self, 'command') and self.command:
                error_msg += f" ({self.command.name})"
            error_msg += f": {e}"
            logger.error(f"[{self.device.mac_uuid}] {error_msg}", exc_info=True)
            self.device.error(Exception(error_msg))


class UtecBleDeviceKey:
    """Helper class for handling BLE device keys with enhanced logging."""

    @staticmethod
    async def get_shared_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the shared key for a device with comprehensive logging."""
        mac_uuid = device.mac_uuid
        logger.debug(f"[{mac_uuid}] Determining encryption method")
        
        # Check available characteristics
        static_char = client.services.get_characteristic(DeviceKeyUUID.STATIC.value)
        md5_char = client.services.get_characteristic(DeviceKeyUUID.MD5.value)
        ecc_char = client.services.get_characteristic(DeviceKeyUUID.ECC.value)
        
        logger.debug(f"[{mac_uuid}] Available encryption methods - Static: {bool(static_char)}, MD5: {bool(md5_char)}, ECC: {bool(ecc_char)}")
        
        if static_char:
            logger.info(f"[{mac_uuid}] Using static key encryption")
            key_data = await client.read_gatt_char(DeviceKeyUUID.STATIC.value)
            result = bytearray(b"Anviz.ut") + key_data
            logger.debug(f"[{mac_uuid}] Static key generated ({len(result)} bytes)")
            return result
            
        elif md5_char:
            logger.info(f"[{mac_uuid}] Using MD5 key encryption")
            return await UtecBleDeviceKey.get_md5_key(client, device)
            
        elif ecc_char:
            logger.info(f"[{mac_uuid}] Using ECC key encryption")
            return await UtecBleDeviceKey.get_ecc_key(client, device)
            
        else:
            error_msg = f"No supported encryption method found"
            logger.error(f"[{mac_uuid}] {error_msg}")
            raise NotImplementedError(f"({client.address}) Unknown encryption.")

    @staticmethod
    async def get_ecc_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the ECC key for a device with detailed logging."""
        mac_uuid = device.mac_uuid
        logger.debug(f"[{mac_uuid}] Starting ECC key exchange")
        
        try:
            # Generate our key pair
            logger.debug(f"[{mac_uuid}] Generating ECC private key")
            private_key = SigningKey.generate(curve=SECP128r1)
            received_pubkey = []
            public_key = private_key.get_verifying_key()
            pub_x = public_key.pubkey.point.x().to_bytes(16, "little")
            pub_y = public_key.pubkey.point.y().to_bytes(16, "little")
            
            logger.debug(f"[{mac_uuid}] Generated public key coordinates ({len(pub_x)} + {len(pub_y)} bytes)")

            notification_event = asyncio.Event()

            def notification_handler(sender, data):
                logger.debug(f"[{mac_uuid}] Received ECC data: {len(data)} bytes")
                received_pubkey.append(data)
                if len(received_pubkey) == 2:
                    logger.debug(f"[{mac_uuid}] Received both ECC coordinates")
                    notification_event.set()

            logger.debug(f"[{mac_uuid}] Starting ECC notifications")
            await client.start_notify(DeviceKeyUUID.ECC.value, notification_handler)
            
            logger.debug(f"[{mac_uuid}] Sending X coordinate")
            await client.write_gatt_char(DeviceKeyUUID.ECC.value, pub_x)
            
            logger.debug(f"[{mac_uuid}] Sending Y coordinate")
            await client.write_gatt_char(DeviceKeyUUID.ECC.value, pub_y)
            
            logger.debug(f"[{mac_uuid}] Waiting for device's public key...")
            await asyncio.wait_for(notification_event.wait(), timeout=10.0)

            await client.stop_notify(DeviceKeyUUID.ECC.value)
            logger.debug(f"[{mac_uuid}] ECC notifications stopped")

            # Calculate shared secret
            logger.debug(f"[{mac_uuid}] Calculating shared secret")
            rec_key_point = Point(
                SECP128r1.curve,
                int.from_bytes(received_pubkey[0], "little"),
                int.from_bytes(received_pubkey[1], "little"),
            )
            shared_point = private_key.privkey.secret_multiplier * rec_key_point
            shared_key = int.to_bytes(shared_point.x(), 16, "little")
            
            logger.info(f"[{mac_uuid}] ECC key exchange completed successfully")
            logger.debug(f"[{mac_uuid}] Shared key: {shared_key.hex()}")
            return shared_key
            
        except asyncio.TimeoutError:
            logger.error(f"[{mac_uuid}] ECC key exchange timed out")
            raise device.error(Exception("ECC key exchange timed out"))
        except Exception as e:
            logger.error(f"[{mac_uuid}] ECC key exchange failed: {str(e)}", exc_info=True)
            try:
                e.add_note(f"({client.address}) Failed to update ECC key: {e}")
            except AttributeError:
                pass
            raise device.error(e)

    @staticmethod
    async def get_md5_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the MD5 key for a device with detailed logging."""
        mac_uuid = device.mac_uuid
        logger.debug(f"[{mac_uuid}] Starting MD5 key exchange")
        
        try:
            logger.debug(f"[{mac_uuid}] Reading secret from device")
            secret = await client.read_gatt_char(DeviceKeyUUID.MD5.value)
            logger.debug(f"[{mac_uuid}] Received secret: {secret.hex()} ({len(secret)} bytes)")

            if len(secret) != 16:
                error_msg = f"Expected secret of length 16, got {len(secret)}"
                logger.error(f"[{mac_uuid}] {error_msg}")
                raise device.error(ValueError(f"({client.address}) {error_msg}"))

            logger.debug(f"[{mac_uuid}] Processing MD5 key algorithm")
            
            part1 = struct.unpack("<Q", secret[:8])[0]  # Little-endian
            part2 = struct.unpack("<Q", secret[8:])[0]

            xor_val1 = (
                part1 ^ 0x716F6C6172744C55
            )  # this value corresponds to 'ULtraloq' in little-endian
            xor_val2_part1 = (part2 >> 56) ^ (part1 >> 56) ^ 0x71
            xor_val2_part2 = ((part2 >> 48) & 0xFF) ^ ((part1 >> 48) & 0xFF) ^ 0x6F
            xor_val2_part3 = ((part2 >> 40) & 0xFF) ^ ((part1 >> 40) & 0xFF) ^ 0x6C
            xor_val2_part4 = ((part2 >> 32) & 0xFF) ^ ((part1 >> 32) & 0xFF) ^ 0x61
            xor_val2_part5 = ((part2 >> 24) & 0xFF) ^ ((part1 >> 24) & 0xFF) ^ 0x72
            xor_val2_part6 = ((part2 >> 16) & 0xFF) ^ ((part1 >> 16) & 0xFF) ^ 0x74
            xor_val2_part7 = ((part2 >> 8) & 0xFF) ^ ((part1 >> 8) & 0xFF) ^ 0x4C
            xor_val2_part8 = (part2 & 0xFF) ^ (part1 & 0xFF) ^ 0x55

            xor_val2 = (
                (xor_val2_part1 << 56)
                | (xor_val2_part2 << 48)
                | (xor_val2_part3 << 40)
                | (xor_val2_part4 << 32)
                | (xor_val2_part5 << 24)
                | (xor_val2_part6 << 16)
                | (xor_val2_part7 << 8)
                | xor_val2_part8
            )

            xor_result = struct.pack("<QQ", xor_val1, xor_val2)

            m = hashlib.md5()
            m.update(xor_result)
            result = m.digest()

            bVar2 = (part1 & 0xFF) ^ 0x55
            if bVar2 & 1:
                logger.debug(f"[{mac_uuid}] Applying additional MD5 hash")
                m = hashlib.md5()
                m.update(result)
                result = m.digest()

            logger.info(f"[{mac_uuid}] MD5 key generated successfully")
            logger.debug(f"[{mac_uuid}] MD5 key: {result.hex()}")
            return result

        except Exception as e:
            logger.error(f"[{mac_uuid}] MD5 key generation failed: {str(e)}", exc_info=True)
            try:
                e.add_note(f"({client.address}) Failed to update MD5 key: {e}")
            except AttributeError:
                pass
            raise device.error(e)