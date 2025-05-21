"""Event system for the U-tec library.

This module provides an event system that allows components to
subscribe to events and be notified when they occur.
"""
from enum import Enum, auto
from typing import Dict, Set, Callable, Any, Optional, List, Union
import asyncio
import functools
import inspect
from dataclasses import dataclass, field


class EventType(Enum):
    """Types of events that can be emitted."""
    
    # Device events
    DEVICE_CONNECTED = auto()
    DEVICE_DISCONNECTED = auto()
    DEVICE_STATUS_UPDATED = auto()
    DEVICE_UNLOCKED = auto()
    DEVICE_LOCKED = auto()
    DEVICE_WORK_MODE_CHANGED = auto()
    DEVICE_AUTO_LOCK_CHANGED = auto()
    DEVICE_BATTERY_UPDATED = auto()
    DEVICE_ERROR = auto()
    
    # API events
    API_CONNECTED = auto()
    API_DISCONNECTED = auto()
    API_DEVICES_SYNCED = auto()
    API_ERROR = auto()
    
    # BLE events
    BLE_SCAN_STARTED = auto()
    BLE_SCAN_STOPPED = auto()
    BLE_DEVICE_FOUND = auto()
    BLE_ERROR = auto()
    
    # Generic events
    CUSTOM = auto()  # For user-defined events


@dataclass
class Event:
    """Event data container."""
    
    type: EventType
    source: Any
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=asyncio.get_event_loop().time)
    
    def __post_init__(self):
        """Set default values."""
        # Ensure source is not None
        if self.source is None:
            raise ValueError("Event source cannot be None")
            
        # Ensure data is a dictionary
        if not isinstance(self.data, dict):
            self.data = {"value": self.data}
            
    def __str__(self) -> str:
        """String representation of the event."""
        source_name = getattr(self.source, "name", str(self.source))
        return f"Event({self.type.name}, source={source_name}, data={self.data})"


class EventEmitter:
    """Event emitter for a specific event source."""
    
    def __init__(self, source: Any, dispatcher: 'EventDispatcher' = None):
        """Initialize the event emitter.
        
        Args:
            source: Event source.
            dispatcher: Event dispatcher to use.
        """
        self.source = source
        self._dispatcher = dispatcher or EventDispatcher.get_instance()
    
    def emit(
        self,
        event_type: EventType,
        data: Any = None,
        dispatcher: 'EventDispatcher' = None
    ) -> Event:
        """Emit an event.
        
        Args:
            event_type: Type of the event.
            data: Event data.
            dispatcher: Specific dispatcher to use (optional).
            
        Returns:
            The emitted event.
        """
        event = Event(type=event_type, source=self.source, data=data)
        dispatcher = dispatcher or self._dispatcher
        dispatcher.dispatch(event)
        return event
    
    async def emit_async(
        self,
        event_type: EventType,
        data: Any = None,
        dispatcher: 'EventDispatcher' = None
    ) -> Event:
        """Emit an event asynchronously.
        
        Args:
            event_type: Type of the event.
            data: Event data.
            dispatcher: Specific dispatcher to use (optional).
            
        Returns:
            The emitted event.
        """
        event = Event(type=event_type, source=self.source, data=data)
        dispatcher = dispatcher or self._dispatcher
        await dispatcher.dispatch_async(event)
        return event


# Type alias for event handler functions
EventHandler = Callable[[Event], Any]


class EventDispatcher:
    """Event dispatcher that manages event subscription and dispatching."""
    
    _instance = None
    
    def __init__(self):
        """Initialize the event dispatcher."""
        self._handlers: Dict[EventType, Set[EventHandler]] = {
            event_type: set() for event_type in EventType
        }
        self._source_filters: Dict[EventHandler, Set[Any]] = {}
    
    @classmethod
    def get_instance(cls) -> 'EventDispatcher':
        """Get the singleton instance of the event dispatcher."""
        if cls._instance is None:
            cls._instance = EventDispatcher()
        return cls._instance
    
    def subscribe(
        self,
        event_type: Union[EventType, List[EventType]],
        handler: EventHandler,
        source_filter: Any = None
    ) -> None:
        """Subscribe to an event.
        
        Args:
            event_type: Type(s) of events to subscribe to.
            handler: Event handler function.
            source_filter: Only receive events from this source.
        """
        # Convert single event type to list
        event_types = event_type if isinstance(event_type, list) else [event_type]
        
        for evt_type in event_types:
            self._handlers[evt_type].add(handler)
        
        # Add source filter if specified
        if source_filter is not None:
            if handler not in self._source_filters:
                self._source_filters[handler] = set()
            
            # Convert single source to list
            sources = (
                source_filter if isinstance(source_filter, list) 
                else [source_filter]
            )
            
            for source in sources:
                self._source_filters[handler].add(source)
    
    def unsubscribe(
        self,
        event_type: Union[EventType, List[EventType]],
        handler: EventHandler
    ) -> None:
        """Unsubscribe from an event.
        
        Args:
            event_type: Type(s) of events to unsubscribe from.
            handler: Event handler function to remove.
        """
        # Convert single event type to list
        event_types = event_type if isinstance(event_type, list) else [event_type]
        
        for evt_type in event_types:
            if handler in self._handlers[evt_type]:
                self._handlers[evt_type].remove(handler)
        
        # Remove source filters
        if handler in self._source_filters:
            del self._source_filters[handler]
    
    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Unsubscribe a handler from all events.
        
        Args:
            handler: Event handler function to remove.
        """
        for event_type in EventType:
            if handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)
        
        # Remove source filters
        if handler in self._source_filters:
            del self._source_filters[handler]
    
    def dispatch(self, event: Event) -> None:
        """Dispatch an event to all subscribed handlers.
        
        Args:
            event: Event to dispatch.
        """
        for handler in self._handlers[event.type].copy():
            # Apply source filter if necessary
            if (handler in self._source_filters and
                event.source not in self._source_filters[handler]):
                continue
            
            # Call the handler
            try:
                # Check if the handler is a coroutine function
                if inspect.iscoroutinefunction(handler):
                    # Create a task to run the handler asynchronously
                    asyncio.create_task(handler(event))
                else:
                    # Call the handler directly
                    handler(event)
            except Exception as e:
                # Log the error but don't stop dispatching to other handlers
                print(f"Error in event handler: {e}")
    
    async def dispatch_async(self, event: Event) -> None:
        """Dispatch an event asynchronously to all subscribed handlers.
        
        Args:
            event: Event to dispatch.
        """
        coroutines = []
        
        for handler in self._handlers[event.type].copy():
            # Apply source filter if necessary
            if (handler in self._source_filters and
                event.source not in self._source_filters[handler]):
                continue
            
            # Call the handler
            try:
                # Check if the handler is a coroutine function
                if inspect.iscoroutinefunction(handler):
                    coroutines.append(handler(event))
                else:
                    # Call the handler directly
                    handler(event)
            except Exception as e:
                # Log the error but don't stop dispatching to other handlers
                print(f"Error in event handler: {e}")
        
        # Await all coroutines
        if coroutines:
            await asyncio.gather(*coroutines, return_exceptions=True)


# Helper decorator for event handlers
def event_handler(
    event_type: Union[EventType, List[EventType]],
    source_filter: Any = None,
    dispatcher: EventDispatcher = None
):
    """Decorator to mark a function as an event handler.
    
    Args:
        event_type: Type(s) of events to subscribe to.
        source_filter: Only receive events from this source.
        dispatcher: Event dispatcher to use.
        
    Returns:
        Decorated function.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        # Register the handler
        dispatcher = dispatcher or EventDispatcher.get_instance()
        dispatcher.subscribe(event_type, func, source_filter)
        
        return wrapper
    
    return decorator


# Example usage:
#
# # Create an event emitter for a device
# emitter = EventEmitter(lock_device)
# 
# # Subscribe to events
# @event_handler(EventType.DEVICE_UNLOCKED)
# def on_device_unlocked(event):
#     print(f"Device unlocked: {event.source.name}")
# 
# # Or subscribe manually
# def on_lock_status_change(event):
#     print(f"Lock status changed: {event.data.get('status')}")
# 
# dispatcher = EventDispatcher.get_instance()
# dispatcher.subscribe(EventType.DEVICE_STATUS_UPDATED, on_lock_status_change)
# 
# # Emit events
# emitter.emit(EventType.DEVICE_UNLOCKED)
# emitter.emit(EventType.DEVICE_STATUS_UPDATED, {"status": "locked"})
# 
# # Unsubscribe
# dispatcher.unsubscribe(EventType.DEVICE_STATUS_UPDATED, on_lock_status_change)