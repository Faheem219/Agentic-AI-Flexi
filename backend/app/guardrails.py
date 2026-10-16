"""Input guardrails: validation, prompt-injection screening and PII redaction.

These are deterministic, explainable checks that run *before* any LLM sees the input.
A second, model-based scope/safety check happens in the planner. Tool outputs are
treated as untrusted data (see prompts.UNTRUSTED_DATA_NOTICE).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_LEN, MAX_LEN = 12, 1200

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override-instructions", re.compile(r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|all|system|your)\b[^.]{0,20}\b(instructions?|rules|prompts?|guidelines)", re.I)),
    ("prompt-exfiltration", re.compile(r"\b(reveal|show|print|repeat|leak|output)\b[^.]{0,30}\b(system prompt|hidden prompt|instructions|api key|secret|password)", re.I)),
    ("role-hijack", re.compile(r"\b(you are now|act as|pretend to be|roleplay as)\b[^.]{0,40}\b(dan|jailbroken|unfiltered|unrestricted|developer mode|evil)", re.I)),
    ("jailbreak-keyword", re.compile(r"\b(jailbreak|do anything now|developer mode enabled)\b", re.I)),
    ("tool-abuse", re.compile(r"\b(call|invoke|execute|run)\b[^.]{0,20}\b(tool|function)\b[^.]{0,30}\b(\d{2,}|thousand|infinite|loop|forever)\b", re.I)),
]

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("card", re.compile(r"\b(?:\d{4}[\s-]){3}\d{4}\b")),
    ("aadhaar", re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("phone", re.compile(
        r"(?<![\w+])(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"
        r"|\+\d{1,3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{3,4}(?!\d)")),
]


@dataclass
class GuardrailResult:
    allowed: bool
    sanitized: str
    reasons: list[str] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)


def check_input(question: str) -> GuardrailResult:
    q = (question or "").strip()
    checks: list[dict] = []
    reasons: list[str] = []

    ok_len = MIN_LEN <= len(q) <= MAX_LEN
    checks.append({"check": "length", "passed": ok_len, "detail": f"{len(q)} chars (allowed {MIN_LEN}-{MAX_LEN})"})
    if not ok_len:
        reasons.append("Question must be between 12 and 1200 characters.")

    hits = [name for name, pat in INJECTION_PATTERNS if pat.search(q)]
    checks.append({"check": "prompt_injection", "passed": not hits, "detail": ", ".join(hits) or "none detected"})
    if hits:
        reasons.append("The request looks like a prompt-injection / jailbreak attempt (" + ", ".join(hits) + ").")

    sanitized = q
    redactions: dict[str, int] = {}
    for name, pat in PII_PATTERNS:
        sanitized, n = pat.subn(f"[REDACTED_{name.upper()}]", sanitized)
        if n:
            redactions[name] = n
    checks.append({"check": "pii_redaction", "passed": True,
                   "detail": ", ".join(f"{k}x{v}" for k, v in redactions.items()) or "no PII found"})

    return GuardrailResult(allowed=not reasons, sanitized=sanitized, reasons=reasons,
                           redactions=redactions, checks=checks)
