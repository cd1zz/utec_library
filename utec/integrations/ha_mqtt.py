"""Home Assistant MQTT integration for U-tec locks."""

import json
import logging
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


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


class HomeAssistantMQTTIntegration:
    """Integration class for publishing U-tec lock data to Home Assistant via MQTT."""
    
    def __init__(self, config: HAMQTTConfig):
        """Initialize the MQTT integration.
        
        Args:
            config: MQTT configuration.
        """
        self.config = config
        
        # Generate client ID if not provided
        client_id = config.client_id
        if not client_id:
            import socket
            import time
            hostname = socket.gethostname()
            timestamp = int(time.time())
            client_id = f"utec_ha_{hostname}_{timestamp}"
        
        # Create MQTT client with client ID
        self.client = mqtt.Client(client_id=client_id, clean_session=False)
        self.is_connected = False
        
        # Set up MQTT client
        if config.username and config.password:
            self.client.username_pw_set(config.username, config.password)
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.on_log = self._on_log
        
        logger.info(f"MQTT client initialized with ID: {client_id}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback for when MQTT client connects."""
        if rc == 0:
            self.is_connected = True
            logger.info(f"Connected to MQTT broker as client '{client._client_id.decode()}'")
        else:
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorised"
            }
            error_msg = error_messages.get(rc, f"Connection refused - unknown error ({rc})")
            logger.error(f"Failed to connect to MQTT broker: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback for when MQTT client disconnects."""
        self.is_connected = False
        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnection (client: {client._client_id.decode()}, rc: {rc})")
        else:
            logger.info(f"Disconnected from MQTT broker (client: {client._client_id.decode()})")
    
    def _on_publish(self, client, userdata, mid):
        """Callback for when message is published."""
        logger.debug(f"Message {mid} published by client {client._client_id.decode()}")
    
    def _on_log(self, client, userdata, level, buf):
        """Callback for MQTT client logging."""
        # Map MQTT log levels to Python logging levels
        log_level_map = {
            mqtt.MQTT_LOG_INFO: logging.INFO,
            mqtt.MQTT_LOG_NOTICE: logging.INFO,
            mqtt.MQTT_LOG_WARNING: logging.WARNING,
            mqtt.MQTT_LOG_ERR: logging.ERROR,
            mqtt.MQTT_LOG_DEBUG: logging.DEBUG
        }
        
        python_level = log_level_map.get(level, logging.INFO)
        logger.log(python_level, f"MQTT ({client._client_id.decode()}): {buf}")
    
    async def connect(self) -> bool:
        """Connect to MQTT broker.
        
        Returns:
            True if connected successfully.
        """
        try:
            self.client.connect(self.config.broker_host, self.config.broker_port, self.config.keepalive)
            self.client.loop_start()
            
            # Wait for connection
            for _ in range(50):  # 5 second timeout
                if self.is_connected:
                    return True
                await asyncio.sleep(0.1)
            
            logger.error("Timeout waiting for MQTT connection")
            return False
            
        except Exception as e:
            logger.error(f"Error connecting to MQTT broker: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info(f"MQTT client {self.client._client_id.decode()} disconnected")

    
    def _get_device_id(self, lock) -> str:
        """Get a clean device ID for Home Assistant.
        
        Args:
            lock: Lock device object.
            
        Returns:
            Clean device ID.
        """
        # Use MAC address as base, replace colons with underscores
        return lock.mac_uuid.replace(":", "_").lower()
    
    def _get_device_name(self, lock) -> str:
        """Get a friendly device name.
        
        Args:
            lock: Lock device object.
            
        Returns:
            Friendly device name.
        """
        return lock.name or f"U-tec Lock {lock.mac_uuid[-5:]}"
    
    def _create_device_info(self, lock) -> Dict[str, Any]:
        """Create device information for Home Assistant discovery.
        
        Args:
            lock: Lock device object.
            
        Returns:
            Device information dictionary.
        """
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
        """Publish discovery configuration for a component.
        
        Args:
            component: Component type (lock, sensor, etc.).
            object_id: Unique object ID.
            config: Configuration dictionary.
            
        Returns:
            True if published successfully.
        """
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
                logger.error(f"Failed to publish discovery config for {object_id}")
                return False
        except Exception as e:
            logger.error(f"Error publishing discovery config for {object_id}: {e}")
            return False
    
    def _publish_state(self, topic: str, state: Any) -> bool:
        """Publish state to MQTT topic.
        
        Args:
            topic: MQTT topic.
            state: State value.
            
        Returns:
            True if published successfully.
        """
        try:
            payload = str(state) if not isinstance(state, (dict, list)) else json.dumps(state)
            result = self.client.publish(topic, payload, qos=self.config.qos, retain=self.config.retain)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                return True
            else:
                logger.error(f"Failed to publish state to {topic}")
                return False
        except Exception as e:
            logger.error(f"Error publishing state to {topic}: {e}")
            return False
    
    def setup_lock_discovery(self, lock) -> bool:
        """Set up Home Assistant discovery for a lock device.
        
        Args:
            lock: Lock device object.
            
        Returns:
            True if setup was successful.
        """
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
        """Publish current lock state to MQTT.
        
        Args:
            lock: Lock device object.
            
        Returns:
            True if all states were published successfully.
        """
        if not self.is_connected:
            logger.error("Not connected to MQTT broker")
            return False
        
        device_id = self._get_device_id(lock)
        success = True
        
        # Map lock status to Home Assistant values
        lock_state_map = {
            0: "UNAVAILABLE",
            1: "UNLOCKED",    
            2: "LOCKED",  
            -1: "UNKNOWN",
            255: "NOTAVAILABLE"   
        }
        
        # Lock mode mapping
        lock_mode_map = {
            0: "Normal",
            1: "Passage Mode", 
            2: "Lockout Mode",
            -1: "Unknown"
        }
        
        # Battery level mapping (convert numeric to percentage)
        battery_map = {
            -1: 0,   # Depleted
            0: 10,   # Replace
            1: 25,   # Low
            2: 60,   # Medium
            3: 90    # High
        }
        
        # Publish lock state
        lock_state = lock_state_map.get(getattr(lock, 'lock_status', -1), "UNKNOWN")
        if not self._publish_state(f"{self.config.device_prefix}/{device_id}/lock/state", lock_state):
            success = False
        
        # Publish battery level
        battery_level = battery_map.get(getattr(lock, 'battery', -1), 0)
        if not self._publish_state(f"{self.config.device_prefix}/{device_id}/battery/state", battery_level):
            success = False
        
        # Publish lock mode
        lock_mode = lock_mode_map.get(getattr(lock, 'lock_mode', -1), "Unknown")
        if not self._publish_state(f"{self.config.device_prefix}/{device_id}/lock_mode/state", lock_mode):
            success = False
        
        # Publish autolock time
        autolock_time = getattr(lock, 'autolock_time', 0)
        if not self._publish_state(f"{self.config.device_prefix}/{device_id}/autolock/state", autolock_time):
            success = False
        
        # Publish mute status
        mute_status = getattr(lock, 'mute', False)
        if not self._publish_state(f"{self.config.device_prefix}/{device_id}/mute/state", str(mute_status)):
            success = False
        
        # Publish signal strength if available
        if hasattr(lock, 'rssi'):
            if not self._publish_state(f"{self.config.device_prefix}/{device_id}/signal/state", lock.rssi):
                success = False
        elif hasattr(lock, 'signal_strength'):
            if not self._publish_state(f"{self.config.device_prefix}/{device_id}/signal/state", lock.signal_strength):
                success = False
        
        if success:
            logger.info(f"Published state for {self._get_device_name(lock)}")
        
        return success
    
    def remove_device(self, lock) -> bool:
        """Remove device from Home Assistant by publishing empty discovery configs.
        
        Args:
            lock: Lock device object.
            
        Returns:
            True if removal was successful.
        """
        if not self.is_connected:
            logger.error("Not connected to MQTT broker")
            return False
        
        device_id = self._get_device_id(lock)
        success = True
        
        # List of components to remove
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


# Example usage function
async def main():
    """Example of how to use the MQTT integration."""
    
    # Configure MQTT
    mqtt_config = HAMQTTConfig(
        broker_host="your-mqtt-broker",  # Change to your MQTT broker
        broker_port=1883,
        username="your-username",       # Optional
        password="your-password",       # Optional
        discovery_prefix="homeassistant",
        device_prefix="utec"
    )
    
    # Create integration
    ha_mqtt = HomeAssistantMQTTIntegration(mqtt_config)
    
    # Connect to MQTT
    if not await ha_mqtt.connect():
        logger.error("Failed to connect to MQTT broker")
        return
    
    try:
        # Your existing U-tec code here to get locks
        # import utec
        # locks = await utec.discover_devices("email", "password")
        
        # For each lock, set up discovery and publish state
        # for lock in locks:
        #     # Set up Home Assistant discovery
        #     if ha_mqtt.setup_lock_discovery(lock):
        #         logger.info(f"Set up discovery for {lock.name}")
        #     
        #     # Update lock status
        #     await lock.async_update_status()
        #     
        #     # Publish current state
        #     if ha_mqtt.publish_lock_state(lock):
        #         logger.info(f"Published state for {lock.name}")
        
        # Keep the connection alive for ongoing updates
        while True:
            await asyncio.sleep(300)  # Update every 5 minutes
            
            # Update and publish states for all locks
            # for lock in locks:
            #     try:
            #         await lock.async_update_status()
            #         ha_mqtt.publish_lock_state(lock)
            #     except Exception as e:
            #         logger.error(f"Error updating {lock.name}: {e}")
    
    finally:
        ha_mqtt.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())