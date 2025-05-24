#!/usr/bin/env python3
"""
Production-grade U-tec to Home Assistant integration.
Optimized for reliability, observability, and maintainability.
"""

import asyncio
import logging
import sys
import os
import signal
from typing import List, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import HomeAssistantMQTTIntegration, HAMQTTConfig

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('utec_ha_bridge.log')
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class ApplicationConfig:
    """Application configuration."""
    utec_email: str
    utec_password: str
    mqtt_config: HAMQTTConfig
    update_interval: int = 300
    max_update_failures: int = 5
    device_timeout: int = 30


class UTecHomeAssistantBridge:
    """Main application class for U-tec to Home Assistant bridge."""
    
    def __init__(self, config: ApplicationConfig):
        self.config = config
        self.mqtt_client: HomeAssistantMQTTIntegration = None
        self.locks: List[Any] = []
        self.previous_states: Dict[str, Dict[str, Any]] = {}
        self.update_failures: Dict[str, int] = {}
        self.shutdown_event = asyncio.Event()
        self.metrics_task = None
    
    async def initialize(self) -> bool:
        """Initialize the bridge application."""
        try:
            logger.info("Initializing U-tec Home Assistant Bridge")
            
            # Set up U-tec library
            utec.setup(
                log_level=utec.LogLevel.INFO,
                ble_max_retries=5
            )
            logger.info("U-tec library initialized")
            
            # Create MQTT client
            self.mqtt_client = HomeAssistantMQTTIntegration(self.config.mqtt_config)
            
            # Connect to MQTT broker
            logger.info("Connecting to MQTT broker...")
            if not await self.mqtt_client.connect():
                logger.error("Failed to connect to MQTT broker")
                return False
            
            logger.info("Connected to MQTT broker successfully")
            
            # Discover U-tec devices
            logger.info("Discovering U-tec devices...")
            self.locks = await utec.discover_devices(
                self.config.utec_email, 
                self.config.utec_password
            )
            
            if not self.locks:
                logger.warning("No U-tec devices found")
                return False
            
            logger.info(f"Found {len(self.locks)} U-tec device(s)")
            
            # Set up devices in Home Assistant
            setup_success = await self._setup_devices()
            if not setup_success:
                logger.error("Failed to set up devices in Home Assistant")
                return False
            
            # Start metrics reporting
            self.metrics_task = asyncio.create_task(self._metrics_reporter())
            
            logger.info("Bridge initialization complete")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    async def _setup_devices(self) -> bool:
        """Set up all devices in Home Assistant."""
        setup_tasks = []
        
        for lock in self.locks:
            setup_tasks.append(self._setup_single_device(lock))
        
        results = await asyncio.gather(*setup_tasks, return_exceptions=True)
        
        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error setting up {self.locks[i].name}: {result}")
            elif result:
                success_count += 1
        
        logger.info(f"Successfully set up {success_count}/{len(self.locks)} devices")
        return success_count > 0
    
    async def _setup_single_device(self, lock) -> bool:
        """Set up a single device in Home Assistant."""
        try:
            logger.info(f"Setting up {lock.name} ({lock.mac_uuid})")
            
            # Set up Home Assistant discovery
            if not await self.mqtt_client.setup_lock_discovery(lock):
                logger.error(f"Failed to set up discovery for {lock.name}")
                return False
            
            # Get initial status with timeout
            try:
                await asyncio.wait_for(
                    lock.async_update_status(),
                    timeout=self.config.device_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout getting initial status for {lock.name}")
                # Continue anyway - we'll try again in the update loop
            except Exception as e:
                logger.error(f"Error getting initial status for {lock.name}: {e}")
                # Continue anyway
            
            # Log device status
            self._log_device_status(lock)
            
            # Publish initial state
            if not await self.mqtt_client.publish_lock_state(lock):
                logger.warning(f"Failed to publish initial state for {lock.name}")
                # Don't fail setup for this
            
            logger.info(f"Successfully set up {lock.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting up {lock.name}: {e}", exc_info=True)
            return False
    
    def _log_device_status(self, lock):
        """Log current device status."""
        logger.info(f"Device status for {lock.name}:")
        logger.info(f"  Battery: {getattr(lock, 'battery', 'Unknown')}")
        logger.info(f"  Lock Status: {getattr(lock, 'lock_status', 'Unknown')}")
        logger.info(f"  Lock Mode: {getattr(lock, 'lock_mode', 'Unknown')}")
        logger.info(f"  Autolock: {getattr(lock, 'autolock_time', 'Unknown')}s")
        logger.info(f"  Mute: {getattr(lock, 'mute', 'Unknown')}")
    
    async def run(self):
        """Main application loop."""
        logger.info(f"Starting monitoring loop (update interval: {self.config.update_interval}s)")
        logger.info("Press Ctrl+C to stop")
        
        try:
            while not self.shutdown_event.is_set():
                # Wait for next update cycle
                try:
                    await asyncio.wait_for(
                        self.shutdown_event.wait(),
                        timeout=self.config.update_interval
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    pass  # Normal timeout - continue with update
                
                # Check MQTT connection health
                if not self.mqtt_client.is_connected:
                    logger.warning("MQTT not connected - skipping update cycle")
                    continue
                
                # Update all devices
                await self._update_all_devices()
                
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    
    async def _update_all_devices(self):
        """Update all device states."""
        logger.info("Updating device states...")
        
        update_tasks = []
        for lock in self.locks:
            update_tasks.append(self._update_single_device(lock))
        
        results = await asyncio.gather(*update_tasks, return_exceptions=True)
        
        # Process results
        successful_updates = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Exception updating {self.locks[i].name}: {result}")
                self._handle_update_failure(self.locks[i])
            elif result:
                successful_updates += 1
                self._handle_update_success(self.locks[i])
            else:
                self._handle_update_failure(self.locks[i])
        
        # Log summary
        total_devices = len(self.locks)
        if successful_updates == total_devices:
            logger.info(f"Successfully updated all {total_devices} devices")
        else:
            logger.warning(f"Updated {successful_updates}/{total_devices} devices")
    
    async def _update_single_device(self, lock) -> bool:
        """Update a single device."""
        try:
            # Update device status with timeout
            await asyncio.wait_for(
                lock.async_update_status(),
                timeout=self.config.device_timeout
            )
            
            # Get current state
            current_state = {
                'battery': getattr(lock, 'battery', -1),
                'lock_status': getattr(lock, 'lock_status', -1),
                'lock_mode': getattr(lock, 'lock_mode', -1),
                'autolock_time': getattr(lock, 'autolock_time', 0),
                'mute': getattr(lock, 'mute', False)
            }
            
            # Check for changes
            device_id = lock.mac_uuid
            previous_state = self.previous_states.get(device_id, {})
            changes = []
            
            for key, value in current_state.items():
                if previous_state.get(key) != value:
                    old_value = previous_state.get(key, 'Unknown')
                    changes.append(f"{key}: {old_value} -> {value}")
            
            # Log changes
            if changes:
                logger.info(f"Changes detected for {lock.name}:")
                for change in changes:
                    logger.info(f"  {change}")
            else:
                logger.debug(f"No changes for {lock.name}")
            
            # Store current state
            self.previous_states[device_id] = current_state
            
            # Publish updated state
            if not await self.mqtt_client.publish_lock_state(lock):
                logger.error(f"Failed to publish state for {lock.name}")
                return False
            
            logger.debug(f"Successfully updated {lock.name}")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout updating {lock.name}")
            return False
        except Exception as e:
            logger.error(f"Error updating {lock.name}: {e}")
            return False
    
    def _handle_update_success(self, lock):
        """Handle successful device update."""
        device_id = lock.mac_uuid
        if device_id in self.update_failures:
            # Reset failure count on success
            del self.update_failures[device_id]
    
    def _handle_update_failure(self, lock):
        """Handle device update failure."""
        device_id = lock.mac_uuid
        self.update_failures[device_id] = self.update_failures.get(device_id, 0) + 1
        
        failure_count = self.update_failures[device_id]
        if failure_count >= self.config.max_update_failures:
            logger.error(f"Device {lock.name} has failed {failure_count} consecutive updates")
            # Could implement device removal or alerting here
    
    async def _metrics_reporter(self):
        """Periodic metrics reporting."""
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(300)  # Report every 5 minutes
                
                # Get MQTT metrics
                mqtt_metrics = self.mqtt_client.get_metrics()
                
                # Application metrics
                total_devices = len(self.locks)
                failed_devices = len(self.update_failures)
                healthy_devices = total_devices - failed_devices
                
                logger.info("=== Bridge Metrics ===")
                logger.info(f"Devices: {healthy_devices}/{total_devices} healthy")
                logger.info(f"MQTT Health: {mqtt_metrics['health']}")
                logger.info(f"MQTT Success Rate: {mqtt_metrics['publish_success_rate']:.1f}%")
                logger.info(f"Messages Published: {mqtt_metrics['messages_published']}")
                logger.info(f"Connection State: {mqtt_metrics['state']}")
                
                if failed_devices > 0:
                    logger.warning(f"Failed devices: {list(self.update_failures.keys())}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics reporter: {e}")
    
    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Starting graceful shutdown...")
        
        # Signal shutdown
        self.shutdown_event.set()
        
        # Cancel metrics task
        if self.metrics_task and not self.metrics_task.done():
            self.metrics_task.cancel()
            try:
                await self.metrics_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect MQTT
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        
        logger.info("Shutdown complete")


def load_configuration() -> ApplicationConfig:
    """Load application configuration from environment variables."""
    # Required variables
    required_vars = {
        'UTEC_EMAIL': os.getenv('UTEC_EMAIL'),
        'UTEC_PASSWORD': os.getenv('UTEC_PASSWORD'),
        'MQTT_HOST': os.getenv('MQTT_HOST')
    }
    
    # Check required variables
    missing_vars = [name for name, value in required_vars.items() if not value]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
    
    # Optional variables with defaults
    mqtt_config = HAMQTTConfig(
        broker_host=required_vars['MQTT_HOST'],
        broker_port=int(os.getenv('MQTT_PORT', '1883')),
        username=os.getenv('MQTT_USERNAME'),
        password=os.getenv('MQTT_PASSWORD'),
        client_id=os.getenv('MQTT_CLIENT_ID'),
        discovery_prefix=os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant'),
        device_prefix=os.getenv('MQTT_DEVICE_PREFIX', 'utec'),
        qos=int(os.getenv('MQTT_QOS', '1')),
        retain=os.getenv('MQTT_RETAIN', 'true').lower() == 'true',
        
        # Advanced settings
        reconnect_enabled=os.getenv('MQTT_RECONNECT_ENABLED', 'true').lower() == 'true',
        max_reconnect_delay=float(os.getenv('MQTT_MAX_RECONNECT_DELAY', '300')),
        connect_timeout=float(os.getenv('MQTT_CONNECT_TIMEOUT', '30')),
        publish_timeout=float(os.getenv('MQTT_PUBLISH_TIMEOUT', '10')),
        
        # Health monitoring
        health_check_enabled=os.getenv('MQTT_HEALTH_CHECK_ENABLED', 'true').lower() == 'true',
        health_check_interval=float(os.getenv('MQTT_HEALTH_CHECK_INTERVAL', '30')),
        ping_interval=float(os.getenv('MQTT_PING_INTERVAL', '120')),
        
        # LWT
        lwt_enabled=os.getenv('MQTT_LWT_ENABLED', 'true').lower() == 'true'
    )
    
    return ApplicationConfig(
        utec_email=required_vars['UTEC_EMAIL'],
        utec_password=required_vars['UTEC_PASSWORD'],
        mqtt_config=mqtt_config,
        update_interval=int(os.getenv('UPDATE_INTERVAL', '300')),
        max_update_failures=int(os.getenv('MAX_UPDATE_FAILURES', '5')),
        device_timeout=int(os.getenv('DEVICE_TIMEOUT', '30'))
    )


async def main():
    """Main application entry point."""
    bridge = None
    
    try:
        # Load configuration
        config = load_configuration()
        logger.info("Configuration loaded successfully")
        
        # Create and initialize bridge
        bridge = UTecHomeAssistantBridge(config)
        
        if not await bridge.initialize():
            logger.error("Bridge initialization failed")
            return 1
        
        # Set up signal handlers for graceful shutdown
        def signal_handler():
            logger.info("Shutdown signal received")
            if bridge:
                asyncio.create_task(bridge.shutdown())
        
        # Register signal handlers
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, lambda s, f: signal_handler())
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, lambda s, f: signal_handler())
        
        # Run the bridge
        await bridge.run()
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        return 1
    finally:
        if bridge:
            await bridge.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))