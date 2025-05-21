"""Factory pattern for device creation.

This module provides a factory class that creates device instances
based on device model and allows registration of custom device classes.
"""
from typing import Dict, Type, Any, Optional, Callable, TypeVar, cast
import importlib
import inspect
from enum import Enum

# Import base classes
from .abstract import BaseBleDevice, BaseLock

# Type variables for factory return types
T = TypeVar('T', bound=BaseBleDevice)
LockType = TypeVar('LockType', bound=BaseLock)


class DeviceCategory(Enum):
    """Categories of supported devices."""
    
    LOCK = "lock"
    DOOR_SENSOR = "door_sensor"
    KEYPAD = "keypad"
    BRIDGE = "bridge"
    GENERIC = "generic"


class DeviceFactory:
    """Factory for creating device instances.
    
    This class maintains a registry of device classes and provides
    methods to create device instances based on device model.
    """
    
    # Registry of device classes by model and category
    _registry: Dict[str, Dict[str, Type[BaseBleDevice]]] = {
        cat.value: {} for cat in DeviceCategory
    }
    
    # Registry of default device classes by category
    _default_classes: Dict[str, Type[BaseBleDevice]] = {}
    
    @classmethod
    def register(
        cls,
        model: str,
        device_class: Type[T],
        category: DeviceCategory = DeviceCategory.GENERIC
    ) -> None:
        """Register a device class for a specific model.
        
        Args:
            model: Model identifier.
            device_class: Device class to register.
            category: Device category.
        """
        cls._registry[category.value][model] = device_class
    
    @classmethod
    def register_default(
        cls,
        device_class: Type[T],
        category: DeviceCategory = DeviceCategory.GENERIC
    ) -> None:
        """Register a default device class for a category.
        
        Args:
            device_class: Device class to register as default.
            category: Device category.
        """
        cls._default_classes[category.value] = device_class
    
    @classmethod
    def create_from_json(
        cls,
        json_config: Dict[str, Any],
        category: DeviceCategory = DeviceCategory.GENERIC
    ) -> BaseBleDevice:
        """Create a device instance from JSON configuration.
        
        Args:
            json_config: JSON configuration data.
            category: Device category.
            
        Returns:
            Device instance.
            
        Raises:
            ValueError: If no suitable device class is found.
        """
        model = json_config.get("model", "")
        
        # Try to find a registered class for this model and category
        device_class = cls._registry[category.value].get(model)
        
        # If not found, try the default class for this category
        if device_class is None:
            device_class = cls._default_classes.get(category.value)
            
        # If still not found, raise an error
        if device_class is None:
            raise ValueError(
                f"No registered device class for model '{model}' "
                f"in category '{category.value}'"
            )
        
        # Create and return the device instance
        return device_class.from_json(json_config)
    
    @classmethod
    def create_lock_from_json(cls, json_config: Dict[str, Any]) -> BaseLock:
        """Create a lock device instance from JSON configuration.
        
        This is a convenience method that creates a lock device.
        
        Args:
            json_config: JSON configuration data.
            
        Returns:
            Lock device instance.
        """
        return cast(
            BaseLock,
            cls.create_from_json(json_config, DeviceCategory.LOCK)
        )
    
    @classmethod
    def discover_and_register(cls, module_path: str) -> None:
        """Automatically discover and register device classes from a module.
        
        This method imports the specified module and registers all device classes
        found in it. Device classes should have a 'model' class attribute and
        inherit from BaseBleDevice.
        
        Args:
            module_path: Import path to the module containing device classes.
        """
        try:
            module = importlib.import_module(module_path)
            
            # Find all classes that inherit from BaseBleDevice
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                    issubclass(obj, BaseBleDevice) and
                    obj is not BaseBleDevice):
                    
                    # Determine the category
                    if issubclass(obj, BaseLock):
                        category = DeviceCategory.LOCK
                    else:
                        category = DeviceCategory.GENERIC
                    
                    # Get the model identifier
                    model = getattr(obj, "model", None)
                    if model:
                        cls.register(model, obj, category)
                    else:
                        # If no model is specified, register as default
                        cls.register_default(obj, category)
                    
        except ImportError as e:
            raise ImportError(
                f"Failed to import module '{module_path}': {e}"
            ) from e


# Example usage:
# 
# # Register the built-in device classes
# from utec.ble.lock import UtecBleLock
# 
# DeviceFactory.register("UL1-BT", UtecBleLock, DeviceCategory.LOCK)
# DeviceFactory.register("Latch-5-NFC", UtecBleLock, DeviceCategory.LOCK)
# # ... register other built-in models
# 
# # Register a default lock class
# DeviceFactory.register_default(UtecBleLock, DeviceCategory.LOCK)
# 
# # Create a lock from JSON configuration
# lock = DeviceFactory.create_lock_from_json(json_config)
# 
# # Automatically discover and register custom device classes
# DeviceFactory.discover_and_register("my_package.devices")