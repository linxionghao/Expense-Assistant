from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "raw" / "manifest.jsonl"


def main() -> None:
    rows = []
    with MANIFEST.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    changed = 0
    for r in rows:
        src = r.get("source", "")
        doc_id = r.get("doc_id", "")

        # Engineering-only: Kubernetes docs
        if "kubernetes.io" in src or doc_id.startswith("k8s-"):
            r["access_roles"] = ["engineering"]
        else:
            # keep AWS public
            r["access_roles"] = r.get("access_roles") or ["public"]

        changed += 1

    # rewrite manifest deterministically
    with MANIFEST.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Updated RBAC tags in manifest: {MANIFEST} (rows={changed})")
    # quick summary
    pub = sum(1 for r in rows if r.get("access_roles") == ["public"])
    eng = sum(1 for r in rows if r.get("access_roles") == ["engineering"])
    print(f"public={pub}, engineering={eng}")


if __name__ == "__main__":
    main()
