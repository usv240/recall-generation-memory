"""Optional Gemini embeddings for transparent, cosine-ranked semantic recall."""
from __future__ import annotations

import math
from typing import Any
import httpx

from .config import config


def embed(text: str) -> list[float] | None:
    if not config.RECALL_EMBEDDINGS_ENABLED or not config.GOOGLE_API_KEY or not text.strip():
        return None
    model = config.GOOGLE_EMBEDDING_MODEL
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
    body: dict[str, Any] = {
        "model": f"models/{model}",
        "content": {"parts": [{"text": text[:8000]}]},
        "taskType": "SEMANTIC_SIMILARITY",
    }
    try:
        response = httpx.post(endpoint, headers={"x-goog-api-key": config.GOOGLE_API_KEY}, json=body, timeout=20)
        response.raise_for_status()
        values = response.json().get("embedding", {}).get("values", [])
        return [float(value) for value in values] if values else None
    except Exception:
        return None


def cosine(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return (sum(a * b for a, b in zip(left, right)) / denominator) if denominator else None