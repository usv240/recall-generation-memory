"""Explainable lexical + optional semantic reuse ranking for Recall."""
from __future__ import annotations
import re
from typing import Any
from .semantic import cosine, embed

_STOP = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "with", "on", "at", "is", "it", "this", "that", "image", "photo"}

def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

def tokens(value: str) -> set[str]:
    return {word for word in normalize(value).split() if word not in _STOP}

def rank(prompt: str, tags: list[str], rows: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    query, query_tokens = normalize(prompt), tokens(prompt)
    query_embedding = embed(prompt)
    tag_tokens = {normalize(tag) for tag in tags if normalize(tag)}
    scored: list[dict[str, Any]] = []
    for row in rows:
        stored = normalize(row.get("prompt", ""))
        asset_tokens = tokens(row.get("prompt", ""))
        union = query_tokens | asset_tokens
        lexical = len(query_tokens & asset_tokens) / len(union) if union else 0.0
        tag_overlap = len(tag_tokens & {normalize(tag) for tag in row.get("tags", [])})
        exact = bool(query and query == stored)
        semantic = cosine(query_embedding, row.get("semantic", {}).get("embedding"))
        text_score = min(0.92, lexical + min(0.15, tag_overlap * 0.08))
        score = 1.0 if exact else max(text_score, semantic or 0.0)
        if exact or score >= 0.28:
            match = "exact" if exact else ("semantic-similar" if semantic is not None and semantic >= text_score else "text-similar")
            scored.append({"generation": row, "score": round(score, 3), "match": match, "lexical_score": round(lexical, 3), "semantic_score": round(semantic, 3) if semantic is not None else None})
    return sorted(scored, key=lambda item: (item["score"], item["generation"].get("created", "")), reverse=True)[:limit]