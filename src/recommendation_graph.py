"""Category rules and persisted product compatibility edges."""

import hashlib
import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from .config import DATA_DIR
from .contracts import EvidenceResult, Product, RecommendationDraft
from .research import research

GRAPH_PATH = DATA_DIR / "recommendation_graph.json"
EDGE_DB_PATH = DATA_DIR / "recommendation_edges.db"


class CategoryEdge(BaseModel):
    source_id: str
    source: str
    target_id: str
    target: str
    query: str
    title_terms: list[str] = Field(min_length=1, max_length=5)
    reason: str


class RecommendationGraph(BaseModel):
    category_edges: list[CategoryEdge]

    @classmethod
    def load(cls, path: Path = GRAPH_PATH):
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def product_fingerprint(product: Product) -> str:
    fields = product.model_dump(
        mode="json", include={"sku", "title", "description", "attributes", "url", "source_date"}
    )
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def check_graph_evidence(
    db: sqlite3.Connection,
    drafts: list[RecommendationDraft],
    *,
    anchors: list[Product],
) -> list[EvidenceResult]:
    db.execute(
        """CREATE TABLE IF NOT EXISTS compatibility_edges (
            anchor_id TEXT NOT NULL, product_id TEXT NOT NULL, anchor_version TEXT NOT NULL,
            product_version TEXT NOT NULL, evidence TEXT NOT NULL,
            PRIMARY KEY (anchor_id, product_id)
        )"""
    )
    anchors_by_id = {anchor.product_id: anchor for anchor in anchors}
    results = []
    for draft in drafts:
        anchor = anchors_by_id.get(draft.anchor_id)
        if anchor is None or draft.type != "cross_sell":
            results.append(research(draft, anchor=anchor))
            continue
        pair = (anchor.product_id, draft.product.product_id)
        versions = (product_fingerprint(anchor), product_fingerprint(draft.product))
        cached = db.execute(
            """SELECT evidence FROM compatibility_edges
               WHERE anchor_id = ? AND product_id = ? AND anchor_version = ? AND product_version = ?""",
            (*pair, *versions),
        ).fetchone()
        if cached:
            result = EvidenceResult.model_validate_json(cached[0])
            result.claim = draft.claim
        else:
            result = research(draft, anchor=anchor)
            if result.status != "unknown" and result.evidence:
                db.execute(
                    """INSERT INTO compatibility_edges VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(anchor_id, product_id) DO UPDATE SET
                       anchor_version = excluded.anchor_version, product_version = excluded.product_version,
                       evidence = excluded.evidence""",
                    (*pair, *versions, result.model_dump_json()),
                )
        results.append(result)
    return results
