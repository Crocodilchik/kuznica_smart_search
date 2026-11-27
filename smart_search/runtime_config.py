"""Shared runtime mode for the UI, API and event storage."""

from os import getenv
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

AppMode = Literal["test", "prod"]


class RuntimeConfigurationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def get_mode() -> AppMode:
    mode = getenv("SMART_SEARCH_MODE", "test").strip().lower()
    if mode == "test":
        return "test"
    if mode == "prod":
        return "prod"
    raise RuntimeConfigurationError("invalid_mode", "SMART_SEARCH_MODE must be test or prod.")
