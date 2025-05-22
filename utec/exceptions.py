"""Custom exceptions for the U-tec library."""


class UtecError(Exception):
    """Base exception for U-tec library."""
    pass


class InvalidResponse(UtecError):
    """Exception raised when the API returns an invalid response."""
    pass


class InvalidCredentials(UtecError):
    """Exception raised when authentication credentials are invalid."""
    pass


class ConnectionError(UtecError):
    """Exception raised when there's a connection error."""
    pass


class DeviceNotFoundError(UtecError):
    """Exception raised when a device is not found."""
    pass


class DeviceError(UtecError):
    """Exception raised when there's an error with a device operation."""
    pass


class BLEError(UtecError):
    """Exception raised for BLE-related errors."""
    pass


class ConfigurationError(UtecError):
    """Exception raised for configuration-related errors."""
    pass