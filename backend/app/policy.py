"""Policy-aware intent matching. Similarity is never enough for constrained creative work."""
from __future__ import annotations
from typing import Any

INTENT_FIELDS = ("campaign", "brand", "format", "license", "language")
POLICY_VERSION = "intent-firewall/v1"


def clean_intent(value: dict[str, Any] | None) -> dict[str, str]:
    source = value or {}
    return {field: str(source[field]).strip() for field in INTENT_FIELDS if source.get(field) and str(source[field]).strip()}


def conflicts(requested: dict[str, Any] | None, candidate: dict[str, Any] | None) -> list[dict[str, str]]:
    requested, candidate = clean_intent(requested), clean_intent(candidate)
    issues: list[dict[str, str]] = []
    for field, wanted in requested.items():
        actual = candidate.get(field)
        if actual and actual.casefold() != wanted.casefold():
            issues.append({"field": field, "requested": wanted, "candidate": actual, "reason": "mismatch"})
        elif not actual and field in {"brand", "license", "format", "language"}:
            issues.append({"field": field, "requested": wanted, "candidate": "", "reason": "candidate_unprofiled"})
    return issues


def decision(matches: list[dict[str, Any]], intent: dict[str, Any] | None, threshold: float = .7) -> tuple[str, list[dict[str, str]], str]:
    if not matches or matches[0]["score"] < threshold:
        return "generate", [], "No sufficiently similar archived asset was found."
    issues = conflicts(intent, matches[0]["generation"].get("intent"))
    if issues:
        return "generate", issues, "Intent Firewall blocked reuse: the closest asset is similar but does not satisfy required constraints."
    return "reuse", [], "Intent Firewall passed; reuse remains an explicit user choice."
