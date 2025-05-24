"""
MQTT Client for Utec and local HA MQTT Broker
"""

import json
import logging
import asyncio
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import paho.mqtt.client as mqtt
from threading import Event, Lock
import socket
import re

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """MQTT connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class HAMQTTConfig:
    """Configuration for Home Assistant MQTT integration."""
    
    broker_host: str = "localhost"
    broker_port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    discovery_prefix: str = "homeassistant"
    device_prefix: str = "utec"
    qos: int = 1
    retain: bool = True
    keepalive: int = 60
    
    # Reconnection settings (optional - backward compatible)
    reconnect_on_failure: bool = True
    max_reconnect_delay: int = 300
    initial_reconnect_delay: int = 1
    connect_timeout: int = 30
    
    # LWT settings (optional - backward compatible) 
    lwt_topic: Optional[str] = None
    lwt_payload: str = "offline"
    lwt_qos: int = 1
    lwt_retain: bool = True

    def __post_init__(self):
        """Set default LWT topic if not provided."""
        if self.lwt_topic is None:
            self.lwt_topic = f"{self.device_prefix}/bridge/status"


class HomeAssistantMQTTIntegration:
    """Improved MQTT integration with minimal breaking changes."""
    
    def __init__(self, config: HAMQTTConfig):
        """Initialize the MQTT integration."""
        self.config = config
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = Lock()
        self._connection_event = Event()
        self._shutdown_event = Event()
        self._reconnect_task = None
        self._reconnect_delay = config.initial_reconnect_delay
        self._last_connect_attempt = 0
        
        # Generate safe client ID
        self._client_id = self._generate_safe_client_id(config.client_id)
        
        # Create MQTT client with version compatibility
        self.client = self._create_mqtt_client()
        
        logger.info(f"MQTT client initialized with ID: {self._client_id}")
    
    def _generate_safe_client_id(self, provided_id: Optional[str]) -> str:
        """Generate MQTT-compliant client ID."""
        if provided_id and len(provided_id) <= 23:
            # If provided ID is short enough, clean it and use it
            clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', provided_id)
            if clean_id and not clean_id[0].isdigit():
                return clean_id
        
        # Generate new ID
        hostname = socket.gethostname()
        timestamp = int(time.time()) % 100000  # Last 5 digits for uniqueness
        base_id = f"utec_ha_{hostname}_{timestamp}"
        
        # Clean and truncate
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', base_id)
        if len(clean_id) > 23:
            clean_id = clean_id[:23]
        
        # Remove trailing underscores/dashes
        clean_id = clean_id.rstrip('_-')
        
        # Ensure valid ID
        if not clean_id or clean_id[0].isdigit():
            clean_id = f"utec_{timestamp}"
        
        logger.info(f"Generated MQTT client ID: {clean_id} (length: {len(clean_id)})")
        return clean_id
    
    def _create_mqtt_client(self) -> mqtt.Client:
        """Create MQTT client with version compatibility."""
        try:
            # Try paho-mqtt 2.0+ syntax first
            try:
                from paho.mqtt.client import CallbackAPIVersion
                client = mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION1,
                    client_id=self._client_id,
                    clean_session=False,
                    protocol=mqtt.MQTTv311
                )
                logger.debug("Using paho-mqtt 2.0+ API")
            except (ImportError, TypeError):
                # Fall back to paho-mqtt 1.x syntax
                client = mqtt.Client(
                    client_id=self._client_id,
                    clean_session=False,
                    protocol=mqtt.MQTTv311
                )
                logger.debug("Using paho-mqtt 1.x API")
            
            # Authentication
            if self.config.username and self.config.password:
                client.username_pw_set(self.config.username, self.config.password)
            
            # Last Will and Testament
            if self.config.lwt_topic:
                client.will_set(
                    self.config.lwt_topic,
                    self.config.lwt_payload,
                    qos=self.config.lwt_qos,
                    retain=self.config.lwt_retain
                )
                logger.debug(f"LWT configured: {self.config.lwt_topic}")
            
            # Connection settings
            client.socket_timeout = self.config.connect_timeout
            client.socket_keepalive = self.config.keepalive
            
            # Set optional limits if methods exist
            try:
                client.max_inflight_messages_set(20)
            except AttributeError:
                pass
            
            try:
                client.max_queued_messages_set(100)
            except AttributeError:
                pass
            
            # Set up callbacks
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_publish = self._on_publish
            client.on_log = self._on_log
            
            return client
            
        except Exception as e:
            logger.error(f"Error creating MQTT client: {e}")
            raise
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        with self._state_lock:
            return self._state == ConnectionState.CONNECTED
    
    def _set_state(self, new_state: ConnectionState):
        """Thread-safe state change."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            
            if new_state == ConnectionState.CONNECTED:
                self._connection_event.set()
            else:
                self._connection_event.clear()
                
            logger.debug(f"State: {old_state.value} -> {new_state.value}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Connection callback."""
        if rc == 0:
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_delay = self.config.initial_reconnect_delay
            
            # Publish online status
            if self.config.lwt_topic:
                try:
                    client.publish(
                        self.config.lwt_topic,
                        "online",
                        qos=self.config.lwt_qos,
                        retain=self.config.lwt_retain
                    )
                except Exception as e:
                    logger.warning(f"Failed to publish online status: {e}")
            
            logger.info(f"Connected to MQTT broker (client: {self._client_id})")
        else:
            self._set_state(ConnectionState.DISCONNECTED)
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier", 
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorised"
            }
            error_msg = error_messages.get(rc, f"Connection refused - unknown error ({rc})")
            logger.error(f"Failed to connect: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Disconnect callback with automatic reconnection."""
        self._set_state(ConnectionState.DISCONNECTED)
        
        if rc == 0:
            logger.info(f"Clean disconnect (client: {self._client_id})")
        else:
            logger.warning(f"Unexpected disconnect (client: {self._client_id}, rc: {rc})")
            
            # Start reconnection if enabled
            if (self.config.reconnect_on_failure and 
                not self._shutdown_event.is_set() and
                (not self._reconnect_task or self._reconnect_task.done())):
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    def _on_publish(self, client, userdata, mid):
        """Publish acknowledgment callback."""
        logger.debug(f"Message {mid} published successfully")
    
    def _on_log(self, client, userdata, level, buf):
        """MQTT client logging."""
        log_level_map = {
            mqtt.MQTT_LOG_INFO: logging.INFO,
            mqtt.MQTT_LOG_NOTICE: logging.INFO,
            mqtt.MQTT_LOG_WARNING: logging.WARNING,
            mqtt.MQTT_LOG_ERR: logging.ERROR,
            mqtt.MQTT_LOG_DEBUG: logging.DEBUG
        }
        
        python_level = log_level_map.get(level, logging.INFO)
        logger.log(python_level, f"MQTT ({self._client_id}): {buf}")
    
    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        if self.is_connected:
            return True
            
        self._set_state(ConnectionState.CONNECTING)
        self._last_connect_attempt = time.time()
        
        try:
            # Start connection
            self.client.connect_async(
                self.config.broker_host,
                self.config.broker_port,
                self.config.keepalive
            )
            self.client.loop_start()
            
            # Wait for connection
            for _ in range(int(self.config.connect_timeout * 10)):  # Check every 0.1s
                if self._connection_event.wait(timeout=0.1):
                    return True
                await asyncio.sleep(0.1)
            
            logger.error(f"Connection timeout after {self.config.connect_timeout}s")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
    
    async def _reconnect_loop(self):
        """Automatic reconnection with exponential backoff."""
        self._set_state(ConnectionState.RECONNECTING)
        
        while (not self.is_connected and not self._shutdown_event.is_set()):
            # Wait before reconnection attempt
            time_since_last = time.time() - self._last_connect_attempt
            if time_since_last < self._reconnect_delay:
                sleep_time = self._reconnect_delay - time_since_last
                logger.info(f"Waiting {sleep_time:.1f}s before reconnection")
                await asyncio.sleep(sleep_time)
            
            logger.info(f"Attempting reconnection (delay: {self._reconnect_delay}s)")
            
            if await self.connect():
                logger.info("Reconnected successfully")
                return
            
            # Increase delay for next attempt
            self._reconnect_delay = min(
                self._reconnect_delay * 1.5,
                self.config.max_reconnect_delay
            )
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        self._shutdown_event.set()
        
        # Cancel reconnection
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        
        if self.client:
            # Publish offline status
            if self.config.lwt_topic and self.is_connected:
                try:
                    self.client.publish(
                        self.config.lwt_topic,
                        self.config.lwt_payload,
                        qos=self.config.lwt_qos,
                        retain=self.config.lwt_retain
                    )
                except Exception as e:
                    logger.warning(f"Failed to publish offline status: {e}")
            
            self.client.loop_stop()
            self.client.disconnect()
            logger.info(f"MQTT client {self._client_id} disconnected")
    
    # Keep all existing methods with same signatures for compatibility
    
    def _get_device_id(self, lock) -> str:
        """Get a clean device ID for Home Assistant."""
        return lock.mac_uuid.replace(":", "_").lower()
    
    def _get_device_name(self, lock) -> str:
        """Get a friendly device name."""
        return lock.name or f"U-tec Lock {lock.mac_uuid[-5:]}"
    
    def _create_device_info(self, lock) -> Dict[str, Any]:
        """Create device information for Home Assistant discovery."""
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        
        return {
            "identifiers": [device_id],
            "name": device_name,
            "model": lock.model or "U-tec Lock",
            "manufacturer": "U-tec",
            "sw_version": getattr(lock, 'firmware_version', 'Unknown'),
            "via_device": f"{self.config.device_prefix}_bridge"
        }
    
    def _publish_discovery_config(self, component: str, object_id: str, 
                                config: Dict[str, Any]) -> bool:
        """Publish discovery configuration for a component."""
        topic = f"{self.config.discovery_prefix}/{component}/{object_id}/config"
        payload = json.dumps(config)
        
        try:
            result = self.client.publish(topic, payload, 
                                       qos=self.config.qos, 
                                       retain=self.config.retain)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published discovery config for {object_id}")
                return True
            else:
                logger.error(f"Failed to publish discovery config for {object_id}: {result.rc}")
                return False
        except Exception as e:
            logger.error(f"Error publishing discovery config for {object_id}: {e}")
            return False
    
    def _publish_state(self, topic: str, state: Any) -> bool:
        """Publish state to MQTT topic."""
        try:
            payload = str(state) if not isinstance(state, (dict, list)) else json.dumps(state)
            result = self.client.publish(topic, payload, qos=self.config.qos, retain=self.config.retain)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                return True
            else:
                logger.error(f"Failed to publish state to {topic}: {result.rc}")
                return False
        except Exception as e:
            logger.error(f"Error publishing state to {topic}: {e}")
            return False
    
    def setup_lock_discovery(self, lock) -> bool:
        """Set up Home Assistant discovery for a lock device."""
        if not self.is_connected:
            logger.error("Not connected to MQTT broker")
            return False
        
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        device_info = self._create_device_info(lock)
        
        success = True
        
        # Main lock entity
        lock_config = {
            "name": f"{device_name} Lock",
            "unique_id": f"{device_id}_lock",
            "device": device_info,
            "state_topic": f"{self.config.device_prefix}/{device_id}/lock/state",
            "command_topic": f"{self.config.device_prefix}/{device_id}/lock/command",
            "payload_lock": "LOCK",
            "payload_unlock": "UNLOCK",
            "state_locked": "LOCKED",
            "state_unlocked": "UNLOCKED",
            "optimistic": False,
            "qos": self.config.qos,
            "device_class": "lock"
        }
        
        if not self._publish_discovery_config("lock", f"{device_id}_lock", lock_config):
            success = False
        
        # Battery sensor
        battery_config = {
            "name": f"{device_name} Battery",
            "unique_id": f"{device_id}_battery",
            "device": device_info,
            "state_topic": f"{self.config.device_prefix}/{device_id}/battery/state",
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
            "entity_category": "diagnostic"
        }
        
        if not self._publish_discovery_config("sensor", f"{device_id}_battery", battery_config):
            success = False
        
        # Lock mode sensor
        lock_mode_config = {
            "name": f"{device_name} Lock Mode",
            "unique_id": f"{device_id}_lock_mode",
            "device": device_info,
            "state_topic": f"{self.config.device_prefix}/{device_id}/lock_mode/state",
            "icon": "mdi:lock-outline",
            "entity_category": "diagnostic"
        }
        
        if not self._publish_discovery_config("sensor", f"{device_id}_lock_mode", lock_mode_config):
            success = False
        
        # Autolock time sensor
        autolock_config = {
            "name": f"{device_name} Autolock Time",
            "unique_id": f"{device_id}_autolock",
            "device": device_info,
            "state_topic": f"{self.config.device_prefix}/{device_id}/autolock/state",
            "unit_of_measurement": "s",
            "icon": "mdi:timer-outline",
            "entity_category": "config"
        }
        
        if not self._publish_discovery_config("sensor", f"{device_id}_autolock", autolock_config):
            success = False
        
        # Mute status binary sensor
        mute_config = {
            "name": f"{device_name} Mute",
            "unique_id": f"{device_id}_mute",
            "device": device_info,
            "state_topic": f"{self.config.device_prefix}/{device_id}/mute/state",
            "payload_on": "True",
            "payload_off": "False",
            "icon": "mdi:volume-off",
            "entity_category": "diagnostic"
        }
        
        if not self._publish_discovery_config("binary_sensor", f"{device_id}_mute", mute_config):
            success = False
        
        # Signal strength sensor (if available)
        if hasattr(lock, 'rssi') or hasattr(lock, 'signal_strength'):
            signal_config = {
                "name": f"{device_name} Signal Strength",
                "unique_id": f"{device_id}_signal",
                "device": device_info,
                "state_topic": f"{self.config.device_prefix}/{device_id}/signal/state",
                "unit_of_measurement": "dBm",
                "device_class": "signal_strength",
                "state_class": "measurement",
                "entity_category": "diagnostic"
            }
            
            if not self._publish_discovery_config("sensor", f"{device_id}_signal", signal_config):
                success = False
        
        return success
    
    def publish_lock_state(self, lock) -> bool:
        """Publish current lock state to MQTT."""
        if not self.is_connected:
            logger.error("Not connected to MQTT broker")
            return False
        
        device_id = self._get_device_id(lock)
        success = True
        
        # State mappings (same as original)
        lock_state_map = {
            0: "UNAVAILABLE", 1: "UNLOCKED", 2: "LOCKED", 
            3: "JAMMED", -1: "UNKNOWN", 255: "NOTAVAILABLE"
        }
        
        lock_mode_map = {
            0: "Normal", 1: "Passage Mode", 2: "Lockout Mode", -1: "Unknown"
        }
        
        battery_map = {
            -1: 0, 0: 10, 1: 25, 2: 60, 3: 90
        }
        
        # Publish all states
        states_to_publish = [
            (f"{self.config.device_prefix}/{device_id}/lock/state", 
             lock_state_map.get(getattr(lock, 'lock_status', -1), "UNKNOWN")),
            (f"{self.config.device_prefix}/{device_id}/battery/state", 
             battery_map.get(getattr(lock, 'battery', -1), 0)),
            (f"{self.config.device_prefix}/{device_id}/lock_mode/state", 
             lock_mode_map.get(getattr(lock, 'lock_mode', -1), "Unknown")),
            (f"{self.config.device_prefix}/{device_id}/autolock/state", 
             getattr(lock, 'autolock_time', 0)),
            (f"{self.config.device_prefix}/{device_id}/mute/state", 
             str(getattr(lock, 'mute', False)))
        ]
        
        # Add signal strength if available
        if hasattr(lock, 'rssi'):
            states_to_publish.append((f"{self.config.device_prefix}/{device_id}/signal/state", lock.rssi))
        elif hasattr(lock, 'signal_strength'):
            states_to_publish.append((f"{self.config.device_prefix}/{device_id}/signal/state", lock.signal_strength))
        
        # Publish all states
        for topic, payload in states_to_publish:
            if not self._publish_state(topic, payload):
                success = False
        
        if success:
            logger.debug(f"Published state for {self._get_device_name(lock)}")
        
        return success
    
    def remove_device(self, lock) -> bool:
        """Remove device from Home Assistant."""
        if not self.is_connected:
            logger.error("Not connected to MQTT broker")
            return False
        
        device_id = self._get_device_id(lock)
        success = True
        
        components = [
            ("lock", f"{device_id}_lock"),
            ("sensor", f"{device_id}_battery"),
            ("sensor", f"{device_id}_lock_mode"),
            ("sensor", f"{device_id}_autolock"),
            ("binary_sensor", f"{device_id}_mute"),
            ("sensor", f"{device_id}_signal")
        ]
        
        for component, object_id in components:
            topic = f"{self.config.discovery_prefix}/{component}/{object_id}/config"
            result = self.client.publish(topic, "", qos=self.config.qos, retain=True)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                success = False
                logger.error(f"Failed to remove {object_id}")
        
        return success
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get basic connection statistics."""
        return {
            "client_id": self._client_id,
            "state": self._state.value,
            "is_connected": self.is_connected,
            "reconnect_delay": self._reconnect_delay
        }