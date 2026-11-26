import json

import pytest
from confluent_kafka import KafkaError

from smart_search.contracts import SearchEvent
from smart_search.events import EventDeliveryError
from smart_search.events.kafka import KafkaEventPublisher


class PendingProducer:
    def __init__(self, error=None):
        self.error = error
        self.messages = []

    def produce(self, topic, *, key, value, on_delivery):
        self.messages.append((topic, key, value))
        self.callback = on_delivery

    def poll(self, _timeout):
        self.callback(self.error, None)


def _event():
    return SearchEvent(
        request_id="request-42",
        normalized_query="сервер",
        intent="product_search",
        status="partial",
        result_count=1,
        latency_ms=8,
    )


def test_publisher_sends_versioned_utf8_json_by_request_id():
    transport = PendingProducer()
    event = _event()
    KafkaEventPublisher(transport, topic="search-events.v1", timeout_ms=500).publish(event)

    [(topic, key, value)] = transport.messages
    assert topic == "search-events.v1"
    assert key == b"request-42"
    document = json.loads(value.decode("utf-8"))
    assert document["event_id"] == "search.completed:request-42"
    assert document["event_type"] == "search.completed"
    assert document["schema_version"] == 1
    assert document["payload"] == event.model_dump(mode="json")


def test_publisher_fails_on_broker_delivery_error():
    transport = PendingProducer(error=KafkaError(KafkaError._MSG_TIMED_OUT))

    with pytest.raises(EventDeliveryError, match="broker_delivery_failed"):
        KafkaEventPublisher(transport, topic="search-events.v1", timeout_ms=500).publish(_event())


def test_publisher_waits_at_most_its_ack_budget(monkeypatch):
    class SilentProducer(PendingProducer):
        def poll(self, _timeout):
            pass

    times = iter([1.0, 1.01, 1.021])
    monkeypatch.setattr("smart_search.events.kafka.monotonic", lambda: next(times))

    with pytest.raises(EventDeliveryError, match="broker_ack_timeout"):
        KafkaEventPublisher(SilentProducer(), topic="search-events.v1", timeout_ms=20).publish(
            _event()
        )
