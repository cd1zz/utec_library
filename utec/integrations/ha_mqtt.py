"""
Simplified MQTT client for U-tec smart locks and Home Assistant integration.
Follows KISS, YAGNI, and SOLID principles.
"""

import json
import logging
import time
import paho.mqtt.client as mqtt
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class UtecMQTTClient:
    """Simple MQTT client for U-tec locks with Home Assistant integration."""
    
    def __init__(self, broker_host: str, broker_port: int = 1883, 
                 username: Optional[str] = None, password: Optional[str] = None):
        """Initialize MQTT client with minimal required configuration."""
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.username = username
        self.password = password
        
        # MQTT settings
        self.keepalive = 60
        self.qos = 1
        self.retain = True
        
        # Topic configuration
        self.discovery_prefix = "homeassistant"
        self.device_prefix = "utec"
        
        # Connection state
        self.connected = False
        self.client = None
        
        # Reconnection settings
        self.reconnect_delay = 5.0
        self.max_reconnect_attempts = 10
        
        logger.info(f"U-tec MQTT client initialized for {broker_host}:{broker_port}")
    
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
                return True
            else:
                logger.error("Failed to connect within timeout period")
                return False
                
        except Exception as e:
            logger.error(f"Connection error: {e}")
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
        
        # Set authentication if provided
        if self.username and self.password:
            client.username_pw_set(self.username, self.password)
        
        # Set callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_log = self._on_log
        
        return client
    
    def _on_connect(self, client, userdata, flags, rc):
        """Handle connection events."""
        if rc == 0:
            self.connected = True
            logger.info("Connected to MQTT broker")
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
    
    def _on_log(self, client, userdata, level, buf):
        """Handle MQTT client logs."""
        if level == mqtt.MQTT_LOG_ERR:
            logger.error(f"MQTT: {buf}")
        elif level == mqtt.MQTT_LOG_WARNING:
            logger.warning(f"MQTT: {buf}")
        else:
            logger.debug(f"MQTT: {buf}")
    
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
    
    def publish(self, topic: str, payload: Any) -> bool:
        """Publish message to MQTT broker."""
        if not self.connected:
            logger.error("Cannot publish: not connected to broker")
            return False
        
        try:
            # Convert payload to JSON if needed
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload)
            else:
                payload_str = str(payload)
            
            result = self.client.publish(topic, payload_str, qos=self.qos, retain=self.retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {topic}")
                return True
            else:
                logger.error(f"Publish failed to {topic}: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Publish error for {topic}: {e}")
            return False
    
    def setup_lock_discovery(self, lock) -> bool:
        """Set up Home Assistant discovery for U-tec lock."""
        if not self.connected:
            logger.error("Cannot setup discovery: not connected")
            return False
        
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        device_info = self._create_device_info(lock)
        
        # Discovery configurations for U-tec lock
        discoveries = [
            # Main lock entity
            ("lock", f"{device_id}_lock", {
                "name": f"{device_name} Lock",
                "unique_id": f"{device_id}_lock",
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/lock/state",
                "command_topic": f"{self.device_prefix}/{device_id}/lock/command",
                "payload_lock": "LOCK",
                "payload_unlock": "UNLOCK",
                "state_locked": "LOCKED", 
                "state_unlocked": "UNLOCKED",
                "optimistic": False,
                "qos": self.qos,
                "device_class": "lock"
            }),
            
            # Battery sensor
            ("sensor", f"{device_id}_battery", {
                "name": f"{device_name} Battery",
                "unique_id": f"{device_id}_battery",
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/battery/state",
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
                "entity_category": "diagnostic"
            }),
            
            # Lock mode sensor  
            ("sensor", f"{device_id}_lock_mode", {
                "name": f"{device_name} Lock Mode",
                "unique_id": f"{device_id}_lock_mode",
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/lock_mode/state",
                "icon": "mdi:lock-outline",
                "entity_category": "diagnostic"
            }),
            
            # Autolock time sensor
            ("sensor", f"{device_id}_autolock", {
                "name": f"{device_name} Autolock Time", 
                "unique_id": f"{device_id}_autolock",
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/autolock/state",
                "unit_of_measurement": "s",
                "icon": "mdi:timer-outline",
                "entity_category": "config"
            }),
            
            # Mute status sensor
            ("binary_sensor", f"{device_id}_mute", {
                "name": f"{device_name} Mute",
                "unique_id": f"{device_id}_mute", 
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/mute/state",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:volume-off",
                "entity_category": "diagnostic"
            })
        ]
        
        # Add signal strength sensor if available
        if hasattr(lock, 'rssi') or hasattr(lock, 'signal_strength'):
            discoveries.append(("sensor", f"{device_id}_signal", {
                "name": f"{device_name} Signal Strength",
                "unique_id": f"{device_id}_signal",
                "device": device_info,
                "state_topic": f"{self.device_prefix}/{device_id}/signal/state",
                "unit_of_measurement": "dBm",
                "device_class": "signal_strength",
                "state_class": "measurement", 
                "entity_category": "diagnostic"
            }))
        
        # Publish all discovery configurations
        success = True
        for component, object_id, config in discoveries:
            topic = f"{self.discovery_prefix}/{component}/{object_id}/config"
            if not self.publish(topic, config):
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
        
        # U-tec specific state mappings
        lock_states = {
            0: "UNAVAILABLE",
            1: "UNLOCKED", 
            2: "LOCKED",
            3: "JAMMED",
            -1: "UNKNOWN",
            255: "NOTAVAILABLE"
        }
        
        lock_modes = {
            0: "Normal",
            1: "Passage Mode", 
            2: "Lockout Mode",
            -1: "Unknown"
        }
        
        # U-tec battery level mappings
        battery_levels = {
            -1: 0,   # Unknown
            0: 10,   # Critical
            1: 25,   # Low
            2: 60,   # Medium  
            3: 90    # High
        }
        
        # Prepare state updates
        states = [
            (f"{self.device_prefix}/{device_id}/lock/state",
             lock_states.get(getattr(lock, 'lock_status', -1), "UNKNOWN")),
            (f"{self.device_prefix}/{device_id}/battery/state", 
             battery_levels.get(getattr(lock, 'battery', -1), 0)),
            (f"{self.device_prefix}/{device_id}/lock_mode/state",
             lock_modes.get(getattr(lock, 'lock_mode', -1), "Unknown")),
            (f"{self.device_prefix}/{device_id}/autolock/state",
             getattr(lock, 'autolock_time', 0)),
            (f"{self.device_prefix}/{device_id}/mute/state", 
             str(getattr(lock, 'mute', False)))
        ]
        
        # Add signal strength if available
        if hasattr(lock, 'rssi'):
            states.append((f"{self.device_prefix}/{device_id}/signal/state", lock.rssi))
        elif hasattr(lock, 'signal_strength'):
            states.append((f"{self.device_prefix}/{device_id}/signal/state", lock.signal_strength))
        
        # Publish all states
        success = True
        for topic, payload in states:
            if not self.publish(topic, payload):
                success = False
        
        if success:
            logger.debug(f"Updated state for {self._get_device_name(lock)}")
        else:
            logger.error(f"Failed to update some states for {self._get_device_name(lock)}")
        
        return success
    
    def remove_device(self, lock) -> bool:
        """Remove U-tec lock device from Home Assistant."""
        if not self.connected:
            return False
        
        device_id = self._get_device_id(lock)
        
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


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Initialize client
    mqtt_client = UtecMQTTClient(
        broker_host="your-ha-broker.local",
        username="mqtt_user", 
        password="mqtt_password"
    )
    
    # Connect and set up discovery
    if mqtt_client.connect():
        # Example lock object (replace with your actual lock data)
        class MockLock:
            mac_uuid = "aa:bb:cc:dd:ee:ff"
            name = "Front Door Lock"
            lock_status = 2  # LOCKED
            battery = 2      # Medium
            lock_mode = 0    # Normal
            autolock_time = 30
            mute = False
            rssi = -45
        
        lock = MockLock()
        
        # Set up discovery once
        mqtt_client.setup_lock_discovery(lock)
        
        # Update state (do this every 5 minutes)
        mqtt_client.update_lock_state(lock)
        
        # Clean disconnect
        mqtt_client.disconnect()