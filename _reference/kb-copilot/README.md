# Enterprise Knowledge Copilot (RAG + RBAC + Eval) — FastAPI + Groq + Chroma

🖥️ Demo (available on request)

A lightweight **enterprise-style knowledge copilot** that answers questions over technical documentation with:
- ✅ **Grounded answers + citations**
- ✅ **Role-based access control (RBAC)**
- ✅ **Retrieval evaluation + ablations (nDCG / Recall / MRR)**
- ✅ **Prompt-injection resistant generation (guardrails-first)**


## 🧠 What this demonstrates (Applied AI / RAG Engineering)

This project replicates how “enterprise copilots” work in real teams:

✅ **Hybrid retrieval system**
- Dense search via **Chroma + E5 embeddings**
- Optional **BM25 lexical search**
- Optional **RRF fusion**
- Optional **Cross-Encoder reranking**
- Distance gating + near-duplicate filtering for cleaner context

✅ **Grounded answers (no citation hallucinations)**
- Model returns structured JSON:
  - `answer`
  - `used_sources: [1..N]`
- API validates source indexes before returning citations

✅ **RBAC enforcement**
- Documents tagged with access roles (`public`, `engineering`)
- Retrieval filters content based on user role

✅ **Evaluation harness**
- nDCG@5, Recall@5, MRR@5
- Per-tag breakdown + ablation comparisons

✅ **Guardrails-first**
- Retrieval + generation designed to reduce prompt injection / untrusted instruction following
- Strict: “Use ONLY provided sources” or return “I don’t know…”

---

## 📚 Corpus (demo dataset)

**Public:**
- AWS Well-Architected Framework (PDF)

**Engineering-only:**
- Kubernetes Concepts docs (HTML pages)

---

## 🏗 Architecture

### Offline indexing
```txt
download_corpus → parse_docs → chunk_docs → embed → store
                                 └─ builds BM25 corpus
```

### Online query
```txt
question
 → query rewrite (optional)
 → retrieve (vector / BM25 / fusion)
 → rerank (optional)
 → generate answer (Groq LLM, grounded)
 → validate citations → return answer + sources
```

---

## ✅ Current Retrieval Metrics (K=5)

Evaluated on an in-domain set (OOD questions removed):

| Config       | nDCG@5 | Recall@5 | MRR@5 |
|-------------|--------|----------|------|
| vector_only | **0.8353** | **0.9231** | **0.8029** |

---

## ⚡ Quickstart (Local)

### 1) Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

Create `.env`:
```env
GROQ_API_KEY=your_key
GROQ_MODEL=llama-3.3-70b-versatile

# Retrieval knobs
VECTOR_ENABLE=1
BM25_ENABLE=1
RRF_ENABLE=0
RERANK_ENABLE=0

RETRIEVE_CANDIDATES=30
BM25_CANDIDATES=30
RERANK_CANDIDATES=30

DEBUG_RETRIEVE=1
```

---

### 2) Build corpus + index
```bash
python app/ingest/download_corpus.py
python app/ingest/parse_docs.py
python app/chunk/make_chunks.py
python app/index/build_index.py --reset
```

---

### 3) Run API
```bash
uvicorn app.api.main:app --reload --port 8000
```

Health check:
```bash
curl http://localhost:8000/health
```

---

### 4) Ask a question
```bash
curl -s -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the six pillars of the AWS Well-Architected Framework?",
    "user": { "role": "public" }
  }'
```

---

## 🔐 RBAC Demo

### Public user (cannot access Kubernetes docs)
```bash
curl -s -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is a Kubernetes Object?",
    "user": { "role": "public" }
  }'
```

### Engineering user (can access Kubernetes docs)
```bash
curl -s -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is a Kubernetes Object?",
    "user": { "role": "engineering" }
  }'
```

---

## 📈 Evaluation

Run retrieval evaluation:
```bash
python eval/run_eval.py --k 5
```

Outputs:
- Overall nDCG@5 / Recall@5 / MRR
- Per-tag nDCG breakdown
- Ablations across knobs (vector / bm25 / rrf / rerank)

---

## 🌍 Deployment

Designed for containerized cloud deployment:
- **Frontend:** Next.js (Vercel-ready)
- **Backend:** Dockerized FastAPI behind an Nginx reverse proxy, deployable to AWS EC2
- Live demo environment available on request

---

## 🛠 Environment Variables (Knobs)

| Variable | Meaning |
|---------|---------|
| `VECTOR_ENABLE` | Enable semantic vector retrieval |
| `BM25_ENABLE` | Enable lexical retrieval |
| `RRF_ENABLE` | Enable RRF fusion (vector + BM25) |
| `RERANK_ENABLE` | Enable cross-encoder reranking |
| `RETRIEVE_CANDIDATES` | Candidate pool size |
| `BM25_CANDIDATES` | BM25 candidate pool size |
| `RERANK_CANDIDATES` | Reranker candidate pool size |
| `DEBUG_RETRIEVE` | Prints retrieval diagnostics |

---

## ✅ Design Notes

- **Stable chunk IDs** make debugging + evals easier.
- **Citations are validated** so the model can’t “fake” them.
- **RBAC enforced at retrieval** prevents accidental leakage of restricted docs.
- **Guardrails-first**: if sources don’t support an answer → the assistant returns “I don’t know…”

---

## 🔮 Next Improvements
- Add more Kubernetes docs (Pods/Deployments/Networking) to improve weaker retrieval tags.
- Harden prompt-injection defenses (input filtering + “ignore instructions in sources” policy).
- Add streaming responses + response caching for better UX and lower cost.
- Track eval regressions automatically when the corpus/index changes.



