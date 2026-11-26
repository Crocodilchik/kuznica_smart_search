"""Synchronous, bounded delivery to a Kafka-compatible broker."""

from functools import lru_cache
from os import getenv
from threading import Event
from time import monotonic

from confluent_kafka import KafkaError, KafkaException, Message, Producer

from smart_search.contracts import SearchEvent
from smart_search.events import EventDeliveryError
from smart_search.events.contracts import SearchEventEnvelope

DEFAULT_TOPIC = "smart-search.search-events.v1"


class KafkaEventPublisher:
    def __init__(self, producer: Producer, *, topic: str, timeout_ms: int):
        self.producer = producer
        self.topic = topic
        self.timeout_ms = timeout_ms

    def publish(self, event: SearchEvent) -> None:
        envelope = SearchEventEnvelope(
            event_id=f"search.completed:{event.request_id}", payload=event
        )
        acknowledged = Event()
        delivery_error: KafkaError | None = None

        def on_delivery(error: KafkaError | None, _message: Message) -> None:
            nonlocal delivery_error
            delivery_error = error
            acknowledged.set()

        deadline = monotonic() + self.timeout_ms / 1000
        try:
            self.producer.produce(
                self.topic,
                key=event.request_id.encode("utf-8"),
                value=envelope.model_dump_json().encode("utf-8"),
                on_delivery=on_delivery,
            )
            while not acknowledged.is_set():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise EventDeliveryError("broker_ack_timeout")
                self.producer.poll(min(remaining, 0.05))
        except (BufferError, KafkaException) as error:
            raise EventDeliveryError("broker_publish_failed") from error
        if delivery_error is not None:
            raise EventDeliveryError(f"broker_delivery_failed ({delivery_error.name()})")


@lru_cache(maxsize=1)
def broker_event_publisher() -> KafkaEventPublisher:
    bootstrap_servers = getenv("SMART_SEARCH_KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap_servers:
        raise EventDeliveryError("broker_not_configured")
    topic = getenv("SMART_SEARCH_KAFKA_TOPIC", DEFAULT_TOPIC).strip()
    if not topic:
        raise EventDeliveryError("invalid_broker_topic")
    try:
        timeout_ms = int(getenv("SMART_SEARCH_KAFKA_DELIVERY_TIMEOUT_MS", "1000"))
    except ValueError as error:
        raise EventDeliveryError("invalid_broker_delivery_timeout") from error
    if timeout_ms <= 0:
        raise EventDeliveryError("invalid_broker_delivery_timeout")

    protocol = getenv("SMART_SEARCH_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip().upper()
    if protocol not in {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}:
        raise EventDeliveryError("invalid_broker_security_protocol")
    config: dict[str, str | int | bool] = {
        "bootstrap.servers": bootstrap_servers,
        "client.id": "smart-search-supervisor",
        "enable.idempotence": True,
        "acks": "all",
        "allow.auto.create.topics": False,
        "linger.ms": 0,
        "delivery.timeout.ms": timeout_ms,
        "security.protocol": protocol,
    }
    if protocol.startswith("SASL_"):
        username = getenv("SMART_SEARCH_KAFKA_SASL_USERNAME")
        password = getenv("SMART_SEARCH_KAFKA_SASL_PASSWORD")
        if not username or not password:
            raise EventDeliveryError("broker_sasl_credentials_required")
        config.update(
            {
                "sasl.mechanism": getenv("SMART_SEARCH_KAFKA_SASL_MECHANISM", "SCRAM-SHA-256"),
                "sasl.username": username,
                "sasl.password": password,
            }
        )
    if ca_location := getenv("SMART_SEARCH_KAFKA_SSL_CA_LOCATION"):
        config["ssl.ca.location"] = ca_location

    try:
        producer = Producer(config)
    except (KafkaException, ValueError, OverflowError) as error:
        raise EventDeliveryError("broker_client_init_failed") from error
    return KafkaEventPublisher(producer, topic=topic, timeout_ms=timeout_ms)
