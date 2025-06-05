import asyncio
import time
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("bleak", reason="bleak not installed")
pytest.importorskip("bleak_retry_connector", reason="bleak_retry_connector not installed")
pytest.importorskip("Crypto", reason="pycryptodome not installed")
pytest.importorskip("ecdsa", reason="ecdsa not installed")

from utec.ble.device import UtecBleDevice


@pytest.mark.asyncio
async def test_scan_cache_pruned(monkeypatch):
    device = UtecBleDevice(uid="1", password="1", mac_uuid="AA:BB:CC:DD:EE:FF", device_name="test")

    past_time = time.time() - (device._scan_cache_ttl + 1)
    device._scan_cache["AA:BB:CC:DD:EE:00"] = (past_time, object())

    async def dummy_cb(addr):
        return None

    device.async_bledevice_callback = dummy_cb

    async def dummy_discover(*args, **kwargs):
        return {}

    monkeypatch.setattr("utec.ble.device.BleakScanner.discover", dummy_discover)

    async def dummy_get_device(address):
        return None

    monkeypatch.setattr("utec.ble.device.get_device", dummy_get_device)

    await device._get_bledevice("AA:BB:CC:DD:EE:FF")
    assert "AA:BB:CC:DD:EE:00" not in device._scan_cache
