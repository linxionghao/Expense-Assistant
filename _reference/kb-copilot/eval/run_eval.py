from __future__ import annotations

import json
import os
import sys
import importlib
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# If this file lives in <repo_root>/eval/run_eval.py, this points at repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

# Eval assets live next to this script (eval/questions.jsonl, eval/results_latest.json)
EVAL_DIR = Path(__file__).resolve().parent
QUERIES_PATH = EVAL_DIR / "questions.jsonl"

TOP_K = 5  # metrics@K


def load_queries() -> List[Dict[str, Any]]:
    queries: List[Dict[str, Any]] = []
    with QUERIES_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    print(f"Loaded {len(queries)} eval queries from {QUERIES_PATH}")
    return queries


def _dedupe_keep_order(ids: List[str]) -> List[str]:
    """Deduplicate while preserving first-occurrence order."""
    seen = set()
    out: List[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def compute_metrics(retrieved_doc_ids: List[str], expected_doc_ids: List[str]) -> Dict[str, float]:
    """
    Doc-level metrics.

    IMPORTANT: If the retriever returns multiple chunks from the same doc in top-K, we must not
    let the same doc contribute multiple times (otherwise "nDCG" can exceed 1.0).
    """
    expected_set = set(expected_doc_ids)
    if not expected_set:
        return {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}

    # Deduplicate retrieved docs (chunks -> docs can repeat) and cap at K known to this eval.
    retrieved = _dedupe_keep_order(retrieved_doc_ids)[:TOP_K]

    # Recall@K (with |expected| in denom; if expected size is 1, this is the same as hit-rate@K)
    hits = len(set(retrieved) & expected_set)
    recall = hits / len(expected_set)

    # MRR (first relevant doc rank among the deduped list)
    mrr = 0.0
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in expected_set:
            mrr = 1.0 / rank
            break

    # nDCG@K (binary relevance)
    dcg = 0.0
    for rank, doc_id in enumerate(retrieved, start=1):
        rel = 1.0 if doc_id in expected_set else 0.0
        dcg += rel / np.log2(rank + 1)

    # Ideal DCG for binary relevance: put as many relevant docs as possible at the top,
    # up to K or the number of relevant docs.
    idcg = 0.0
    for rank in range(1, min(TOP_K, len(expected_set)) + 1):
        idcg += 1.0 / np.log2(rank + 1)

    ndcg = (dcg / idcg) if idcg > 0 else 0.0

    # nDCG should be in [0, 1] for this setup; clip tiny fp noise just in case.
    ndcg = float(max(0.0, min(1.0, ndcg)))

    return {"recall": float(recall), "mrr": float(mrr), "ndcg": ndcg}


@contextmanager
def temporary_environ(overrides: Dict[str, str]):
    """
    Temporarily set environment variables for a single ablation run,
    then restore the previous state.
    """
    old_values: Dict[str, str] = {}
    new_keys: List[str] = []

    for k, v in overrides.items():
        if k in os.environ:
            old_values[k] = os.environ[k]
        else:
            new_keys.append(k)
        os.environ[k] = v

    try:
        yield
    finally:
        # Remove keys that didn't exist before
        for k in new_keys:
            os.environ.pop(k, None)
        # Restore old values
        for k, v in old_values.items():
            os.environ[k] = v


def _build_retriever():
    """
    retriever.py reads feature flags (BM25_ENABLE/RRF_ENABLE/RERANK_ENABLE/...) at import time.
    So for each config, we reload the module after applying env overrides.
    """
    mod = importlib.import_module("app.retrieve.retriever")
    mod = importlib.reload(mod)
    return mod.Retriever()


def run_eval_config(config_name: str, env_overrides: Dict[str, str]) -> Dict[str, Any]:
    queries = load_queries()

    results: List[Dict[str, Any]] = []
    metrics_by_tag: defaultdict[str, List[float]] = defaultdict(list)

    with temporary_environ(env_overrides):
        retriever = _build_retriever()

        for q in queries:
            retrieved_chunks = retriever.retrieve(
                q["question"],
                top_k=TOP_K,
                user_role="engineering" if "k8s" in q.get("tags", []) else "public",
            )

            # Convert chunk_ids like "doc_id::chunk_idx" -> doc_id (doc-level evaluation)
            retrieved_doc_ids = [c.chunk_id.split("::", 1)[0] for c in retrieved_chunks]

            mets = compute_metrics(retrieved_doc_ids, q["expected_doc_ids"])
            results.append({"id": q["id"], "question": q["question"], **mets})

            for tag in q.get("tags", []):
                metrics_by_tag[tag].append(mets["ndcg"])

    overall = {
        "ndcg": float(np.mean([r["ndcg"] for r in results])) if results else 0.0,
        "recall": float(np.mean([r["recall"] for r in results])) if results else 0.0,
        "mrr": float(np.mean([r["mrr"] for r in results])) if results else 0.0,
    }
    tag_avgs = {tag: float(np.mean(scores)) for tag, scores in metrics_by_tag.items() if scores}

    print(f"\n=== {config_name} ===")
    print(
        f"Overall nDCG@{TOP_K}: {overall['ndcg']:.4f} | "
        f"Recall@{TOP_K}: {overall['recall']:.4f} | MRR: {overall['mrr']:.4f}"
    )
    for tag in sorted(tag_avgs.keys()):
        print(f"  [{tag}] nDCG@{TOP_K}: {tag_avgs[tag]:.4f}")

    # return {"config": config_name, "overall": overall, "per_tag": tag_avgs, "details": results}
    return {"config": config_name, "overall": overall, "per_tag": tag_avgs}


def main():

    configs = [
        ("vector_only", {"VECTOR_ENABLE":"1","BM25_ENABLE":"0","RRF_ENABLE":"0","RERANK_ENABLE":"0"}),
        # ("bm25_only",   {"VECTOR_ENABLE":"0","BM25_ENABLE":"1","RRF_ENABLE":"0","RERANK_ENABLE":"0"}),
        # ("vector_plus_bm25", {"VECTOR_ENABLE":"1","BM25_ENABLE":"1","RRF_ENABLE":"0","RERANK_ENABLE":"0"}),
        # ("hybrid_rrf",  {"VECTOR_ENABLE":"1","BM25_ENABLE":"1","RRF_ENABLE":"1","RERANK_ENABLE":"0"}),
        # ("hybrid_rrf_rerank", {"VECTOR_ENABLE":"1","BM25_ENABLE":"1","RRF_ENABLE":"1","RERANK_ENABLE":"1"}),
        # ("vector_rerank", {"VECTOR_ENABLE":"1","BM25_ENABLE":"0","RRF_ENABLE":"0","RERANK_ENABLE":"1"}),
    ]


    all_results: List[Dict[str, Any]] = []
    for name, overrides in configs:
        all_results.append(run_eval_config(name, overrides))

    print("\n\n### Ablation Results (nDCG@5)\n")
    print("| Config                  | Overall | aws   | k8s   |")
    print("|-------------------------|---------|-------|-------|")
    for res in all_results:
        overall = res["overall"]["ndcg"]
        aws = res["per_tag"].get("aws", 0.0)
        k8s = res["per_tag"].get("k8s", 0.0)
        print(f"| {res['config']:<23} | {overall:.4f} | {aws:.4f} | {k8s:.4f} |")

    out_path = EVAL_DIR / "results_latest.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
