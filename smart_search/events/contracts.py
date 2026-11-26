"""JSON message schema for the search analytics topic."""

from typing import Literal

from smart_search.contracts import Contract, NonEmptyText, SearchEvent


class SearchEventEnvelope(Contract):
    event_id: NonEmptyText
    event_type: Literal["search.completed"] = "search.completed"
    schema_version: Literal[1] = 1
    payload: SearchEvent
