"""U-tec API client implementation."""

import asyncio
import json
import secrets
import string
import time
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession, ClientResponse

from ..abstract import BaseUtecClient
from ..factory import DeviceFactory
from ..ble.lock import UtecBleLock
from ..config import config
from ..exceptions import InvalidResponse, InvalidCredentials

# Get logger for this module
logger = config.get_logger("api.client")

# API Constants
APP_ID = "13ca0de1e6054747c44665ae13e36c2c"
CLIENT_ID = "1375ac0809878483ee236497d57f371f"
TIME_ZONE = "-4"
VERSION = "V3.2"
USER_AGENT = "U-tec/2.1.14 (iPhone; iOS 15.1; Scale/3.00)"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; U-tec-Python/1.0)",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}


class UtecClient(BaseUtecClient):
    """U-tec API client."""

    def __init__(
        self, email: str, password: str, session: Optional[ClientSession] = None
    ) -> None:
        """Initialize U-tec client using the user provided email and password.

        Args:
            email: User email.
            password: User password.
            session: aiohttp.ClientSession.
        """
        self.mobile_uuid: Optional[str] = None
        self.email: str = email
        self.password: str = password
        self.session = session
        self._session_owned = session is None  # Track if we own the session
        self.token: Optional[str] = None
        self.timeout: int = config.api_timeout
        self.addresses: List[Dict[str, Any]] = []
        self.rooms: List[Dict[str, Any]] = []
        self.devices: List[Dict[str, Any]] = []
        self._generate_random_mobile_uuid(32)

    def _generate_random_mobile_uuid(self, length: int) -> None:
        """Generates a random mobile device UUID.
        
        Args:
            length: Length of the UUID.
        """
        letters_nums = string.ascii_uppercase + string.digits
        self.mobile_uuid = "".join(secrets.choice(letters_nums) for i in range(length))

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the client session if we own it."""
        if self.session and self._session_owned and not self.session.closed:
            await self.session.close()
            self.session = None
            logger.debug("Closed HTTP session")

    async def _ensure_session(self) -> ClientSession:
        """Ensure we have a session available."""
        if not self.session or self.session.closed:
            self.session = ClientSession()
            self._session_owned = True
            logger.debug("Created new HTTP session")
        return self.session

    async def _fetch_token(self) -> None:
        """Fetch the token that is used to log into the app.
        
        Raises:
            InvalidResponse: If the response is invalid.
        """
        logger.info("Attempting to fetch authentication token...")
        
        url = "https://uemc.u-tec.com/app/token"
        headers = HEADERS
        data = {
            "appid": APP_ID,
            "clientid": CLIENT_ID,
            "timezone": TIME_ZONE,
            "uuid": self.mobile_uuid,
            "version": VERSION,
        }

        try:
            response = await self._post(url, headers, data)
            if response is None:
                logger.error("Received None response when fetching token")
                raise InvalidResponse("Null response when fetching token")
                
            if "error" in response and response["error"]:
                logger.error(f"Error fetching token: {response.get('error')}")
                raise InvalidResponse(f"Error fetching token: {response.get('error')}")
                
            if "data" not in response or "token" not in response["data"]:
                logger.error(f"Token missing from response: {response}")
                raise InvalidResponse("Missing token in response")

            self.token = response["data"]["token"]
            logger.info("Successfully obtained token")
        except Exception as e:
            logger.error(f"Exception during token fetch: {str(e)}")
            raise

    async def _login(self) -> None:
        """Log in to account using previous token obtained.
        
        Raises:
            InvalidResponse: If the response is invalid.
            InvalidCredentials: If the credentials are invalid.
        """
        logger.info(f"Attempting to login with email: {self.email}")
        
        if not self.token:
            logger.error("No token available for login")
            raise InvalidResponse("No token available for login. Call _fetch_token first.")

        url = "https://cloud.u-tec.com/app/user/login"
        headers = HEADERS
        auth_data = {
            "email": self.email,
            "timestamp": str(time.time()),
            "password": self.password,
        }
        data = {"data": json.dumps(auth_data), "token": self.token}

        try:
            response = await self._post(url, headers, data)
            if response is None:
                logger.error("Received None response when logging in")
                raise InvalidResponse("Null response when logging in")
                
            if "error" in response and response["error"]:
                logger.error(f"Login failed: {response.get('error')}")
                raise InvalidCredentials(f"Login failed: {response.get('error')}")
                
            logger.info(f"Successfully logged in as {self.email}")
        except Exception as e:
            logger.error(f"Exception during login: {str(e)}")
            raise

    async def _get_addresses(self) -> None:
        """Fetch all addresses associated with an account."""
        logger.info("Fetching addresses...")
        
        url = "https://cloud.u-tec.com/app/address"
        headers = HEADERS
        body_data = {"timestamp": str(time.time())}
        data = {"data": json.dumps(body_data), "token": self.token}

        try:
            response = await self._post(url, headers, data)
            if response is None:
                logger.error("Received None response when fetching addresses")
                return
                
            if "error" in response and response["error"]:
                logger.error(f"Error fetching addresses: {response.get('error')}")
                return
                
            if "data" not in response:
                logger.error(f"No address data in response: {response}")
                return
                
            for address in response["data"]:
                self.addresses.append(address)
                
            logger.info(f"Found {len(self.addresses)} addresses")
        except Exception as e:
            logger.error(f"Exception during address fetch: {str(e)}")
            raise

    async def _get_rooms_at_address(self, address: Dict[str, Any]) -> None:
        """Get all the room IDs within an address.
        
        Args:
            address: Address data.
        """
        logger.info(f"Fetching rooms for address ID: {address.get('id', 'unknown')}")
        
        url = "https://cloud.u-tec.com/app/room"
        headers = HEADERS
        body_data = {"id": address["id"], "timestamp": str(time.time())}
        data = {"data": json.dumps(body_data), "token": self.token}

        try:
            response = await self._post(url, headers, data)
            if response is None:
                logger.error("Received None response when fetching rooms")
                return
                
            if "error" in response and response["error"]:
                logger.error(f"Error fetching rooms: {response.get('error')}")
                return
                
            if "data" not in response:
                logger.error(f"No room data in response: {response}")
                return
                
            for room in response["data"]:
                self.rooms.append(room)
                
            logger.info(f"Found {len(response['data'])} rooms")
        except Exception as e:
            logger.error(f"Exception during room fetch: {str(e)}")
            raise

    async def _get_devices_in_room(self, room: Dict[str, Any]) -> None:
        """Fetches all the devices that are located in a room.
        
        Args:
            room: Room data.
        """
        logger.info(f"Fetching devices for room ID: {room.get('id', 'unknown')}")
        
        url = "https://cloud.u-tec.com/app/device/list"
        headers = HEADERS
        body_data = {"room_id": room["id"], "timestamp": str(time.time())}
        data = {"data": json.dumps(body_data), "token": self.token}

        try:
            response = await self._post(url, headers, data)
            if response is None:
                logger.error("Received None response when fetching devices")
                return
                
            if "error" in response and response["error"]:
                logger.error(f"Error fetching devices: {response.get('error')}")
                return
                
            if "data" not in response:
                logger.error(f"No device data in response: {response}")
                return
                
            for api_device in response["data"]:
                self.devices.append(api_device)
                
            logger.info(f"Found {len(response['data'])} devices")
        except Exception as e:
            logger.error(f"Exception during device fetch: {str(e)}")
            raise

    async def _post(
        self, url: str, headers: Dict[str, str], data: Dict[str, str]
    ) -> Dict[str, Any]:
        """Make POST API call.
        
        Args:
            url: URL to call.
            headers: Headers to send.
            data: Data to send.
            
        Returns:
            Response data.
        """
        logger.debug(f"Making POST request to {url}")
        
        session = await self._ensure_session()

        try:
            async with session.post(
                url, headers=headers, data=data, timeout=self.timeout
            ) as resp:
                if resp.status != 200:
                    logger.error(f"HTTP error: {resp.status} - {await resp.text()}")
                    return {"error": f"HTTP {resp.status}", "message": await resp.text()}
                    
                return await self._response(resp)
        except asyncio.TimeoutError:
            logger.error(f"Request to {url} timed out after {self.timeout} seconds")
            return {"error": "Timeout", "message": f"Request timed out after {self.timeout} seconds"}
        except Exception as e:
            logger.error(f"Unexpected error in request to {url}: {str(e)}")
            return {"error": "UnexpectedError", "message": str(e)}

    @staticmethod
    async def _response(resp: ClientResponse) -> Dict[str, Any]:
        """Return response from API call.
        
        Args:
            resp: Response object.
            
        Returns:
            Response data.
        """
        try:
            response: Dict[str, Any] = await resp.json()
            return response
        except Exception as e:
            logger.error(f"Error parsing response: {str(e)}")
            try:
                # Try to read the text instead
                text = await resp.text()
                logger.error(f"Response text: {text}")
            except:
                logger.error("Could not read response text")
            return {"error": "ResponseParseError", "message": str(e)}

    async def connect(self) -> bool:
        """Connect to the service and authenticate.
        
        Returns:
            True if connection was successful.
        """
        logger.info("Starting connection process...")
        try:
            await self._fetch_token()
            await self._login()
            logger.info("Connection successful")
            return True
        except InvalidResponse as e:
            logger.error(f"Connection failed due to API response error: {str(e)}")
            return False
        except InvalidCredentials as e:
            logger.error(f"Connection failed due to invalid credentials: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Connection failed with unexpected error: {str(e)}")
            return False

    async def sync_devices(self) -> bool:
        """Sync all devices, addresses, and rooms.
        
        Returns:
            True if sync was successful.
        """
        logger.info("Starting device sync...")
        
        # Check if we have a valid token first
        if not self.token:
            logger.error("No authentication token available. Call connect() first.")
            return False
        
        # Clear previous data
        self.addresses = []
        self.rooms = []
        self.devices = []
        
        try:
            await self._get_addresses()
            logger.info(f"Found {len(self.addresses)} addresses")
            
            for address in self.addresses:
                try:
                    await self._get_rooms_at_address(address)
                except Exception as e:
                    logger.error(f"Error getting rooms for address {address.get('id')}: {str(e)}")
                    
            logger.info(f"Found {len(self.rooms)} rooms")
            
            for room in self.rooms:
                try:
                    await self._get_devices_in_room(room)
                except Exception as e:
                    logger.error(f"Error getting devices for room {room.get('id')}: {str(e)}")
                    
            logger.info(f"Found {len(self.devices)} devices")
            return True
        except Exception as e:
            logger.error(f"Device sync failed: {str(e)}")
            return False

    async def get_ble_devices(self, sync: bool = True) -> List[UtecBleLock]:
        """Get all BLE-capable devices.
        
        Args:
            sync: Whether to sync devices first.
            
        Returns:
            List of BLE devices.
        """
        logger.info("Getting BLE devices...")
        
        # Only sync if explicitly requested AND we don't have devices
        if sync and not self.devices:
            if not await self.sync_devices():
                logger.error("Failed to sync devices")
                return []
        elif not self.devices:
            logger.warning("No device data available and sync=False")
            return []
        else:
            logger.debug("Using cached device data")

        devices = []
        for api_device in self.devices:
            try:
                # Use the factory to create the device
                device = DeviceFactory.create_lock_from_json(api_device)
                
                # Check if it has Bluetooth capability
                if hasattr(device.capabilities, 'bluetooth') and device.capabilities.bluetooth:
                    logger.info(f"Found BLE device: {device.name} (Model: {device.model})")
                    devices.append(device)
                else:
                    logger.debug(f"Device does not have Bluetooth capability: {device.name}")
                    
            except KeyError as e:
                logger.error(f"KeyError while processing device: {e}")
                logger.debug(f"Device data that caused error: {api_device}")
            except Exception as e:
                logger.error(f"Error processing device: {str(e)}")

        logger.info(f"Found {len(devices)} BLE-capable devices")
        return devices

    async def get_json(self) -> List[Dict[str, Any]]:
        """Get raw JSON data for all devices.
        
        Returns:
            List of device data.
        """
        await self.sync_devices()
        return self.devices