import sys
import os
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Skip if optional dependencies are missing
pytest.importorskip("paho.mqtt", reason="paho-mqtt not installed")
pytest.importorskip("aiohttp", reason="aiohttp not installed")
pytest.importorskip("bleak", reason="bleak not installed")
pytest.importorskip("bleak_retry_connector", reason="bleak_retry_connector not installed")
pytest.importorskip("Crypto", reason="pycryptodome not installed")
pytest.importorskip("ecdsa", reason="ecdsa not installed")

from utec.integrations.ha_mqtt import UtecMQTTClient
from utec.integrations.ha_constants import HA_LOCK_STATES, HA_BATTERY_LEVELS


# --- Helpers ---

class DummyMqttClient:
    """Records publish calls instead of sending to a broker."""
    def __init__(self):
        self.unsubscribed = []
        self.subscribed = []

    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0,)  # MQTT_ERR_SUCCESS


class DummyLock:
    def __init__(self, **kwargs):
        self.mac_uuid = kwargs.get("mac_uuid", "AA:BB:CC:DD:EE:FF")
        self.name = kwargs.get("name", "Test Lock")
        self.model = kwargs.get("model", "U-Bolt-PRO")
        self.firmware_version = kwargs.get("firmware_version", "1.0")
        self.lock_status = kwargs.get("lock_status", 2)
        self.battery = kwargs.get("battery", 3)
        self.lock_mode = kwargs.get("lock_mode", 0)
        self.autolock_time = kwargs.get("autolock_time", 30)
        self.mute = kwargs.get("mute", False)


def make_connected_client():
    """Create a UtecMQTTClient wired up with a recording publish stub."""
    client = UtecMQTTClient(broker_host="localhost")
    client.client = DummyMqttClient()
    client.connected = True

    published = {}

    def fake_publish(topic, payload, qos=None, retain=None):
        published[topic] = payload
        return True

    client.publish = fake_publish
    return client, published


# --- HA_LOCK_STATES mapping tests ---

def test_lock_state_mapping_locked():
    assert HA_LOCK_STATES[2] == "LOCKED"


def test_lock_state_mapping_unlocked():
    assert HA_LOCK_STATES[1] == "UNLOCKED"


def test_lock_state_mapping_jammed():
    assert HA_LOCK_STATES[3] == "JAMMED"


def test_lock_state_mapping_unavailable():
    assert HA_LOCK_STATES[0] == "UNAVAILABLE"
    assert HA_LOCK_STATES[255] == "UNAVAILABLE"


# --- HA_BATTERY_LEVELS mapping tests ---

def test_battery_mapping():
    assert HA_BATTERY_LEVELS[0] == 5    # Replace / critical
    assert HA_BATTERY_LEVELS[1] == 25   # Low
    assert HA_BATTERY_LEVELS[2] == 60   # Medium
    assert HA_BATTERY_LEVELS[3] == 90   # High


# --- update_lock_state tests ---

def test_update_lock_state_publishes_correct_topics():
    client, published = make_connected_client()
    lock = DummyLock(lock_status=2, battery=3, lock_mode=0, autolock_time=30, mute=False)
    device_id = client._get_device_id(lock)

    client.update_lock_state(lock)

    assert published[f"utec/{device_id}/lock/state"] == "LOCKED"
    assert published[f"utec/{device_id}/battery/state"] == 90
    assert published[f"utec/{device_id}/autolock/state"] == 30
    assert published[f"utec/{device_id}/mute/state"] == "False"


def test_update_lock_state_unlocked():
    client, published = make_connected_client()
    lock = DummyLock(lock_status=1, battery=1)
    device_id = client._get_device_id(lock)

    client.update_lock_state(lock)

    assert published[f"utec/{device_id}/lock/state"] == "UNLOCKED"
    assert published[f"utec/{device_id}/battery/state"] == 25


def test_update_lock_state_disconnected():
    client = UtecMQTTClient(broker_host="localhost")
    client.connected = False
    lock = DummyLock()

    assert client.update_lock_state(lock) is False


# --- setup_lock_discovery tests ---

def test_setup_lock_discovery_publishes_all_entities():
    client, published = make_connected_client()
    client.client = DummyMqttClient()
    lock = DummyLock()
    device_id = client._get_device_id(lock)

    client.setup_lock_discovery(lock)

    # Should publish discovery configs for: lock, battery, lock_mode, autolock, mute
    expected_components = [
        f"homeassistant/lock/{device_id}_lock/config",
        f"homeassistant/sensor/{device_id}_battery/config",
        f"homeassistant/sensor/{device_id}_lock_mode/config",
        f"homeassistant/sensor/{device_id}_autolock/config",
        f"homeassistant/binary_sensor/{device_id}_mute/config",
    ]
    for topic in expected_components:
        assert topic in published, f"Missing discovery topic: {topic}"


def test_setup_lock_discovery_lock_entity_has_command_topic():
    client, published = make_connected_client()
    client.client = DummyMqttClient()
    lock = DummyLock()
    device_id = client._get_device_id(lock)

    client.setup_lock_discovery(lock)

    lock_config = published[f"homeassistant/lock/{device_id}_lock/config"]
    # publish converts dicts to JSON strings
    if isinstance(lock_config, str):
        lock_config = json.loads(lock_config)
    assert lock_config["command_topic"] == f"utec/{device_id}/lock/command"
    assert lock_config["state_topic"] == f"utec/{device_id}/lock/state"
