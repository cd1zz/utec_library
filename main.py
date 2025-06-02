#!/usr/bin/env python3
"""
U-tec Smart Lock Home Assistant Bridge
Monitors lock status and handles lock/unlock commands via MQTT for home assistant integration.
"""

import asyncio
import time
import logging
import sys
import os
import signal
import argparse
import psutil
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import UtecMQTTClient
from utec.integrations.ha_constants import MQTT_TOPICS

# Configure logging
if os.name == 'nt':  # Windows
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('utec_ha_bridge.log', encoding='utf-8')
        ]
    )
else:
    # Unix/Linux systems
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('utec_ha_bridge.log')
        ]
    )
logger = logging.getLogger(__name__)


class UtecHaBridge:
    """U-tec to Home Assistant bridge with monitoring and control."""
    
    def __init__(self, utec_email: str, utec_password: str, mqtt_host: str, 
                 mqtt_port: int = 1883, mqtt_username: Optional[str] = None, 
                 mqtt_password: Optional[str] = None, update_interval: int = 300):
        """Initialize the bridge with required parameters."""
        self.utec_email = utec_email
        self.utec_password = utec_password
        self.update_interval = update_interval
        self.running = True
        self.locks: List = []
        self.device_map: Dict[str, Any] = {}
        self.start_time = time.time()
        self.last_successful_update = 0
        
        # Initialize single MQTT client with command handling
        self.mqtt_client = UtecMQTTClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            command_handler=self._process_command  # Direct async handler
        )
        
        logger.info(f"Bridge initialized (update interval: {update_interval}s)")
    
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
            self.locks = await utec.discover_devices(self.utec_email, self.utec_password)
            
            if not self.locks:
                logger.warning("No U-tec devices found")
                return False
            
            logger.info(f"Found {len(self.locks)} U-tec device(s)")
            
            # Build device mapping for commands
            for lock in self.locks:
                device_id = lock.mac_uuid.replace(":", "_").lower()
                self.device_map[device_id] = lock
                logger.info(f"Mapped {lock.name} -> {device_id}")
            
            # Set up Home Assistant discovery using MQTT client
            self._setup_bridge_discovery()
            
            for lock in self.locks:
                logger.info(f"Setting up Home Assistant discovery for {lock.name}")
                if not self.mqtt_client.setup_lock_discovery(lock):
                    logger.error(f"Failed to set up discovery for {lock.name}")
                    continue
                
                # Get initial status and publish
                await self._update_lock_status(lock)
                self.mqtt_client.update_lock_state(lock)
                logger.info(f"Successfully set up {lock.name}")
            
            logger.info("Bridge initialization complete")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    # Remove the old _handle_command method entirely - no longer needed
    
    async def _process_command(self, device_id: str, command: str):
        """Process commands asynchronously."""
        try:
            if device_id == "bridge":
                await self._handle_bridge_command(command)
            else:
                await self._execute_lock_command(device_id, command)
        except Exception as e:
            logger.error(f"Failed to process command: {e}")
    
    async def _handle_bridge_command(self, command: str):
        """Handle bridge management commands."""
        try:
            if command.upper() == "UPDATE_STATUS":
                logger.info("Manual status update requested")
                await self._update_all_locks()
                
            elif command.upper() == "STATUS":
                status = {
                    "running": self.running,
                    "locks_count": len(self.locks),
                    "last_update": time.time(),
                    "locks": [{"name": lock.name, "model": lock.model} for lock in self.locks]
                }
                self.mqtt_client.publish(MQTT_TOPICS['bridge_health'], status)
                logger.info("Published bridge status")
                
            else:
                logger.warning(f"Unknown bridge command: {command}")
                
        except Exception as e:
            logger.error(f"Error handling bridge command '{command}': {e}")
    
    async def _execute_lock_command(self, device_id: str, command: str):
        """Execute lock command asynchronously."""
        if device_id not in self.device_map:
            logger.warning(f"Unknown device ID: '{device_id}'")
            logger.info("Available devices:")
            for did, lock in self.device_map.items():
                logger.info(f"   - {did} -> {lock.name}")
            return
            
        lock = self.device_map[device_id]
        
        try:
            if lock.is_busy:
                logger.warning(f"Lock {lock.name} is busy, ignoring command")
                return
            
            logger.info(f"Executing {command} on {lock.name}")
            
            if command.upper() == "LOCK":
                await lock.async_lock(update=True)
                logger.info(f"Successfully locked {lock.name}")
                
            elif command.upper() == "UNLOCK":
                await lock.async_unlock(update=True)
                logger.info(f"Successfully unlocked {lock.name}")
                
            else:
                logger.warning(f"Unknown command: {command}")
                return
            
            # Update and publish status immediately after command
            await self._update_lock_status(lock)
            self.mqtt_client.update_lock_state(lock)
            logger.info(f"Status updated and published for {lock.name}")
            
        except Exception as e:
            logger.error(f"Failed to execute {command} on {device_id}: {e}")
            
            # Try to update status even after error
            try:
                await self._update_lock_status(lock)
                self.mqtt_client.update_lock_state(lock)
            except:
                pass
    
    def _setup_bridge_discovery(self):
        """Set up Home Assistant auto-discovery for bridge monitoring."""
        try:
            device_info = {
                "identifiers": ["utec_bridge"],
                "name": "Utec Smart Lock Bridge",
                "model": "Raspberry Pi Bridge",
                "manufacturer": "Custom",
                "sw_version": "1.0"
            }
            
            sensors = [
                {
                    "name": "Utec Bridge Status",
                    "object_id": "utec_bridge_status",
                    "state_topic": MQTT_TOPICS['bridge_health'],
                    "value_template": "{{ value_json.status }}",
                    "json_attributes_topic": MQTT_TOPICS['bridge_health'],
                    "icon": "mdi:bridge"
                },
                {
                    "name": "Utec Bridge CPU",
                    "object_id": "utec_bridge_cpu",
                    "state_topic": MQTT_TOPICS['bridge_health'], 
                    "value_template": "{{ value_json.system.cpu_percent | round(1) }}",
                    "unit_of_measurement": "%",
                    "icon": "mdi:cpu-64-bit"
                },
                {
                    "name": "Utec Bridge Memory",
                    "object_id": "utec_bridge_memory",
                    "state_topic": MQTT_TOPICS['bridge_health'],
                    "value_template": "{{ value_json.system.memory_percent | round(1) }}",
                    "unit_of_measurement": "%", 
                    "icon": "mdi:memory"
                }
            ]
            
            self.mqtt_client.setup_bridge_discovery(device_info, sensors)
            logger.info("Bridge auto-discovery setup complete")
            
        except Exception as e:
            logger.error(f"Failed to setup bridge discovery: {e}")
    
    async def _update_lock_status(self, lock) -> bool:
        """Update a single lock's status."""
        try:
            if lock.is_busy:
                logger.debug(f"Skipping status update for {lock.name} (busy)")
                return False
                
            await lock.async_update_status()
            logger.debug(f"Updated status for {lock.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to update {lock.name}: {e}")
            return False
    
    async def _update_all_locks(self):
        """Update all locks and publish their states."""
        logger.info("Updating all lock states...")
        
        successful_updates = 0
        for lock in self.locks:
            if await self._update_lock_status(lock):
                if self.mqtt_client.update_lock_state(lock):
                    successful_updates += 1
        
        total_locks = len(self.locks)
        if successful_updates == total_locks:
            logger.info(f"Successfully updated all {total_locks} locks")
            self.last_successful_update = time.time()
        else:
            logger.warning(f"Updated {successful_updates}/{total_locks} locks")
    
    def _get_health_data(self) -> Dict[str, Any]:
        """Get bridge health data."""
        try:
            # Get system metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Get load average if available
            load_avg = None
            if hasattr(os, 'getloadavg'):
                load_avg = os.getloadavg()[0]
            
            # Count online locks
            locks_online = len([lock for lock in self.locks if not getattr(lock, 'is_busy', False)])
            
            # Build lock details
            lock_details = []
            for i, lock in enumerate(self.locks):
                lock_details.append({
                    "name": getattr(lock, 'name', f'Lock_{i}'),
                    "mac": getattr(lock, 'mac_uuid', 'Unknown'),
                    "model": getattr(lock, 'model', 'Unknown'),
                    "is_busy": getattr(lock, 'is_busy', False),
                    "last_status": getattr(lock, 'last_update_time', 0)
                })
            
            return {
                "status": "online" if self.running else "offline",
                "timestamp": time.time(),
                "uptime_seconds": int(time.time() - self.start_time),
                "locks_online": locks_online,
                "total_locks": len(self.locks),
                "mqtt_connected": self.mqtt_client.connected,
                "last_update": self.last_successful_update,
                "system": {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": (disk.used / disk.total) * 100,
                    "load_average": load_avg
                },
                "locks": lock_details
            }
            
        except Exception as e:
            logger.error(f"Failed to collect health data: {e}")
            return {
                "status": "error",
                "timestamp": time.time(),
                "error": str(e),
                "running": self.running
            }
    
    async def run(self):
        """Run the main bridge loop with monitoring and command handling."""
        # Set the event loop reference in the MQTT client
        self.mqtt_client.set_event_loop(asyncio.get_running_loop())
        
        logger.info("Starting bridge main loop...")
        logger.info(f"Status update interval: {self.update_interval} seconds")
        logger.info("Listening for MQTT commands...")
        logger.info("Press Ctrl+C to stop")
        
        try:
            last_update_time = 0
            last_health_time = 0
            
            while self.running:
                current_time = time.time()
                
                # Periodic status updates
                if current_time - last_update_time >= self.update_interval:
                    logger.info("=== Starting periodic status update ===")
                    start_time = time.time()
                    
                    try:
                        await self._update_all_locks()
                        elapsed = time.time() - start_time
                        logger.info(f"Status update completed successfully in {elapsed:.1f}s")
                    except Exception as e:
                        logger.error(f"Status update failed: {e}")
                        elapsed = time.time() - start_time
                        logger.error(f"Failed status update took {elapsed:.1f}s")
                    
                    last_update_time = current_time
                
                # Health status every 30 seconds
                if current_time - last_health_time >= 30:
                    try:
                        health_data = self._get_health_data()
                        self.mqtt_client.publish_bridge_health(health_data)
                        logger.debug("Health status update completed")
                    except Exception as e:
                        logger.error(f"Health status update failed: {e}")
                    
                    last_health_time = current_time
                
                # Short sleep to avoid busy waiting
                await asyncio.sleep(5)
                
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        finally:
            logger.info("Exiting main loop")
            self.shutdown()
    
    def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        # Disconnect MQTT client
        if self.mqtt_client:
            self.mqtt_client.disconnect()
            logger.info("MQTT client disconnected")
        
        logger.info("Shutdown complete")
    
    def stop(self):
        """Stop the bridge (for signal handlers)."""
        self.running = False


# Testing functions remain the same...
async def test_discovery(utec_email: str, utec_password: str):
    """Test device discovery."""
    print("\n" + "="*60)
    print("TESTING DEVICE DISCOVERY")
    print("="*60)
    
    try:
        print("Initializing U-tec library...")
        utec.setup(log_level=utec.LogLevel.INFO)
        
        print("Discovering devices...")
        locks = await utec.discover_devices(utec_email, utec_password)
        
        if not locks:
            print("❌ No devices found")
            return False
        
        print(f"✅ Found {len(locks)} device(s):")
        print("-" * 40)
        
        for i, lock in enumerate(locks, 1):
            print(f"{i:2d}. {lock.name}")
            print(f"     MAC: {lock.mac_uuid}")
            print(f"     Model: {lock.model}")
            print(f"     UID: {lock.uid}")
            print(f"     Serial: {getattr(lock, 'sn', 'Unknown')}")
            
            # Show capabilities
            caps = []
            if getattr(lock.capabilities, 'bluetooth', False): caps.append('BLE')
            if getattr(lock.capabilities, 'autolock', False): caps.append('AutoLock')
            if getattr(lock.capabilities, 'keypad', False): caps.append('Keypad')
            print(f"     Features: {', '.join(caps) if caps else 'Basic'}")
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ Discovery failed: {e}")
        return False


def test_mqtt_connection(mqtt_host: str, mqtt_port: int, mqtt_username: Optional[str], mqtt_password: Optional[str]):
    """Test MQTT connection using the new MQTT client."""
    print("\n" + "="*60)
    print("TESTING MQTT CONNECTION")
    print("="*60)
    
    print(f"Testing connection to {mqtt_host}:{mqtt_port}")
    
    try:
        # Use the new MQTT client for testing
        test_client = UtecMQTTClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password
        )
        
        if test_client.connect():
            print("✅ Connected to MQTT broker")
            test_client.disconnect()
            return True
        else:
            print("❌ Connection failed")
            return False
        
    except Exception as e:
        print(f"❌ MQTT test failed: {e}")
        return False


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
        
        print("❌ Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease create a .env file with:")
        print("   UTEC_EMAIL=your@email.com")
        print("   UTEC_PASSWORD=your_password")
        print("   MQTT_HOST=your_homeassistant_ip")
        print("   MQTT_USERNAME=your_mqtt_user  # optional")
        print("   MQTT_PASSWORD=your_mqtt_pass  # optional")
        print("   UPDATE_INTERVAL=300          # optional (seconds)")
        
        raise ValueError(f"Missing required environment variables: {missing}")
    
    # Optional variables with defaults
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    mqtt_username = os.getenv('MQTT_USERNAME')
    mqtt_password = os.getenv('MQTT_PASSWORD')
    update_interval = int(os.getenv('UPDATE_INTERVAL', '300'))
    
    return {
        'utec_email': utec_email,
        'utec_password': utec_password,
        'mqtt_host': mqtt_host,
        'mqtt_port': mqtt_port,
        'mqtt_username': mqtt_username,
        'mqtt_password': mqtt_password,
        'update_interval': update_interval
    }


def main():
    """Main application entry point."""
    parser = argparse.ArgumentParser(description='U-tec Home Assistant Bridge')
    parser.add_argument('--test-discovery', action='store_true', help='Test device discovery')
    parser.add_argument('--test-mqtt', action='store_true', help='Test MQTT connection')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    # Setup logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    async def async_main():
        bridge = None
        
        try:
            # Load configuration
            config = load_config()
            logger.info("Configuration loaded successfully")
            
            # Handle test modes
            if args.test_discovery:
                return 0 if await test_discovery(config['utec_email'], config['utec_password']) else 1
            
            if args.test_mqtt:
                return 0 if test_mqtt_connection(
                    config['mqtt_host'], config['mqtt_port'], 
                    config['mqtt_username'], config['mqtt_password']
                ) else 1
            
            # Normal operation - create and run bridge
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