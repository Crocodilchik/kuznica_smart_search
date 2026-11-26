"""Load and validate the Streamlit display configuration."""

from pathlib import Path
from typing import Self, get_args

from pydantic import model_validator

from smart_search.contracts import Contract, NonEmptyText, QueryText, RelationType, SearchStatus

UI_CONFIG_PATH = Path(__file__).with_name("ui_config.json")


class UIConfig(Contract):
    examples: list[QueryText]
    relation_labels: dict[RelationType, NonEmptyText]
    status_labels: dict[SearchStatus, NonEmptyText]

    @model_validator(mode="after")
    def require_labels_for_every_code(self) -> Self:
        missing_relations = set(get_args(RelationType)) - self.relation_labels.keys()
        missing_statuses = set(get_args(SearchStatus)) - self.status_labels.keys()
        if missing_relations or missing_statuses:
            missing = ", ".join(sorted(missing_relations | missing_statuses))
            raise ValueError(f"Missing labels for: {missing}")
        return self


def load_ui_config() -> UIConfig:
    return UIConfig.model_validate_json(UI_CONFIG_PATH.read_text(encoding="utf-8"))
