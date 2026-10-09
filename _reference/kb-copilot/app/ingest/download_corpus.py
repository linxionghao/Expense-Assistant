from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import requests
from bs4 import BeautifulSoup


AWS_WA_PDF = "https://docs.aws.amazon.com/pdfs/wellarchitected/latest/framework/wellarchitected-framework.pdf"

K8S_URLS = [
    "https://kubernetes.io/docs/concepts/overview/",
    "https://kubernetes.io/docs/concepts/overview/components/",
    "https://kubernetes.io/docs/concepts/architecture/",
    "https://kubernetes.io/docs/concepts/overview/working-with-objects/",
    "https://kubernetes.io/docs/concepts/overview/working-with-objects/names/",
    "https://kubernetes.io/docs/concepts/overview/kubernetes-api/",
    "https://kubernetes.io/docs/concepts/services-networking/service/",
    "https://kubernetes.io/docs/concepts/workloads/pods/",
]

ROOT = Path(__file__).resolve().parents[2]  # repo root
RAW_DIR = ROOT / "data" / "raw"
AWS_DIR = RAW_DIR / "aws"
K8S_DIR = RAW_DIR / "k8s"
MANIFEST_PATH = RAW_DIR / "manifest.jsonl"


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "doc"


def fetch(url: str) -> bytes:
    headers = {
        "User-Agent": "enterprise-kb-copilot/0.1 (+https://kubernetes.io/ and https://docs.aws.amazon.com/)"
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.content


def save_manifest_row(row: Dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    AWS_DIR.mkdir(parents=True, exist_ok=True)
    K8S_DIR.mkdir(parents=True, exist_ok=True)

    # reset manifest each run (simple + deterministic)
    if MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()

    # 1) AWS PDF
    pdf_bytes = fetch(AWS_WA_PDF)
    pdf_path = AWS_DIR / "wellarchitected-framework.pdf"
    pdf_path.write_bytes(pdf_bytes)
    save_manifest_row(
        {
            "doc_id": "aws-well-architected-framework",
            "doc_type": "pdf",
            "title": "AWS Well-Architected Framework",
            "source": AWS_WA_PDF,
            "path": str(pdf_path.relative_to(ROOT)),
            "access_roles": ["public"],
        }
    )
    print(f"✅ Downloaded AWS PDF: {pdf_path} ({len(pdf_bytes)} bytes)")

    # 2) Kubernetes HTML pages
    for url in K8S_URLS:
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.text.strip() if soup.title else url)
        file_name = slugify(url.replace("https://kubernetes.io", "").strip("/")) + ".html"
        out_path = K8S_DIR / file_name
        out_path.write_bytes(html)

        doc_id = "k8s-" + slugify(title)[:80]
        save_manifest_row(
            {
                "doc_id": doc_id,
                "doc_type": "html",
                "title": title,
                "source": url,
                "path": str(out_path.relative_to(ROOT)),
                "access_roles": ["public"],
            }
        )
        print(f"✅ Saved K8s page: {out_path.name}  |  {title}")

    print(f"\n📄 Manifest written: {MANIFEST_PATH} ({sum(1 for _ in MANIFEST_PATH.open('r', encoding='utf-8'))} lines)")


if __name__ == "__main__":
    main()
