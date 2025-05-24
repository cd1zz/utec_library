#!/usr/bin/env python3
"""
Simple U-tec to Home Assistant integration.
Follows KISS, YAGNI, and SOLID principles.
"""

import time
import logging
import sys
import os
import signal
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import UtecMQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('utec_ha_bridge.log')
    ]
)
logger = logging.getLogger(__name__)


class UtecHaBridge:
    """Simple U-tec to Home Assistant bridge."""
    
    def __init__(self, utec_email: str, utec_password: str, mqtt_host: str, 
                 mqtt_port: int = 1883, mqtt_username: Optional[str] = None, 
                 mqtt_password: Optional[str] = None, update_interval: int = 300):
        """Initialize the bridge with required parameters."""
        self.utec_email = utec_email
        self.utec_password = utec_password
        self.update_interval = update_interval
        self.running = True
        
        # Initialize MQTT client
        self.mqtt_client = UtecMQTTClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password
        )
        
        self.locks: List = []
        logger.info(f"Bridge initialized (update interval: {update_interval}s)")
    
    def initialize(self) -> bool:
        """Initialize U-tec library and discover devices."""
        try:
            logger.info("Initializing U-tec library...")
            utec.setup(log_level=utec.LogLevel.INFO)
            
            logger.info("Connecting to MQTT broker...")
            if not self.mqtt_client.connect():
                logger.error("Failed to connect to MQTT broker")
                return False
            
            logger.info("Discovering U-tec devices...")
            self.locks = utec.discover_devices(self.utec_email, self.utec_password)
            
            if not self.locks:
                logger.warning("No U-tec devices found")
                return False
            
            logger.info(f"Found {len(self.locks)} U-tec device(s)")
            
            # Set up Home Assistant discovery for each device
            for lock in self.locks:
                logger.info(f"Setting up {lock.name} ({lock.mac_uuid})")
                if not self.mqtt_client.setup_lock_discovery(lock):
                    logger.error(f"Failed to set up discovery for {lock.name}")
                    continue
                
                # Get initial status and publish
                self._update_lock_status(lock)
                self._publish_lock_state(lock)
                logger.info(f"Successfully set up {lock.name}")
            
            logger.info("Bridge initialization complete")
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            return False
    
    def _update_lock_status(self, lock) -> bool:
        """Update a single lock's status."""
        try:
            lock.update_status()
            logger.debug(f"Updated status for {lock.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to update {lock.name}: {e}")
            return False
    
    def _publish_lock_state(self, lock) -> bool:
        """Publish a single lock's state to MQTT."""
        try:
            if self.mqtt_client.update_lock_state(lock):
                logger.debug(f"Published state for {lock.name}")
                return True
            else:
                logger.error(f"Failed to publish state for {lock.name}")
                return False
        except Exception as e:
            logger.error(f"Exception publishing state for {lock.name}: {e}")
            return False
    
    def _update_all_locks(self):
        """Update all locks and publish their states."""
        logger.info("Updating all lock states...")
        
        successful_updates = 0
        for lock in self.locks:
            if self._update_lock_status(lock):
                if self._publish_lock_state(lock):
                    successful_updates += 1
        
        total_locks = len(self.locks)
        if successful_updates == total_locks:
            logger.info(f"Successfully updated all {total_locks} locks")
        else:
            logger.warning(f"Updated {successful_updates}/{total_locks} locks")
    
    def run(self):
        """Main application loop."""
        logger.info("Starting monitoring loop...")
        logger.info("Press Ctrl+C to stop")
        
        try:
            while self.running:
                start_time = time.time()
                
                # Update all locks
                self._update_all_locks()
                
                # Calculate sleep time
                elapsed = time.time() - start_time
                sleep_time = max(0, self.update_interval - elapsed)
                
                if sleep_time > 0:
                    logger.debug(f"Sleeping for {sleep_time:.1f} seconds...")
                    time.sleep(sleep_time)
                else:
                    logger.warning(f"Update cycle took {elapsed:.1f}s (longer than {self.update_interval}s interval)")
                    
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        self.running = False
        
        if self.mqtt_client:
            self.mqtt_client.disconnect()
        
        logger.info("Shutdown complete")
    
    def stop(self):
        """Stop the bridge (for signal handlers)."""
        self.running = False


def load_config():
    """Load configuration from environment variables."""
    # Required variables
    utec_email = os.getenv('UTEC_EMAIL')
    utec_password = os.getenv('UTEC_PASSWORD') 
    mqtt_host = os.getenv('MQTT_HOST')
    
    if not all([utec_email, utec_password, mqtt_host]):
        missing = []
        if not utec_email: missing.append('UTEC_EMAIL')
        if not utec_password: missing.append('UTEC_PASSWORD')
        if not mqtt_host: missing.append('MQTT_HOST')
        raise ValueError(f"Missing required environment variables: {missing}")
    
    # Optional variables with defaults
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    mqtt_username = os.getenv('MQTT_USERNAME')
    mqtt_password = os.getenv('MQTT_PASSWORD')
    update_interval = int(os.getenv('UPDATE_INTERVAL', '300'))
    
    return {
        'utec_email': utec_email,
        'utec_password': utec_password,
        'mqtt_host': mqtt_host,
        'mqtt_port': mqtt_port,
        'mqtt_username': mqtt_username,
        'mqtt_password': mqtt_password,
        'update_interval': update_interval
    }


def main():
    """Main application entry point."""
    bridge = None
    
    try:
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded")
        
        # Create bridge
        bridge = UtecHaBridge(**config)
        
        # Set up signal handlers for clean shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            if bridge:
                bridge.stop()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Initialize and run
        if bridge.initialize():
            bridge.run()
            return 0
        else:
            logger.error("Failed to initialize bridge")
            return 1
            
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        return 1
    finally:
        if bridge:
            bridge.shutdown()


if __name__ == "__main__":
    sys.exit(main())