"""Configuration system for the U-tec library.

This module provides a centralized configuration system that allows 
customization of the library's behavior.
"""
from dataclasses import dataclass, field
import logging
from typing import Dict, Any, Optional, Type, ClassVar
from enum import Enum, auto
import sys


class LogLevel(Enum):
    """Log levels for the library."""
    
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class BLERetryStrategy(Enum):
    """Available retry strategies for BLE connections."""
    
    CONSTANT = auto()  # Fixed delay between retries
    EXPONENTIAL = auto()  # Exponential backoff
    LINEAR = auto()  # Linearly increasing delay


@dataclass
class UtecConfig:
    """Configuration for the U-tec library."""
    
    # Singleton instance
    _instance: ClassVar[Optional['UtecConfig']] = None
    _logger_configured: ClassVar[bool] = False
    
    # Logging configuration
    log_level: LogLevel = LogLevel.INFO
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logger_name: str = "utecio"
    custom_logger: Optional[logging.Logger] = None
    
    # BLE configuration
    ble_retry_delay: float = 1.5  # seconds
    ble_max_retries: int = 4
    ble_connection_timeout: float = 30.0  # seconds
    ble_retry_strategy: BLERetryStrategy = BLERetryStrategy.EXPONENTIAL
    ble_scan_timeout: float = 10.0  # seconds
    ble_scan_cache_ttl: float = 10.0  # seconds
    
    # API configuration
    api_timeout: float = 5 * 60  # seconds
    api_retry_delay: float = 1.0  # seconds
    api_max_retries: int = 3
    
    # Advanced options
    enable_extended_logging: bool = False
    experimental_features: Dict[str, bool] = field(default_factory=dict)
    
    # Allow custom options for extension points
    custom_options: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def get_instance(cls) -> 'UtecConfig':
        """Get the singleton configuration instance."""
        if cls._instance is None:
            cls._instance = UtecConfig()
            cls._instance._setup_logger()
        return cls._instance
    
    @classmethod
    def configure(cls, **kwargs) -> 'UtecConfig':
        """Configure the library.
        
        This method allows programmatic configuration of the library.
        It returns the singleton configuration instance for chaining.
        
        Args:
            **kwargs: Configuration options to set.
        
        Returns:
            The configuration instance.
        """
        instance = cls.get_instance()
        old_log_level = instance.log_level
        old_custom_logger = instance.custom_logger
        
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
            else:
                instance.custom_options[key] = value
        
        # Only reconfigure logger if log-related options changed
        if ('log_level' in kwargs and kwargs['log_level'] != old_log_level) or \
           ('custom_logger' in kwargs and kwargs['custom_logger'] != old_custom_logger):
            cls._logger_configured = False  # Reset flag to allow reconfiguration
            instance._setup_logger()
            
        return instance
    
    def _setup_logger(self) -> None:
        """Set up the logger based on the current configuration."""
        # Prevent duplicate logger setup
        if self.__class__._logger_configured and not self.custom_logger:
            return
            
        if self.custom_logger:
            self.logger = self.custom_logger
            return
        
        logger = logging.getLogger(self.logger_name)
        
        # Only configure if not already configured or if level changed
        if not self.__class__._logger_configured:
            logger.setLevel(self.log_level.value)
            
            # Remove existing handlers to avoid duplicates
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
            
            # Prevent propagation to root logger to avoid duplicates
            logger.propagate = False
            
            # Add console handler only if no handlers exist
            if not logger.handlers:
                ch = logging.StreamHandler(sys.stdout)
                ch.setLevel(self.log_level.value)
                formatter = logging.Formatter(self.log_format)
                ch.setFormatter(formatter)
                logger.addHandler(ch)
            
            self.__class__._logger_configured = True
        else:
            # Just update the level if logger already exists
            logger.setLevel(self.log_level.value)
            for handler in logger.handlers:
                handler.setLevel(self.log_level.value)
        
        self.logger = logger
    
    def get_logger(self, name: Optional[str] = None) -> logging.Logger:
        """Get a logger for a specific component.
        
        If a name is provided, it will return a child logger with the given name.
        Otherwise, it returns the main logger.
        
        Args:
            name: Name of the component.
        
        Returns:
            Logger instance.
        """
        if name:
            child_logger = self.logger.getChild(name)
            # Ensure child logger doesn't propagate to avoid duplicates
            child_logger.propagate = False
            
            # Only add handler if child doesn't have one
            if not child_logger.handlers:
                ch = logging.StreamHandler(sys.stdout)
                ch.setLevel(self.log_level.value)
                formatter = logging.Formatter(self.log_format)
                ch.setFormatter(formatter)
                child_logger.addHandler(ch)
            
            return child_logger
        return self.logger
    
    def get_option(self, key: str, default: Any = None) -> Any:
        """Get a configuration option.
        
        First checks if the option is a direct attribute of the config, then
        looks in the custom_options dictionary.
        
        Args:
            key: Option name.
            default: Default value if the option is not found.
        
        Returns:
            Option value or default.
        """
        if hasattr(self, key):
            return getattr(self, key)
        return self.custom_options.get(key, default)


# Create a singleton instance
config = UtecConfig.get_instance()

# Example usage:
# from utec.config import config
# 
# # Configure the library
# config.configure(
#     log_level=LogLevel.DEBUG,
#     ble_max_retries=5,
#     custom_options={'my_feature': True}
# )
# 
# # Get a logger
# logger = config.get_logger('my_component')
# 
# # Use configuration in your code
# max_retries = config.ble_max_retries
# custom_value = config.get_option('my_feature', False)