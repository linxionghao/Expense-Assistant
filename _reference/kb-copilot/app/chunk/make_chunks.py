from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
DOCS_PATH = ROOT / "data" / "processed" / "docs.jsonl"
OUT_PATH = ROOT / "data" / "processed" / "chunks.jsonl"

CHUNK_TOKENS = 450
OVERLAP_TOKENS = 80

# --- token utils (tiktoken preferred, fallback to word-based) ---
try:
    import tiktoken  # type: ignore

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

    def split_by_tokens(text: str, chunk_tokens: int, overlap_tokens: int) -> List[str]:
        toks = _ENC.encode(text)
        if not toks:
            return []
        step = max(1, chunk_tokens - overlap_tokens)
        out = []
        for start in range(0, len(toks), step):
            end = min(start + chunk_tokens, len(toks))
            out.append(_ENC.decode(toks[start:end]).strip())
            if end >= len(toks):
                break
        return [c for c in out if c]

except Exception:
    def count_tokens(text: str) -> int:
        # rough approximation: 1 token ~ 0.75 words
        return int(len(text.split()) / 0.75) if text.strip() else 0

    def split_by_tokens(text: str, chunk_tokens: int, overlap_tokens: int) -> List[str]:
        # fallback: split by words
        words = text.split()
        if not words:
            return []
        # tokens->words approximate
        chunk_words = max(50, int(chunk_tokens * 0.75))
        overlap_words = max(0, int(overlap_tokens * 0.75))
        step = max(1, chunk_words - overlap_words)

        out = []
        for start in range(0, len(words), step):
            end = min(start + chunk_words, len(words))
            out.append(" ".join(words[start:end]).strip())
            if end >= len(words):
                break
        return [c for c in out if c]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def has_headings(text: str) -> bool:
    # We inserted headings as "# ", "## ", "### "
    return "\n# " in ("\n" + text) or "\n## " in ("\n" + text) or "\n### " in ("\n" + text)


def split_into_sections(text: str) -> List[Tuple[str, str]]:
    """
    Split a markdown-ish text into sections by headings.
    Returns list of (section_title, section_text).
    """
    lines = text.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    cur_title = "Introduction"
    cur_buf: List[str] = []

    def flush():
        nonlocal cur_title, cur_buf
        body = "\n".join(cur_buf).strip()
        if body:
            sections.append((cur_title, body))
        cur_buf = []

    for ln in lines:
        if ln.startswith("# "):
            flush()
            cur_title = ln[2:].strip() or "Untitled"
        elif ln.startswith("## "):
            flush()
            cur_title = ln[3:].strip() or "Untitled"
        elif ln.startswith("### "):
            flush()
            cur_title = ln[4:].strip() or "Untitled"
        else:
            cur_buf.append(ln)

    flush()
    return sections


def make_chunks_for_doc(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    doc_id = doc["doc_id"]
    title = doc.get("title", "")
    source = doc.get("source", "")
    doc_type = doc.get("doc_type", "")
    roles = doc.get("access_roles", ["public"])
    text = (doc.get("text") or "").strip()

    chunks: List[Dict[str, Any]] = []
    if not text:
        return chunks

    strategy = "heading" if has_headings(text) else "baseline"

    if strategy == "heading":
        sections = split_into_sections(text)
        for s_idx, (s_title, s_body) in enumerate(sections):
            for c_idx, piece in enumerate(split_by_tokens(s_body, CHUNK_TOKENS, OVERLAP_TOKENS)):
                chunk_id = f"{doc_id}::s{s_idx}::c{c_idx}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "doc_title": title,
                        "source": source,
                        "doc_type": doc_type,
                        "access_roles": roles,
                        "chunk_strategy": "heading",
                        "section_title": s_title,
                        "section_index": s_idx,
                        "chunk_index": c_idx,
                        "text": piece,
                        "n_tokens": count_tokens(piece),
                    }
                )
    else:
        for c_idx, piece in enumerate(split_by_tokens(text, CHUNK_TOKENS, OVERLAP_TOKENS)):
            chunk_id = f"{doc_id}::c{c_idx}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "doc_title": title,
                    "source": source,
                    "doc_type": doc_type,
                    "access_roles": roles,
                    "chunk_strategy": "baseline",
                    "section_title": None,
                    "section_index": None,
                    "chunk_index": c_idx,
                    "text": piece,
                    "n_tokens": count_tokens(piece),
                }
            )

    return chunks


def main() -> None:
    docs = read_jsonl(DOCS_PATH)

    # --- sanity check: doc lengths ---
    print("Doc text length sanity check:")
    for d in docs:
        t = (d.get("text") or "")
        print(f"- {d['doc_id']:<40} type={d.get('doc_type'):<4} chars={len(t):>8}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        OUT_PATH.unlink()

    total_chunks = 0
    total_tokens = 0

    with OUT_PATH.open("a", encoding="utf-8") as out:
        for d in docs:
            chs = make_chunks_for_doc(d)
            for c in chs:
                out.write(json.dumps(c, ensure_ascii=False) + "\n")
                total_chunks += 1
                total_tokens += int(c.get("n_tokens") or 0)

    avg_tokens = (total_tokens / total_chunks) if total_chunks else 0
    print(f"\nWrote chunks: {OUT_PATH}")
    print(f"chunks={total_chunks}, avg_tokens≈{avg_tokens:.1f}")

    # preview 1 chunk
    with OUT_PATH.open("r", encoding="utf-8") as f:
        first = json.loads(next(f))
    print("\n--- First chunk preview ---")
    print(f"chunk_id: {first['chunk_id']}")
    print(f"doc_id: {first['doc_id']}")
    print(f"strategy: {first['chunk_strategy']}")
    print(f"section_title: {first.get('section_title')}")
    print(f"roles: {first.get('access_roles')}")
    print(first["text"][:500].replace("\n", "\\n") + "...")


if __name__ == "__main__":
    main()
