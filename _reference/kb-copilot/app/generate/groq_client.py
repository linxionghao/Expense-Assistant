# app/generate/groq_client.py
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Keep below Groq RPM=30 to avoid 429 spikes
GROQ_RPM_LIMIT = int(os.getenv("GROQ_RPM_LIMIT", "28"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "5"))

# Sliding window limiter (process-local)
_recent_requests: deque[float] = deque()

class GroqError(RuntimeError):
    pass


def _rate_limit() -> None:
    """Simple RPM limiter."""
    if GROQ_RPM_LIMIT <= 0:
        return

    now = time.time()
    window = 60.0

    while _recent_requests and (now - _recent_requests[0]) > window:
        _recent_requests.popleft()

    if len(_recent_requests) >= GROQ_RPM_LIMIT:
        sleep_for = window - (now - _recent_requests[0]) + 0.05
        if sleep_for > 0:
            time.sleep(sleep_for)

    _recent_requests.append(time.time())


def _parse_retry_after_seconds(resp: requests.Response) -> Optional[float]:
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return float(ra)
        except Exception:
            pass

    # Groq often includes: "Please try again in 2s."
    try:
        msg = resp.text
        m = re.search(r"try again in (\d+)\s*s", msg, flags=re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception:
        return None

    return None


def chat_completion(messages: List[Dict[str, str]], *, temperature: float = 0.0) -> str:
    if not GROQ_API_KEY:
        raise GroqError("Missing GROQ_API_KEY in environment/.env")

    url = f"{GROQ_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
    }

    last_err: Optional[str] = None

    for attempt in range(GROQ_MAX_RETRIES + 1):
        _rate_limit()

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
        except Exception as e:
            last_err = str(e)
            # backoff
            time.sleep(min(2.0 * (attempt + 1), 8.0))
            continue

        if r.status_code == 429:
            wait_s = _parse_retry_after_seconds(r) or min(2.0 * (attempt + 1), 10.0)
            time.sleep(wait_s)
            last_err = f"429 rate limit: waited {wait_s}s"
            continue

        if r.status_code >= 400:
            raise GroqError(f"Groq API error {r.status_code}: {r.text}")

        data = r.json()
        return data["choices"][0]["message"]["content"]

    raise GroqError(f"Groq API error: retries exhausted. Last error: {last_err}")
