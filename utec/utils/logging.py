"""Logging utilities for the U-tec library."""

import logging
from ..config import config

def get_logger(name):
    """Get a logger for a component.
    
    Args:
        name: Name of the component.
        
    Returns:
        Logger instance.
    """
    return config.get_logger(name)

# Pre-create common loggers for convenience
api_logger = get_logger("api")
ble_logger = get_logger("ble")