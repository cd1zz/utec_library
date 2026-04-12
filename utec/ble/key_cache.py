"""
BLE encryption key cache for U-tec devices.

This module provides caching for negotiated encryption keys to avoid
repeated key exchanges which can take 5+ seconds for ECC.
"""

import time
import json
import os
from typing import Dict, Optional, Tuple
from threading import Lock

from ..config import config
from ..utils.logging import get_logger

logger = get_logger(__name__)


class BleKeyCache:
    """Cache for BLE encryption keys."""

    def __init__(self, cache_file: Optional[str] = None):
        """Initialize the key cache.

        Args:
            cache_file: Optional path to persist cache to disk
        """
        self._cache: Dict[str, Tuple[bytes, float, str]] = (
            {}
        )  # mac -> (key, timestamp, method)
        self._lock = Lock()
        self._cache_file = cache_file or os.path.expanduser("~/.utec_key_cache.json")
        self._cache_ttl = 86400 * 1  # 1 day default TTL

        # Load cache from disk if it exists
        self._load_cache()

    def _load_cache(self):
        """Load cache from disk."""
        if not os.path.exists(self._cache_file):
            return

        try:
            with open(self._cache_file, "r") as f:
                data = json.load(f)

            # Convert from JSON format
            now = time.time()
            for mac, entry in data.items():
                timestamp = entry["timestamp"]
                # Skip expired entries
                if now - timestamp > self._cache_ttl:
                    continue

                # Restore bytes from hex string
                key_hex = entry["key"]
                key_bytes = bytes.fromhex(key_hex)
                method = entry.get("method", "unknown")

                self._cache[mac] = (key_bytes, timestamp, method)

            logger.info(f"Loaded {len(self._cache)} keys from cache")

        except Exception as e:
            logger.warning(f"Failed to load key cache: {e}")

    def _save_cache(self):
        """Save cache to disk."""
        try:
            # Convert to JSON-serializable format
            data = {}
            for mac, (key, timestamp, method) in self._cache.items():
                data[mac] = {"key": key.hex(), "timestamp": timestamp, "method": method}

            # Ensure directory exists
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)

            # Write atomically
            temp_file = self._cache_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self._cache_file)

            logger.debug(f"Saved {len(data)} keys to cache")

        except Exception as e:
            logger.error(f"Failed to save key cache: {e}")

    def get(self, mac_address: str) -> Optional[bytes]:
        """Get cached key for a device.

        Args:
            mac_address: Device MAC address

        Returns:
            Cached key bytes or None if not found/expired
        """
        with self._lock:
            entry = self._cache.get(mac_address.upper())
            if not entry:
                logger.debug(f"No cached key for {mac_address}")
                return None

            key, timestamp, method = entry
            age = time.time() - timestamp

            # Check if expired
            if age > self._cache_ttl:
                logger.info(
                    f"Cached key for {mac_address} expired (age: {age/3600:.1f}h)"
                )
                del self._cache[mac_address.upper()]
                self._save_cache()
                return None

            logger.info(
                f"Using cached {method} key for {mac_address} (age: {age/3600:.1f}h)"
            )
            return key

    def set(self, mac_address: str, key: bytes, method: str = "unknown"):
        """Cache a key for a device.

        Args:
            mac_address: Device MAC address
            key: Encryption key bytes
            method: Key exchange method (ECC, MD5, static)
        """
        with self._lock:
            self._cache[mac_address.upper()] = (key, time.time(), method)
            logger.info(f"Cached {method} key for {mac_address}")
            self._save_cache()

    def remove(self, mac_address: str):
        """Remove a cached key.

        Args:
            mac_address: Device MAC address
        """
        with self._lock:
            if mac_address.upper() in self._cache:
                del self._cache[mac_address.upper()]
                logger.info(f"Removed cached key for {mac_address}")
                self._save_cache()

    def clear(self):
        """Clear all cached keys."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Cleared {count} cached keys")
            self._save_cache()

    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with self._lock:
            now = time.time()
            total = len(self._cache)
            expired = sum(
                1 for _, (_, ts, _) in self._cache.items() if now - ts > self._cache_ttl
            )

            return {"total": total, "active": total - expired, "expired": expired}


# Global key cache instance
_key_cache: Optional[BleKeyCache] = None


def get_key_cache() -> BleKeyCache:
    """Get the global key cache instance."""
    global _key_cache
    if _key_cache is None:
        _key_cache = BleKeyCache()
    return _key_cache

