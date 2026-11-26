"""Route Supervisor events to the analytics destination for the process mode."""

from smart_search.analytics import local_event_store
from smart_search.contracts import SearchEvent
from smart_search.events.kafka import broker_event_publisher
from smart_search.runtime_config import get_mode


def record_search_event(event: SearchEvent) -> None:
    if get_mode() == "test":
        local_event_store().record(event)
    else:
        broker_event_publisher().publish(event)
