import asyncio
import logging
import time
import runpy
import pytest

module = runpy.run_path('utec/events.py')
Event = module['Event']
EventType = module['EventType']
EventDispatcher = module['EventDispatcher']
event_handler = module['event_handler']


@pytest.fixture(autouse=True)
def fresh_dispatcher():
    """Reset the singleton dispatcher between tests."""
    EventDispatcher._instance = None
    yield
    EventDispatcher._instance = None


def test_dispatch_sync_handler():
    dispatcher = EventDispatcher.get_instance()
    received = []

    def handler(event):
        received.append(event)

    dispatcher.subscribe(EventType.DEVICE_CONNECTED, handler)
    event = Event(type=EventType.DEVICE_CONNECTED, source="test")
    dispatcher.dispatch(event)

    assert len(received) == 1
    assert received[0] is event


@pytest.mark.asyncio
async def test_dispatch_async_handler_error_logged(caplog):
    dispatcher = EventDispatcher.get_instance()

    async def bad_handler(event):
        raise ValueError("handler boom")

    dispatcher.subscribe(EventType.DEVICE_CONNECTED, bad_handler)
    event = Event(type=EventType.DEVICE_CONNECTED, source="test")

    with caplog.at_level(logging.ERROR):
        dispatcher.dispatch(event)
        # Let the task run
        await asyncio.sleep(0.05)

    assert any("handler boom" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_async_gathers_exceptions(caplog):
    dispatcher = EventDispatcher.get_instance()

    async def bad_handler(event):
        raise RuntimeError("gather boom")

    dispatcher.subscribe(EventType.DEVICE_LOCKED, bad_handler)
    event = Event(type=EventType.DEVICE_LOCKED, source="test")

    with caplog.at_level(logging.ERROR):
        await dispatcher.dispatch_async(event)

    assert any("gather boom" in record.message for record in caplog.records)


def test_event_timestamp_is_wall_clock():
    before = time.time()
    event = Event(type=EventType.DEVICE_CONNECTED, source="test")
    after = time.time()

    assert before <= event.timestamp <= after


def test_source_filter():
    dispatcher = EventDispatcher.get_instance()
    received = []

    def handler(event):
        received.append(event.source)

    dispatcher.subscribe(EventType.DEVICE_CONNECTED, handler, source_filter="lock_a")

    dispatcher.dispatch(Event(type=EventType.DEVICE_CONNECTED, source="lock_a"))
    dispatcher.dispatch(Event(type=EventType.DEVICE_CONNECTED, source="lock_b"))

    assert received == ["lock_a"]


def test_event_handler_decorator():
    """Verify the @event_handler decorator registers without UnboundLocalError."""
    @event_handler(EventType.DEVICE_UNLOCKED)
    def on_unlock(event):
        pass

    dispatcher = EventDispatcher.get_instance()
    assert on_unlock in dispatcher._handlers[EventType.DEVICE_UNLOCKED] or \
           any(True for h in dispatcher._handlers[EventType.DEVICE_UNLOCKED])
