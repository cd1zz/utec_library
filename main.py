#!/usr/bin/env python3
"""
Main script to run U-tec to Home Assistant integration.
Updated for compatibility with improved MQTT client.
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
    
    # New optional environment variables for improved MQTT client
    mqtt_reconnect_on_failure = os.getenv('MQTT_RECONNECT_ON_FAILURE', 'true').lower() == 'true'
    mqtt_max_reconnect_delay = int(os.getenv('MQTT_MAX_RECONNECT_DELAY', '300'))
    mqtt_health_check_interval = int(os.getenv('MQTT_HEALTH_CHECK_INTERVAL', '30'))
    mqtt_connect_timeout = int(os.getenv('MQTT_CONNECT_TIMEOUT', '30'))
    mqtt_publish_timeout = int(os.getenv('MQTT_PUBLISH_TIMEOUT', '10'))
    
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
            device_prefix=mqtt_device_prefix,
            # New configuration options
            reconnect_on_failure=mqtt_reconnect_on_failure,
            max_reconnect_delay=mqtt_max_reconnect_delay,
            health_check_interval=mqtt_health_check_interval,
            connect_timeout=mqtt_connect_timeout,
            publish_timeout=mqtt_publish_timeout
        ),
        'update_interval': update_interval
    }


async def setup_device_discovery(ha_mqtt, lock):
    """Set up Home Assistant discovery for a single device with error handling."""
    try:
        logger.info(f"Setting up {lock.name} ({lock.mac_uuid})")
        
        # Set up Home Assistant discovery (now async)
        if await ha_mqtt.setup_lock_discovery(lock):
            logger.info(f" Set up Home Assistant discovery for {lock.name}")
            return True
        else:
            logger.error(f" Failed to set up discovery for {lock.name}")
            return False
            
    except Exception as e:
        logger.error(f" Error setting up discovery for {lock.name}: {e}")
        return False


async def update_and_publish_device_state(ha_mqtt, lock):
    """Update device status and publish to MQTT with error handling."""
    try:
        # Get initial/updated status
        await lock.async_update_status()
        logger.debug(f" Updated status for {lock.name}")
        
        # Log current status (same as before)
        logger.info(f"  Battery: {lock.battery}")
        logger.info(f"  Lock Status: {lock.lock_status}")
        logger.info(f"  Lock Mode: {lock.lock_mode}")
        logger.info(f"  Autolock: {lock.autolock_time}s")
        logger.info(f"  Mute: {lock.mute}")
        
        # Publish state to Home Assistant (now async)
        if await ha_mqtt.publish_lock_state(lock):
            logger.debug(f" Published state for {lock.name}")
            return True
        else:
            logger.warning(f" Failed to publish state for {lock.name}")
            return False
            
    except Exception as e:
        logger.error(f" Failed to update/publish state for {lock.name}: {e}")
        return False


async def monitor_connection_health(ha_mqtt):
    """Monitor MQTT connection health and log status."""
    while True:
        try:
            # Get connection statistics from improved client
            stats = ha_mqtt.get_connection_stats()
            
            # Log health status - only log issues or periodic updates
            if stats['consecutive_failures'] > 0 or stats['health'] != 'healthy':
                logger.warning(f"MQTT Health: {stats['health']}, "
                             f"Failures: {stats['consecutive_failures']}, "
                             f"Uptime: {stats['uptime']:.1f}s, "
                             f"Reconnections: {stats['total_reconnections']}")
            elif int(stats['uptime']) % 300 == 0 and stats['uptime'] > 0:  # Every 5 minutes
                logger.info(f"MQTT Health: {stats['health']}, "
                          f"Uptime: {stats['uptime']:.1f}s")
            
            await asyncio.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Error monitoring MQTT health: {e}")
            await asyncio.sleep(30)


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
    
    # Create MQTT integration with improved client
    ha_mqtt = HomeAssistantMQTTIntegration(config['mqtt_config'])
    
    # Connect to MQTT broker (now async)
    logger.info("Connecting to MQTT broker...")
    if not await ha_mqtt.connect():
        logger.error("Failed to connect to MQTT broker")
        return
    
    logger.info("Connected to MQTT broker successfully")
    
    # Start health monitoring in background
    health_monitor_task = asyncio.create_task(monitor_connection_health(ha_mqtt))
    
    try:
        # Discover U-tec devices
        logger.info("Discovering U-tec devices...")
        locks = await utec.discover_devices(config['utec_email'], config['utec_password'])
        
        if not locks:
            logger.warning("No U-tec devices found")
            return
        
        logger.info(f"Found {len(locks)} U-tec device(s)")
        
        # Set up each lock in Home Assistant
        setup_success_count = 0
        for lock in locks:
            if await setup_device_discovery(ha_mqtt, lock):
                setup_success_count += 1
                
                # Get and publish initial status
                if await update_and_publish_device_state(ha_mqtt, lock):
                    logger.info(f" Published initial state for {lock.name}")
                else:
                    logger.error(f" Failed to publish initial state for {lock.name}")
        
        if setup_success_count == 0:
            logger.error("Failed to set up any devices - exiting")
            return
        
        logger.info(f"Successfully set up {setup_success_count}/{len(locks)} devices")
        
        # Main loop - update states periodically
        previous_states = {}
        
        logger.info(f"Starting monitoring loop (updates every {config['update_interval']}s, Ctrl+C to stop)...")
        
        while True:
            await asyncio.sleep(config['update_interval'])
            
            logger.info("Updating lock states...")
            
            # Check MQTT connection health before attempting updates
            if not ha_mqtt.is_connected:
                logger.warning("MQTT not connected - skipping state updates")
                continue
            
            successful_updates = 0
            for lock in locks:
                try:
                    # Update lock status
                    await lock.async_update_status()
                    
                    # Current state (same logic as before)
                    current_state = {
                        'battery': lock.battery,
                        'lock_status': lock.lock_status,
                        'lock_mode': lock.lock_mode,
                        'autolock_time': lock.autolock_time,
                        'mute': lock.mute
                    }
                    
                    # Check for changes (same logic as before)
                    previous_state = previous_states.get(lock.mac_uuid, {})
                    changes = []
                    
                    for key, value in current_state.items():
                        if previous_state.get(key) != value:
                            changes.append(f"{key}: {previous_state.get(key)} -> {value}")
                    
                    if changes:
                        logger.info(f"Changes detected for {lock.name}:")
                        for change in changes:
                            logger.info(f"  {change}")
                    else:
                        logger.debug(f"No changes for {lock.name}")
                    
                    # Store current state for next comparison
                    previous_states[lock.mac_uuid] = current_state
                    
                    # Publish updated state (now async with better error handling)
                    if await ha_mqtt.publish_lock_state(lock):
                        logger.debug(f"Published state for {lock.name}")
                        successful_updates += 1
                    else:
                        logger.warning(f"Failed to publish state for {lock.name}")
                        
                except Exception as e:
                    logger.error(f"Error updating {lock.name}: {e}")
            
            # Log summary of update cycle
            if successful_updates == len(locks):
                logger.info(f"Successfully updated all {len(locks)} devices")
            else:
                logger.warning(f"Successfully updated {successful_updates}/{len(locks)} devices")
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Clean up
        logger.info("Cleaning up...")
        
        # Cancel health monitoring
        if not health_monitor_task.done():
            health_monitor_task.cancel()
            try:
                await health_monitor_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect from MQTT (this is now improved with proper cleanup)
        ha_mqtt.disconnect()
        logger.info("Disconnected from MQTT broker")


if __name__ == "__main__":
    asyncio.run(main())