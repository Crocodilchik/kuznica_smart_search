"""Integration re-exports. Merge these search-side models with developer 1's shared contracts."""

from softline_search.contracts import (
    HardFilters,
    Preferences,
    Product,
    QuerySpec,
    SearchHit,
    SearchParams,
    SearchResult,
)

__all__ = [
    "HardFilters",
    "Preferences",
    "Product",
    "QuerySpec",
    "SearchHit",
    "SearchParams",
    "SearchResult",
]
