"""Compute 384-d dense embeddings on CUDA GPU for all products in catalog.sqlite."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog"))
    parser.add_argument("--output", type=Path, default=Path(".local/dense_embeddings.f32.npy"))
    parser.add_argument(
        "--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is not available in this Python environment.")

    db_path = (args.catalog / "catalog.sqlite").resolve()
    conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(
        f"[GPU-EMBED] Loaded {total:,} products from {db_path}; GPU: {torch.cuda.get_device_name(0)}"
    )

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModel.from_pretrained(args.model, dtype=torch.float16).cuda().eval()
    dim = model.config.hidden_size

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out = np.lib.format.open_memmap(
        str(args.output), mode="w+", dtype=np.float32, shape=(total, dim)
    )

    started = time.perf_counter()
    cursor = conn.execute("SELECT payload FROM products ORDER BY rowid")
    batch: list[str] = []
    offset = 0

    def flush(texts: list[str]) -> None:
        nonlocal offset
        inp = tokenizer(
            texts, padding=True, truncation=True, max_length=128, return_tensors="pt"
        ).to("cuda")
        with torch.no_grad():
            hidden = model(**inp).last_hidden_state
            mask = inp["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
            emb = (hidden.float() * mask).sum(1) / torch.clamp(mask.sum(1), min=1e-9)
            emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        arr = emb.cpu().numpy()
        out[offset : offset + len(texts)] = arr
        offset += len(texts)

    for (payload_json,) in cursor:
        batch.append(json.loads(payload_json)["search_text"])
        if len(batch) >= args.batch_size:
            flush(batch)
            batch.clear()
            if offset % (args.batch_size * 20) == 0:
                elapsed = time.perf_counter() - started
                rate = offset / max(elapsed, 0.001)
                print(
                    f"[GPU-EMBED] {offset:,}/{total:,} ({offset / total:.1%}) | {rate:,.0f} docs/s",
                    flush=True,
                )

    if batch:
        flush(batch)
        batch.clear()

    out.flush()
    conn.close()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    print(f"[GPU-EMBED] Done {offset:,} embeddings in {elapsed:.2f}s -> {args.output}")


if __name__ == "__main__":
    main()
