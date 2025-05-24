"""
MQTT client implementation for Utec smart locks & HA MQTT messages
"""

import json
import logging
import asyncio
import time
from typing import Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import paho.mqtt.client as mqtt
from threading import Event, Lock
import socket
import re
from collections import defaultdict
import os

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """MQTT connection states."""
    DISCONNECTED = "disconnected" 
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class HealthStatus(Enum):
    """Connection health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"


@dataclass
class ConnectionMetrics:
    """Comprehensive connection health metrics."""
    connection_attempts: int = 0
    successful_connections: int = 0
    disconnections: int = 0
    messages_published: int = 0
    messages_failed: int = 0
    last_successful_publish: float = 0
    last_connection_time: float = 0
    total_uptime: float = 0
    consecutive_failures: int = 0
    
    @property
    def success_rate(self) -> float:
        """Calculate connection success rate."""
        if self.connection_attempts == 0:
            return 0.0
        return (self.successful_connections / self.connection_attempts) * 100
    
    @property
    def publish_success_rate(self) -> float:
        """Calculate publish success rate."""
        total = self.messages_published + self.messages_failed
        if total == 0:
            return 0.0
        return (self.messages_published / total) * 100


@dataclass
class HAMQTTConfig:
    """Comprehensive MQTT configuration."""
    
    # Connection settings
    broker_host: str
    broker_port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    keepalive: int = 60
    
    # Topic configuration
    discovery_prefix: str = "homeassistant"
    device_prefix: str = "utec"
    
    # QoS and reliability
    qos: int = 1
    retain: bool = True
    clean_session: bool = False
    
    # Reconnection strategy
    reconnect_enabled: bool = True
    initial_reconnect_delay: float = 1.0
    max_reconnect_delay: float = 300.0
    reconnect_backoff_multiplier: float = 1.5
    max_reconnect_attempts: int = -1  # -1 = infinite
    
    # Timeouts
    connect_timeout: float = 30.0
    publish_timeout: float = 10.0
    
    # Health monitoring
    health_check_enabled: bool = True
    health_check_interval: float = 30.0
    ping_interval: float = 120.0
    
    # Last Will and Testament
    lwt_enabled: bool = True
    lwt_topic: Optional[str] = None
    lwt_payload: str = "offline"
    lwt_qos: int = 1
    lwt_retain: bool = True
    
    # Performance tuning
    max_inflight_messages: int = 20
    max_queued_messages: int = 100
    
    def __post_init__(self):
        """Set defaults and validate configuration."""
        if self.lwt_enabled and self.lwt_topic is None:
            self.lwt_topic = f"{self.device_prefix}/bridge/status"
        
        if self.broker_host is None:
            raise ValueError("broker_host is required")


class MQTTPublishResult:
    """Result of an MQTT publish operation."""
    
    def __init__(self, success: bool, message_id: Optional[int] = None, 
                 error: Optional[str] = None, topic: Optional[str] = None):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.topic = topic
        self.timestamp = time.time()


class HomeAssistantMQTTIntegration:
    """Production-grade MQTT integration for Home Assistant."""
    
    def __init__(self, config: HAMQTTConfig):
        """Initialize the MQTT integration."""
        self.config = config
        self._state = ConnectionState.DISCONNECTED
        self._health = HealthStatus.CRITICAL
        self._state_lock = Lock()
        self._connection_event = Event()
        self._shutdown_event = Event()
        
        # Metrics and monitoring
        self.metrics = ConnectionMetrics()
        self._pending_publishes: Dict[int, Tuple[str, asyncio.Future]] = {}
        self._publish_results: Dict[int, MQTTPublishResult] = {}
        
        # Background tasks
        self._reconnect_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None
        
        # Connection management
        self._reconnect_delay = config.initial_reconnect_delay
        self._reconnect_attempts = 0
        self._last_ping = 0
        
        # Client setup
        self._client_id = self._generate_client_id()
        self.client = self._create_mqtt_client()
        
        logger.info(f"MQTT client initialized: {self._client_id}")
    
    def _generate_client_id(self) -> str:
        """Generate optimal MQTT client ID."""
        if self.config.client_id:
            return self._sanitize_client_id(self.config.client_id)
        
        # Generate unique ID
        hostname = socket.gethostname()
        process_id = os.getpid() if hasattr(os, 'getpid') else 0
        timestamp = int(time.time()) % 100000
        
        base_id = f"utec-ha-{hostname}-{process_id}-{timestamp}"
        return self._sanitize_client_id(base_id)
    
    def _sanitize_client_id(self, client_id: str) -> str:
        """Sanitize client ID for MQTT compliance."""
        # Clean invalid characters
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '-', client_id)
        
        # Ensure proper length (MQTT 3.1 = 23 chars max)
        if len(clean_id) > 23:
            clean_id = clean_id[:23]
        
        # Remove trailing separators
        clean_id = clean_id.rstrip('-_')
        
        # Ensure it starts with letter
        if clean_id and clean_id[0].isdigit():
            clean_id = f"c-{clean_id[2:]}"
        
        # Fallback if empty
        if not clean_id:
            clean_id = f"utec-{int(time.time()) % 10000}"
        
        return clean_id
    
    def _create_mqtt_client(self) -> mqtt.Client:
        """Create optimally configured MQTT client."""
        try:
            # Handle paho-mqtt version differences
            try:
                from paho.mqtt.client import CallbackAPIVersion
                client = mqtt.Client(
                    callback_api_version=CallbackAPIVersion.VERSION1,
                    client_id=self._client_id,
                    clean_session=self.config.clean_session,
                    protocol=mqtt.MQTTv311
                )
                logger.debug("Using paho-mqtt 2.x API")
            except (ImportError, TypeError):
                client = mqtt.Client(
                    client_id=self._client_id,
                    clean_session=self.config.clean_session,
                    protocol=mqtt.MQTTv311
                )
                logger.debug("Using paho-mqtt 1.x API")
            
            # Authentication
            if self.config.username and self.config.password:
                client.username_pw_set(self.config.username, self.config.password)
            
            # Last Will and Testament
            if self.config.lwt_enabled and self.config.lwt_topic:
                client.will_set(
                    self.config.lwt_topic,
                    self.config.lwt_payload,
                    qos=self.config.lwt_qos,
                    retain=self.config.lwt_retain
                )
                logger.debug(f"LWT configured: {self.config.lwt_topic}")
            
            # Performance settings
            client.socket_timeout = self.config.connect_timeout
            client.socket_keepalive = self.config.keepalive
            
            # Set limits if available
            for method_name, value in [
                ('max_inflight_messages_set', self.config.max_inflight_messages),
                ('max_queued_messages_set', self.config.max_queued_messages)
            ]:
                try:
                    getattr(client, method_name)(value)
                except AttributeError:
                    logger.debug(f"Method {method_name} not available")
            
            # Callbacks
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_publish = self._on_publish
            client.on_log = self._on_log
            
            return client
            
        except Exception as e:
            logger.error(f"Failed to create MQTT client: {e}")
            raise
    
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
    def health_status(self) -> HealthStatus:
        """Get current health status."""
        return self._health
    
    def _set_state(self, new_state: ConnectionState):
        """Thread-safe state transition."""
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            
            if new_state == ConnectionState.CONNECTED:
                self._connection_event.set()
            else:
                self._connection_event.clear()
            
            logger.debug(f"State: {old_state.value} -> {new_state.value}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Enhanced connection callback."""
        self.metrics.connection_attempts += 1
        
        if rc == 0:
            self._set_state(ConnectionState.CONNECTED)
            self._health = HealthStatus.HEALTHY
            self.metrics.successful_connections += 1
            self.metrics.last_connection_time = time.time()
            self.metrics.consecutive_failures = 0
            self._reconnect_delay = self.config.initial_reconnect_delay
            self._reconnect_attempts = 0
            
            # Start health monitoring
            if (self.config.health_check_enabled and 
                (not self._health_monitor_task or self._health_monitor_task.done())):
                self._health_monitor_task = asyncio.create_task(self._health_monitor())
            
            # Publish online status
            if self.config.lwt_enabled and self.config.lwt_topic:
                try:
                    client.publish(
                        self.config.lwt_topic,
                        "online",
                        qos=self.config.lwt_qos,
                        retain=self.config.lwt_retain
                    )
                except Exception as e:
                    logger.warning(f"Failed to publish online status: {e}")
            
            session_present = flags.get('session present', False)
            logger.info(f"Connected to MQTT broker (session_present: {session_present})")
            
        else:
            self._set_state(ConnectionState.FAILED)
            self._health = HealthStatus.CRITICAL
            self.metrics.consecutive_failures += 1
            
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
        """Enhanced disconnect callback."""
        self.metrics.disconnections += 1
        old_state = self._state
        
        # Stop health monitoring
        if self._health_monitor_task and not self._health_monitor_task.done():
            self._health_monitor_task.cancel()
        
        if rc == 0:
            self._set_state(ConnectionState.DISCONNECTED)
            logger.info("Clean disconnect")
        else:
            self._set_state(ConnectionState.DISCONNECTED)
            self._health = HealthStatus.CRITICAL
            logger.warning(f"Unexpected disconnect (rc: {rc})")
            
            # Start reconnection
            if (self.config.reconnect_enabled and 
                not self._shutdown_event.is_set() and
                (not self._reconnect_task or self._reconnect_task.done())):
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    def _on_publish(self, client, userdata, mid):
        """Handle publish acknowledgments."""
        self.metrics.messages_published += 1
        self.metrics.last_successful_publish = time.time()
        self.metrics.consecutive_failures = 0
        
        # Update health if recovering
        if self._health in [HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]:
            self._health = HealthStatus.HEALTHY
        
        # Handle async publish tracking
        if mid in self._pending_publishes:
            topic, future = self._pending_publishes.pop(mid)
            if not future.done():
                future.set_result(MQTTPublishResult(True, mid, topic=topic))
        
        logger.debug(f"Message {mid} published successfully")
    
    def _on_log(self, client, userdata, level, buf):
        """MQTT client logging."""
        log_levels = {
            mqtt.MQTT_LOG_DEBUG: logging.DEBUG,
            mqtt.MQTT_LOG_INFO: logging.INFO,
            mqtt.MQTT_LOG_NOTICE: logging.INFO,
            mqtt.MQTT_LOG_WARNING: logging.WARNING,
            mqtt.MQTT_LOG_ERR: logging.ERROR
        }
        
        python_level = log_levels.get(level, logging.INFO)
        logger.log(python_level, f"MQTT: {buf}")
    
    async def connect(self) -> bool:
        """Connect to MQTT broker with comprehensive error handling."""
        if self.is_connected:
            return True
        
        self._set_state(ConnectionState.CONNECTING)
        logger.info(f"Connecting to {self.config.broker_host}:{self.config.broker_port}")
        
        try:
            # Start connection
            self.client.connect_async(
                self.config.broker_host,
                self.config.broker_port,
                self.config.keepalive
            )
            self.client.loop_start()
            
            # Wait for connection
            connected = await asyncio.wait_for(
                self._wait_for_connection(),
                timeout=self.config.connect_timeout
            )
            
            return connected
            
        except asyncio.TimeoutError:
            logger.error(f"Connection timeout ({self.config.connect_timeout}s)")
            self._set_state(ConnectionState.FAILED)
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._set_state(ConnectionState.FAILED)
            return False
    
    async def _wait_for_connection(self) -> bool:
        """Wait for connection event."""
        while not self._shutdown_event.is_set():
            if self._connection_event.wait(timeout=0.1):
                return True
            await asyncio.sleep(0.1)
        return False
    
    async def _reconnect_loop(self):
        """Intelligent reconnection with exponential backoff."""
        self._set_state(ConnectionState.RECONNECTING)
        logger.info("Starting reconnection process")
        
        while (not self.is_connected and 
               not self._shutdown_event.is_set() and
               (self.config.max_reconnect_attempts == -1 or 
                self._reconnect_attempts < self.config.max_reconnect_attempts)):
            
            self._reconnect_attempts += 1
            
            logger.info(f"Reconnection attempt {self._reconnect_attempts} "
                       f"(delay: {self._reconnect_delay:.1f}s)")
            
            # Wait before attempting
            await asyncio.sleep(self._reconnect_delay)
            
            # Attempt reconnection
            if await self.connect():
                logger.info(f"Reconnected after {self._reconnect_attempts} attempts")
                return
            
            # Exponential backoff
            self._reconnect_delay = min(
                self._reconnect_delay * self.config.reconnect_backoff_multiplier,
                self.config.max_reconnect_delay
            )
        
        if self._reconnect_attempts >= self.config.max_reconnect_attempts > 0:
            logger.error(f"Max reconnection attempts ({self.config.max_reconnect_attempts}) exceeded")
            self._set_state(ConnectionState.FAILED)
    
    async def _health_monitor(self):
        """Continuous health monitoring."""
        logger.info("Health monitoring started")
        
        while not self._shutdown_event.is_set() and self.is_connected:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                current_time = time.time()
                time_since_publish = current_time - self.metrics.last_successful_publish
                time_since_ping = current_time - self._last_ping
                
                # Determine health status
                if self.metrics.consecutive_failures > 5:
                    self._health = HealthStatus.CRITICAL
                elif time_since_publish > 600:  # 10 minutes
                    self._health = HealthStatus.UNHEALTHY
                elif time_since_publish > 300:  # 5 minutes
                    self._health = HealthStatus.DEGRADED
                else:
                    self._health = HealthStatus.HEALTHY
                
                # Perform ping test if needed
                if time_since_ping > self.config.ping_interval:
                    await self._perform_ping_test()
                
                # Log health issues
                if self._health != HealthStatus.HEALTHY:
                    logger.warning(f"Health: {self._health.value}, "
                                 f"failures: {self.metrics.consecutive_failures}, "
                                 f"time_since_publish: {time_since_publish:.1f}s")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
                await asyncio.sleep(5)
        
        logger.info("Health monitoring stopped")
    
    async def _perform_ping_test(self):
        """Active health check via ping message."""
        try:
            ping_topic = f"{self.config.device_prefix}/bridge/ping"
            result = await self.publish_async(ping_topic, str(int(time.time())), qos=0)
            
            if result.success:
                self._last_ping = time.time()
                logger.debug("Ping test successful")
            else:
                logger.warning("Ping test failed")
                self.metrics.consecutive_failures += 1
                
        except Exception as e:
            logger.error(f"Ping test error: {e}")
            self.metrics.consecutive_failures += 1
    
    async def publish_async(self, topic: str, payload: Any, 
                           qos: Optional[int] = None, 
                           retain: Optional[bool] = None,
                           timeout: Optional[float] = None) -> MQTTPublishResult:
        """Async publish with comprehensive result tracking."""
        if not self.is_connected:
            error = "Not connected to MQTT broker"
            logger.error(error)
            self.metrics.messages_failed += 1
            return MQTTPublishResult(False, error=error, topic=topic)
        
        qos = qos if qos is not None else self.config.qos
        retain = retain if retain is not None else self.config.retain
        timeout = timeout if timeout is not None else self.config.publish_timeout
        
        try:
            # Prepare payload
            if isinstance(payload, (dict, list)):
                payload_str = json.dumps(payload)
            else:
                payload_str = str(payload)
            
            # Publish message
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                error = f"Publish failed: {result.rc}"
                logger.error(f"{error} (topic: {topic})")
                self.metrics.messages_failed += 1
                return MQTTPublishResult(False, error=error, topic=topic)
            
            # For QoS 0, return immediately
            if qos == 0:
                return MQTTPublishResult(True, result.mid, topic=topic)
            
            # For QoS > 0, wait for acknowledgment
            future = asyncio.Future()
            self._pending_publishes[result.mid] = (topic, future)
            
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending_publishes.pop(result.mid, None)
                error = f"Publish timeout ({timeout}s)"
                logger.warning(f"{error} (topic: {topic})")
                self.metrics.messages_failed += 1
                return MQTTPublishResult(False, result.mid, error=error, topic=topic)
            
        except Exception as e:
            error = f"Publish exception: {e}"
            logger.error(f"{error} (topic: {topic})")
            self.metrics.messages_failed += 1
            return MQTTPublishResult(False, error=error, topic=topic)
    
    def disconnect(self):
        """Graceful shutdown with cleanup."""
        logger.info("Shutting down MQTT client")
        self._shutdown_event.set()
        
        # Cancel background tasks
        for task in [self._reconnect_task, self._health_monitor_task]:
            if task and not task.done():
                task.cancel()
        
        # Publish offline status
        if (self.config.lwt_enabled and self.config.lwt_topic and 
            self.is_connected):
            try:
                self.client.publish(
                    self.config.lwt_topic,
                    self.config.lwt_payload,
                    qos=self.config.lwt_qos,
                    retain=self.config.lwt_retain
                )
            except Exception as e:
                logger.warning(f"Failed to publish offline status: {e}")
        
        # Disconnect client
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        
        logger.info("MQTT client shutdown complete")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get comprehensive metrics."""
        current_time = time.time()
        
        return {
            "client_id": self._client_id,
            "state": self._state.value,
            "health": self._health.value,
            "connection_attempts": self.metrics.connection_attempts,
            "success_rate": self.metrics.success_rate,
            "publish_success_rate": self.metrics.publish_success_rate,
            "messages_published": self.metrics.messages_published,
            "messages_failed": self.metrics.messages_failed,
            "disconnections": self.metrics.disconnections,
            "consecutive_failures": self.metrics.consecutive_failures,
            "time_since_last_publish": (
                current_time - self.metrics.last_successful_publish
                if self.metrics.last_successful_publish > 0 else -1
            ),
            "reconnect_attempts": self._reconnect_attempts,
            "pending_publishes": len(self._pending_publishes)
        }
    
    # Home Assistant specific methods
    
    def _get_device_id(self, lock) -> str:
        """Get clean device ID."""
        return lock.mac_uuid.replace(":", "_").lower()
    
    def _get_device_name(self, lock) -> str:
        """Get friendly device name."""
        return lock.name or f"U-tec Lock {lock.mac_uuid[-5:]}"
    
    def _create_device_info(self, lock) -> Dict[str, Any]:
        """Create HA device info."""
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        
        return {
            "identifiers": [device_id],
            "name": device_name,
            "model": getattr(lock, 'model', 'U-tec Lock'),
            "manufacturer": "U-tec",
            "sw_version": getattr(lock, 'firmware_version', 'Unknown'),
            "via_device": f"{self.config.device_prefix}_bridge"
        }
    
    async def setup_lock_discovery(self, lock) -> bool:
        """Set up HA discovery for lock device."""
        if not self.is_connected:
            logger.error("Cannot set up discovery: not connected")
            return False
        
        device_id = self._get_device_id(lock)
        device_name = self._get_device_name(lock)
        device_info = self._create_device_info(lock)
        
        # Discovery configurations
        discoveries = [
            # Main lock
            ("lock", f"{device_id}_lock", {
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
            }),
            
            # Battery sensor
            ("sensor", f"{device_id}_battery", {
                "name": f"{device_name} Battery",
                "unique_id": f"{device_id}_battery",
                "device": device_info,
                "state_topic": f"{self.config.device_prefix}/{device_id}/battery/state",
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
                "state_topic": f"{self.config.device_prefix}/{device_id}/lock_mode/state",
                "icon": "mdi:lock-outline",
                "entity_category": "diagnostic"
            }),
            
            # Autolock sensor
            ("sensor", f"{device_id}_autolock", {
                "name": f"{device_name} Autolock Time",
                "unique_id": f"{device_id}_autolock",
                "device": device_info, 
                "state_topic": f"{self.config.device_prefix}/{device_id}/autolock/state",
                "unit_of_measurement": "s",
                "icon": "mdi:timer-outline",
                "entity_category": "config"
            }),
            
            # Mute sensor
            ("binary_sensor", f"{device_id}_mute", {
                "name": f"{device_name} Mute",
                "unique_id": f"{device_id}_mute",
                "device": device_info,
                "state_topic": f"{self.config.device_prefix}/{device_id}/mute/state",
                "payload_on": "True",
                "payload_off": "False", 
                "icon": "mdi:volume-off",
                "entity_category": "diagnostic"
            })
        ]
        
        # Add signal strength if available
        if hasattr(lock, 'rssi') or hasattr(lock, 'signal_strength'):
            discoveries.append(("sensor", f"{device_id}_signal", {
                "name": f"{device_name} Signal Strength",
                "unique_id": f"{device_id}_signal",
                "device": device_info,
                "state_topic": f"{self.config.device_prefix}/{device_id}/signal/state",
                "unit_of_measurement": "dBm",
                "device_class": "signal_strength", 
                "state_class": "measurement",
                "entity_category": "diagnostic"
            }))
        
        # Publish all discovery configs
        success = True
        for component, object_id, config in discoveries:
            topic = f"{self.config.discovery_prefix}/{component}/{object_id}/config"
            result = await self.publish_async(topic, config)
            if not result.success:
                success = False
                logger.error(f"Failed to publish discovery for {object_id}: {result.error}")
        
        if success:
            logger.info(f"Set up discovery for {device_name}")
        
        return success
    
    async def publish_lock_state(self, lock) -> bool:
        """Publish lock state to MQTT."""
        if not self.is_connected:
            logger.error("Cannot publish state: not connected")
            return False
        
        device_id = self._get_device_id(lock)
        
        # State mappings
        lock_states = {0: "UNAVAILABLE", 1: "UNLOCKED", 2: "LOCKED", 
                      3: "JAMMED", -1: "UNKNOWN", 255: "NOTAVAILABLE"}
        lock_modes = {0: "Normal", 1: "Passage Mode", 2: "Lockout Mode", -1: "Unknown"}
        battery_levels = {-1: 0, 0: 10, 1: 25, 2: 60, 3: 90}
        
        # Prepare state updates
        states = [
            (f"{self.config.device_prefix}/{device_id}/lock/state",
             lock_states.get(getattr(lock, 'lock_status', -1), "UNKNOWN")),
            (f"{self.config.device_prefix}/{device_id}/battery/state",
             battery_levels.get(getattr(lock, 'battery', -1), 0)),
            (f"{self.config.device_prefix}/{device_id}/lock_mode/state",
             lock_modes.get(getattr(lock, 'lock_mode', -1), "Unknown")),
            (f"{self.config.device_prefix}/{device_id}/autolock/state",
             getattr(lock, 'autolock_time', 0)),
            (f"{self.config.device_prefix}/{device_id}/mute/state",
             str(getattr(lock, 'mute', False)))
        ]
        
        # Add signal strength
        if hasattr(lock, 'rssi'):
            states.append((f"{self.config.device_prefix}/{device_id}/signal/state", lock.rssi))
        elif hasattr(lock, 'signal_strength'):
            states.append((f"{self.config.device_prefix}/{device_id}/signal/state", lock.signal_strength))
        
        # Publish all states concurrently
        tasks = [self.publish_async(topic, payload) for topic, payload in states]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check results
        success = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Exception publishing {states[i][0]}: {result}")
                success = False
            elif not result.success:
                logger.error(f"Failed to publish {states[i][0]}: {result.error}")
                success = False
        
        if success:
            logger.debug(f"Published state for {self._get_device_name(lock)}")
        
        return success
    
    async def remove_device(self, lock) -> bool:
        """Remove device from Home Assistant."""
        if not self.is_connected:
            return False
        
        device_id = self._get_device_id(lock)
        
        # Components to remove
        components = [
            ("lock", f"{device_id}_lock"),
            ("sensor", f"{device_id}_battery"),
            ("sensor", f"{device_id}_lock_mode"),
            ("sensor", f"{device_id}_autolock"),
            ("binary_sensor", f"{device_id}_mute"),
            ("sensor", f"{device_id}_signal")
        ]
        
        # Remove all components
        tasks = []
        for component, object_id in components:
            topic = f"{self.config.discovery_prefix}/{component}/{object_id}/config"
            tasks.append(self.publish_async(topic, "", retain=True))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success = all(
            not isinstance(r, Exception) and r.success 
            for r in results
        )
        
        if success:
            logger.info(f"Removed device {self._get_device_name(lock)}")
        
        return success