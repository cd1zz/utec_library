#!/usr/bin/env python3
"""
Simple U-tec to Home Assistant integration.
Follows KISS, YAGNI, and SOLID principles.
Enhanced with comprehensive timeout handling.
"""

import asyncio
import time
import logging
import sys
import os
import signal
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import UtecMQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('utec_ha_bridge.log')
    ]
)
logger = logging.getLogger(__name__)


class DeviceTimeoutError(Exception):
    """Exception raised when a device operation times out."""
    pass


class UtecHaBridge:
    """Simple U-tec to Home Assistant bridge with timeout protection."""
    
    def __init__(self, utec_email: str, utec_password: str, mqtt_host: str, 
                 mqtt_port: int = 1883, mqtt_username: Optional[str] = None, 
                 mqtt_password: Optional[str] = None, update_interval: int = 300,
                 device_timeout: int = 60, total_update_timeout: int = 240):
        """Initialize the bridge with required parameters.
        
        Args:
            utec_email: U-tec account email
            utec_password: U-tec account password
            mqtt_host: MQTT broker host
            mqtt_port: MQTT broker port
            mqtt_username: MQTT username (optional)
            mqtt_password: MQTT password (optional)
            update_interval: Time between update cycles in seconds
            device_timeout: Timeout for individual device operations in seconds
            total_update_timeout: Total timeout for all devices update cycle in seconds
        """
        self.utec_email = utec_email
        self.utec_password = utec_password
        self.update_interval = update_interval
        self.device_timeout = device_timeout
        self.total_update_timeout = total_update_timeout
        self.running = True
        
        # Track device health
        self.device_failures = {}
        self.device_last_success = {}
        
        # Initialize MQTT client
        self.mqtt_client = UtecMQTTClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password
        )
        
        self.locks: List = []
        logger.info(f"Bridge initialized (update interval: {update_interval}s, "
                   f"device timeout: {device_timeout}s, total timeout: {total_update_timeout}s)")
    
    async def initialize(self) -> bool:
        """Initialize U-tec library and discover devices."""
        try:
            logger.info("Initializing U-tec library...")
            utec.setup(log_level=utec.LogLevel.INFO)
            
            logger.info("Connecting to MQTT broker...")
            if not self.mqtt_client.connect():
                logger.error("Failed to connect to MQTT broker")
                return False
            
            logger.info("Discovering U-tec devices...")
            # Add timeout to device discovery
            try:
                self.locks = await asyncio.wait_for(
                    utec.discover_devices(self.utec_email, self.utec_password),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                logger.error("Device discovery timed out after 30 seconds")
                return False
            
            if not self.locks:
                logger.warning("No U-tec devices found")
                return False
            
            logger.info(f"Found {len(self.locks)} U-tec device(s)")
            
            # Set up Home Assistant discovery for each device
            for lock in self.locks:
                logger.info(f"Setting up {lock.name} ({lock.mac_uuid})")
                if not self.mqtt_client.setup_lock_discovery(lock):
                    logger.error(f"Failed to set up discovery for {lock.name}")
                    continue
                
                # Initialize device tracking
                self.device_failures[lock.mac_uuid] = 0
                self.device_last_success[lock.mac_uuid] = time.time()
                
                # Get initial status with timeout
                success = await self._update_lock_status_safe(lock)
                if success:
                    self._publish_lock_state(lock)
                    logger.info(f"Successfully set up {lock.name}")
                else:
                    logger.warning(f"Initial status update failed for {lock.name}")
            
            logger.info("Bridge initialization complete")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    async def _update_lock_status_safe(self, lock) -> bool:
        """Safely update a single lock's status with timeout protection.
        
        Args:
            lock: Lock device to update
            
        Returns:
            True if update was successful, False otherwise
        """
        try:
            logger.debug(f"Starting status update for {lock.name} (timeout: {self.device_timeout}s)")
            
            # Use asyncio.wait_for to add timeout protection
            await asyncio.wait_for(
                lock.async_update_status(),
                timeout=self.device_timeout
            )
            
            # Update success tracking
            self.device_last_success[lock.mac_uuid] = time.time()
            if lock.mac_uuid in self.device_failures:
                if self.device_failures[lock.mac_uuid] > 0:
                    logger.info(f"{lock.name} recovered after {self.device_failures[lock.mac_uuid]} failures")
                self.device_failures[lock.mac_uuid] = 0
            
            logger.debug(f"Successfully updated status for {lock.name}")
            return True
            
        except asyncio.TimeoutError:
            self._handle_device_timeout(lock)
            return False
        except Exception as e:
            self._handle_device_error(lock, e)
            return False
    
    def _handle_device_timeout(self, lock) -> None:
        """Handle device timeout.
        
        Args:
            lock: Lock device that timed out
        """
        self.device_failures[lock.mac_uuid] = self.device_failures.get(lock.mac_uuid, 0) + 1
        failure_count = self.device_failures[lock.mac_uuid]
        
        logger.error(f"Device {lock.name} ({lock.mac_uuid}) timed out after {self.device_timeout}s "
                    f"(failure #{failure_count})")
        
        if failure_count >= 3:
            logger.warning(f"Device {lock.name} has failed {failure_count} times consecutively")
    
    def _handle_device_error(self, lock, error: Exception) -> None:
        """Handle device error.
        
        Args:
            lock: Lock device that errored
            error: Exception that occurred
        """
        self.device_failures[lock.mac_uuid] = self.device_failures.get(lock.mac_uuid, 0) + 1
        failure_count = self.device_failures[lock.mac_uuid]
        
        logger.error(f"Error updating {lock.name} ({lock.mac_uuid}): {error} "
                    f"(failure #{failure_count})")
        
        if failure_count >= 5:
            logger.warning(f"Device {lock.name} has failed {failure_count} times consecutively")
    
    def _publish_lock_state(self, lock) -> bool:
        """Publish a single lock's state to MQTT.
        
        Args:
            lock: Lock device to publish state for
            
        Returns:
            True if state was published successfully
        """
        try:
            if self.mqtt_client.update_lock_state(lock):
                logger.debug(f"Published state for {lock.name}")
                return True
            else:
                logger.error(f"Failed to publish state for {lock.name}")
                return False
        except Exception as e:
            logger.error(f"Exception publishing state for {lock.name}: {e}")
            return False
    
    def _should_skip_device(self, lock) -> bool:
        """Determine if a device should be skipped due to repeated failures.
        
        Args:
            lock: Lock device to check
            
        Returns:
            True if device should be skipped
        """
        failure_count = self.device_failures.get(lock.mac_uuid, 0)
        last_success = self.device_last_success.get(lock.mac_uuid, 0)
        
        # Skip if too many recent failures
        if failure_count >= 5:
            # Only try again every 10 minutes for problem devices
            if time.time() - last_success < 600:
                logger.debug(f"Skipping {lock.name} due to {failure_count} consecutive failures")
                return True
        
        return False
    
    async def _update_all_locks(self):
        """Update all locks and publish their states with comprehensive timeout protection."""
        logger.info("Starting lock update cycle...")
        start_time = time.time()
        
        successful_updates = 0
        skipped_devices = 0
        failed_updates = 0
        
        try:
            # Use total timeout for the entire update cycle
            async with asyncio.timeout(self.total_update_timeout):
                for i, lock in enumerate(self.locks):
                    try:
                        # Check if we should skip this device
                        if self._should_skip_device(lock):
                            skipped_devices += 1
                            continue
                        
                        logger.info(f"Updating {lock.name} ({i+1}/{len(self.locks)})...")
                        
                        # Update device status with timeout protection
                        if await self._update_lock_status_safe(lock):
                            # Publish state if update successful
                            if self._publish_lock_state(lock):
                                successful_updates += 1
                                logger.info(f"Successfully updated and published {lock.name}")
                            else:
                                logger.warning(f"Updated {lock.name} but failed to publish state")
                                failed_updates += 1
                        else:
                            failed_updates += 1
                        
                        # Small delay between devices to prevent BLE stack overload
                        if i < len(self.locks) - 1:  # Don't sleep after last device
                            await asyncio.sleep(1)
                            
                    except Exception as e:
                        logger.error(f"Unexpected error processing {lock.name}: {e}")
                        failed_updates += 1
                        
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(f"Total update cycle timed out after {elapsed:.1f}s "
                        f"(limit: {self.total_update_timeout}s)")
        
        # Log summary
        total_processed = successful_updates + failed_updates
        elapsed = time.time() - start_time
        
        logger.info(f"Update cycle completed in {elapsed:.1f}s: "
                   f"{successful_updates} successful, {failed_updates} failed, "
                   f"{skipped_devices} skipped")
        
        if successful_updates == 0 and len(self.locks) > 0:
            logger.warning("No devices were successfully updated!")
        elif failed_updates > 0:
            logger.warning(f"{failed_updates} devices failed to update")
    
    async def run(self):
        """Main application loop with robust error handling."""
        logger.info("Starting monitoring loop...")
        logger.info("Press Ctrl+C to stop")
        
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        try:
            while self.running:
                cycle_start_time = time.time()
                
                try:
                    # Update all locks with timeout protection
                    await self._update_all_locks()
                    consecutive_failures = 0  # Reset on successful cycle
                    
                except Exception as e:
                    consecutive_failures += 1
                    logger.error(f"Update cycle failed ({consecutive_failures}/{max_consecutive_failures}): {e}")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        logger.critical(f"Too many consecutive failures ({consecutive_failures}). "
                                      "Something may be seriously wrong.")
                        # Add exponential backoff for problem situations
                        backoff_time = min(300, 30 * (2 ** (consecutive_failures - max_consecutive_failures)))
                        logger.info(f"Backing off for {backoff_time} seconds...")
                        await asyncio.sleep(backoff_time)
                
                # Calculate sleep time for next cycle
                elapsed = time.time() - cycle_start_time
                sleep_time = max(0, self.update_interval - elapsed)
                
                if sleep_time > 0:
                    logger.debug(f"Cycle completed in {elapsed:.1f}s, sleeping for {sleep_time:.1f}s...")
                    await asyncio.sleep(sleep_time)
                else:
                    logger.warning(f"Update cycle took {elapsed:.1f}s "
                                 f"(longer than {self.update_interval}s interval)")
                    
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        
        # Log final device health summary
        if self.device_failures:
            logger.info("Final device health summary:")
            for mac_uuid, failures in self.device_failures.items():
                device_name = next((lock.name for lock in self.locks if lock.mac_uuid == mac_uuid), mac_uuid)
                last_success = self.device_last_success.get(mac_uuid, 0)
                time_since_success = time.time() - last_success if last_success > 0 else float('inf')
                
                if failures > 0:
                    logger.info(f"  {device_name}: {failures} failures, "
                              f"last success {time_since_success:.0f}s ago")
                else:
                    logger.info(f"  {device_name}: healthy")
        
        logger.info("Shutdown complete")
    
    def stop(self):
        """Stop the bridge (for signal handlers)."""
        self.running = False


def load_config():
    """Load configuration from environment variables."""
    # Required variables
    utec_email = os.getenv('UTEC_EMAIL')
    utec_password = os.getenv('UTEC_PASSWORD') 
    mqtt_host = os.getenv('MQTT_HOST')
    
    if not all([utec_email, utec_password, mqtt_host]):
        missing = []
        if not utec_email: missing.append('UTEC_EMAIL')
        if not utec_password: missing.append('UTEC_PASSWORD')
        if not mqtt_host: missing.append('MQTT_HOST')
        raise ValueError(f"Missing required environment variables: {missing}")
    
    # Optional variables with defaults
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    mqtt_username = os.getenv('MQTT_USERNAME')
    mqtt_password = os.getenv('MQTT_PASSWORD')
    update_interval = int(os.getenv('UPDATE_INTERVAL', '300'))
    device_timeout = int(os.getenv('DEVICE_TIMEOUT', '60'))
    total_update_timeout = int(os.getenv('TOTAL_UPDATE_TIMEOUT', '240'))
    
    return {
        'utec_email': utec_email,
        'utec_password': utec_password,
        'mqtt_host': mqtt_host,
        'mqtt_port': mqtt_port,
        'mqtt_username': mqtt_username,
        'mqtt_password': mqtt_password,
        'update_interval': update_interval,
        'device_timeout': device_timeout,
        'total_update_timeout': total_update_timeout
    }


def main():
    """Main application entry point."""
    async def async_main():
        bridge = None
        
        try:
            # Load configuration
            config = load_config()
            logger.info("Configuration loaded")
            logger.info(f"Timeouts configured - Device: {config['device_timeout']}s, "
                       f"Total cycle: {config['total_update_timeout']}s")
            
            # Create bridge
            bridge = UtecHaBridge(**config)
            
            # Set up signal handlers for clean shutdown
            def signal_handler(signum, frame):
                logger.info(f"Received signal {signum}")
                if bridge:
                    bridge.stop()
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            # Initialize and run
            if await bridge.initialize():
                await bridge.run()
                return 0
            else:
                logger.error("Failed to initialize bridge")
                return 1
                
        except Exception as e:
            logger.error(f"Application error: {e}", exc_info=True)
            return 1
        finally:
            if bridge:
                bridge.shutdown()
    
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())