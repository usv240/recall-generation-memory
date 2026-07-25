from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def _bool(name: str, default: str = "false") -> bool:
    return (_env(name, default) or default).lower() in {"1", "true", "yes", "on"}


class Config:
    B2_KEY_ID = _env("B2_KEY_ID")
    B2_APP_KEY = _env("B2_APP_KEY")
    B2_BUCKET = _env("B2_BUCKET", "recall-media")
    B2_REGION = _env("B2_REGION")
    B2_S3_ENDPOINT = _env("B2_S3_ENDPOINT")
    B2_LOCK_DAYS = int(_env("B2_LOCK_DAYS", "30") or 30)

    GMI_API_KEY = _env("GMI_API_KEY")
    GMI_IMAGE_BASE_URL = _env("GMI_IMAGE_BASE_URL", "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey")
    GOOGLE_API_KEY = _env("GOOGLE_API_KEY")
    GOOGLE_MODEL_IMAGE = _env("GOOGLE_MODEL_IMAGE", "gemini-3.1-flash-image")
    RECALL_PROVIDER = _env("RECALL_PROVIDER", "google")
    RECALL_MODEL = _env("RECALL_MODEL", GOOGLE_MODEL_IMAGE)
    RECALL_MODEL_COST_USD = _env("RECALL_MODEL_COST_USD")
    RECALL_NATIVE_SINK = _bool("RECALL_NATIVE_SINK")
    RECALL_FALLBACK_MODELS = [m.strip() for m in (_env("RECALL_FALLBACK_MODELS", "") or "").split(",") if m.strip()]
    RECALL_GENERATION_RETRIES = max(1, int(_env("RECALL_GENERATION_RETRIES", "2") or 2))

    # A public demo remains usable by judges while protecting provider credits. Supplying
    # RECALL_API_KEYS (comma-separated secrets) gives integrations an authenticated lane.
    RECALL_ALLOW_PUBLIC_GENERATE = _bool("RECALL_ALLOW_PUBLIC_GENERATE", "true")
    RECALL_PUBLIC_GENERATIONS_PER_HOUR = max(1, int(_env("RECALL_PUBLIC_GENERATIONS_PER_HOUR", "3") or 3))
    RECALL_API_KEYS = [key.strip() for key in (_env("RECALL_API_KEYS", "") or "").split(",") if key.strip()]
    RECALL_CORS_ORIGINS = [origin.strip() for origin in (_env("RECALL_CORS_ORIGINS", "") or "").split(",") if origin.strip()]

    # Optional semantic recall. It is intentionally opt-in because embedding requests are
    # external provider calls; lexical matching always remains available.
    RECALL_EMBEDDINGS_ENABLED = _bool("RECALL_EMBEDDINGS_ENABLED")
    GOOGLE_EMBEDDING_MODEL = _env("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")
    LOCAL_DATA_DIR = Path(_env("RECALL_LOCAL_DATA_DIR", str(ROOT / ".data")) or ".data")

    @property
    def has_b2(self) -> bool:
        return bool(self.B2_KEY_ID and self.B2_APP_KEY and self.B2_REGION)

    @property
    def has_generation_provider(self) -> bool:
        return bool(self.GOOGLE_API_KEY if self.RECALL_PROVIDER == "google" else self.GMI_API_KEY)


config = Config()