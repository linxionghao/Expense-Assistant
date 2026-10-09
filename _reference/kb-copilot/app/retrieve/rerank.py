from __future__ import annotations

import os 
from typing import List

from sentence_transformers import CrossEncoder

DEFAULT_RERANK_MODEL = os.getenv(
    "RERANK_MODEL",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
)

MAX_TEXT_CHARS = int(os.getenv("RERANK_MAX_TEXT_CHARS", "2000"))

class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL) -> None:
        self.model = CrossEncoder(model_name)
    
    def score(self, query: str, docs:List[str]) -> List[float]:
        pairs = [(query, (d or "")[:MAX_TEXT_CHARS]) for d in docs]
        scores = self.model.predict(pairs)
        return [float(s) for s in scores]

        
