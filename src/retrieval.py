"""Offer-level cosine search and a replaceable reranking stage."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import numpy as np
from pydantic import BaseModel, Field
from scipy import sparse

from .catalog import open_catalog


class Filters(BaseModel):
    vendor: str | None = None
    category_id: str | None = None
    only_available: bool = False
    min_price: float | None = Field(None, ge=0)
    max_price: float | None = Field(None, ge=0)
    params: dict[str, str] = Field(default_factory=dict)

    def matches(self, offer):
        price = offer["price"]
        params = {key.casefold(): values for key, values in offer["params"].items()}
        return (
            (self.vendor is None or self.vendor.casefold() == offer["vendor"].casefold())
            and (self.category_id is None or self.category_id == offer["category_id"])
            and (not self.only_available or offer["available"])
            and (self.min_price is None or price is not None and price >= self.min_price)
            and (self.max_price is None or price is not None and price <= self.max_price)
            and all(
                value.casefold() in {item.casefold() for item in params.get(key.casefold(), [])}
                for key, value in self.params.items()
            )
        )


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(12, ge=1, le=100)
    threshold: float | None = Field(None, ge=-1, le=1)
    filters: Filters = Field(default_factory=Filters)


@dataclass
class Candidate:
    product: dict
    cosine: float
    score: float


class SearchEngine(Protocol):
    def search_products(self, args: SearchArgs) -> list[Candidate]: ...


class E5:
    def __init__(self, model, revision=None):
        from sentence_transformers import SentenceTransformer

        device = os.getenv("SOFTLINE_DEVICE")
        self.model = SentenceTransformer(model, revision=revision, device=device)
        self.metadata = {
            "model": model,
            "revision": getattr(self.model[0].auto_model.config, "_commit_hash", None) or revision,
        }
        self.model.max_seq_length = int(os.getenv("SOFTLINE_MAX_TOKENS", "192"))
        self.metadata["max_tokens"] = self.model.max_seq_length

    def encode(self, texts, kind):
        vectors = self.model.encode(
            [f"{kind}: {item}" for item in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.astype(np.float32, copy=False)


class CharacterEncoder:
    def __init__(self):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.metadata = {"model": "character-hash", "revision": "1"}
        self.vectorizer = HashingVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 4),
            n_features=2**18,
            alternate_sign=False,
            binary=True,
            norm="l2",
            dtype=np.float32,
        )

    def encode(self, texts, kind):
        return self.vectorizer.transform(texts)


def representation(product):
    attributes = "; ".join(
        f"{key}: {', '.join(values)}" for key, values in product["params"].items()
    )
    return " | ".join(
        (
            product["name"],
            product["vendor"],
            product["vendor_code"],
            " > ".join(product["category_path"]),
            attributes,
            product["description"],
        )
    )


def compact_representation(product):
    return " | ".join(
        (
            product["name"],
            product["vendor"],
            product["vendor_code"],
            " > ".join(product["category_path"][-2:]),
        )
    )


def build_index(db_path, index_path, encoder, batch_size=128, render=representation):
    from contextlib import closing

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ids = []
    vectors = []
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(open_catalog(db_path)) as db:
        generation = db.execute("SELECT generation FROM metadata").fetchone()[0]
        cursor = db.execute("SELECT id, product FROM offers ORDER BY id")
        while rows := cursor.fetchmany(batch_size):
            ids.extend(row[0] for row in rows)
            vectors.append(encoder.encode([render(json.loads(row[1])) for row in rows], "passage"))
            if len(vectors) % 50 == 0:
                print(f"Indexed {len(ids)} offers", flush=True)
    metadata = {"generation": generation, "representation": render.__qualname__, **encoder.metadata}
    with TemporaryDirectory(dir=index_path.parent) as temporary:
        path = Path(temporary) / "index.npz"
        if sparse.issparse(vectors[0]):
            matrix = sparse.vstack(vectors, format="csr")
            np.savez(
                path,
                ids=ids,
                data=matrix.data,
                indices=matrix.indices,
                indptr=matrix.indptr,
                shape=matrix.shape,
                metadata=json.dumps(metadata),
            )
        else:
            np.savez(path, ids=ids, vectors=np.concatenate(vectors), metadata=json.dumps(metadata))
        path.replace(index_path)
    return len(ids)


class CosineSearch:
    def __init__(self, db_path, index_path, encoder=None, rerank=None, max_candidates=100):
        with np.load(index_path, allow_pickle=False) as index:
            self.ids = index["ids"]
            self.vectors = (
                index["vectors"]
                if "vectors" in index
                else sparse.csr_matrix(
                    (index["data"], index["indices"], index["indptr"]),
                    shape=tuple(index["shape"]),
                )
            )
            self.metadata = json.loads(index["metadata"].item())
        self.db_path = db_path
        self.encoder = encoder
        if self.encoder is None:
            self.encoder = (
                CharacterEncoder()
                if self.metadata["model"] == "character-hash"
                else E5(self.metadata["model"], self.metadata["revision"])
            )
        if self.encoder.metadata != {key: self.metadata[key] for key in self.encoder.metadata}:
            raise ValueError("Encoder configuration does not match the index")
        self.rerank = rerank
        self.max_candidates = max_candidates

    def search_products(self, args: SearchArgs) -> list[Candidate]:
        from contextlib import closing

        query = self.encoder.encode([args.query], "query")[0]
        scores = (
            (self.vectors @ query.T).toarray().ravel()
            if sparse.issparse(query)
            else self.vectors @ query
        )
        threshold = args.threshold
        if threshold is None and self.metadata["model"] == "character-hash":
            threshold = np.finfo(np.float32).eps
        limit = self.max_candidates if self.rerank else min(self.max_candidates, args.top_k)
        results = []
        with closing(open_catalog(self.db_path)) as db:
            if (
                db.execute("SELECT generation FROM metadata").fetchone()[0]
                != self.metadata["generation"]
            ):
                raise ValueError("Catalog has changed. Rebuild the vector index.")
            for position in np.argsort(-scores, kind="stable"):
                score = float(scores[position])
                if threshold is not None and score < threshold:
                    break
                row = db.execute(
                    "SELECT product FROM offers WHERE id = ?", (str(self.ids[position]),)
                ).fetchone()
                product = json.loads(row[0])
                if args.filters.matches(product) and product["url"]:
                    candidate = Candidate(product, score, score)
                    if self.rerank:
                        candidate.score = self.rerank(
                            args.query, query, self.vectors[position], candidate
                        )
                    results.append(candidate)
                    if len(results) == limit:
                        break
        return sorted(results, key=lambda result: -result.score)[: args.top_k]
