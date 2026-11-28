"""Import the team backend and bind its public tools for prod mode."""

from dataclasses import MISSING, fields
from importlib import import_module

from smart_search.tools import Services


def load_backend(module_name: str) -> Services:
    backend = import_module(module_name)
    functions = {field.name: getattr(backend, field.name, None) for field in fields(Services)}
    missing = [
        field.name
        for field in fields(Services)
        if not callable(functions[field.name])
        and (field.default is MISSING or functions[field.name] is not None)
    ]
    if missing:
        raise ValueError(
            f"Backend {module_name} is missing callable services: {', '.join(missing)}"
        )
    return Services(**functions)
