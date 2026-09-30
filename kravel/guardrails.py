from __future__ import annotations

import re
import time
import unicodedata


SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [^-\r\n]{0,40}PRIVATE KEY-----[\s\S]*?-----END [^-\r\n]{0,40}PRIVATE KEY-----", re.I)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("credential_assignment", re.compile(r"\b(api[_-]?key|password|passwd|access[_-]?token|client[_-]?secret)\s*[:=]\s*[\"']?[^\s\"',;]{4,}", re.I)),
]
INSTRUCTION_PATTERNS = [
    re.compile(r"ignore (?:all |any )?(?:previous|prior) instructions", re.I),
    re.compile(r"(?:reveal|print|return).{0,30}(?:system prompt|developer message|hidden instruction)", re.I),
    re.compile(r"(?:you are now|act as) (?:an? )?(?:assistant|system|administrator|root)", re.I),
    re.compile(r"</?(?:tool_call|system|assistant|developer)>", re.I),
]


def _ms(started):
    return (time.perf_counter() - started) * 1000


def _normalize(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32)


def _redact(text: str):
    findings = []
    for name, pattern in SECRET_PATTERNS:
        text, count = pattern.subn(f"<redacted:{name}>", text)
        if count:
            findings.append({"code": name, "count": count})
    return text, findings


def guard_model_input(value, target: str, max_characters: int = 20_000):
    started = time.perf_counter()
    text, findings = _redact(_normalize(value))
    lines, quarantined = [], 0
    for line in text.split("\n"):
        if any(pattern.search(line) for pattern in INSTRUCTION_PATTERNS):
            lines.append("[quarantined instruction-like text from untrusted cluster evidence]")
            quarantined += 1
        else:
            lines.append(line)
    text = "\n".join(lines)
    if quarantined:
        findings.append({"code": "prompt_injection_pattern", "count": quarantined})
    if len(text) > max_characters:
        text = text[: max(0, max_characters - 80)] + f"\n[truncated by {target} input guardrail]"
        findings.append({"code": "input_truncated", "count": 1})
    if not text.strip():
        raise ValueError(f"{target} input guardrail rejected empty input")
    return {"value": text, "decision": "allow_with_redactions" if findings else "allow", "findings": findings, "latencyMs": _ms(started)}


def guard_debugger_output(value, max_characters: int = 12_000):
    started = time.perf_counter()
    text, findings = _redact(_normalize(value))
    pattern = re.compile(r"^.*\bkubectl\s+(?:delete|apply|patch|replace|scale|set|edit|create|rollout)\b.*$", re.I | re.M)
    text, count = pattern.subn("[mutation command withheld; remediation requires human approval]", text)
    if count:
        findings.append({"code": "mutation_command_withheld", "count": count})
    if not re.search(r"uncertainty|unknown|missing evidence", text, re.I):
        findings.append({"code": "missing_uncertainty_statement", "count": 1})
    if len(text) > max_characters:
        text = text[: max(0, max_characters - 80)] + "\n[truncated by Qwen output guardrail]"
        findings.append({"code": "output_truncated", "count": 1})
    if not text.strip():
        raise ValueError("Debugger output guardrail rejected empty output")
    return {"value": text, "decision": "allow_with_warnings" if findings else "allow", "findings": findings, "latencyMs": _ms(started)}
