"""Secret detection on write.

The goal is to stop an agent from copying an API key into memory, not to
replace a full scanner. The repositories' pre-receive hook remains the second
line of defense (phase 3).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("LoreMd token", re.compile(r"\blmd_[a-z0-9]{6,}_[A-Za-z0-9_-]{20,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "URL with password",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s/@]{6,}@"),
    ),
)

#: `api_key = "…"`, `password: …` and variants.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|secret|password|passwd|token|private[_-]?key|access[_-]?key)\b
    \s*[:=]\s*
    ['"]?(?P<value>[A-Za-z0-9/+_\-\.]{16,})['"]?
    """
)

#: Obviously fake values: documentation must not be blocked.
_PLACEHOLDERS = re.compile(
    r"(?i)^(?:x{4,}|\.{3,}|<[^>]+>|\$\{[^}]+\}|change[-_]?me|your[-_].*|"
    r"placeholder|example|redacted|dummy|todo|null|none)$"
)


@dataclass(frozen=True)
class SecretFinding:
    kind: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.kind} ({self.excerpt})"


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-2:]}"


def scan(text: str) -> list[SecretFinding]:
    """Returns the likely secrets found in the text."""
    findings: list[SecretFinding] = []
    seen: set[str] = set()

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in seen:
                seen.add(value)
                findings.append(SecretFinding(kind, _mask(value)))

    for match in _ASSIGNMENT.finditer(text):
        value = match.group("value")
        if value in seen or _PLACEHOLDERS.match(value):
            continue
        # An assignment only counts if the value looks random.
        if _shannon_entropy(value) >= 3.5:
            seen.add(value)
            findings.append(SecretFinding("high-entropy value", _mask(value)))

    return findings
