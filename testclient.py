#!/usr/bin/env python3
import os
import sys
import asyncio
import logging
import argparse
import time
from typing import Optional, List, Dict, Any
import json

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("utec_client")

# Add the current directory to the path to find the utec package
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import the utec library
import utec
from utec import LogLevel
from utec.abstract import BaseLock
from bleak.backends.device import BLEDevice
from bleak import BleakScanner
from bleak.exc import BleakError


class MyUtecClient:
    def __init__(self, email: str, password: str):
        """Initialize the U-tec client with the given credentials."""
        self.email = email
        self.password = password
        
        # Configure the library
        utec.setup(
            log_level=LogLevel.INFO,
            ble_scan_cache_ttl=30.0,
            ble_retry_delay=2.0,
            ble_max_retries=3
        )
        
        self._client = None
        
        # BLE device scanning setup
        self.scanner = BleakScanner()
        self._device_cache = {}
        self._scanner_lock = asyncio.Lock()
        self._last_scan_time = 0
        self._scan_cache_ttl = 30  # seconds

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = utec.UtecClient(self.email, self.password)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.close()

    @property
    def client(self):
        """Get the client instance."""
        if self._client is None:
            raise RuntimeError("Client must be used within an async context manager")
        return self._client

    async def connect_and_sync(self) -> bool:
        """Connect to the U-tec cloud service and sync devices."""
        logger.info("Connecting to U-tec cloud...")
        try:
            if not await self.client.connect():
                return False
            
            logger.info("Syncing devices...")
            return await self.client.sync_devices()
        except Exception as e:
            logger.error(f"Failed to connect or sync: {e}")
            return False

    async def get_locks(self, sync: bool = False) -> List[BaseLock]:
        """Get all locks from the U-tec cloud."""
        try:
            locks = await self.client.get_ble_devices(sync=sync)
            
            if locks:
                logger.info(f"Found {len(locks)} BLE-capable locks")
                
                # Set callbacks for all locks at once
                for lock in locks:
                    lock.async_bledevice_callback = self.async_bledevice_callback
                    lock.error_callback = self.error_callback
            else:
                logger.warning("No BLE-capable locks found in your account")
                
            return locks
        except Exception as e:
            logger.error(f"Failed to get locks: {e}")
            return []

    async def async_bledevice_callback(self, address: str) -> Optional[BLEDevice]:
        """Find a BLE device by address with improved retry logic."""
        if not address:
            return None
            
        address = address.upper()
        current_time = time.time()
        
        # Check cache first
        if address in self._device_cache:
            cache_time, device = self._device_cache[address]
            if current_time - cache_time < self._scan_cache_ttl:
                logger.debug(f"Using cached device: {address}")
                return device
        
        async with self._scanner_lock:
            # Rate limiting
            if current_time - self._last_scan_time < 2:
                await asyncio.sleep(2)
            
            for attempt in range(1, 4):  # max 3 attempts
                try:
                    logger.info(f"Scanning for {address} (attempt {attempt}/3)")
                    
                    # Primary method: discover all devices
                    all_devices = await BleakScanner.discover(timeout=8.0)
                    for device in all_devices:
                        if device.address.upper() == address:
                            logger.info(f"Found device: {address} ({device.name})")
                            self._device_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            return device
                    
                    # Fallback: direct lookup
                    try:
                        device = await self.scanner.find_device_by_address(address)
                        if device:
                            logger.info(f"Found device via direct lookup: {address}")
                            self._device_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            return device
                    except Exception as e:
                        logger.debug(f"Direct lookup failed: {e}")
                    
                    if attempt < 3:
                        await asyncio.sleep(3)
                        
                except Exception as e:
                    logger.error(f"Scan attempt {attempt} failed: {e}")
                    
                    if "Operation already in progress" in str(e):
                        logger.info("BLE operation in progress, waiting...")
                        await asyncio.sleep(10)
                        self.scanner = BleakScanner()  # Reset scanner
                    elif attempt < 3:
                        await asyncio.sleep(3)
            
            self._last_scan_time = current_time
            logger.error(f"Failed to find device {address} after 3 attempts")
            return None

    def error_callback(self, device_id: str, error: Exception) -> None:
        """Handle errors from the lock."""
        logger.error(f"Lock error ({device_id}): {error}")

    def _format_lock_info(self, info: Dict[str, Any]) -> str:
        """Format lock information for display."""
        return f"""Lock: {info['name']}
  MAC Address: {info['mac_address']}
  Model: {info['model']}
  Battery: {info['battery']}%
  Lock Status: {info['lock_status']}
  Bolt Status: {info['bolt_status']}
  Lock Mode: {info['lock_mode']}
  Autolock Time: {info['autolock_time']} seconds
  Mute: {info['mute']}
  Serial Number: {info['serial_number']}"""

    async def get_lock_info(self, lock: BaseLock) -> Dict[str, Any]:
        """Get information about a lock."""
        logger.info(f"Getting status for: {lock.name}")
        await lock.async_update_status()
        
        return {
            "name": lock.name,
            "mac_address": lock.mac_uuid,
            "model": lock.model,
            "battery": lock.battery,
            "lock_status": getattr(lock, "lock_status", -1),
            "bolt_status": lock.bolt_status,
            "lock_mode": lock.lock_mode,
            "autolock_time": lock.autolock_time,
            "mute": lock.mute,
            "serial_number": lock.sn
        }

    async def _execute_lock_action(self, lock: BaseLock, action: str, *args) -> bool:
        """Execute a lock action with unified error handling."""
        logger.info(f"{action.title()} lock: {lock.name}")
        try:
            if action == "unlock":
                await lock.async_unlock()
            elif action == "lock":
                await lock.async_lock()
            elif action == "set_autolock":
                await lock.async_set_autolock(args[0])
            else:
                raise ValueError(f"Unknown action: {action}")
            return True
        except Exception as e:
            logger.error(f"Failed to {action} lock {lock.name}: {e}")
            return False

    async def unlock_lock(self, lock: BaseLock) -> bool:
        """Unlock a lock."""
        return await self._execute_lock_action(lock, "unlock")

    async def lock_lock(self, lock: BaseLock) -> bool:
        """Lock a lock."""
        return await self._execute_lock_action(lock, "lock")

    async def set_autolock(self, lock: BaseLock, seconds: int) -> bool:
        """Set the autolock time for a lock."""
        return await self._execute_lock_action(lock, "set_autolock", seconds)


def find_lock_by_name(locks: List[BaseLock], name: str) -> Optional[BaseLock]:
    """Find a lock by name (case-insensitive)."""
    return next((l for l in locks if l.name.lower() == name.lower()), None)


async def display_lock_info(client: MyUtecClient, lock: BaseLock) -> None:
    """Display information for a single lock."""
    try:
        info = await client.get_lock_info(lock)
        print(client._format_lock_info(info))
    except Exception as e:
        logger.error(f"Failed to get info for {lock.name}: {e}")


async def display_all_locks_info(client: MyUtecClient, locks: List[BaseLock]) -> None:
    """Display information for all locks."""
    for i, lock in enumerate(locks):
        await display_lock_info(client, lock)
        if i < len(locks) - 1:  # Add separator between locks
            print()


async def run_client_operations(email: str, password: str, args):
    """Run client operations within proper context manager."""
    async with MyUtecClient(email, password) as client:
        # Single connection and sync
        if not await client.connect_and_sync():
            logger.error("Failed to connect to U-tec cloud or sync devices")
            return
        
        # Get locks (use cached data, don't sync again)
        locks = await client.get_locks(sync=False)  # Don't sync again!
        if not locks:
            logger.error("No locks found or accessible")
            return
        
        # Handle different operations
        if args.all:
            await display_all_locks_info(client, locks)
            
        elif args.name:
            lock = find_lock_by_name(locks, args.name)
            if not lock:
                logger.error(f"Lock '{args.name}' not found. Available: {[l.name for l in locks]}")
                return
            
            # Execute specific action
            if args.info:
                await display_lock_info(client, lock)
            elif args.unlock:
                success = await client.unlock_lock(lock)
                print(f"{'Successfully unlocked' if success else 'Failed to unlock'} {lock.name}")
            elif args.lock:
                success = await client.lock_lock(lock)
                print(f"{'Successfully locked' if success else 'Failed to lock'} {lock.name}")
            elif args.autolock is not None:
                success = await client.set_autolock(lock, args.autolock)
                action = f"set autolock to {args.autolock}s for"
                print(f"{'Successfully' if success else 'Failed to'} {action} {lock.name}")
        else:
            # Default: show available locks and info for single lock
            print(f"Available locks:")
            for lock in locks:
                print(f"  {lock.name} ({lock.model})")
            
            if len(locks) == 1:
                print()  # Add spacing
                await display_lock_info(client, locks[0])


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="U-tec Smart Lock Client")
    parser.add_argument("--email", "-e", help="U-tec account email")
    parser.add_argument("--password", "-p", help="U-tec account password")
    parser.add_argument("--config", "-c", help="Config file with email and password")
    
    # Actions
    parser.add_argument("--unlock", "-u", action="store_true", help="Unlock specified lock")
    parser.add_argument("--lock", "-l", action="store_true", help="Lock specified lock")
    parser.add_argument("--info", "-i", action="store_true", help="Get info for specified lock")
    parser.add_argument("--all", action="store_true", help="Get info for all locks")
    parser.add_argument("--autolock", "-a", type=int, help="Set autolock time in seconds")
    
    # Options
    parser.add_argument("--name", "-n", help="Lock name for targeted actions")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Configure logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        utec.setup(log_level=LogLevel.DEBUG)
    
    # Get credentials
    email, password = None, None
    if args.config:
        try:
            with open(args.config, "r") as f:
                config_data = json.load(f)
                email = config_data.get("email", "")
                password = config_data.get("password", "")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}")
            return
    else:
        email, password = args.email, args.password
    
    if not email or not password:
        logger.error("Email and password are required (via args or config file)")
        return
    
    # Run operations
    await run_client_operations(email, password, args)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()