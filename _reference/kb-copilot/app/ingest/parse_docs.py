from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]  # repo root
RAW_MANIFEST = ROOT / "data" / "raw" / "manifest.jsonl"
OUT_PATH = ROOT / "data" / "processed" / "docs.jsonl"


def iter_manifest() -> List[Dict[str, Any]]:
    rows = []
    with RAW_MANIFEST.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def clean_ws(text: str) -> str:
    # normalize whitespace but preserve paragraph breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts: List[str] = []
    for i, page in enumerate(reader.pages, start=1):
        t = page.extract_text() or ""
        t = clean_ws(t)
        if t:
            parts.append(f"=== Page {i} ===\n{t}")
    return clean_ws("\n\n".join(parts))


def extract_html_text(html_path: Path) -> str:
    html_bytes = html_path.read_bytes()
    soup = BeautifulSoup(html_bytes, "html.parser")

    # Remove noisy elements
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    container = soup.find("main") or soup.find("article") or soup.body
    if container is None:
        return ""

    lines: List[str] = []
    for el in container.find_all(["h1", "h2", "h3", "p", "li", "pre"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue

        if el.name in ("h1", "h2", "h3"):
            prefix = {"h1": "# ", "h2": "## ", "h3": "### "}[el.name]
            lines.append(prefix + text)
        elif el.name == "pre":
            # keep code blocks readable
            lines.append("```")
            lines.append(text)
            lines.append("```")
        else:
            lines.append(text)

    return clean_ws("\n".join(lines))


def main() -> None:
    rows = iter_manifest()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        OUT_PATH.unlink()

    written = 0
    for r in rows:
        rel_path = r["path"]
        doc_path = ROOT / rel_path

        if r["doc_type"] == "pdf":
            text = extract_pdf_text(doc_path)
        elif r["doc_type"] == "html":
            text = extract_html_text(doc_path)
        else:
            text = ""

        record = {
            "doc_id": r["doc_id"],
            "title": r["title"],
            "source": r["source"],
            "doc_type": r["doc_type"],
            "access_roles": r.get("access_roles", ["public"]),
            "text": text,
        }

        with OUT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        written += 1

    # quick stats
    total_chars = 0
    with OUT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            total_chars += len(obj.get("text", ""))

    print(f"Parsed docs written: {OUT_PATH} (docs={written}, total_chars={total_chars})")

    # show a small preview of the first doc
    with OUT_PATH.open("r", encoding="utf-8") as f:
        first = json.loads(next(f))
    preview = (first.get("text", "")[:600] + "…") if first.get("text") else "(empty)"
    print("\n--- Preview (first doc) ---")
    print(f"title: {first.get('title')}")
    print(f"source: {first.get('source')}")
    print(preview)


if __name__ == "__main__":
    main()
