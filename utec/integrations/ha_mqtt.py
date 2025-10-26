"""
Enhanced MQTT client for U-tec smart locks with command handling.
Follows KISS, YAGNI, and SOLID principles.
"""

import json
import logging
import time
import asyncio
from typing import Dict, Any, Optional, Callable, List
import paho.mqtt.client as mqtt

# Import constants
from ..utils.constants import BATTERY_LEVEL, LOCK_MODE, BOLT_STATUS
from .ha_constants import HA_LOCK_STATES, HA_BATTERY_LEVELS, MQTT_TOPICS, HA_LOCK_DISCOVERY_CONFIG

logger = logging.getLogger(__name__)


class UtecMQTTClient:
    """Enhanced MQTT client for U-tec locks with bidirectional communication."""
    
    def __init__(self, broker_host: str, broker_port: int = 1883, 
                 username: Optional[str] = None, password: Optional[str] = None,
                 command_handler: Optional[Callable] = None, event_loop: Optional[asyncio.AbstractEventLoop] = None):
        """Initialize MQTT client with optional command handling."""
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.username = username
        self.password = password
        self.command_handler = command_handler
        self.event_loop = event_loop  # Store reference to main event loop
        
        # MQTT settings
        self.keepalive = 60
        self.qos = 1
        self.retain = False
        
        # Topic configuration (use constants)
        self.discovery_prefix = MQTT_TOPICS['discovery_prefix']
        self.device_prefix = MQTT_TOPICS['device_prefix']
        
        # Connection state
        self.connected = False
        self.client = None
        
        # Command subscriptions
        self.device_subscriptions: List[str] = []
        
        # Reconnection settings
        self.reconnect_delay = 5.0
        self.max_reconnect_attempts = 10
        
        logger.info(f"U-tec MQTT client initialized for {broker_host}:{broker_port}")
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async command handling."""
        self.event_loop = loop
    
    def set_command_handler(self, handler: Callable[[str, str], None]):
        """Set the command handler function."""
        self.command_handler = handler
    
    def connect(self) -> bool:
        """Connect to MQTT broker with simple retry logic."""
        if self.connected:
            return True
        
        try:
            self.client = self._create_client()
            
            logger.info(f"Connecting to MQTT broker {self.broker_host}:{self.broker_port}")
            self.client.connect(self.broker_host, self.broker_port, self.keepalive)
            self.client.loop_start()
            
            # Simple wait for connection
            attempts = 0
            while not self.connected and attempts < 30:  # 3 second timeout
                time.sleep(0.1)
                attempts += 1
            
            if self.connected:
                logger.info("Successfully connected to MQTT broker")
                self._setup_initial_subscriptions()
                return True
            else:
                logger.error("Failed to connect within timeout period")
                if self.client:
                    self.client.loop_stop()
                    self.client.disconnect()
                return False

        except Exception as e:
            logger.error(f"Connection error: {e}")
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()
            return False
    
    def _create_client(self) -> mqtt.Client:
        """Create and configure MQTT client."""
        client_id = f"utec-ha-{int(time.time())}"
        
        # Handle paho-mqtt version differences
        try:
            from paho.mqtt.client import CallbackAPIVersion
            client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION1,
                client_id=client_id
            )
        except (ImportError, TypeError):
            client = mqtt.Client(client_id=client_id)
        
        # Set Last Will and Testament (use constants)
        client.will_set(
            MQTT_TOPICS['bridge_availability'], 
            "offline", 
            qos=1, 
            retain=True
        )
        
        # Set authentication if provided
        if self.username and self.password:
            client.username_pw_set(self.username, self.password)
        
        # Set callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_log = self._on_log
        
        return client
    
    def _on_connect(self, client, userdata, flags, rc):
        """Handle connection events."""
        if rc == 0:
            self.connected = True
            logger.info("Connected to MQTT broker")
            
            # Publish availability immediately (use constants)
            self.publish(MQTT_TOPICS['bridge_availability'], "online", retain=True)
            
            # Resubscribe to all topics
            self._resubscribe_all()
            
        else:
            self.connected = False
            error_messages = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier", 
                3: "Server unavailable",
                4: "Bad username/password",
                5: "Not authorized"
            }
            error = error_messages.get(rc, f"Unknown error ({rc})")
            logger.error(f"Connection failed: {error}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Handle disconnect events."""
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnect (rc: {rc})")
            self._attempt_reconnect()
        else:
            logger.info("Clean disconnect")
    
    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.info(f"MQTT message received: {topic} -> {payload}")
            
            # Handle bridge commands (use constants)
            if topic == MQTT_TOPICS['bridge_command']:
                self._schedule_command("bridge", payload)
                return
            
            # Handle lock commands
            # Topic format: utec/{device_id}/lock/command
            topic_parts = topic.split('/')
            if (len(topic_parts) == 4 and 
                topic_parts[0] == self.device_prefix and 
                topic_parts[2] == 'lock' and 
                topic_parts[3] == 'command'):
                
                device_id = topic_parts[1]
                self._schedule_command(device_id, payload)
            else:
                logger.warning(f"Unhandled topic format: {topic}")
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _schedule_command(self, device_id: str, command: str):
        """Schedule command execution in the main event loop."""
        if not self.command_handler:
            logger.warning(f"No command handler set for device {device_id}")
            return
        
        if not self.event_loop:
            logger.error("No event loop set for command scheduling")
            return
        
        try:
            if self.event_loop.is_closed():
                logger.error("Event loop is closed, cannot schedule command")
                return
            
            # Schedule the command in the main event loop thread-safely
            self.event_loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._execute_async_command(device_id, command))
            )
            logger.debug(f"Scheduled command {command} for device {device_id}")
            
        except Exception as e:
            logger.error(f"Failed to schedule command: {e}")
    
    async def _execute_async_command(self, device_id: str, command: str):
        """Execute the command handler asynchronously."""
        try:
            if asyncio.iscoroutinefunction(self.command_handler):
                await self.command_handler(device_id, command)
            else:
                self.command_handler(device_id, command)
        except Exception as e:
            logger.error(f"Command handler failed: {e}")
    
    def _on_log(self, client, userdata, level, buf):
        """Handle MQTT client logs."""
        if level == mqtt.MQTT_LOG_ERR:
            logger.error(f"MQTT: {buf}")
        elif level == mqtt.MQTT_LOG_WARNING:
            logger.warning(f"MQTT: {buf}")
        else:
            logger.debug(f"MQTT: {buf}")
    
    def _setup_initial_subscriptions(self):
        """Set up initial MQTT subscriptions."""
        if not self.connected:
            return
        
        # Subscribe to bridge commands (use constants)
        bridge_topic = MQTT_TOPICS['bridge_command']
        self.client.subscribe(bridge_topic, qos=self.qos)
        logger.info(f"Subscribed to {bridge_topic}")
        
        # Subscribe to wildcard for device commands (use constants)
        wildcard_topic = f"{self.device_prefix}/+/lock/command"
        self.client.subscribe(wildcard_topic, qos=self.qos)
        logger.info(f"Subscribed to {wildcard_topic}")
    
    def subscribe_to_device_commands(self, device_id: str):
        """Subscribe to commands for a specific device."""
        if not self.connected:
            logger.warning(f"Cannot subscribe to {device_id}: not connected")
            return False
        
        topic = f"{self.device_prefix}/{device_id}/lock/command"
        
        if topic not in self.device_subscriptions:
            result = self.client.subscribe(topic, qos=self.qos)
            if result[0] == mqtt.MQTT_ERR_SUCCESS:
                self.device_subscriptions.append(topic)
                logger.info(f"Subscribed to commands for device {device_id}")
                return True
            else:
                logger.error(f"Failed to subscribe to {topic}")
                return False
        
        return True  # Already subscribed
    
    def _resubscribe_all(self):
        """Resubscribe to all topics after reconnection."""
        # Resubscribe to bridge commands
        self._setup_initial_subscriptions()
        
        # Resubscribe to all device commands
        for topic in self.device_subscriptions:
            self.client.subscribe(topic, qos=self.qos)
            logger.debug(f"Resubscribed to {topic}")
    
    def _attempt_reconnect(self):
        """Simple reconnection with limited retries."""
        attempt = 0
        
        while not self.connected and attempt < self.max_reconnect_attempts:
            attempt += 1
            logger.info(f"Reconnection attempt {attempt}/{self.max_reconnect_attempts}")
            
            time.sleep(self.reconnect_delay)
            
            try:
                self.client.reconnect()
                # Wait a moment for connection
                time.sleep(1)
            except Exception as e:
                logger.error(f"Reconnection attempt {attempt} failed: {e}")
        
        if not self.connected:
            logger.error("Max reconnection attempts exceeded")
    
    def publish(self, topic: str, payload: Any, qos: Optional[int] = None, retain: Optional[bool] = None) -> bool:
        """Publish message to MQTT broker with flexible QoS and retain settings."""
        if not self.connected:
            logger.error("Cannot publish: not connected to broker")
            return False
        
        try:
            # Use provided values or fall back to instance defaults
            actual_qos = qos if qos is not None else self.qos
            actual_retain = retain if retain is not None else self.retain
            
            # Convert payload to JSON if needed
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload)
            else:
                payload_str = str(payload)
            
            result = self.client.publish(topic, payload_str, qos=actual_qos, retain=actual_retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {topic} (qos={actual_qos}, retain={actual_retain})")
                return True
            else:
                logger.error(f"Publish failed to {topic}: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Publish error for {topic}: {e}")
            return False
    
    def publish_bridge_health(self, health_data: Dict[str, Any]) -> bool:
        """Publish bridge health status."""
        return self.publish(MQTT_TOPICS['bridge_health'], health_data, retain=False)
    
    def setup_bridge_discovery(self, device_info: Dict[str, Any], sensors: List[Dict[str, Any]]) -> bool:
        """Set up Home Assistant auto-discovery for bridge monitoring."""
        if not self.connected:
            logger.error("Cannot setup bridge discovery: not connected")
            return False
        
        success = True
        
        # Publish discovery messages for each sensor
        for sensor in sensors:
            sensor["device"] = device_info
            sensor["unique_id"] = sensor["object_id"]
            # Replace deprecated object_id with default_entity_id
            sensor["default_entity_id"] = f"sensor.{sensor['object_id']}"
            del sensor["object_id"]  # Remove deprecated field
            
            discovery_topic = f"{self.discovery_prefix}/sensor/utec_bridge/{sensor['unique_id']}/config"
            if not self.publish(discovery_topic, sensor, retain=True):
                success = False
                logger.error(f"Failed to publish discovery for {sensor['name']}")
        
        # Binary sensor for connectivity (use constants)
        binary_sensor = {
            "name": "Utec Bridge Online",
            "default_entity_id": "binary_sensor.utec_bridge_online",
            "state_topic": MQTT_TOPICS['bridge_availability'],
            "payload_on": "online",
            "payload_off": "offline", 
            "device_class": "connectivity",
            "device": device_info,
            "unique_id": "utec_bridge_online"
        }
        
        discovery_topic = f"{self.discovery_prefix}/binary_sensor/utec_bridge/utec_bridge_online/config"
        if not self.publish(discovery_topic, binary_sensor, retain=True):
            success = False
        
        if success:
            logger.info("Bridge auto-discovery setup complete")
        
        return success
    
    def setup_lock_discovery(self, lock) -> bool:
        """Set up Home Assistant discovery for U-tec lock."""
        if not self.connected:
            logger.error("Cannot setup discovery: not connected")
            return False
        
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        device_info = self._create_device_info(lock)
        
        # Subscribe to commands for this device
        self.subscribe_to_device_commands(device_id)
        
        # Discovery configurations for U-tec lock (use constants for base config)
        discoveries = [
            # Main lock entity
            ("lock", f"{device_id}_lock", {
                "name": f"{device_name} Lock",
                "unique_id": f"{device_id}_lock",
                "default_entity_id": f"lock.{device_id}_lock",
                "device": device_info,
                "state_topic": MQTT_TOPICS['lock_state'].format(device_id=device_id),
                "command_topic": MQTT_TOPICS['lock_command'].format(device_id=device_id),
                "qos": self.qos,
                **HA_LOCK_DISCOVERY_CONFIG['lock']
            }),
            
            # Battery sensor
            ("sensor", f"{device_id}_battery", {
                "name": f"{device_name} Battery",
                "unique_id": f"{device_id}_battery",
                "default_entity_id": f"sensor.{device_id}_battery",
                "device": device_info,
                "state_topic": MQTT_TOPICS['battery_state'].format(device_id=device_id),
                **HA_LOCK_DISCOVERY_CONFIG['battery']
            }),
            
            # Lock mode sensor  
            ("sensor", f"{device_id}_lock_mode", {
                "name": f"{device_name} Lock Mode",
                "unique_id": f"{device_id}_lock_mode",
                "default_entity_id": f"sensor.{device_id}_lock_mode",
                "device": device_info,
                "state_topic": MQTT_TOPICS['lock_mode_state'].format(device_id=device_id),
                **HA_LOCK_DISCOVERY_CONFIG['lock_mode']
            }),
            
            # Autolock time sensor
            ("sensor", f"{device_id}_autolock", {
                "name": f"{device_name} Autolock Time", 
                "unique_id": f"{device_id}_autolock",
                "default_entity_id": f"sensor.{device_id}_autolock",
                "device": device_info,
                "state_topic": MQTT_TOPICS['autolock_state'].format(device_id=device_id),
                **HA_LOCK_DISCOVERY_CONFIG['autolock']
            }),
            
            # Mute status sensor
            ("binary_sensor", f"{device_id}_mute", {
                "name": f"{device_name} Mute",
                "unique_id": f"{device_id}_mute",
                "default_entity_id": f"binary_sensor.{device_id}_mute", 
                "device": device_info,
                "state_topic": MQTT_TOPICS['mute_state'].format(device_id=device_id),
                **HA_LOCK_DISCOVERY_CONFIG['mute']
            })
        ]
        
        # Add signal strength sensor if available (use constants)
        if hasattr(lock, 'rssi') or hasattr(lock, 'signal_strength'):
            discoveries.append(("sensor", f"{device_id}_signal", {
                "name": f"{device_name} Signal Strength",
                "unique_id": f"{device_id}_signal",
                "default_entity_id": f"sensor.{device_id}_signal",
                "device": device_info,
                "state_topic": MQTT_TOPICS['signal_state'].format(device_id=device_id),
                **HA_LOCK_DISCOVERY_CONFIG['signal']
            }))
        
        # Publish all discovery configurations
        success = True
        for component, object_id, config in discoveries:
            topic = f"{self.discovery_prefix}/{component}/{object_id}/config"
            if not self.publish(topic, config, retain=True):
                success = False
                logger.error(f"Failed to publish discovery for {object_id}")
        
        if success:
            logger.info(f"Set up Home Assistant discovery for {device_name}")
        
        return success
    
    def update_lock_state(self, lock) -> bool:
        """Update U-tec lock state in Home Assistant."""
        if not self.connected:
            logger.error("Cannot update state: not connected")
            return False
        
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        
        # Get raw values from lock
        raw_lock_status = getattr(lock, 'lock_status', -1)
        raw_battery = getattr(lock, 'battery', -1)
        raw_lock_mode = getattr(lock, 'lock_mode', -1)
        raw_autolock = getattr(lock, 'autolock_time', 0)
        raw_mute = getattr(lock, 'mute', False)
        
        # Convert to HA values using constants
        ha_lock_state = HA_LOCK_STATES.get(raw_lock_status, "UNKNOWN")
        ha_battery = HA_BATTERY_LEVELS.get(raw_battery, 0)
        ha_lock_mode = LOCK_MODE.get(raw_lock_mode, "Unknown")
        
        # Log the sensor values being published
        logger.info(f"Publishing states for {device_name}:")
        logger.info(f"  Lock: {ha_lock_state} (raw: {raw_lock_status}, meaning: {BOLT_STATUS.get(raw_lock_status, 'Unknown')})")
        logger.info(f"  Battery: {ha_battery}% (raw: {raw_battery}, meaning: {BATTERY_LEVEL.get(raw_battery, 'Unknown')})")
        logger.info(f"  Lock Mode: {ha_lock_mode} (raw: {raw_lock_mode})")
        logger.info(f"  Autolock: {raw_autolock}s")
        logger.info(f"  Mute: {raw_mute}")
        
        # Add signal strength if available
        if hasattr(lock, 'rssi'):
            logger.info(f"  Signal: {lock.rssi} dBm")
        elif hasattr(lock, 'signal_strength'):
            logger.info(f"  Signal: {lock.signal_strength} dBm")
        
        # Prepare state updates (use constants for topics)
        states = [
            (MQTT_TOPICS['lock_state'].format(device_id=device_id), ha_lock_state),
            (MQTT_TOPICS['battery_state'].format(device_id=device_id), ha_battery),
            (MQTT_TOPICS['lock_mode_state'].format(device_id=device_id), ha_lock_mode),
            (MQTT_TOPICS['autolock_state'].format(device_id=device_id), raw_autolock),
            (MQTT_TOPICS['mute_state'].format(device_id=device_id), str(raw_mute))
        ]
        
        # Add signal strength if available (use constants)
        if hasattr(lock, 'rssi'):
            states.append((MQTT_TOPICS['signal_state'].format(device_id=device_id), lock.rssi))
        elif hasattr(lock, 'signal_strength'):
            states.append((MQTT_TOPICS['signal_state'].format(device_id=device_id), lock.signal_strength))
        
        # Publish all states
        success = True
        for topic, payload in states:
            if not self.publish(topic, payload):
                success = False
        
        if success:
            logger.debug(f"Updated state for {device_name}")
        else:
            logger.error(f"Failed to update some states for {device_name}")
        
        return success
    
    def remove_device(self, lock) -> bool:
        """Remove U-tec lock device from Home Assistant."""
        if not self.connected:
            return False

        device_id = self._get_device_id(lock)

        # Unsubscribe from the device's command topic
        topic = f"{self.device_prefix}/{device_id}/lock/command"
        if self.client:
            self.client.unsubscribe(topic)
        if topic in self.device_subscriptions:
            self.device_subscriptions.remove(topic)

        # Remove all discovery configurations by publishing empty payloads
        components = [
            ("lock", f"{device_id}_lock"),
            ("sensor", f"{device_id}_battery"),
            ("sensor", f"{device_id}_lock_mode"),
            ("sensor", f"{device_id}_autolock"),
            ("binary_sensor", f"{device_id}_mute"),
            ("sensor", f"{device_id}_signal")
        ]
        
        success = True
        for component, object_id in components:
            topic = f"{self.discovery_prefix}/{component}/{object_id}/config"
            if not self.publish(topic, ""):  # Empty payload removes device
                success = False
        
        if success:
            logger.info(f"Removed device {self._get_device_name(lock)} from Home Assistant")
        
        return success
    
    def disconnect(self):
        """Gracefully disconnect from MQTT broker."""
        if self.client and self.connected:
            logger.info("Disconnecting from MQTT broker")

            # Publish offline status before disconnecting (use constants)
            self.publish(MQTT_TOPICS['bridge_availability'], "offline", retain=True)

            # Unsubscribe from all device command topics
            for topic in list(self.device_subscriptions):
                self.client.unsubscribe(topic)
            self.device_subscriptions.clear()

            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            logger.info("MQTT client disconnected")
    
    def _get_device_id(self, lock) -> str:
        """Get clean device ID from lock MAC."""
        return lock.mac_uuid.replace(":", "_").lower()
    
    def _get_device_name(self, lock) -> str:
        """Get friendly device name."""
        return lock.name or f"U-tec Lock {lock.mac_uuid[-5:]}"
    
    def _create_device_info(self, lock) -> Dict[str, Any]:
        """Create Home Assistant device information."""
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        
        return {
            "identifiers": [device_id],
            "name": device_name,
            "model": getattr(lock, 'model', 'U-tec Lock'),
            "manufacturer": "U-tec",
            "sw_version": getattr(lock, 'firmware_version', 'Unknown'),
            "via_device": f"{self.device_prefix}_bridge"
        }