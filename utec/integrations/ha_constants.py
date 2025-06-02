"""
Home Assistant specific constants for U-tec integration.
Maps U-tec raw values to Home Assistant expected formats.
"""

from ..utils.constants import BATTERY_LEVEL, LOCK_MODE, BOLT_STATUS

# Home Assistant lock state mappings
# Maps raw lock status values to HA lock entity states
HA_LOCK_STATES = {
    0: "UNLOCKED",      # BOLT_STATUS[0] = "Unlocked"
    1: "LOCKED",        # BOLT_STATUS[1] = "Locked"  
    255: "UNAVAILABLE", # BOLT_STATUS[255] = "Unavailable"
    2: "LOCKED",        # Additional U-tec state
    3: "JAMMED",        # Additional U-tec state (lock mechanism stuck)
    -1: "UNKNOWN"       # Fallback for unknown states
}

# Home Assistant battery percentage mappings
# Maps U-tec battery level codes to percentage values
HA_BATTERY_LEVELS = {
    -1: 0,   # Unknown/Fallback state -> 0%
    0: 5,    # BATTERY_LEVEL[0] = "Replace" -> 5% (critical)
    1: 25,   # BATTERY_LEVEL[1] = "Low" -> 25%
    2: 60,   # BATTERY_LEVEL[2] = "Medium" -> 60%
    3: 90    # BATTERY_LEVEL[3] = "High" -> 90%
}

# Home Assistant device classes for sensors
HA_DEVICE_CLASSES = {
    'battery': 'battery',
    'signal_strength': 'signal_strength',
    'lock': 'lock',
    'connectivity': 'connectivity'
}

# MQTT topics structure
MQTT_TOPICS = {
    'discovery_prefix': 'homeassistant',
    'device_prefix': 'utec',
    'bridge_availability': 'utec/bridge/availability',
    'bridge_health': 'utec/bridge/health',
    'bridge_command': 'utec/bridge/command',
    'lock_state': 'utec/{device_id}/lock/state',
    'lock_command': 'utec/{device_id}/lock/command',
    'battery_state': 'utec/{device_id}/battery/state',
    'lock_mode_state': 'utec/{device_id}/lock_mode/state',
    'autolock_state': 'utec/{device_id}/autolock/state',
    'mute_state': 'utec/{device_id}/mute/state',
    'signal_state': 'utec/{device_id}/signal/state'
}

# Home Assistant discovery entity configurations
HA_LOCK_DISCOVERY_CONFIG = {
    'lock': {
        'payload_lock': 'LOCK',
        'payload_unlock': 'UNLOCK',
        'state_locked': 'LOCKED',
        'state_unlocked': 'UNLOCKED',
        'optimistic': False,
        'device_class': 'lock'
    },
    'battery': {
        'unit_of_measurement': '%',
        'device_class': 'battery',
        'state_class': 'measurement',
        'entity_category': 'diagnostic'
    },
    'lock_mode': {
        'icon': 'mdi:lock-outline',
        'entity_category': 'diagnostic'
    },
    'autolock': {
        'unit_of_measurement': 's',
        'icon': 'mdi:timer-outline',
        'entity_category': 'config'
    },
    'mute': {
        'payload_on': 'True',
        'payload_off': 'False',
        'icon': 'mdi:volume-off',
        'entity_category': 'diagnostic'
    },
    'signal': {
        'unit_of_measurement': 'dBm',
        'device_class': 'signal_strength',
        'state_class': 'measurement',
        'entity_category': 'diagnostic'
    }
}