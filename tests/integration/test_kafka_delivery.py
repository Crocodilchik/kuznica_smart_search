import json
from time import monotonic

from confluent_kafka import OFFSET_BEGINNING, Consumer, Producer, TopicPartition
from fastapi.testclient import TestClient

from smart_search.analytics import local_event_store
from smart_search.events.kafka import DEFAULT_TOPIC, broker_event_publisher
from smart_search.interfaces.api import app


def test_prod_posts_search_event_to_kafka_compatible_broker(prod_backend, monkeypatch):
    mock_broker = Producer({"test.mock.num.brokers": 1, "allow.auto.create.topics": True})
    metadata = mock_broker.list_topics(topic=DEFAULT_TOPIC, timeout=5)
    topic = metadata.topics[DEFAULT_TOPIC]
    assert topic.error is None
    broker = next(iter(metadata.brokers.values()))
    address = f"{broker.host}:{broker.port}"
    monkeypatch.setenv("SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS", address)
    monkeypatch.setenv("SMART_SEARCH_KAFKA_TOPIC", DEFAULT_TOPIC)
    monkeypatch.setenv("SMART_SEARCH_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    monkeypatch.setenv("SMART_SEARCH_KAFKA_DELIVERY_TIMEOUT_MS", "5000")
    monkeypatch.delenv("SMART_SEARCH_KAFKA_SSL_CA_LOCATION", raising=False)
    consumer = Consumer(
        {"bootstrap.servers": address, "group.id": "search-smoke", "enable.auto.commit": False}
    )
    try:
        consumer.assign(
            [TopicPartition(topic.topic, number, OFFSET_BEGINNING) for number in topic.partitions]
        )
        response = TestClient(app).post(
            "/v1/search", json={"query": "Монитор", "request_id": "broker-smoke"}
        )
        assert response.status_code == 200
        assert not local_event_store().path.exists()
        deadline = monotonic() + 5
        message = None
        while message is None and monotonic() < deadline:
            message = consumer.poll(0.2)
        assert message is not None
        assert message.error() is None
        assert message.key() == b"broker-smoke"
        body = json.loads(message.value())
        assert body["event_id"] == "search.completed:broker-smoke"
        assert body["event_type"] == "search.completed"
        assert body["schema_version"] == 1
        assert body["payload"]["status"] == response.json()["status"]
        assert body["payload"]["result_count"] == len(response.json()["products"])
    finally:
        consumer.close()
        broker_event_publisher.cache_clear()
