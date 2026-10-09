from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

from app.retrieve.retriever import Retriever
from app.generate.answer import generate_answer_json, validate_used_sources

app = FastAPI(title="Enterprise Knowledge Copilot")

retriever = Retriever()

class AskUser(BaseModel):
    role: str = "public"

class AskRequest(BaseModel):
    question: str
    user: Optional[AskUser] = None

class SourceItem(BaseModel):
    title: str
    source: str
    chunk_id: str
    snippet: str

class AskResponse(BaseModel):
    answer: str
    sources: List[SourceItem]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    role = (req.user.role if req.user else "public")

    question = (req.question or "").strip()
    if len(question) > 2000:
        question = question[:2000] + "…"

    chunks = retriever.retrieve(
        question,
        top_k=5,
        user_role=role,
    )

    sources = [
        SourceItem(
            title=c.title,
            source=c.source,
            chunk_id=c.chunk_id,
            snippet=c.snippet,
        )
        for c in chunks
    ]

    gen = generate_answer_json(question, chunks)
    used = validate_used_sources(gen.get("used_sources", []), len(sources))

    answer_text = gen.get("answer", "").strip()

    if (not used) or (answer_text == "I don’t know based on the provided documents."):
        return AskResponse(
            answer="I don’t know based on the provided documents.",
            sources=[]
        )

    used_sources = [sources[i - 1] for i in used]

    citations = ", ".join([f"[{i}]" for i in range(1, len(used_sources) + 1)])
    answer_text = answer_text.rstrip() + f"\n\nCitations: {citations}"

    return AskResponse(answer=answer_text, sources=used_sources)

