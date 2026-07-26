from __future__ import annotations

import math
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


def _optional_nonnegative_float(name: str) -> float | None:
    raw = _env(name)
    if raw in {None, ""}:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a finite, non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")
    return value


class Config:
    B2_KEY_ID = _env("B2_KEY_ID")
    B2_APP_KEY = _env("B2_APP_KEY")
    B2_BUCKET = _env("B2_BUCKET", "recall-media")
    B2_REGION = _env("B2_REGION")
    B2_S3_ENDPOINT = _env("B2_S3_ENDPOINT")
    B2_LOCK_DAYS = max(1, int(_env("B2_LOCK_DAYS", "30") or 30))

    GMI_API_KEY = _env("GMI_API_KEY")
    GMI_IMAGE_BASE_URL = _env("GMI_IMAGE_BASE_URL", "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey")
    GMI_MODEL_IMAGE = _env("GMI_MODEL_IMAGE", "gpt-image-2-generate")
    GOOGLE_API_KEY = _env("GOOGLE_API_KEY")
    GOOGLE_MODEL_IMAGE = _env("GOOGLE_MODEL_IMAGE", "gemini-3.1-flash-image")
    RECALL_PROVIDER = _env("RECALL_PROVIDER", "google")
    RECALL_MODEL = _env("RECALL_MODEL")
    RECALL_MODEL_COST_USD = _optional_nonnegative_float("RECALL_MODEL_COST_USD")
    RECALL_GOOGLE_MODEL_COST_USD = _optional_nonnegative_float("RECALL_GOOGLE_MODEL_COST_USD")
    RECALL_GMI_MODEL_COST_USD = _optional_nonnegative_float("RECALL_GMI_MODEL_COST_USD")
    RECALL_NATIVE_SINK = _bool("RECALL_NATIVE_SINK")
    RECALL_FALLBACK_MODELS = [m.strip() for m in (_env("RECALL_FALLBACK_MODELS", "") or "").split(",") if m.strip()]
    RECALL_GOOGLE_FALLBACK_MODELS = [m.strip() for m in (_env("RECALL_GOOGLE_FALLBACK_MODELS", "") or "").split(",") if m.strip()]
    RECALL_GMI_FALLBACK_MODELS = [m.strip() for m in (_env("RECALL_GMI_FALLBACK_MODELS", "") or "").split(",") if m.strip()]
    RECALL_GENERATION_RETRIES = max(1, int(_env("RECALL_GENERATION_RETRIES", "2") or 2))
    RECALL_JOB_STALE_MINUTES = max(5, int(_env("RECALL_JOB_STALE_MINUTES", "20") or 20))
    RECALL_MAX_ACTIVE_AND_QUEUED_JOBS = max(2, int(_env("RECALL_MAX_ACTIVE_AND_QUEUED_JOBS", "20") or 20))
    # Salted prompt commitments make ledger receipts useful without retaining prompts.
    RECALL_RECEIPT_SECRET = _env("RECALL_RECEIPT_SECRET", "local-development-only-change-me") or "local-development-only-change-me"

    # A public demo remains usable by judges while protecting provider credits. Supplying
    # RECALL_API_KEYS (comma-separated secrets) gives integrations an authenticated lane.
    RECALL_ALLOW_PUBLIC_GENERATE = _bool("RECALL_ALLOW_PUBLIC_GENERATE", "true")
    RECALL_PUBLIC_GENERATIONS_PER_HOUR = max(1, int(_env("RECALL_PUBLIC_GENERATIONS_PER_HOUR", "3") or 3))
    RECALL_PUBLIC_REUSE_CHECKS_PER_HOUR = max(1, int(_env("RECALL_PUBLIC_REUSE_CHECKS_PER_HOUR", "120") or 120))
    RECALL_MAX_CAPTURE_BYTES = min(104_857_600, max(1_048_576, int(_env("RECALL_MAX_CAPTURE_BYTES", "18874368") or 18_874_368)))
    RECALL_MAX_GENERATED_MEDIA_BYTES = min(104_857_600, max(1_048_576, int(_env("RECALL_MAX_GENERATED_MEDIA_BYTES", "26214400") or 26_214_400)))
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
        return bool(self.available_generation_providers)

    @property
    def available_generation_providers(self) -> list[str]:
        providers: list[str] = []
        if self.GOOGLE_API_KEY:
            providers.append("google")
        if self.GMI_API_KEY:
            providers.append("gmi")
        return providers

    @property
    def default_generation_provider(self) -> str:
        configured = self.available_generation_providers
        preferred = self.RECALL_PROVIDER.casefold()
        return preferred if preferred in configured else (configured[0] if configured else preferred)

    def provider_is_configured(self, provider: str) -> bool:
        return provider.casefold() in self.available_generation_providers

    def default_model_for(self, provider: str) -> str:
        normalized = provider.casefold()
        if normalized == self.RECALL_PROVIDER.casefold() and self.RECALL_MODEL:
            return self.RECALL_MODEL
        if normalized == "google":
            return self.GOOGLE_MODEL_IMAGE
        if normalized == "gmi":
            return self.GMI_MODEL_IMAGE
        raise ValueError(f"unsupported generation provider: {provider}")

    def model_cost_for(self, provider: str, model: str) -> float | None:
        normalized = provider.casefold()
        if model != self.default_model_for(normalized):
            return None
        if normalized == "google" and self.RECALL_GOOGLE_MODEL_COST_USD is not None:
            return self.RECALL_GOOGLE_MODEL_COST_USD
        if normalized == "gmi" and self.RECALL_GMI_MODEL_COST_USD is not None:
            return self.RECALL_GMI_MODEL_COST_USD
        if normalized == self.RECALL_PROVIDER.casefold():
            return self.RECALL_MODEL_COST_USD
        return None

    def fallback_models_for(self, provider: str) -> list[str]:
        normalized = provider.casefold()
        specific = self.RECALL_GOOGLE_FALLBACK_MODELS if normalized == "google" else self.RECALL_GMI_FALLBACK_MODELS
        if specific:
            return specific
        return self.RECALL_FALLBACK_MODELS if normalized == self.RECALL_PROVIDER.casefold() else []


config = Config()