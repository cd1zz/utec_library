#!/usr/bin/env python3
"""
Main script to run U-tec to Home Assistant integration.
"""

import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec
from utec.integrations.ha_mqtt import HomeAssistantMQTTIntegration, HAMQTTConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from environment variables."""
    # Required environment variables
    utec_email = os.getenv('UTEC_EMAIL')
    utec_password = os.getenv('UTEC_PASSWORD')
    mqtt_host = os.getenv('MQTT_HOST')
    
    if not utec_email:
        raise ValueError("UTEC_EMAIL environment variable is required")
    if not utec_password:
        raise ValueError("UTEC_PASSWORD environment variable is required")
    if not mqtt_host:
        raise ValueError("MQTT_HOST environment variable is required")
    
    # Optional environment variables with defaults
    mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
    mqtt_username = os.getenv('MQTT_USERNAME')
    mqtt_password = os.getenv('MQTT_PASSWORD')
    mqtt_client_id = os.getenv('MQTT_CLIENT_ID')
    mqtt_discovery_prefix = os.getenv('MQTT_DISCOVERY_PREFIX', 'homeassistant')
    mqtt_device_prefix = os.getenv('MQTT_DEVICE_PREFIX', 'utec')
    update_interval = int(os.getenv('UPDATE_INTERVAL', '300'))  # 5 minutes default
    
    return {
        'utec_email': utec_email,
        'utec_password': utec_password,
        'mqtt_config': HAMQTTConfig(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            client_id=mqtt_client_id,
            discovery_prefix=mqtt_discovery_prefix,
            device_prefix=mqtt_device_prefix
        ),
        'update_interval': update_interval
    }


async def main():
    """Main function to run the U-tec to Home Assistant integration."""
    
    try:
        # Load configuration from environment variables
        config = load_config()
        logger.info("Configuration loaded successfully")
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your .env file and ensure all required variables are set")
        return
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return
    
    # Set up U-tec library
    utec.setup(
        log_level=utec.LogLevel.INFO,
        ble_max_retries=5
    )
    
    # Create MQTT integration
    ha_mqtt = HomeAssistantMQTTIntegration(config['mqtt_config'])
    
    # Connect to MQTT broker
    logger.info("Connecting to MQTT broker...")
    if not await ha_mqtt.connect():
        logger.error("Failed to connect to MQTT broker")
        return
    
    logger.info("Connected to MQTT broker successfully")
    
    try:
        # Discover U-tec devices
        logger.info("Discovering U-tec devices...")
        locks = await utec.discover_devices(config['utec_email'], config['utec_password'])
        
        if not locks:
            logger.warning("No U-tec devices found")
            return
        
        logger.info(f"Found {len(locks)} U-tec device(s)")
        
        # Set up each lock in Home Assistant
        for lock in locks:
            logger.info(f"Setting up {lock.name} ({lock.mac_uuid})")
            
            # Set up Home Assistant discovery
            if ha_mqtt.setup_lock_discovery(lock):
                logger.info(f" Set up Home Assistant discovery for {lock.name}")
            else:
                logger.error(f" Failed to set up discovery for {lock.name}")
                continue
            
            # Get initial status
            try:
                await lock.async_update_status()
                logger.info(f" Updated status for {lock.name}")
                
                # Log current status
                logger.info(f"  Battery: {lock.battery}")
                logger.info(f"  Lock Status: {lock.lock_status}")
                logger.info(f"  Lock Mode: {lock.lock_mode}")
                logger.info(f"  Autolock: {lock.autolock_time}s")
                logger.info(f"  Mute: {lock.mute}")
                
            except Exception as e:
                logger.error(f" Failed to update status for {lock.name}: {e}")
                continue
            
            # Publish initial state to Home Assistant
            if ha_mqtt.publish_lock_state(lock):
                logger.info(f" Published initial state for {lock.name}")
            else:
                logger.error(f" Failed to publish state for {lock.name}")
        
        # Main loop - update states periodically
        logger.info(f"Starting monitoring loop (updates every {config['update_interval']}s, Ctrl+C to stop)...")
        
        while True:
            await asyncio.sleep(config['update_interval'])
            
            logger.info("Updating lock states...")
            
            for lock in locks:
                try:
                    # Update lock status
                    await lock.async_update_status()
                    
                    # Publish updated state
                    if ha_mqtt.publish_lock_state(lock):
                        logger.debug(f"Updated state for {lock.name}")
                    else:
                        logger.warning(f"Failed to publish state for {lock.name}")
                        
                except Exception as e:
                    logger.error(f"Error updating {lock.name}: {e}")
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Clean up
        ha_mqtt.disconnect()
        logger.info("Disconnected from MQTT broker")


if __name__ == "__main__":
    asyncio.run(main())