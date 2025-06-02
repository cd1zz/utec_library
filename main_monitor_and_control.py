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

import paho.mqtt.client as mqtt

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import UtecMQTTClient

# Configure logging
if os.name == 'nt':  # Windows
    # Configure for Windows console encoding
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
        self.device_map: Dict[str, Any] = {}  # Map device_id -> lock object
        self.command_queue = asyncio.Queue()  # Queue for commands from MQTT
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # Store event loop reference
        
        # MQTT connection details
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        
        # Initialize MQTT clients
        self.status_client = UtecMQTTClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password
        )
        self.command_client: Optional[mqtt.Client] = None
        
        logger.info(f"Bridge initialized (update interval: {update_interval}s)")
    
    async def initialize(self) -> bool:
        """Initialize U-tec library and discover devices."""
        try:
            logger.info("Initializing U-tec library...")
            utec.setup(log_level=utec.LogLevel.INFO)
            
            logger.info("Connecting to MQTT broker for status publishing...")
            if not self.status_client.connect():
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
            
            # Set up MQTT command listener
            await self._setup_command_listener()
            
            # Set up Home Assistant discovery for each device
            for lock in self.locks:
                logger.info(f"Setting up Home Assistant discovery for {lock.name}")
                if not self.status_client.setup_lock_discovery(lock):
                    logger.error(f"Failed to set up discovery for {lock.name}")
                    continue
                
                # Get initial status and publish
                await self._update_lock_status(lock)
                self._publish_lock_state(lock)
                logger.info(f"Successfully set up {lock.name}")
            
            logger.info("Bridge initialization complete")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    async def _setup_command_listener(self):
        """Set up MQTT client for listening to lock commands."""
        try:
            client_id = f"utec-bridge-commands-{int(time.time())}"
            
            # Handle paho-mqtt version differences
            try:
                from paho.mqtt.client import CallbackAPIVersion
                self.command_client = mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION1,
                    client_id=client_id
                )
            except (ImportError, TypeError):
                self.command_client = mqtt.Client(client_id=client_id)
            
            # Set authentication if provided
            if self.mqtt_username and self.mqtt_password:
                self.command_client.username_pw_set(self.mqtt_username, self.mqtt_password)
            
            # Set callbacks
            self.command_client.on_connect = self._on_command_connect
            self.command_client.on_message = self._on_command_message
            self.command_client.on_disconnect = self._on_command_disconnect
            
            # Connect
            self.command_client.connect(self.mqtt_host, self.mqtt_port, 60)
            self.command_client.loop_start()
            
            # Wait for connection
            await asyncio.sleep(2)
            
            logger.info("Command listener connected to MQTT")
            
        except Exception as e:
            logger.error(f"Failed to setup command listener: {e}")
            raise
    
    def _on_command_connect(self, client, userdata, flags, rc):
        """Handle command client connection."""
        if rc == 0:
            logger.info("Command client connected to MQTT")
            
            # Subscribe to all lock command topics
            for device_id in self.device_map.keys():
                topic = f"utec/{device_id}/lock/command"
                client.subscribe(topic, qos=1)
                logger.info(f"Subscribed to {topic}")
            
            # Subscribe to bridge management
            client.subscribe("utec/bridge/command", qos=1)
            logger.info("Subscribed to utec/bridge/command")
            
            # Also subscribe to a wildcard to catch any commands we might miss
            client.subscribe("utec/+/lock/command", qos=1)
            logger.info("Subscribed to wildcard topic: utec/+/lock/command")
        else:
            logger.error(f"Command client connection failed: {rc}")
    
    def _on_command_disconnect(self, client, userdata, rc):
        """Handle command client disconnection."""
        if rc != 0:
            logger.warning(f"Command client unexpected disconnect: {rc}")
    
    def _on_command_message(self, client, userdata, msg):
        """Handle incoming command messages."""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.info(f"MQTT COMMAND RECEIVED: {topic} -> {payload}")
            
            # Handle bridge management commands
            if topic == "utec/bridge/command":
                self._queue_command("bridge", payload)
                return
            
            # Extract device ID from lock command topic
            # Topic format: utec/{device_id}/lock/command
            topic_parts = topic.split('/')
            if len(topic_parts) != 4 or topic_parts[0] != 'utec' or topic_parts[2] != 'lock':
                logger.warning(f"Invalid command topic format: {topic}")
                return
            
            device_id = topic_parts[1]
            logger.info(f"Extracted device_id: '{device_id}'")
            
            if device_id not in self.device_map:
                logger.warning(f"Unknown device ID: '{device_id}'")
                logger.info(f"Available devices:")
                for did, lock in self.device_map.items():
                    logger.info(f"   - {did} -> {lock.name}")
                return
            
            # Queue the command for async processing
            logger.info(f"Queueing command for device: {self.device_map[device_id].name}")
            self._queue_command(device_id, payload)
            
        except Exception as e:
            logger.error(f"Error processing command message: {e}")
            logger.error(f"Topic: {topic}, Payload: {payload}")
    
    def _queue_command(self, device_id: str, command: str):
        """Queue a command for async processing."""
        try:
            if self.loop and not self.loop.is_closed():
                # Use call_soon_threadsafe to queue the command from another thread
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._process_queued_command(device_id, command))
                )
            else:
                logger.error("Event loop not available for command queuing")
        except Exception as e:
            logger.error(f"Failed to queue command: {e}")
    
    async def _process_queued_command(self, device_id: str, command: str):
        """Process a queued command."""
        try:
            if device_id == "bridge":
                await self._handle_bridge_command(command)
            else:
                await self._execute_lock_command(device_id, command)
        except Exception as e:
            logger.error(f"Failed to process queued command: {e}")
    
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
                self.status_client.publish("utec/bridge/status", status)
                logger.info("Published bridge status")
                
            else:
                logger.warning(f"Unknown bridge command: {command}")
                
        except Exception as e:
            logger.error(f"Error handling bridge command '{command}': {e}")
    
    async def _execute_lock_command(self, device_id: str, command: str):
        """Execute lock command asynchronously."""
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
            self._publish_lock_state(lock)
            logger.info(f"Status updated and published for {lock.name}")
            
        except Exception as e:
            logger.error(f"Failed to execute {command} on {device_id}: {e}")
            
            # Try to update status even after error
            try:
                await self._update_lock_status(lock)
                self._publish_lock_state(lock)
            except:
                pass
    
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
    
    def _publish_lock_state(self, lock) -> bool:
        """Publish a single lock's state to MQTT."""
        try:
            if self.status_client.update_lock_state(lock):
                logger.debug(f"Published state for {lock.name}")
                return True
            else:
                logger.error(f"Failed to publish state for {lock.name}")
                return False
        except Exception as e:
            logger.error(f"Exception publishing state for {lock.name}: {e}")
            return False
    
    async def _update_all_locks(self):
        """Update all locks and publish their states."""
        logger.info("Updating all lock states...")
        
        successful_updates = 0
        for lock in self.locks:
            if await self._update_lock_status(lock):
                if self._publish_lock_state(lock):
                    successful_updates += 1
        
        total_locks = len(self.locks)
        if successful_updates == total_locks:
            logger.info(f"Successfully updated all {total_locks} locks")
        else:
            logger.warning(f"Updated {successful_updates}/{total_locks} locks")

    async def _publish_bridge_health(self):
        """Publish comprehensive bridge health status with detailed error logging."""
        try:
            logger.debug("Starting bridge health status publication")
            
            # Import psutil with error handling
            try:
                import psutil
                logger.debug("psutil imported successfully")
            except ImportError as e:
                logger.error(f"Failed to import psutil: {e}")
                logger.error("Install psutil with: pip install psutil")
                return
            
            # Get system metrics with individual error handling
            cpu_percent = None
            memory_percent = None
            disk_percent = None
            load_avg = None
            
            try:
                cpu_percent = psutil.cpu_percent(interval=1)
                logger.debug(f"CPU usage retrieved: {cpu_percent}%")
            except Exception as e:
                logger.error(f"Failed to get CPU usage: {e}")
                cpu_percent = -1  # Use -1 to indicate error
            
            try:
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                logger.debug(f"Memory usage retrieved: {memory_percent}% ({memory.used}/{memory.total} bytes)")
            except Exception as e:
                logger.error(f"Failed to get memory usage: {e}")
                memory_percent = -1
            
            try:
                disk = psutil.disk_usage('/')
                disk_percent = (disk.used / disk.total) * 100
                logger.debug(f"Disk usage retrieved: {disk_percent:.1f}% ({disk.used}/{disk.total} bytes)")
            except Exception as e:
                logger.error(f"Failed to get disk usage: {e}")
                disk_percent = -1
            
            try:
                if hasattr(os, 'getloadavg'):
                    load_avg = os.getloadavg()[0]
                    logger.debug(f"Load average retrieved: {load_avg}")
                else:
                    logger.debug("Load average not available on this platform")
                    load_avg = None
            except Exception as e:
                logger.error(f"Failed to get load average: {e}")
                load_avg = None
            
            # Get bridge uptime with error handling
            try:
                bridge_uptime = int(time.time() - getattr(self, 'start_time', time.time()))
                logger.debug(f"Bridge uptime calculated: {bridge_uptime} seconds")
            except Exception as e:
                logger.error(f"Failed to calculate bridge uptime: {e}")
                bridge_uptime = 0
            
            # Count online locks with error handling
            locks_online = 0
            total_locks = 0
            lock_details = []
            
            try:
                total_locks = len(self.locks)
                logger.debug(f"Total locks found: {total_locks}")
                
                for i, lock in enumerate(self.locks):
                    try:
                        is_busy = getattr(lock, 'is_busy', False)
                        if not is_busy:
                            locks_online += 1
                        
                        # Get lock details safely
                        lock_detail = {
                            "name": getattr(lock, 'name', f'Lock_{i}'),
                            "mac": getattr(lock, 'mac_uuid', 'Unknown'),
                            "model": getattr(lock, 'model', 'Unknown'),
                            "is_busy": is_busy,
                            "last_status": getattr(lock, 'last_update_time', 0)
                        }
                        lock_details.append(lock_detail)
                        logger.debug(f"Lock {i+1}: {lock_detail['name']} - {'Busy' if is_busy else 'Available'}")
                        
                    except Exception as e:
                        logger.error(f"Failed to process lock {i}: {e}")
                        # Add error entry for this lock
                        lock_details.append({
                            "name": f"Lock_{i}_ERROR",
                            "mac": "Unknown",
                            "model": "Unknown", 
                            "is_busy": True,
                            "last_status": 0,
                            "error": str(e)
                        })
                
                logger.debug(f"Locks online: {locks_online}/{total_locks}")
                
            except Exception as e:
                logger.error(f"Failed to process locks list: {e}")
                locks_online = 0
                total_locks = 0
                lock_details = [{"error": "Failed to enumerate locks"}]
            
            # Check MQTT connection status
            mqtt_connected = False
            try:
                if self.command_client:
                    mqtt_connected = self.command_client.is_connected()
                    logger.debug(f"MQTT command client connected: {mqtt_connected}")
                else:
                    logger.warning("MQTT command client is None")
            except Exception as e:
                logger.error(f"Failed to check MQTT connection status: {e}")
                mqtt_connected = False
            
            # Get last successful update time
            try:
                last_update = getattr(self, 'last_successful_update', 0)
                if last_update > 0:
                    time_since_update = time.time() - last_update
                    logger.debug(f"Last successful update: {time_since_update:.1f} seconds ago")
                else:
                    logger.debug("No successful updates recorded yet")
            except Exception as e:
                logger.error(f"Failed to get last update time: {e}")
                last_update = 0
            
            # Build health status payload
            try:
                health_status = {
                    "status": "online" if self.running else "offline",
                    "timestamp": time.time(),
                    "uptime_seconds": bridge_uptime,
                    "locks_online": locks_online,
                    "total_locks": total_locks,
                    "mqtt_connected": mqtt_connected,
                    "last_update": last_update,
                    "system": {
                        "cpu_percent": cpu_percent,
                        "memory_percent": memory_percent,
                        "disk_percent": disk_percent,
                        "load_average": load_avg
                    },
                    "locks": lock_details
                }
                
                logger.debug(f"Health status payload created: {len(str(health_status))} bytes")
                
            except Exception as e:
                logger.error(f"Failed to create health status payload: {e}")
                # Create minimal fallback payload
                health_status = {
                    "status": "error",
                    "timestamp": time.time(),
                    "error": "Failed to create full health status",
                    "running": self.running
                }
            
            # Publish to MQTT with error handling
            try:
                if not self.status_client:
                    logger.error("Status client is None - cannot publish health status")
                    return
                
                publish_result = self.status_client.publish("utec/bridge/health", health_status)
                if publish_result:
                    logger.debug("Health status published successfully")
                else:
                    logger.error("Failed to publish health status - publish method returned False")
                    
            except Exception as e:
                logger.error(f"Exception during health status publication: {e}")
                logger.error(f"Status client type: {type(self.status_client)}")
                logger.error(f"Health payload size: {len(str(health_status))} characters")
            
            # Publish availability with error handling
            try:
                availability_result = self.status_client.publish("utec/bridge/availability", "online", retain=True)
                if availability_result:
                    logger.debug("Availability status published successfully")
                else:
                    logger.error("Failed to publish availability status")
                    
            except Exception as e:
                logger.error(f"Exception during availability publication: {e}")
            
            logger.debug("Bridge health status publication completed")
            
        except Exception as e:
            logger.error(f"Critical error in _publish_bridge_health: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception args: {e.args}")
            
            # Try to publish a minimal error status
            try:
                if hasattr(self, 'status_client') and self.status_client:
                    error_status = {
                        "status": "error",
                        "timestamp": time.time(),
                        "error": str(e),
                        "function": "_publish_bridge_health"
                    }
                    self.status_client.publish("utec/bridge/health", error_status)
                    logger.info("Published minimal error status to MQTT")
            except Exception as nested_e:
                logger.error(f"Failed to publish error status: {nested_e}")

    async def run(self):
        """Run the main bridge loop with monitoring and command handling."""
        # Store the event loop reference for MQTT callbacks
        self.loop = asyncio.get_running_loop()
        self.start_time = time.time()  # Initialize start time for uptime calculation
        
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
                        self.last_successful_update = time.time()  # Track successful updates
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
                        await self._publish_bridge_health()
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
        
        # Disconnect MQTT clients
        if self.command_client:
            self.command_client.loop_stop()
            self.command_client.disconnect()
            logger.info("Command client disconnected")
        
        if self.status_client:
            self.status_client.disconnect()
            logger.info("Status client disconnected")
        
        logger.info("Shutdown complete")
    
    def stop(self):
        """Stop the bridge (for signal handlers)."""
        self.running = False


# Testing functions
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
    """Test MQTT connection."""
    print("\n" + "="*60)
    print("TESTING MQTT CONNECTION")
    print("="*60)
    
    print(f"Testing connection to {mqtt_host}:{mqtt_port}")
    
    try:
        connected = False
        
        def on_connect(client, userdata, flags, rc):
            nonlocal connected
            if rc == 0:
                connected = True
                print("✅ Connected to MQTT broker")
            else:
                print(f"❌ Connection failed with code {rc}")
        
        client = mqtt.Client("utec-test")
        client.on_connect = on_connect
        
        if mqtt_username and mqtt_password:
            client.username_pw_set(mqtt_username, mqtt_password)
            print(f"Using authentication: {mqtt_username}")
        
        client.connect(mqtt_host, mqtt_port, 60)
        client.loop_start()
        
        # Wait for connection
        for _ in range(30):  # 3 second timeout
            if connected:
                break
            time.sleep(0.1)
        
        client.loop_stop()
        client.disconnect()
        
        return connected
        
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