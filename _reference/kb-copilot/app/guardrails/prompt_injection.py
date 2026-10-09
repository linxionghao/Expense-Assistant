from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple, Literal

Severity = Literal["low", "medium", "high"]

# (name, severity, regex)
_PATTERNS: List[Tuple[str, Severity, str]] = [
    # HIGH: exfiltration / system prompt / role injection templates
    ("system_prompt", "high", r"\b(system prompt|developer message|hidden instructions)\b"),
    ("exfiltrate", "high", r"\b(reveal|show|print|leak|dump)\b.{0,60}\b(prompt|instructions|policy|keys?|secrets?)\b"),
    ("role_hack", "high", r"```(system|developer|assistant)"),
    ("mal_delims", "high", r"(BEGIN|END)\s+(SYSTEM|DEVELOPER|INSTRUCTIONS)"),

    # MED: "ignore rules" style injections
    ("ignore_rules", "medium", r"\b(ignore|disregard|bypass|override)\b.{0,60}\b(instructions|rules|system|developer|policy)\b"),
    ("role_play", "medium", r"\b(you are|act as|pretend to be)\b.{0,60}\b(system|developer|admin|root)\b"),

    # LOW: tool-related text can be legit (but often used in jailbreaks)
    ("tool_abuse", "low", r"\b(call|use|invoke)\b.{0,40}\b(tool|function|browser|python)\b"),
]

# Weights for scoring
_WEIGHTS = {"low": 0.10, "medium": 0.35, "high": 1.00}


@dataclass
class InjectionCheck:
    suspicious: bool
    score: float
    matches: List[str]
    high_confidence: bool


def detect_prompt_injection(text: str) -> InjectionCheck:
    """
    Detection:
    - Any HIGH match => suspicious immediately
    - Else weighted scoring for medium/low combos
    """
    t = (text or "").lower()
    hits: List[str] = []
    score = 0.0
    high = False

    for name, severity, pat in _PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE | re.DOTALL):
            hits.append(name)
            score += _WEIGHTS[severity]
            if severity == "high":
                high = True

    # Cap score to [0, 1]
    score = min(1.0, score)

    # Trigger rule:
    # - any high severity hit triggers immediately
    # - OR score >= threshold (2 mediums OR medium+low combos)
    threshold = 0.45
    suspicious = high or (score >= threshold)

    return InjectionCheck(
        suspicious=suspicious,
        score=score,
        matches=hits,
        high_confidence=high,
    )


def safe_truncate(text: str, max_chars: int = 4000) -> str:
    """
    Prevent token/cost bombs and reduce injection surface area.
    Also removes null bytes which can mess with logs/parsers.
    """
    if not text:
        return ""
    text = text.replace("\x00", "")
    return text if len(text) <= max_chars else text[:max_chars] + "…"
