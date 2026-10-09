from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.retrieve.query_rewrite import rewrite_query
from app.retrieve.rerank import CrossEncoderReranker

# -----------------------
# Runtime knobs (env vars)
# -----------------------
# New: allow disabling semantic/vector retrieval entirely.
VECTOR_ENABLE = os.getenv("VECTOR_ENABLE", "1") == "1"

BM25_ENABLE = os.getenv("BM25_ENABLE", "1") == "1"
BM25_CANDIDATES = int(os.getenv("BM25_CANDIDATES", "30"))

RRF_ENABLE = os.getenv("RRF_ENABLE", "1") == "1"
RRF_K = int(os.getenv("RRF_K", "60"))

RERANK_ENABLE = os.getenv("RERANK_ENABLE", "1") == "1"
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "30"))
RERANK_TOP_M = int(os.getenv("RERANK_TOP_M", "50"))

MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "0.2"))
RETRIEVE_CANDIDATES = int(os.getenv("RETRIEVE_CANDIDATES", str(RERANK_CANDIDATES)))

DEBUG_RETRIEVE = os.getenv("DEBUG_RETRIEVE", "1") == "1"

# -----------------------
# Paths / models
# -----------------------
ROOT = Path(__file__).resolve().parents[2]
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION_NAME = "enterprise_kb_copilot"

EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/e5-small-v2")
BM25_CORPUS_PATH = ROOT / "data" / "bm25" / "bm25_corpus.jsonl"

_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


ROLE_LEVEL = {
    "public": 0,
    "engineering": 10,
    "security": 20,
    "admin": 30,
}


def role_to_level(role: Optional[str]) -> int:
    return int(ROLE_LEVEL.get((role or "public").lower(), 0))


@dataclass
class RetrievedChunk:
    chunk_id: str
    title: str
    source: str
    snippet: str
    text: str
    metadata: Dict[str, Any]


class Retriever:
    def __init__(self) -> None:
        # Always keep Chroma available because we may need to fetch BM25 hits by chunk_id.

        print("ENV VECTOR_ENABLE =", os.getenv("VECTOR_ENABLE"))
        print("ENV BM25_ENABLE =", os.getenv("BM25_ENABLE"))
        print("ENV RERANK_ENABLE =", os.getenv("RERANK_ENABLE"))
        print("ENV RRF_ENABLE =", os.getenv("RRF_ENABLE"))

        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Only load embedding model if vector retrieval is enabled.
        self.model: Optional[SentenceTransformer] = None
        if VECTOR_ENABLE:
            self.model = SentenceTransformer(EMBED_MODEL)

        # BM25 structures
        self.bm25: Optional[BM25Okapi] = None
        self.bm25_chunk_ids: List[str] = []
        self.bm25_access_levels: List[int] = []

        if BM25_ENABLE and BM25_CORPUS_PATH.exists():
            tokenized_corpus: List[List[str]] = []
            with BM25_CORPUS_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    self.bm25_chunk_ids.append(row["chunk_id"])
                    self.bm25_access_levels.append(int(row.get("access_level", 0)))
                    tokenized_corpus.append(row.get("tokens") or [])
            self.bm25 = BM25Okapi(tokenized_corpus)
            if DEBUG_RETRIEVE:
                print(f"✅ BM25 loaded: {BM25_CORPUS_PATH} (chunks={len(self.bm25_chunk_ids)})")
        else:
            if DEBUG_RETRIEVE:
                print("ℹ️ BM25 disabled or corpus file missing")

        # Reranker
        self.reranker: Optional[CrossEncoderReranker] = CrossEncoderReranker() if RERANK_ENABLE else None

        if DEBUG_RETRIEVE:
            print(
                f"Retriever init knobs: VECTOR_ENABLE={VECTOR_ENABLE} "
                f"BM25_ENABLE={BM25_ENABLE} RRF_ENABLE={RRF_ENABLE} RERANK_ENABLE={RERANK_ENABLE}"
            )

    # -----------------------
    # BM25
    # -----------------------
    def bm25_retrieve_ids(self, query: str, user_level: int, k: int) -> List[str]:
        if not self.bm25:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scores = self.bm25.get_scores(q_tokens)

        # RBAC filter + top-k selection
        idxs = [
            i
            for i, s in enumerate(scores)
            if self.bm25_access_levels[i] <= user_level and s > 0
        ]
        idxs.sort(key=lambda i: scores[i], reverse=True)

        return [self.bm25_chunk_ids[i] for i in idxs[:k]]

    # -----------------------
    # Embedding
    # -----------------------
    def embed_query(self, q: str) -> List[float]:
        if not self.model:
            raise RuntimeError("VECTOR_ENABLE=0 but embed_query() was called.")
        emb = self.model.encode(
            ["query: " + q],
            normalize_embeddings=True,
        )[0]
        return emb.tolist()

    # -----------------------
    # Retrieval
    # -----------------------
    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        user_role: str = "public",
    ) -> List[RetrievedChunk]:
        user_level = role_to_level(user_role)
        rewritten = rewrite_query(question)

        # BM25 candidate IDs (chunk IDs)
        bm25_ids = self.bm25_retrieve_ids(rewritten, user_level, BM25_CANDIDATES)
        bm25_id_set = set(bm25_ids)

        # Vector retrieval (optional)
        ids: List[str] = []
        docs: List[str] = []
        metas: List[dict] = []
        dists: List[float] = []

        if VECTOR_ENABLE:
            q_emb = self.embed_query(rewritten)
            candidate_k = max(top_k, RETRIEVE_CANDIDATES)

            res = self.collection.query(
                query_embeddings=[q_emb],
                n_results=candidate_k,
                where={"access_level": {"$lte": user_level}},
                include=["documents", "metadatas", "distances"],
            )

            ids = res.get("ids", [[]])[0] or []
            docs = res.get("documents", [[]])[0] or []
            metas = res.get("metadatas", [[]])[0] or []
            dists = res.get("distances", [[]])[0] or []

        # Build candidates from vector results (if any)
        candidates: List[RetrievedChunk] = []
        for cid, doc, md, dist in zip(ids, docs, metas, dists):
            if dist is not None and float(dist) > MAX_DISTANCE:
                continue

            title = (md.get("doc_title") if isinstance(md, dict) else "") or ""
            source = (md.get("source") if isinstance(md, dict) else "") or ""
            snippet = (doc[:300] + "...") if doc and len(doc) > 300 else (doc or "")

            meta = dict(md or {})
            meta["vector_distance"] = float(dist) if dist is not None else None
            meta["from_vector"] = True
            meta["from_bm25"] = cid in bm25_id_set

            candidates.append(
                RetrievedChunk(
                    chunk_id=cid,
                    title=title,
                    source=source,
                    snippet=snippet,
                    text=doc or "",
                    metadata=meta,
                )
            )

        # Add BM25-only chunks (not already in candidates)
        if bm25_ids:
            candidate_id_set = {c.chunk_id for c in candidates}
            missing_bm25_ids = [cid for cid in bm25_ids if cid not in candidate_id_set]

            if missing_bm25_ids:
                fetched = self.collection.get(
                    ids=missing_bm25_ids,
                    include=["documents", "metadatas"],
                )
                f_ids = fetched.get("ids", []) or []
                f_docs = fetched.get("documents", []) or []
                f_metas = fetched.get("metadatas", []) or []

                for cid, doc, md in zip(f_ids, f_docs, f_metas):
                    title = (md.get("doc_title") if isinstance(md, dict) else "") or ""
                    source = (md.get("source") if isinstance(md, dict) else "") or ""
                    snippet = (doc[:300] + "...") if doc and len(doc) > 300 else (doc or "")

                    meta = dict(md or {})
                    meta["vector_distance"] = None
                    meta["from_vector"] = False
                    meta["from_bm25"] = True

                    candidates.append(
                        RetrievedChunk(
                            chunk_id=cid,
                            title=title,
                            source=source,
                            snippet=snippet,
                            text=doc or "",
                            metadata=meta,
                        )
                    )

        # === Near-duplicate filtering (hash normalized text) ===
        seen_hashes = set()
        deduped_candidates: List[RetrievedChunk] = []
        for c in candidates:
            norm_text = re.sub(r"\s+", " ", c.text.lower().strip())
            text_hash = sha256(norm_text.encode("utf-8")).hexdigest()
            if text_hash not in seen_hashes:
                seen_hashes.add(text_hash)
                deduped_candidates.append(c)
        candidates = deduped_candidates

        # === RRF fusion (meaningful only if BOTH sources contributed) ===
        if RRF_ENABLE and bm25_ids and ids:
            vector_ranks = {cid: i + 1 for i, cid in enumerate(ids)}
            bm25_ranks = {cid: i + 1 for i, cid in enumerate(bm25_ids)}

            for c in candidates:
                vr = vector_ranks.get(c.chunk_id, 0)
                br = bm25_ranks.get(c.chunk_id, 0)
                rrf_score = (1.0 / (RRF_K + vr) if vr > 0 else 0.0) + (
                    1.0 / (RRF_K + br) if br > 0 else 0.0
                )
                c.metadata["rrf_score"] = float(rrf_score)

            candidates.sort(key=lambda c: c.metadata.get("rrf_score", 0.0), reverse=True)

        # === Rerank only top M after RRF/merge ===
        rerank_pool = candidates[:RERANK_TOP_M]
        if self.reranker and rerank_pool:
            scores = self.reranker.score(question, [c.text for c in rerank_pool])
            for c, s in zip(rerank_pool, scores):
                c.metadata["rerank_score"] = float(s)

            # Final sort: reranked ones first, then rest by RRF (if present)
            candidates = sorted(
                candidates,
                key=lambda c: c.metadata.get("rerank_score", c.metadata.get("rrf_score", -1e9)),
                reverse=True,
            )

        if DEBUG_RETRIEVE:
            v_only = sum(
                1
                for c in candidates
                if c.metadata.get("from_vector") and not c.metadata.get("from_bm25")
            )
            b_only = sum(
                1
                for c in candidates
                if c.metadata.get("from_bm25") and not c.metadata.get("from_vector")
            )
            both = sum(
                1
                for c in candidates
                if c.metadata.get("from_vector") and c.metadata.get("from_bm25")
            )

            print("\n--- RETRIEVAL DEBUG ---")
            print(f"question={question!r}")
            print(f"rewritten={rewritten!r}")
            print(f"user_role={user_role} level={user_level}")
            print(
                f"vector_ids={len(ids)} bm25_ids={len(bm25_ids)} "
                f"candidates={len(candidates)} (v_only={v_only}, b_only={b_only}, both={both}) "
                f"top_k={top_k}"
            )
            print(
                f"knobs: VECTOR={VECTOR_ENABLE} BM25={BM25_ENABLE} "
                f"RRF={RRF_ENABLE} RERANK={bool(self.reranker)}"
            )

        return candidates[:top_k]
