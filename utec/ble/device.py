"""BLE device implementation for the U-tec library with enhanced timeout handling."""
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


class UtecBleTimeoutError(Exception):
    """Exception raised when a BLE operation times out."""
    pass


class UtecBleDevice(BaseBleDevice):
    """Base class for U-tec BLE devices with timeout protection."""

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
        connection_timeout: float = 30.0,
        operation_timeout: float = 15.0,
    ):
        """Initialize a U-tec BLE device.
        
        Args:
            uid: User ID.
            password: User password.
            mac_uuid: Device MAC address.
            device_name: Device name.
            wurx_uuid: Wake-up receiver UUID.
            device_model: Device model.
            async_bledevice_callback: Callback for finding BLE devices.
            error_callback: Callback for handling errors.
            connection_timeout: Timeout for BLE connection establishment.
            operation_timeout: Timeout for individual BLE operations.
        """
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
        
        # Timeout configuration
        self.connection_timeout = connection_timeout
        self.operation_timeout = operation_timeout
        
        # Get BLE configuration from the central config
        self._scanner_lock = asyncio.Lock()
        self._last_scan_time = 0
        self._scan_cache = {}
        self._scan_cache_ttl = config.ble_scan_cache_ttl

    @classmethod
    def from_json(cls, json_config: Dict[str, Any]) -> "UtecBleDevice":
        """Create a BLE device from JSON data.
        
        Args:
            json_config: JSON configuration data.
            
        Returns:
            A new BLE device instance.
        """
        try:
            # Extract required values with safe defaults
            name = json_config.get("name", "Unknown")
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
            new_device.config = json_config
            
            return new_device
        except Exception as e:
            logger.error(f"Error creating device from JSON: {e}")
            raise

    async def async_update_status(self) -> None:
        """Update the device status.
        
        This method should be implemented by subclasses.
        """
        pass

    def error(self, e: Exception, note: str = "") -> Exception:
        """Handle an error.
        
        Args:
            e: Exception to handle.
            note: Additional note to add.
            
        Returns:
            The original exception.
        """
        if note:
            try:
                e.add_note(note)
            except AttributeError:
                # Python < 3.11 doesn't have add_note
                pass

        if self.error_callback:
            self.error_callback(self.mac_uuid, e)

        logger.debug(f"({self.mac_uuid}) {e}")
        return e

    def debug(self, msg: str, *args: Any) -> None:
        """Log a debug message.
        
        Args:
            msg: Message to log.
            *args: Additional arguments for formatting.
        """
        if logger.level <= LogLevel.DEBUG.value:
            if args:
                logger.debug(msg, *args)
            else:
                logger.debug(msg)

    def add_request(self, request: "UtecBleRequest", priority: bool = False) -> None:
        """Add a request to the queue.
        
        Args:
            request: Request to add.
            priority: Whether to add the request to the front of the queue.
        """
        request.device = self
        if priority:
            self._requests.insert(0, request)
        else:
            self._requests.append(request)

    async def send_requests(self) -> bool:
        """Send all queued requests to the device with comprehensive timeout protection.
        
        Returns:
            True if all requests were sent successfully.
        """
        if self.is_busy:
            logger.warning(f"({self.mac_uuid}) Device is busy, skipping requests")
            return False
            
        client: Optional[BleakClient] = None
        operation_start_time = time.time()
        
        try:
            if len(self._requests) < 1:
                logger.debug(f"({self.mac_uuid}) No requests to process")
                return True

            self.is_busy = True
            logger.debug(f"({self.mac_uuid}) Processing {len(self._requests)} requests with timeout {self.connection_timeout}s")
            
            try:
                # Wrap the entire connection and request process in a timeout
                await asyncio.wait_for(
                    self._process_requests_internal(),
                    timeout=self.connection_timeout
                )
                return True
                
            except asyncio.TimeoutError:
                elapsed = time.time() - operation_start_time
                raise self.error(
                    UtecBleTimeoutError(
                        f"Device operation timed out after {elapsed:.1f}s "
                        f"(limit: {self.connection_timeout}s) for {self.name}({self.mac_uuid})"
                    )
                )
                
        except Exception as e:
            logger.error(f"({self.mac_uuid}) Request processing failed: {e}")
            return False
        finally:
            self._requests.clear()
            self.is_busy = False

    async def _process_requests_internal(self) -> None:
        """Internal method to process requests without timeout wrapper."""
        client: Optional[BleakClient] = None
        
        try:
            # First try to get device with timeout
            device = await asyncio.wait_for(
                self._get_bledevice(self.mac_uuid),
                timeout=10.0
            )
            
            if not device:
                # If we can't find the device directly, try wakeup if available
                if self.wurx_uuid:
                    logger.debug(f"({self.mac_uuid}) Device not found, attempting wakeup")
                    await asyncio.wait_for(
                        self.async_wakeup_device(),
                        timeout=10.0
                    )
                    # Try again after wakeup
                    device = await asyncio.wait_for(
                        self._get_bledevice(self.mac_uuid),
                        timeout=10.0
                    )
                    
                if not device:
                    raise self.error(
                        UtecBleNotFoundError(
                            f"Could not find device {self.name}({self.mac_uuid}) "
                            f"after scan and wakeup attempts"
                        )
                    )
            
            # Establish connection with timeout
            logger.debug(f"({self.mac_uuid}) Establishing connection...")
            client = await establish_connection(
                client_class=BleakClient,
                device=device,
                name=self.mac_uuid,
                max_attempts=2,  # Reduced attempts to fail faster
                ble_device_callback=self._brc_get_lock_device,
            )
            
            logger.debug(f"({self.mac_uuid}) Connection established, getting shared key...")
            
            # Get shared key with timeout
            try:
                aes_key = await asyncio.wait_for(
                    UtecBleDeviceKey.get_shared_key(client=client, device=self),
                    timeout=self.operation_timeout
                )
            except asyncio.TimeoutError:
                raise self.error(
                    UtecBleTimeoutError(
                        f"Shared key exchange timed out after {self.operation_timeout}s "
                        f"for {self.name}({self.mac_uuid})"
                    )
                )

            # Process all requests with individual timeouts
            for i, request in enumerate(self._requests[:]):
                if not request.sent or not request.response.completed:
                    logger.debug(f"({self.mac_uuid}) Sending request {i+1}/{len(self._requests)}: {request.command.name}")
                    
                    request.aes_key = aes_key
                    request.device = self
                    request.sent = True
                    
                    try:
                        # Each request gets its own timeout
                        await asyncio.wait_for(
                            request._get_response(client),
                            timeout=self.operation_timeout
                        )
                        self._requests.remove(request)
                        logger.debug(f"({self.mac_uuid}) Request {request.command.name} completed successfully")

                    except asyncio.TimeoutError:
                        raise self.error(
                            UtecBleTimeoutError(
                                f"Request {request.command.name} timed out after {self.operation_timeout}s "
                                f"for {self.name}({self.mac_uuid})"
                            )
                        )
                    except Exception as e:
                        raise self.error(
                            UtecBleDeviceError(
                                f"Request {request.command.name} failed for {self.name}({self.mac_uuid}): {str(e)}"
                            )
                        ) from None

        finally:
            if client:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    logger.debug(f"({self.mac_uuid}) Disconnected from device")
                except Exception as e:
                    logger.debug(f"({self.mac_uuid}) Error during disconnect: {e}")

    async def _get_bledevice(self, address: str) -> Optional[BLEDevice]:
        """Improved method to get BLE device with caching and retry.
        
        Args:
            address: Device MAC address.
            
        Returns:
            BLE device or None if not found.
        """
        if not address:
            return None
            
        # Normalize address format
        address = address.upper()
        
        # Check cache first
        current_time = time.time()
        if address in self._scan_cache:
            cache_time, device = self._scan_cache[address]
            if current_time - cache_time < self._scan_cache_ttl:
                self.debug(f"Using cached device: {address}")
                return device
        
        # If callback is provided, use it first
        if self.async_bledevice_callback:
            try:
                device = await asyncio.wait_for(
                    self.async_bledevice_callback(address),
                    timeout=5.0
                )
                if device:
                    # Update cache and return
                    self._scan_cache[address] = (current_time, device)
                    return device
            except asyncio.TimeoutError:
                logger.debug(f"Device callback timed out for {address}")
            except Exception as e:
                logger.debug(f"Device callback failed for {address}: {e}")
        
        # Otherwise use our own improved scanning method
        async with self._scanner_lock:
            # If we recently did a scan, wait a bit to let Bluetooth settle
            if current_time - self._last_scan_time < 2:
                await asyncio.sleep(2)
            
            # Try discovery method first (more reliable)
            try:
                self.debug(f"Scanning for device: {address}")
                
                # Use shorter timeout for discovery to fail faster
                all_devices = await asyncio.wait_for(
                    BleakScanner.discover(timeout=config.ble_scan_timeout),
                    timeout=config.ble_scan_timeout + 5
                )
                
                # Look through discovered devices
                for device in all_devices:
                    if device.address.upper() == address.upper():
                        self.debug(f"Found device in discovery: {address}")
                        
                        # Update cache
                        self._scan_cache[address] = (current_time, device)
                        self._last_scan_time = current_time
                        
                        return device
                
                # If device not found via discovery, try direct method as fallback
                try:
                    device = await asyncio.wait_for(
                        get_device(address),
                        timeout=5.0
                    )
                    if device:
                        self.debug(f"Found device via direct lookup: {address}")
                        
                        # Update cache
                        self._scan_cache[address] = (current_time, device)
                        self._last_scan_time = current_time
                        
                        return device
                except asyncio.TimeoutError:
                    self.debug(f"Direct device lookup timed out for {address}")
                except Exception as e:
                    self.debug(f"Direct device lookup failed: {e}")
            
            except asyncio.TimeoutError:
                self.debug(f"Device discovery timed out for {address}")
            except Exception as e:
                self.debug(f"Error during device search: {e}")
                
                # For the specific "operation in progress" error, wait longer
                if "Operation already in progress" in str(e):
                    self.debug("Detected scan in progress, waiting...")
                    await asyncio.sleep(5)  # Reduced wait time
                    
                    # Try one more time after waiting
                    try:
                        device = await asyncio.wait_for(
                            get_device(address),
                            timeout=5.0
                        )
                        if device:
                            # Update cache
                            self._scan_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            
                            return device
                    except Exception:
                        pass
            
            self._last_scan_time = current_time
            return None

    async def _brc_get_lock_device(self) -> Optional[BLEDevice]:
        """Callback for bleak_retry_connector to get the lock device.
        
        Returns:
            Lock BLE device or None if not found.
        """
        return await self._get_bledevice(self.mac_uuid)

    async def _brc_get_wurx_device(self) -> Optional[BLEDevice]:
        """Callback for bleak_retry_connector to get the wurx device.
        
        Returns:
            Wurx BLE device or None if not found.
        """
        return await self._get_bledevice(self.wurx_uuid) if self.wurx_uuid else None

    async def async_wakeup_device(self) -> None:
        """Attempt to wake up the device using the wake-up receiver with timeout."""
        if not self.wurx_uuid:
            return
            
        try:
            device = await asyncio.wait_for(
                self._get_bledevice(self.wurx_uuid),
                timeout=10.0
            )
            if not device:
                raise BleakNotFoundError(f"Wake-up receiver {self.wurx_uuid} not found")
                
            wclient: BleakClient = await establish_connection(
                client_class=BleakClient,
                device=device,
                name=self.wurx_uuid,
                max_attempts=2,
                ble_device_callback=self._brc_get_wurx_device,
            )
            self.debug(f"({self.mac_uuid}) Wake-up receiver {self.wurx_uuid} connected.")
            
            # Wait a moment for the wake-up signal to take effect
            await asyncio.sleep(2)
            
            # Clean up connection
            await wclient.disconnect()
            
        except asyncio.TimeoutError:
            self.debug(f"({self.mac_uuid}) Wake-up attempt timed out")
        except Exception as e:
            self.debug(f"({self.mac_uuid}) Wake-up attempt failed: {str(e)}")
            # Continue anyway, as we might still be able to connect directly


class UtecBleRequest(BaseBleRequest):
    """Request to send to a U-tec BLE device with timeout protection."""

    def __init__(
        self,
        command: BLECommandCode,
        device: Optional[UtecBleDevice] = None,
        data: bytes = bytes(),
        auth_required: bool = False,
    ):
        """Initialize a BLE request.
        
        Args:
            command: Command to send.
            device: Device to send the command to.
            data: Data to send with the command.
            auth_required: Whether authentication is required.
        """
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

    def _append_data(self, data: bytes) -> None:
        """Append data to the request.
        
        Args:
            data: Data to append.
        """
        data_len = len(data)
        self.buffer[self._write_pos : self._write_pos + data_len] = data
        self._write_pos += data_len

    def _append_auth(self, uid: str, password: str = "") -> None:
        """Append authentication data to the request.
        
        Args:
            uid: User ID.
            password: User password.
        """
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
        """Get the request package.
        
        Returns:
            Request package.
        """
        return self.buffer[: self._write_pos]

    def encrypted_package(self, aes_key: bytes) -> bytearray:
        """Get the encrypted request package.
        
        Args:
            aes_key: AES key to use for encryption.
            
        Returns:
            Encrypted request package.
        """
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
        return pkg

    async def _get_response(self, client: BleakClient) -> None:
        """Get the response from the device with timeout protection.
        
        Args:
            client: BLE client to use.
            
        Raises:
            Exception: If there's an error getting the response.
        """
        if not self.device or not self.aes_key:
            raise ValueError("Device and AES key must be set before getting response")
            
        self.response = UtecBleResponse(self, self.device)
        
        try:
            # Start notifications
            await client.start_notify(self.uuid, self.response._receive_write_response)
            
            # Send the request
            await client.write_gatt_char(
                self.uuid, self.encrypted_package(self.aes_key)
            )
            
            # Wait for response with timeout (handled by caller)
            await self.response.response_completed.wait()
            
        except Exception as e:
            if self.device:
                raise self.device.error(e)
            else:
                raise
        finally:
            try:
                await client.stop_notify(self.uuid)
            except Exception as e:
                # Ignore errors during cleanup
                logger.debug(f"Error stopping notifications: {e}")


class UtecBleResponse:
    """Response from a U-tec BLE device."""

    def __init__(self, request: UtecBleRequest, device: UtecBleDevice):
        """Initialize a BLE response.
        
        Args:
            request: Request that triggered this response.
            device: Device that sent the response.
        """
        self.buffer = bytearray()
        self.request = request
        self.response_completed = asyncio.Event()
        self.device = device
        self.completed = False

    async def _receive_write_response(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Receive a write response from the device.
        
        Args:
            sender: Characteristic that sent the data.
            data: Data received.
            
        Raises:
            Exception: If there's an error processing the response.
        """
        try:
            if not self.request.aes_key:
                raise ValueError("AES key must be set before receiving response")
                
            self._append(data, bytearray(self.request.aes_key))
            if self.completed and self.is_valid:
                await self._read_response()
                self.response_completed.set()
        except Exception as e:
            try:
                e.add_note(f"({self.device.mac_uuid}) Error receiving write response.")
            except AttributeError:
                # Python < 3.11 doesn't have add_note
                pass
            self.response_completed.set()  # Ensure we don't hang on errors
            raise self.device.error(e)

    def reset(self) -> None:
        """Reset the response."""
        self.buffer = bytearray(0)
        self.completed = False

    def _append(self, barr: bytearray, aes_key: bytearray) -> None:
        """Append data to the response.
        
        Args:
            barr: Data to append.
            aes_key: AES key to use for decryption.
        """
        f495iv = bytearray(16)
        cipher = AES.new(aes_key, AES.MODE_CBC, f495iv)
        output = cipher.decrypt(barr)

        if (self.length > 0 and self.buffer[0] == 0x7F) or output[0] == 0x7F:
            self.buffer += output
            
        # Update completed flag
        self.completed = True if self.length > 3 and self.length >= self.package_len else False

    def _parameter(self, index: int) -> Optional[bytearray]:
        """Get a parameter from the response.
        
        Args:
            index: Parameter index.
            
        Returns:
            Parameter data or None if not found.
        """
        data_len = self.data_len
        if data_len < 3:
            return None

        param_size = (data_len - 2) - index
        bArr2 = bytearray([0] * param_size)
        bArr2[:] = self.buffer[index + 4 : index + 4 + param_size]

        return bytearray(bArr2)

    @property
    def is_valid(self) -> bool:
        """Check if the response is valid.
        
        Returns:
            True if the response is valid.
        """
        cmd = self.command
        return (
            True
            if (self.completed and cmd and isinstance(cmd, BleResponseCode))
            else False
        )

    @property
    def length(self) -> int:
        """Get the response length.
        
        Returns:
            Response length.
        """
        return len(self.buffer)

    @property
    def data_len(self) -> int:
        """Get the data length.
        
        Returns:
            Data length.
        """
        return (
            int.from_bytes(self.buffer[1:3], byteorder="little")
            if self.length > 3
            else 0
        )

    @property
    def package_len(self) -> int:
        """Get the package length.
        
        Returns:
            Package length.
        """
        return self.data_len + 4 if self.length > 3 else 0

    @property
    def package(self) -> bytearray:
        """Get the response package.
        
        Returns:
            Response package.
        """
        return self.buffer[: self.package_len - 1]

    @property
    def command(self) -> Union[BleResponseCode, None]:
        """Get the response command.
        
        Returns:
            Response command or None if not completed.
        """
        return BleResponseCode(self.buffer[3]) if self.completed else None

    @property
    def success(self) -> bool:
        """Check if the response was successful.
        
        Returns:
            True if the response was successful.
        """
        return True if self.completed and self.buffer[4] == 0 else False

    @property
    def data(self) -> bytearray:
        """Get the response data.
        
        Returns:
            Response data.
        """
        if self.is_valid:
            return self.buffer[5 : self.data_len + 5]
        else:
            return bytearray()

    async def _read_response(self) -> None:
        """Read the response and update the device state."""
        try:
            self.device.debug(
                "(%s) Response %s (%s): %s",
                self.device.mac_uuid,
                self.command.name,
                "Success" if self.success else "Failed",
                self.package.hex(),
            )

            if self.command == BleResponseCode.GET_LOCK_STATUS:
                self.device.lock_mode = int(self.data[0])
                self.device.bolt_status = int(self.data[1])
                self.device.debug(
                    f"({self.device.mac_uuid}) lock:{self.device.lock_mode} ({LOCK_MODE[self.device.lock_mode]}) |  bolt:{self.device.bolt_status} ({BOLT_STATUS[self.device.bolt_status]})"
                )

            elif self.command == BleResponseCode.SET_LOCK_STATUS:
                self.device.lock_mode = self.data[0]
                self.device.debug(
                    f"({self.device.mac_uuid}) workmode:{self.device.lock_mode}"
                )

            elif self.command == BleResponseCode.GET_BATTERY:
                self.device.battery = int(self.data[0])
                self.device.debug(
                    f"({self.device.mac_uuid}) power level:{self.device.battery}, {BATTERY_LEVEL[self.device.battery]}"
                )

            elif self.command == BleResponseCode.GET_AUTOLOCK:
                self.device.autolock_time = bytes_to_int2(self.data[:2])
                self.device.debug(
                    "(%s) autolock:%s", self.device.mac_uuid, self.device.autolock_time
                )

            elif self.command == BleResponseCode.SET_AUTOLOCK:
                if self.success:
                    self.device.autolock_time = bytes_to_int2(self.data[:2])
                    self.device.debug(
                        "(%s) autolock:%s",
                        self.device.mac_uuid,
                        self.device.autolock_time,
                    )

            elif self.command == BleResponseCode.GET_BATTERY:
                self.device.battery = int(self.data[0])
                self.device.debug(
                    f"({self.device.mac_uuid}) power level:{self.device.battery}, {BATTERY_LEVEL[self.device.battery]}"
                )

            elif self.command == BleResponseCode.GET_SN:
                self.device.sn = self.data.decode("ISO8859-1")
                self.device.debug(
                    "(%s) serial:%s", self.device.mac_uuid, self.device.sn
                )

            elif self.command == BleResponseCode.GET_MUTE:
                self.device.mute = bool(self.data[0])
                self.device.debug(f"({self.device.mac_uuid}) mute:{self.device.mute}")

            elif self.command == BleResponseCode.SET_WORK_MODE:
                if self.success:
                    self.device.lock_mode = self.data[0]
                    self.device.debug(
                        f"({self.device.mac_uuid}) workmode:{self.device.lock_mode}"
                    )

            elif self.command == BleResponseCode.UNLOCK:
                self.device.debug(
                    f"({self.device.mac_uuid}) {self.device.name} - Unlocked."
                )

            elif self.command == BleResponseCode.BOLT_LOCK:
                self.device.debug(
                    f"({self.device.mac_uuid}) {self.device.name} - Bolt Locked"
                )

            elif self.command == BleResponseCode.LOCK_STATUS:
                self.device.lock_status = int(self.data[0])
                self.device.bolt_status = int(self.data[1])
                self.device.debug(
                    f"({self.device.mac_uuid}) lock:{self.device.lock_status} |  bolt:{self.device.bolt_status}"
                )
                if self.length > 16:
                    self.device.battery = int(self.data[2])
                    self.device.lock_mode = int(self.data[3])
                    self.device.mute = bool(self.data[4])
                    self.device.debug(
                        f"({self.device.mac_uuid}) power level:{self.device.battery} | mute:{self.device.mute} | mode:{self.device.lock_mode}"
                    )

            self.device.debug(
                f"({self.device.mac_uuid}) Command Completed - {self.command.name}"
            )

        except Exception as e:
            error_msg = f"({self.device.mac_uuid}) Error updating lock data"
            if hasattr(self, 'command') and self.command:
                error_msg += f" ({self.command.name})"
            error_msg += f": {e}"
            self.device.error(Exception(error_msg))


class UtecBleDeviceKey:
    """Helper class for handling BLE device keys with timeout protection."""

    @staticmethod
    async def get_shared_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the shared key for a device with timeout protection.
        
        Args:
            client: BLE client.
            device: Device to get the key for.
            
        Returns:
            Shared key.
            
        Raises:
            NotImplementedError: If the encryption method is not supported.
        """
        if client.services.get_characteristic(DeviceKeyUUID.STATIC.value):
            key_data = await client.read_gatt_char(DeviceKeyUUID.STATIC.value)
            return bytearray(b"Anviz.ut") + key_data
        elif client.services.get_characteristic(DeviceKeyUUID.MD5.value):
            return await UtecBleDeviceKey.get_md5_key(client, device)
        elif client.services.get_characteristic(DeviceKeyUUID.ECC.value):
            return await UtecBleDeviceKey.get_ecc_key(client, device)
        else:
            raise NotImplementedError(f"({client.address}) Unknown encryption.")

    @staticmethod
    async def get_ecc_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the ECC key for a device with timeout protection.
        
        Args:
            client: BLE client.
            device: Device to get the key for.
            
        Returns:
            ECC key.
            
        Raises:
            Exception: If there's an error getting the key.
        """
        try:
            private_key = SigningKey.generate(curve=SECP128r1)
            received_pubkey = []
            public_key = private_key.get_verifying_key()  # type: ignore # noqa
            pub_x = public_key.pubkey.point.x().to_bytes(16, "little")  # type: ignore # noqa
            pub_y = public_key.pubkey.point.y().to_bytes(16, "little")  # type: ignore # noqa

            notification_event = asyncio.Event()

            def notification_handler(sender, data):
                # logger.debug(f"({client._mac_address}) ECC data:{data.hex()}")
                received_pubkey.append(data)
                if len(received_pubkey) == 2:
                    notification_event.set()

            await client.start_notify(DeviceKeyUUID.ECC.value, notification_handler)
            await client.write_gatt_char(DeviceKeyUUID.ECC.value, pub_x)
            await client.write_gatt_char(DeviceKeyUUID.ECC.value, pub_y)
            
            # Wait for ECC exchange with timeout
            await asyncio.wait_for(notification_event.wait(), timeout=10.0)

            await client.stop_notify(DeviceKeyUUID.ECC.value)

            rec_key_point = Point(
                SECP128r1.curve,
                int.from_bytes(received_pubkey[0], "little"),
                int.from_bytes(received_pubkey[1], "little"),
            )
            shared_point = private_key.privkey.secret_multiplier * rec_key_point  # type: ignore # noqa
            shared_key = int.to_bytes(shared_point.x(), 16, "little")
            device.debug(f"({client.address}) ECC key updated.")
            return shared_key
        except asyncio.TimeoutError:
            raise device.error(
                UtecBleTimeoutError(f"({client.address}) ECC key exchange timed out")
            )
        except Exception as e:
            try:
                e.add_note(f"({client.address}) Failed to update ECC key: {e}")
            except AttributeError:
                # Python < 3.11 doesn't have add_note
                pass
            raise device.error(e)

    @staticmethod
    async def get_md5_key(client: BleakClient, device: UtecBleDevice) -> bytes:
        """Get the MD5 key for a device with timeout protection.
        
        Args:
            client: BLE client.
            device: Device to get the key for.
            
        Returns:
            MD5 key.
            
        Raises:
            Exception: If there's an error getting the key.
        """
        try:
            secret = await client.read_gatt_char(DeviceKeyUUID.MD5.value)

            device.debug(f"({client.address}) Secret: {secret.hex()}")

            if len(secret) != 16:
                raise device.error(
                    ValueError(f"({client.address}) Expected secret of length 16.")
                )

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
                m = hashlib.md5()
                m.update(result)
                result = m.digest()

            device.debug(f"({client.address}) MD5 key:{result.hex()}")
            return result

        except Exception as e:
            try:
                e.add_note(f"({client.address}) Failed to update MD5 key: {e}")
            except AttributeError:
                # Python < 3.11 doesn't have add_note
                pass
            raise device.error(e)