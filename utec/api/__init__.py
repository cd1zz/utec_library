"""API client for the U-tec library."""

import logging
from ..config import config

# Get a logger for this module
logger = logging.getLogger("utecio.api")

# Import client classes
from .client import UtecClient

# Define exports
__all__ = [
    "UtecClient",
    "logger"
]