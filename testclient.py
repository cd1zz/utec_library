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
            ble_scan_cache_ttl=30.0,  # Match previous cache TTL
            ble_retry_delay=2.0,      # Slightly increased retry delay
            ble_max_retries=3         # Match your previous max attempts
        )
        
        # Create the client - will be set properly in async context manager
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

    async def connect(self) -> bool:
        """Connect to the U-tec cloud service."""
        logger.info("Connecting to U-tec cloud...")
        try:
            return await self.client.connect()
        except Exception as e:
            logger.error(f"Failed to connect to U-tec cloud: {e}")
            return False

    async def get_locks(self) -> List[BaseLock]:
        """Get all locks from the U-tec cloud."""
        try:
            locks = await self.client.get_ble_devices()
            logger.info(f"Found {len(locks)} locks")
            
            # Set the BLE device callback for each lock
            for lock in locks:
                lock.async_bledevice_callback = self.async_bledevice_callback
                lock.error_callback = self.error_callback
                
            return locks
        except Exception as e:
            logger.error(f"Failed to get locks: {e}")
            return []

    async def async_bledevice_callback(self, address: str) -> Optional[BLEDevice]:
        """Find a BLE device by address with improved retry logic."""
        if not address:
            return None
            
        # Normalize address format
        address = address.upper()
        
        # Check cache first
        current_time = time.time()
        if address in self._device_cache:
            cache_time, device = self._device_cache[address]
            if current_time - cache_time < self._scan_cache_ttl:
                logger.debug(f"Using cached device: {address}")
                return device
        
        # Use a lock to prevent multiple concurrent scans
        async with self._scanner_lock:
            # If we recently did a scan, wait a bit
            if current_time - self._last_scan_time < 2:
                await asyncio.sleep(2)
            
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Scanning for device: {address}, attempt {attempt}/{max_attempts}")
                    
                    # Use discover which gets all devices at once (more reliable)
                    all_devices = await BleakScanner.discover(timeout=8.0)
                    
                    # Check if our device is in the results
                    for device in all_devices:
                        if device.address.upper() == address.upper():
                            logger.info(f"Found device: {address} ({device.name})")
                            
                            # Update cache
                            self._device_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            
                            return device
                    
                    # If not found via discover, try direct lookup as fallback
                    try:
                        logger.debug(f"Trying direct device lookup for: {address}")
                        device = await self.scanner.find_device_by_address(address)
                        if device:
                            logger.info(f"Found device by direct lookup: {address}")
                            
                            # Update cache
                            self._device_cache[address] = (current_time, device)
                            self._last_scan_time = current_time
                            
                            return device
                    except Exception as e:
                        logger.debug(f"Direct lookup failed: {str(e)}")
                    
                    logger.warning(f"Device {address} not found in attempt {attempt}")
                    
                    # Wait before next attempt
                    if attempt < max_attempts:
                        await asyncio.sleep(3)
                        
                except Exception as e:
                    logger.error(f"Error in scan attempt {attempt}: {str(e)}")
                    
                    # For operation in progress errors, wait longer
                    if "Operation already in progress" in str(e):
                        logger.info("Detected 'Operation already in progress', waiting...")
                        await asyncio.sleep(10)
                        
                        # Try to reset the scanner
                        try:
                            self.scanner = BleakScanner()
                        except Exception:
                            pass
                            
                    elif attempt < max_attempts:
                        await asyncio.sleep(3)
            
            self._last_scan_time = current_time
            logger.error(f"Failed to find device {address} after {max_attempts} attempts")
            return None

    def error_callback(self, device_id: str, error: Exception) -> None:
        """Handle errors from the lock."""
        logger.error(f"Lock error ({device_id}): {error}")

    async def get_lock_info(self, lock: BaseLock) -> Dict[str, Any]:
        """Get information about a lock."""
        logger.info(f"Getting information for lock: {lock.name}")
        try:
            await lock.async_update_status()
            
            return {
                "name": lock.name,
                "mac_address": lock.mac_uuid,
                "model": lock.model,
                "battery": lock.battery,
                "lock_status": lock.lock_status if hasattr(lock, "lock_status") else -1,
                "bolt_status": lock.bolt_status,
                "lock_mode": lock.lock_mode,
                "autolock_time": lock.autolock_time,
                "mute": lock.mute,
                "serial_number": lock.sn
            }
        except Exception as e:
            logger.error(f"Failed to get lock info: {lock.name}")
            logger.error(f"Error: {e}")
            raise

    async def unlock_lock(self, lock: BaseLock) -> bool:
        """Unlock a lock."""
        logger.info(f"Unlocking lock: {lock.name}")
        try:
            await lock.async_unlock()
            return True
        except Exception as e:
            logger.error(f"Failed to unlock lock: {lock.name}")
            logger.error(f"Error: {e}")
            return False

    async def lock_lock(self, lock: BaseLock) -> bool:
        """Lock a lock."""
        logger.info(f"Locking lock: {lock.name}")
        try:
            await lock.async_lock()
            return True
        except Exception as e:
            logger.error(f"Failed to lock lock: {lock.name}")
            logger.error(f"Error: {e}")
            return False

    async def set_autolock(self, lock: BaseLock, seconds: int) -> bool:
        """Set the autolock time for a lock."""
        logger.info(f"Setting autolock time for lock: {lock.name} to {seconds} seconds")
        try:
            await lock.async_set_autolock(seconds)
            return True
        except Exception as e:
            logger.error(f"Failed to set autolock time for lock: {lock.name}")
            logger.error(f"Error: {e}")
            return False

    async def reset_bluetooth(self) -> None:
        """Reset the Bluetooth system if needed."""
        logger.info("Resetting Bluetooth scanner")
        try:
            # Create a new scanner
            self.scanner = BleakScanner()
            # Clear device cache
            self._device_cache = {}
            self._last_scan_time = 0
        except Exception as e:
            logger.error(f"Failed to reset Bluetooth scanner: {e}")


async def run_client_operations(email: str, password: str, args):
    """Run client operations within proper context manager."""
    async with MyUtecClient(email, password) as client:
        # Connect to U-tec cloud
        if not await client.connect():
            logger.error("Failed to connect to U-tec cloud")
            return
        
        # Get locks
        locks = await client.get_locks()
        
        if args.all:
            # Get info for all locks
            for lock in locks:
                try:
                    info = await client.get_lock_info(lock)
                    print(f"Lock: {info['name']}")
                    print(f"  MAC Address: {info['mac_address']}")
                    print(f"  Model: {info['model']}")
                    print(f"  Battery: {info['battery']}%")
                    print(f"  Lock Status: {info['lock_status']}")
                    print(f"  Bolt Status: {info['bolt_status']}")
                    print(f"  Lock Mode: {info['lock_mode']}")
                    print(f"  Autolock Time: {info['autolock_time']} seconds")
                    print(f"  Mute: {info['mute']}")
                    print(f"  Serial Number: {info['serial_number']}")
                    print()
                except Exception:
                    # Error already logged in get_lock_info
                    continue
        elif args.name:
            # Find lock with given name
            lock = next((l for l in locks if l.name.lower() == args.name.lower()), None)
            if not lock:
                logger.error(f"Lock with name '{args.name}' not found")
                return
            
            # Perform action
            if args.info:
                # Get info for lock
                try:
                    info = await client.get_lock_info(lock)
                    print(f"Lock: {info['name']}")
                    print(f"  MAC Address: {info['mac_address']}")
                    print(f"  Model: {info['model']}")
                    print(f"  Battery: {info['battery']}%")
                    print(f"  Lock Status: {info['lock_status']}")
                    print(f"  Bolt Status: {info['bolt_status']}")
                    print(f"  Lock Mode: {info['lock_mode']}")
                    print(f"  Autolock Time: {info['autolock_time']} seconds")
                    print(f"  Mute: {info['mute']}")
                    print(f"  Serial Number: {info['serial_number']}")
                except Exception:
                    # Error already logged in get_lock_info
                    pass
            elif args.unlock:
                # Unlock lock
                success = await client.unlock_lock(lock)
                if success:
                    print(f"Unlocked lock: {lock.name}")
                else:
                    print(f"Failed to unlock lock: {lock.name}")
            elif args.lock:
                # Lock lock
                success = await client.lock_lock(lock)
                if success:
                    print(f"Locked lock: {lock.name}")
                else:
                    print(f"Failed to lock lock: {lock.name}")
            elif args.autolock is not None:
                # Set autolock time
                success = await client.set_autolock(lock, args.autolock)
                if success:
                    print(f"Set autolock time for lock: {lock.name} to {args.autolock} seconds")
                else:
                    print(f"Failed to set autolock time for lock: {lock.name}")
        else:
            # No action specified
            logger.info(f"Found {len(locks)} locks:")
            for lock in locks:
                print(f"  {lock.name} ({lock.model})")
            
            # If only one lock is available, get info for it
            if len(locks) == 1:
                try:
                    lock = locks[0]
                    info = await client.get_lock_info(lock)
                    print(f"\nLock: {info['name']}")
                    print(f"  MAC Address: {info['mac_address']}")
                    print(f"  Model: {info['model']}")
                    print(f"  Battery: {info['battery']}%")
                    print(f"  Lock Status: {info['lock_status']}")
                    print(f"  Bolt Status: {info['bolt_status']}")
                    print(f"  Lock Mode: {info['lock_mode']}")
                    print(f"  Autolock Time: {info['autolock_time']} seconds")
                    print(f"  Mute: {info['mute']}")
                    print(f"  Serial Number: {info['serial_number']}")
                except Exception as e:
                    logger.error(f"Fatal error in main")
                    logger.error(e)


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="U-tec Client")
    parser.add_argument("--email", "-e", help="U-tec account email")
    parser.add_argument("--password", "-p", help="U-tec account password")
    parser.add_argument("--unlock", "-u", help="Unlock lock with given name", action="store_true")
    parser.add_argument("--lock", "-l", help="Lock lock with given name", action="store_true")
    parser.add_argument("--name", "-n", help="Lock name")
    parser.add_argument("--autolock", "-a", help="Set autolock time in seconds", type=int)
    parser.add_argument("--info", "-i", help="Get info for lock with given name", action="store_true")
    parser.add_argument("--all", help="Get info for all locks", action="store_true")
    parser.add_argument("--config", "-c", help="Config file with email and password")
    parser.add_argument("--debug", "-d", help="Enable debug logging", action="store_true")
    
    args = parser.parse_args()
    
    # Set debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        utec.setup(log_level=LogLevel.DEBUG)
    
    # Load config file if provided
    if args.config:
        try:
            with open(args.config, "r") as f:
                config = json.load(f)
                email = config.get("email", "")
                password = config.get("password", "")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}")
            return
    else:
        # Use command line arguments
        email = args.email
        password = args.password
    
    # Check for required arguments
    if not email or not password:
        logger.error("Email and password are required")
        return
    
    # Run all operations within the proper context manager
    await run_client_operations(email, password, args)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error in main")
        logger.error(e)