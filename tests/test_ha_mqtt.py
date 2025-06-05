import sys
import os
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

class DummyClient:
    def __init__(self):
        self.unsubscribed = []
    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)
        return (0,)

class DummyLock:
    mac_uuid = "AA:BB:CC:DD:EE:FF"
    name = "Test Lock"

def test_remove_device_unsubscribes():
    client = UtecMQTTClient(broker_host="localhost")
    dummy_client = DummyClient()
    client.client = dummy_client
    client.connected = True
    lock = DummyLock()

    topic = f"{client.device_prefix}/{client._get_device_id(lock)}/lock/command"
    client.device_subscriptions.append(topic)

    client.publish = lambda *args, **kwargs: True

    client.remove_device(lock)

    assert topic not in client.device_subscriptions
    assert topic in dummy_client.unsubscribed
