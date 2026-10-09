from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import re

import chromadb
import numpy as np
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[2]
CHUNKS_PATH = ROOT / "data" / "processed" / "chunks.jsonl"
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION_NAME = "enterprise_kb_copilot"

BM25_DIR = ROOT / "data" / "bm25"
BM25_CORPUS_PATH = BM25_DIR / "bm25_corpus.jsonl"

_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)

def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())

EMBED_MODEL = "intfloat/e5-small-v2"  # 384-dim, MIT
BATCH_SIZE = 64

ROLE_LEVEL = {
    "public": 0,
    "engineering": 10,
    "security": 20,
    "admin": 30,
}


def role_list_to_access_level(roles: List[str]) -> int:
    # Lowest required level among allowed roles (simple + extensible)
    # For now everything is "public" anyway.
    levels = [ROLE_LEVEL.get(r, 999) for r in roles] if roles else [0]
    return int(min(levels))


def read_chunks() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with CHUNKS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def embed_passages(model: SentenceTransformer, texts: List[str]) -> List[List[float]]:
    # E5 retrieval best-practice: prefix passages with "passage: "
    # and normalize embeddings for cosine similarity.
    prefixed = ["passage: " + t for t in texts]
    emb = model.encode(
        prefixed,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return emb.astype(np.float32).tolist()


def embed_query(model: SentenceTransformer, text: str) -> List[float]:
    emb = model.encode(
        ["query: " + text],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return emb[0].astype(np.float32).tolist()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="Delete and recreate the collection")
    ap.add_argument("--smoke", action="store_true", help="Run a quick retrieval smoke test after indexing")
    args = ap.parse_args()

    chunks = read_chunks()
    print(f"Loaded chunks: {len(chunks)} from {CHUNKS_PATH}")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if args.reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"Deleted existing collection: {COLLECTION_NAME}")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    model = SentenceTransformer(EMBED_MODEL)
    print(f"Embedding model: {EMBED_MODEL}")

    ids: List[str] = []
    docs: List[str] = []
    metas: List[Dict[str, Any]] = []

    for c in chunks:
        ids.append(c["chunk_id"])
        docs.append(c["text"])
        metas.append(
            {
                "doc_id": c["doc_id"],
                "doc_title": c.get("doc_title", ""),
                "source": c.get("source", ""),
                "doc_type": c.get("doc_type", ""),
                "chunk_strategy": c.get("chunk_strategy", ""),
                "section_title": c.get("section_title", "") or "",
                "access_level": role_list_to_access_level(c.get("access_roles", ["public"])),
            }
        )

    # -----------------------
    # Build BM25 corpus file
    # -----------------------
    BM25_DIR.mkdir(parents=True, exist_ok=True)
    with BM25_CORPUS_PATH.open("w", encoding="utf-8") as f:
        for cid, doc, md in zip(ids, docs, metas):
            row = {
                "chunk_id": cid,
                "access_level": int(md.get("access_level", 0)),
                "tokens": tokenize(doc),
                "doc_title": md.get("doc_title", ""),
                "source": md.get("source", ""),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"✅ BM25 corpus written: {BM25_CORPUS_PATH} (chunks={len(ids)})")
    if ids:
        print(f"BM25 preview: {ids[0]} tokens={len(tokenize(docs[0]))}")


    # Batch upserts
    for start in tqdm(range(0, len(ids), BATCH_SIZE), desc="Indexing"):
        end = min(start + BATCH_SIZE, len(ids))
        batch_ids = ids[start:end]
        batch_docs = docs[start:end]
        batch_metas = metas[start:end]
        batch_embs = embed_passages(model, batch_docs)

        collection.upsert(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=batch_metas,
            embeddings=batch_embs,
        )

    count = collection.count()
    print(f"✅ Indexed into Chroma: collection='{COLLECTION_NAME}', count={count}, path={CHROMA_DIR}")

    if args.smoke:
        q = "What are the pillars of the AWS Well-Architected Framework?"
        q_emb = embed_query(model, q)
        res = collection.query(
            query_embeddings=[q_emb],
            n_results=5,
            # We'll use access_level filtering later in the API:
            # where={"access_level": {"$lte": user_level}}
        )
        print("\n--- Smoke test query ---")
        print(q)
        for i in range(len(res["ids"][0])):
            cid = res["ids"][0][i]
            md = res["metadatas"][0][i]
            snippet = (res["documents"][0][i][:180] + "...").replace("\n", " ")
            print(f"{i+1}. {cid} | {md.get('doc_title')} | {md.get('source')}")
            print(f"   {snippet}")


if __name__ == "__main__":
    main()
