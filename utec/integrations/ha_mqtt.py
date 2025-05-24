"""Complete Home Assistant MQTT integration with robust disconnection handling."""

import json
import logging
import asyncio
import time
from typing import Dict, Any, Optional, Callable, Tuple, List
from dataclasses import dataclass, field
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


class ConnectionHealth(Enum):
    """Connection health states."""
    HEALTHY = "healthy"
    DEGRADED = "degraded" 
    UNHEALTHY = "unhealthy"
    FAILED = "failed"


@dataclass
class ConnectionMetrics:
    """Track connection health metrics."""
    last_successful_publish: float = 0
    last_ping_response: float = 0
    consecutive_failures: int = 0
    total_reconnections: int = 0
    connection_uptime_start: float = 0
    
    def reset_failures(self):
        """Reset failure counters on successful operation."""
        self.consecutive_failures = 0
        self.last_successful_publish = time.time()
    
    def record_failure(self):
        """Record a failure event."""
        self.consecutive_failures += 1
    
    @property
    def uptime(self) -> float:
        """Get current connection uptime in seconds."""
        if self.connection_uptime_start > 0:
            return time.time() - self.connection_uptime_start
        return 0


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
    
    # Reconnection settings
    reconnect_on_failure: bool = True
    max_reconnect_delay: int = 300  # 5 minutes max
    reconnect_delay_multiplier: float = 1.5
    initial_reconnect_delay: int = 1
    
    # Last Will and Testament
    lwt_topic: Optional[str] = None
    lwt_payload: str = "offline"
    lwt_qos: int = 1
    lwt_retain: bool = True
    
    # Connection timeouts
    connect_timeout: int = 30
    publish_timeout: int = 10
    
    # Health monitoring
    health_check_interval: int = 30
    ping_test_interval: int = 300  # 5 minutes

    def __post_init__(self):
        """Set default LWT topic if not provided."""
        if self.lwt_topic is None:
            self.lwt_topic = f"{self.device_prefix}/bridge/status"


class HomeAssistantMQTTIntegration:
    """Complete MQTT integration with robust disconnection handling."""
    
    def __init__(self, config: HAMQTTConfig):
        """Initialize the MQTT integration.
        
        Args:
            config: MQTT configuration.
        """
        self.config = config
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = Lock()
        self._connection_event = Event()
        self._shutdown_event = Event()
        self._health_check_task = None
        self._reconnect_task = None
        
        # Connection health tracking
        self._metrics = ConnectionMetrics()
        self._health_status = ConnectionHealth.FAILED
        self._pending_publishes = {}  # Track QoS > 0 messages
        self._message_id_counter = 0
        
        # Reconnection state
        self._reconnect_delay = config.initial_reconnect_delay
        self._last_connect_attempt = 0
        
        # Generate and validate client ID
        self._client_id = self._generate_safe_client_id(config.client_id)
        
        # Create MQTT client
        self.client = self._create_mqtt_client()
        
        logger.info(f"MQTT client initialized with ID: {self._client_id}")
    
    def _generate_safe_client_id(self, provided_id: Optional[str]) -> str:
        """Generate MQTT-compliant client ID."""
        if provided_id:
            base_id = provided_id
        else:
            hostname = socket.gethostname()
            timestamp = int(time.time())
            base_id = f"utec_ha_{hostname}_{timestamp}"
        
        # Clean the client ID for MQTT compliance
        # - Max 23 characters for MQTT 3.1 compatibility
        # - Only alphanumeric, dash, underscore allowed
        # - Should not start with number (some brokers)
        
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', base_id)
        
        # Ensure it starts with a letter
        if clean_id and clean_id[0].isdigit():
            clean_id = f"c_{clean_id}"
        
        # Truncate if too long (leave room for potential broker suffixes)
        if len(clean_id) > 20:
            clean_id = clean_id[:20]
        
        # Ensure we have a valid ID
        if not clean_id:
            clean_id = f"utec_{int(time.time()) % 10000}"
        
        logger.info(f"Using MQTT client ID: {clean_id} (length: {len(clean_id)})")
        return clean_id
    
    def _create_mqtt_client(self) -> mqtt.Client:
        """Create and configure MQTT client."""
        # Use MQTT 3.1.1 for better compatibility
        client = mqtt.Client(
            client_id=self._client_id,
            clean_session=False,  # Persistent session for reliability
            protocol=mqtt.MQTTv311,
            transport="tcp"
        )
        
        # Authentication
        if self.config.username and self.config.password:
            client.username_pw_set(self.config.username, self.config.password)
        
        # Last Will and Testament - Critical for unexpected disconnections
        if self.config.lwt_topic:
            client.will_set(
                self.config.lwt_topic,
                self.config.lwt_payload,
                qos=self.config.lwt_qos,
                retain=self.config.lwt_retain
            )
            logger.debug(f"LWT configured: {self.config.lwt_topic}")
        
        # Connection settings optimized for reliability
        client.socket_timeout = self.config.connect_timeout
        client.socket_keepalive = self.config.keepalive
        client.max_inflight_messages_set(20)  # Limit inflight for QoS > 0
        client.max_queued_messages_set(100)   # Queue management
        client.message_retry_set(5)           # Retry failed messages
        
        # Set up callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_publish = self._on_publish
        client.on_log = self._on_log
        client.on_socket_close = self._on_socket_close
        client.on_socket_open = self._on_socket_open
        
        return client
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        with self._state_lock:
            return self._state == ConnectionState.CONNECTED
    
    @property 
    def connection_state(self) -> ConnectionState:
        """Get current connection state."""
        with self._state_lock:
            return self._state
    
    @property
    def health_status(self) -> ConnectionHealth:
        """Get current health status."""
        return self._health_status
    
    def _set_state(self, new_state: ConnectionState):
        """Thread-safe state change."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            
            if new_state == ConnectionState.CONNECTED:
                self._connection_event.set()
            else:
                self._connection_event.clear()
                
            logger.debug(f"State transition: {old_state.value} -> {new_state.value}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Enhanced connection callback with health reset."""
        if rc == 0:
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_delay = self.config.initial_reconnect_delay
            self._metrics.connection_uptime_start = time.time()
            self._metrics.last_ping_response = time.time()
            self._metrics.reset_failures()
            self._health_status = ConnectionHealth.HEALTHY
            
            # Start health monitoring
            if not self._health_check_task or self._health_check_task.done():
                self._health_check_task = asyncio.create_task(self._health_monitor())
            
            # Publish online status if LWT is configured
            if self.config.lwt_topic:
                self._publish_immediate(
                    self.config.lwt_topic, 
                    "online", 
                    qos=self.config.lwt_qos, 
                    retain=self.config.lwt_retain
                )
            
            logger.info(f"Connected to MQTT broker (client: {self._client_id})")
            session_present = flags.get('session present', False)
            logger.info(f"Session present: {session_present}")
        else:
            self._set_state(ConnectionState.DISCONNECTED)
            self._health_status = ConnectionHealth.FAILED
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
        """Enhanced disconnect callback with reconnection logic."""
        old_state = self._state
        self._set_state(ConnectionState.DISCONNECTED)
        self._health_status = ConnectionHealth.FAILED
        
        # Stop health monitoring
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
        
        if rc == 0:
            logger.info(f"Clean disconnect (client: {self._client_id})")
        else:
            disconnect_reasons = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier", 
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorised",
                16: "Keep alive timeout",
                128: "Unspecified error"
            }
            reason = disconnect_reasons.get(rc, f"Unknown error ({rc})")
            logger.warning(f"Unexpected disconnect: {reason} (client: {self._client_id})")
            
            self._metrics.record_failure()
            
            # Start reconnection if enabled and not shutting down
            if self.config.reconnect_on_failure and not self._shutdown_event.is_set():
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    def _on_socket_open(self, client, userdata, sock):
        """Socket opened - good sign for connection health."""
        logger.debug(f"Socket opened for {self._client_id}")
    
    def _on_socket_close(self, client, userdata, sock):
        """Socket closed - indicates network issues."""
        logger.warning(f"Socket closed unexpectedly for {self._client_id}")
        if self._health_status != ConnectionHealth.FAILED:
            self._health_status = ConnectionHealth.UNHEALTHY
    
    def _on_publish(self, client, userdata, mid):
        """Handle publish acknowledgments for QoS > 0."""
        logger.debug(f"Message {mid} acknowledged by broker")
        
        # Update health metrics
        self._metrics.last_successful_publish = time.time()
        self._metrics.reset_failures()
        
        # Update health status if recovering
        if self._health_status == ConnectionHealth.DEGRADED:
            self._health_status = ConnectionHealth.HEALTHY
        
        # Handle pending publishes tracking
        if mid in self._pending_publishes:
            topic, future = self._pending_publishes.pop(mid)
            if not future.done():
                future.set_result(True)
    
    def _on_log(self, client, userdata, level, buf):
        """MQTT client logging with proper level mapping."""
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
        """Connect to MQTT broker with timeout and retry logic."""
        if self.is_connected:
            return True
            
        self._set_state(ConnectionState.CONNECTING)
        self._last_connect_attempt = time.time()
        
        try:
            # Start connection attempt
            self.client.connect_async(
                self.config.broker_host, 
                self.config.broker_port, 
                self.config.keepalive
            )
            self.client.loop_start()
            
            # Wait for connection with timeout
            connected = await asyncio.wait_for(
                self._wait_for_connection(),
                timeout=self.config.connect_timeout
            )
            
            return connected
            
        except asyncio.TimeoutError:
            logger.error(f"Connection timeout after {self.config.connect_timeout}s")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._set_state(ConnectionState.DISCONNECTED)
            return False
    
    async def _wait_for_connection(self) -> bool:
        """Wait for connection event."""
        while not self._shutdown_event.is_set():
            if self._connection_event.wait(timeout=0.1):
                return True
            await asyncio.sleep(0.1)
        return False
    
    async def _reconnect_loop(self):
        """Automatic reconnection with exponential backoff."""
        if self.connection_state == ConnectionState.RECONNECTING:
            return  # Already reconnecting
            
        self._set_state(ConnectionState.RECONNECTING)
        
        while (not self.is_connected and 
               not self._shutdown_event.is_set() and 
               self.config.reconnect_on_failure):
            
            # Exponential backoff
            time_since_last_attempt = time.time() - self._last_connect_attempt
            if time_since_last_attempt < self._reconnect_delay:
                sleep_time = self._reconnect_delay - time_since_last_attempt
                logger.info(f"Waiting {sleep_time:.1f}s before reconnection attempt")
                await asyncio.sleep(sleep_time)
            
            logger.info(f"Attempting to reconnect (delay: {self._reconnect_delay}s, "
                       f"attempt #{self._metrics.total_reconnections + 1})")
            
            if await self.connect():
                self._metrics.total_reconnections += 1
                logger.info(f"Reconnected successfully (total reconnections: {self._metrics.total_reconnections})")
                return
            
            # Increase delay for next attempt
            self._reconnect_delay = min(
                self._reconnect_delay * self.config.reconnect_delay_multiplier,
                self.config.max_reconnect_delay
            )
    
    async def _health_monitor(self):
        """Continuous health monitoring to detect connection issues."""
        logger.info("Starting connection health monitoring")
        
        while not self._shutdown_event.is_set() and self.is_connected:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                current_time = time.time()
                
                # Check if we haven't published anything recently
                time_since_publish = current_time - self._metrics.last_successful_publish
                time_since_ping = current_time - self._metrics.last_ping_response
                
                # Determine health status
                if time_since_publish > 300:  # 5 minutes without successful publish
                    if self._health_status == ConnectionHealth.HEALTHY:
                        self._health_status = ConnectionHealth.DEGRADED
                        logger.warning("Connection health degraded - no recent successful publishes")
                elif time_since_ping > 120:  # 2 minutes without ping response
                    if self._health_status != ConnectionHealth.UNHEALTHY:
                        self._health_status = ConnectionHealth.UNHEALTHY
                        logger.warning("Connection unhealthy - no ping responses")
                
                # Perform active health check by publishing a ping
                if (self._health_status in [ConnectionHealth.DEGRADED, ConnectionHealth.UNHEALTHY] or
                    time_since_publish > self.config.ping_test_interval):
                    await self._perform_ping_test()
                
                # Force reconnection if connection seems dead
                if (time_since_ping > 180 and 
                    self._metrics.consecutive_failures > 3):
                    logger.error("Connection appears dead - forcing reconnection")
                    self.client.disconnect()  # Trigger reconnection
                    break
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
                await asyncio.sleep(5)
        
        logger.info("Health monitoring stopped")
    
    async def _perform_ping_test(self):
        """Perform active ping test to verify connection."""
        try:
            # Publish a small test message
            test_topic = f"{self.config.device_prefix}/bridge/ping"
            result = self.client.publish(test_topic, 
                                       payload=str(int(time.time())), 
                                       qos=0)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self._metrics.last_ping_response = time.time()
                if self._health_status == ConnectionHealth.UNHEALTHY:
                    self._health_status = ConnectionHealth.DEGRADED
                    logger.info("Ping test successful - connection recovering")
            else:
                logger.warning(f"Ping test failed: {result.rc}")
                self._metrics.record_failure()
                
        except Exception as e:
            logger.error(f"Error performing ping test: {e}")
            self._metrics.record_failure()
    
    def disconnect(self):
        """Graceful disconnect with cleanup."""
        self._shutdown_event.set()
        
        # Cancel background tasks
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        
        if self.client:
            # Publish offline status if LWT configured
            if self.config.lwt_topic and self.is_connected:
                self._publish_immediate(
                    self.config.lwt_topic,
                    self.config.lwt_payload,
                    qos=self.config.lwt_qos,
                    retain=self.config.lwt_retain
                )
            
            self.client.loop_stop()
            self.client.disconnect()
            logger.info(f"MQTT client {self._client_id} disconnected")
    
    def _publish_immediate(self, topic: str, payload: Any, 
                          qos: int = None, retain: bool = None) -> bool:
        """Synchronous publish for internal use."""
        if not self.is_connected:
            return False
            
        qos = qos if qos is not None else self.config.qos
        retain = retain if retain is not None else self.config.retain
        
        try:
            payload_str = str(payload) if not isinstance(payload, (dict, list)) else json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Error in immediate publish to {topic}: {e}")
            return False
    
    async def publish_with_ack(self, topic: str, payload: Any, 
                              qos: int = None, retain: bool = None, 
                              timeout: float = None) -> bool:
        """Publish message and wait for acknowledgment if QoS > 0."""
        if not self.is_connected:
            logger.error("Cannot publish: not connected to MQTT broker")
            return False
        
        qos = qos if qos is not None else self.config.qos
        retain = retain if retain is not None else self.config.retain
        timeout = timeout if timeout is not None else self.config.publish_timeout
        
        try:
            payload_str = str(payload) if not isinstance(payload, (dict, list)) else json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to publish to {topic}: {result.rc}")
                self._metrics.record_failure()
                return False
            
            # For QoS 0, return immediately
            if qos == 0:
                self._metrics.reset_failures()
                return True
            
            # For QoS > 0, wait for acknowledgment
            future = asyncio.Future()
            self._pending_publishes[result.mid] = (topic, future)
            
            try:
                await asyncio.wait_for(future, timeout=timeout)
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Publish acknowledgment timeout for {topic}")
                self._pending_publishes.pop(result.mid, None)
                self._metrics.record_failure()
                return False
                
        except Exception as e:
            logger.error(f"Error publishing to {topic}: {e}")
            self._metrics.record_failure()
            return False
    
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
            3: "JAMMED",
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
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics for monitoring."""
        return {
            "client_id": self._client_id,
            "state": self._state.value,
            "health": self._health_status.value,
            "uptime": self._metrics.uptime,
            "consecutive_failures": self._metrics.consecutive_failures,
            "total_reconnections": self._metrics.total_reconnections,
            "last_successful_publish": self._metrics.last_successful_publish,
            "time_since_last_publish": time.time() - self._metrics.last_successful_publish if self._metrics.last_successful_publish > 0 else -1,
            "reconnect_delay": self._reconnect_delay
        }
