from __future__ import annotations

import base64
import contextvars
import binascii
import hashlib
import datetime as dt
import json
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import mimetypes
import re
import secrets
import hmac
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import config
from .pipeline import RecallPipeline
from .reuse import rank
from .policy import POLICY_VERSION, clean_intent, decision
from .ledger import create_receipt, verify_receipt, prompt_commitment
from .semantic import embed
from .media import image_dhash, sha256
from .security import require_generation_access, require_integration_access, require_private_api_key, require_reuse_access
from .storage import RecallStore, now

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
logger = logging.getLogger("recall")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        _savings_snapshot(force=True)
    except Exception as exc:
        logger.warning("savings_warm_failed error_type=%s", type(exc).__name__)
    yield


app = FastAPI(
    title="Recall API",
    version="1.0.0",
    description="A provenance-first reusable generation memory powered by Genblaze and Backblaze B2.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.RECALL_CORS_ORIGINS or ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Recall-Key", "X-Recall-Workspace", "X-Recall-Workspace-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https: data:; media-src 'self' https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


_store: RecallStore | None = None
_workspace_stores: dict[str, RecallStore] = {}
_workspace_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("recall_workspace", default=None)
_workspace_store_lock = threading.Lock()
_receipt_lock_guard = threading.Lock()
_receipt_locks: dict[str, threading.Lock] = {}
_savings_cache_lock = threading.Lock()
_savings_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_savings_refreshing: set[tuple[str, int]] = set()
_SAVINGS_REFRESH_SECONDS = 60.0
_jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="recall-generation")
_job_slots = threading.BoundedSemaphore(config.RECALL_MAX_ACTIVE_AND_QUEUED_JOBS)


def root_store() -> RecallStore:
    global _store
    if _store is None:
        _store = RecallStore()
    return _store


def store() -> RecallStore:
    workspace_id = _workspace_context.get()
    if not workspace_id:
        return root_store()
    if workspace_id not in _workspace_stores:
        with _workspace_store_lock:
            if workspace_id not in _workspace_stores:
                _workspace_stores[workspace_id] = RecallStore(workspace_id)
    return _workspace_stores[workspace_id]


def receipt_chain_lock() -> threading.Lock:
    scope = _workspace_context.get() or "root"
    with _receipt_lock_guard:
        if scope not in _receipt_locks:
            _receipt_locks[scope] = threading.Lock()
        return _receipt_locks[scope]


@app.middleware("http")
async def workspace_scope(request: Request, call_next: Any) -> Response:
    workspace_id = request.headers.get("x-recall-workspace", "").strip()
    if not workspace_id:
        return await call_next(request)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", workspace_id):
        return JSONResponse(status_code=401, content={"detail": "Invalid Recall workspace credentials."})
    token = request.headers.get("x-recall-workspace-key", "").strip()
    try:
        record = root_store().workspace(workspace_id)
    except Exception as exc:
        logger.warning("workspace_auth_store_unavailable error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Workspace authentication is temporarily unavailable."})
    expected = (record or {}).get("key_sha256", "")
    supplied = hashlib.sha256(token.encode()).hexdigest() if token else ""
    if not expected or not hmac.compare_digest(expected, supplied):
        return JSONResponse(status_code=401, content={"detail": "Invalid Recall workspace credentials."})
    marker = _workspace_context.set(workspace_id)
    request.state.workspace_actor = f"workspace_{workspace_id}"
    try:
        return await call_next(request)
    finally:
        _workspace_context.reset(marker)


class IntentProfile(BaseModel):
    campaign: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    format: str | None = Field(default=None, max_length=80)
    license: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, max_length=80)


ShortTag = Annotated[str, Field(min_length=1, max_length=80)]
SUPPORTED_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "video/mp4", "audio/mpeg", "audio/wav"}
MAX_CAPTURE_BASE64_CHARS = ((config.RECALL_MAX_CAPTURE_BYTES + 2) // 3) * 4
SENSITIVE_PARAM_NAMES = {"api_key", "apikey", "authorization", "password", "secret", "token"}


def media_signature_matches(media: bytes, media_type: str) -> bool:
    checks = {
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP",
        "video/mp4": lambda value: len(value) >= 12 and value[4:8] == b"ftyp",
        "audio/mpeg": lambda value: value.startswith(b"ID3") or (len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0),
        "audio/wav": lambda value: value.startswith(b"RIFF") and value[8:12] == b"WAVE",
    }
    return bool(media and checks[media_type](media))


class ParameterizedRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def validate_safe_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("params must contain finite JSON values") from exc
        if len(encoded) > 32_768:
            raise ValueError("params may not exceed 32 KB")

        def inspect(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    normalized = str(key).casefold().replace("-", "_")
                    if normalized in SENSITIVE_PARAM_NAMES or normalized.endswith("_secret") or normalized.endswith("_token"):
                        raise ValueError(f"params must not contain credentials ({key})")
                    inspect(nested)
            elif isinstance(item, list):
                for nested in item:
                    inspect(nested)

        inspect(value)
        return value


class GenerateRequest(ParameterizedRequest):
    prompt: str = Field(min_length=3, max_length=1000)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    tags: list[ShortTag] = Field(default_factory=list, max_length=20)
    parent_gen_id: str | None = Field(default=None, max_length=80)
    intent: IntentProfile = Field(default_factory=IntentProfile)


class ReuseRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    tags: list[ShortTag] = Field(default_factory=list, max_length=20)
    intent: IntentProfile = Field(default_factory=IntentProfile)


class ForkRequest(ParameterizedRequest):
    prompt: str = Field(min_length=3, max_length=1000)
    intent: IntentProfile = Field(default_factory=IntentProfile)


class CaptureRequest(ParameterizedRequest):
    prompt: str = Field(min_length=3, max_length=1000)
    media_base64: str = Field(min_length=4, max_length=MAX_CAPTURE_BASE64_CHARS)
    media_type: str = Field(default="image/png", min_length=1, max_length=120)
    provider: str = Field(default="external", min_length=1, max_length=80)
    model: str = Field(default="external", min_length=1, max_length=160)
    tags: list[ShortTag] = Field(default_factory=list, max_length=20)
    intent: IntentProfile = Field(default_factory=IntentProfile)
    cost_usd: float | None = Field(default=None, ge=0, le=100_000)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if value.casefold() not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("unsupported media_type")
        return value.casefold()


class WorkspaceCreateRequest(BaseModel):
    label: str = Field(min_length=2, max_length=80)


class FeedbackRequest(BaseModel):
    receipt_id: str = Field(min_length=4, max_length=80)
    verdict: str = Field(pattern="^(correct_reuse|too_similar|never_suggest|always_eligible)$")
    note: str = Field(default="", max_length=500)


def public(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    semantic = data.pop("semantic", None)
    data["semantic_indexed"] = bool(semantic and semantic.get("embedding"))
    asset_key = row.get("asset", {}).get("b2_key")
    data["asset_url"] = store().url(asset_key) if asset_key else None
    data["manifest_url"] = store().url(row["manifest_key"]) if row.get("manifest_key") else None
    data["raw_manifest_url"] = store().url(row["raw_manifest_key"]) if row.get("raw_manifest_key") else None
    return data


def event(kind: str, *, gen_id: str | None = None, actor: str | None = None, **extra: Any) -> bool:
    try:
        store().record_event({"event_id": f"evt_{uuid.uuid4().hex[:12]}", "created": now(), "kind": kind, "gen_id": gen_id, "actor": actor, **extra})
        _apply_savings_event(kind, gen_id, extra)
        return True
    except Exception as exc:
        if kind in {"generate", "capture", "fork"}:
            _apply_savings_event(kind, gen_id, extra)
        logger.warning("event_write_failed kind=%s error_type=%s", kind, type(exc).__name__)
        return False


def submit_generation_job(job_id: str, payload: GenerateRequest, actor: str) -> None:
    if not _job_slots.acquire(blocking=False):
        queued = store().job(job_id)
        if queued:
            queued.update({"status":"rejected", "completed":now(), "error":"Generation queue was full; no provider call was made."})
            store().save_job(queued)
        raise HTTPException(503, "Generation queue is full. Retry after an active job completes.")
    try:
        _jobs.submit(_run_job, job_id, payload, actor, _workspace_context.get())
    except Exception:
        _job_slots.release()
        raise


def _run_job(job_id: str, payload: GenerateRequest, actor: str, workspace_id: str | None) -> None:
    marker = _workspace_context.set(workspace_id)
    try:
        task_store = store()
        job = task_store.job(job_id) or {"job_id": job_id}
        job.update({"status": "running", "started": now()})
        task_store.save_job(job)
        try:
            row = RecallPipeline(task_store).generate(prompt=payload.prompt, model=payload.model or config.RECALL_MODEL, params=payload.params, tags=payload.tags, parent_id=payload.parent_gen_id, intent=clean_intent(payload.intent.model_dump()))
            event_recorded = event("generate", gen_id=row["gen_id"], actor=actor, model=row["model"], cost_usd=row.get("cost_usd"), job_id=job_id)
            job.update({"status": "completed", "completed": now(), "generation_id": row["gen_id"]})
            if not event_recorded:
                job["warning"] = "Generation archived, but its analytics event could not be recorded."
        except Exception as exc:
            try:
                event("generate_failed", actor=actor, job_id=job_id, reason=str(exc)[:180])
            except Exception:
                pass
            job.update({"status": "failed", "completed": now(), "error": str(exc)[:240]})
        task_store.save_job(job)
    finally:
        _workspace_context.reset(marker)
        _job_slots.release()

def recover_stale_jobs() -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=config.RECALL_JOB_STALE_MINUTES)
    recovered = 0
    completed_events = {item.get("job_id"):item for item in store().events() if item.get("kind") == "generate" and item.get("job_id") and item.get("gen_id")}
    for job in store().jobs():
        if job.get("status") not in {"queued", "running"}:
            continue
        stamp = job.get("started") or job.get("created")
        try:
            created = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except Exception:
            continue
        if created < cutoff:
            completion = completed_events.get(job.get("job_id"))
            generation_id = (completion or {}).get("gen_id")
            if generation_id and store().generation(generation_id):
                job.update({"status":"completed", "completed":now(), "generation_id":generation_id, "warning":"Recovered completion from the durable generation event after an interrupted status write."})
            else:
                job.update({"status": "interrupted", "completed": now(), "error": "Worker did not finish before the recovery threshold. Retry safely from the stored request."})
            store().save_job(job)
            recovered += 1
    return recovered

@app.post("/api/v1/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreateRequest, request: Request) -> dict[str, Any]:
    actor = require_private_api_key(request)
    workspace_id = f"ws-{secrets.token_hex(8)}"
    workspace_key = secrets.token_urlsafe(32)
    root_store().save_workspace({"workspace_id":workspace_id, "label":payload.label, "created":now(), "created_by":actor.label, "key_sha256":hashlib.sha256(workspace_key.encode()).hexdigest(), "storage_prefix":f"recall/workspaces/{workspace_id}/"})
    return {"workspace_id":workspace_id, "workspace_key":workspace_key, "storage_prefix":f"recall/workspaces/{workspace_id}/", "warning":"Save workspace_key now. It is never stored or returned again."}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "storage": store().mode,
        "live_mode": True,
        "generation_provider": config.has_generation_provider,
        "api_version": "v1",
        "public_demo_generation_limit_per_hour": config.RECALL_PUBLIC_GENERATIONS_PER_HOUR if config.RECALL_ALLOW_PUBLIC_GENERATE else 0,
        "public_reuse_check_limit_per_hour": config.RECALL_PUBLIC_REUSE_CHECKS_PER_HOUR,
        "api_key_access": bool(config.RECALL_API_KEYS),
        "limits": {"capture_bytes":config.RECALL_MAX_CAPTURE_BYTES, "generated_media_bytes":config.RECALL_MAX_GENERATED_MEDIA_BYTES, "active_and_queued_jobs":config.RECALL_MAX_ACTIVE_AND_QUEUED_JOBS},
    }


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    if store().mode != "b2":
        raise HTTPException(503, "B2 archive is not configured")
    if not config.has_generation_provider:
        raise HTTPException(503, "Generation provider is not configured")
    try:
        recovered_jobs = recover_stale_jobs()
        store().list_keys("recall/index/")
    except Exception as exc:
        raise HTTPException(503, f"B2 archive check failed: {str(exc)[:120]}") from exc
    warnings = []
    receipt_secret_strong = config.RECALL_RECEIPT_SECRET != "local-development-only-change-me" and len(config.RECALL_RECEIPT_SECRET) >= 32
    if not receipt_secret_strong:
        warnings.append("Set a strong RECALL_RECEIPT_SECRET before handling private workspaces.")
    if not config.RECALL_CORS_ORIGINS:
        warnings.append("Set RECALL_CORS_ORIGINS to the production frontend origin.")
    return {"ok": True, "checks": {"b2": "reachable", "generation_provider": "configured", "receipt_commitments":"configured" if receipt_secret_strong else "development-default"}, "warnings":warnings, "recovered_interrupted_jobs": recovered_jobs}


@app.post("/api/v1/reuse-check")
@app.post("/api/reuse-check")
def reuse_check(payload: ReuseRequest, request: Request) -> dict[str, Any]:
    actor = require_reuse_access(request)
    matches = rank(payload.prompt, payload.tags, store().generations())
    intent = clean_intent(payload.intent.model_dump())
    recommendation, blockers, reason = decision(matches, intent)
    if matches:
        candidate_id = matches[0]["generation"].get("gen_id")
        commitment = prompt_commitment(payload.prompt)
        feedback = [item for item in store().feedback() if item.get("candidate_gen_id") == candidate_id and item.get("prompt_commitment_hmac_sha256") == commitment]
        if any(item.get("verdict") in {"too_similar", "never_suggest"} for item in feedback):
            recommendation, blockers, reason = "generate", [{"field":"user_feedback", "requested":"new generation", "candidate":candidate_id, "reason":"previously_rejected"}], "This exact request/candidate pair was previously rejected by the workspace."
    with receipt_chain_lock():
        receipts = store().receipts()
        receipt = create_receipt(prompt=payload.prompt, intent=intent, recommendation=recommendation, reason=reason, blockers=blockers, match=matches[0] if matches else None, previous_receipt_hash=receipts[-1]["receipt_hash"] if receipts else None)
        stored = store().save_receipt(receipt)
    event("reuse_assessed", gen_id=matches[0]["generation"].get("gen_id") if matches else None, actor=actor.label, recommendation=recommendation, receipt_id=receipt["receipt_id"], policy_version=POLICY_VERSION)
    return {
        "recommendation": recommendation,
        "matches": [{**public(item["generation"]), "similarity": item["score"], "match_type": item["match"]} for item in matches],
        "intent": intent, "policy_version": POLICY_VERSION, "blockers": blockers, "reason": reason,
        "receipt": {"receipt_id": receipt["receipt_id"], "receipt_hash": receipt["receipt_hash"], "b2_key": stored["b2_key"], "verify_url": f"/api/v1/receipts/{receipt['receipt_id']}/verify"},
        "note": "Near matches are suggestions; Recall never reuses an asset without an explicit user action.",
    }


def archive_capture(payload: CaptureRequest, actor: str) -> tuple[dict[str, Any], bool]:
    try:
        media = base64.b64decode(payload.media_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "media_base64 must be valid base64") from exc
    if len(media) > config.RECALL_MAX_CAPTURE_BYTES:
        raise HTTPException(413, f"captured media exceeds the {config.RECALL_MAX_CAPTURE_BYTES} byte relay limit")
    if not media_signature_matches(media, payload.media_type):
        raise HTTPException(415, "media bytes do not match the declared media_type")
    media_hash = sha256(media)
    for existing in store().generations():
        if existing.get("asset", {}).get("sha256") == media_hash:
            event("capture_deduplicated", gen_id=existing["gen_id"], actor=actor, asset_sha256=media_hash)
            return existing, True
    suffix = {"image/png":"png", "image/jpeg":"jpg", "image/webp":"webp", "video/mp4":"mp4", "audio/mpeg":"mp3", "audio/wav":"wav"}.get(payload.media_type, "bin")
    gen_id = f"cap_{uuid.uuid4().hex[:12]}"
    asset = store().put(f"recall/assets/{gen_id}/output.{suffix}", media, payload.media_type)
    asset["content_type"] = payload.media_type
    created = now()
    provenance = {"schema":"recall-external-capture/v1", "captured_at":created, "provider":payload.provider, "model":payload.model, "asset_sha256":media_hash, "cost_usd":payload.cost_usd, "cost_source":"caller_reported" if payload.cost_usd is not None else "unpriced", "note":"Captured after provider completion; this record is not represented as a Genblaze-generated manifest."}
    raw_key = f"recall/captures/{gen_id}.json"; store().put(raw_key, json.dumps(provenance, indent=2).encode(), "application/json")
    recipe = {"generation":gen_id, "created":created, "prompt":payload.prompt, "model":payload.model, "params":payload.params, "provider":payload.provider, "cost_usd":payload.cost_usd, "cost_source":"caller_reported" if payload.cost_usd is not None else "unpriced", "capture_provenance_key":raw_key}
    manifest_key = f"recall/manifests/{gen_id}.json"; store().put(manifest_key, json.dumps(recipe, indent=2).encode(), "application/json")
    vector=embed(payload.prompt)
    row={"gen_id":gen_id,"created":created,"modality":payload.media_type.split("/",1)[0],"prompt":payload.prompt,"provider":payload.provider,"model":payload.model,"params":payload.params,"tags":payload.tags,"genblaze":{},"provenance_kind":"external_capture","asset":asset,"manifest_key":manifest_key,"raw_manifest_key":raw_key,"cost_usd":payload.cost_usd,"cost_source":"caller_reported" if payload.cost_usd is not None else "unpriced","parent_gen_id":None,"intent":clean_intent(payload.intent.model_dump()),"media_fingerprint":image_dhash(media),"locked":False,"approval":None,"semantic":{"model":config.GOOGLE_EMBEDDING_MODEL,"embedding":vector} if vector else None}
    store().save_generation(row); event("capture", gen_id=gen_id, actor=actor, provider=payload.provider, model=payload.model, asset_sha256=media_hash, cost_usd=payload.cost_usd)
    return row, False


@app.post("/api/v1/capture")
def capture_completed_media(payload: CaptureRequest, request: Request) -> dict[str, Any]:
    """Archive a completed BYO-provider result; it never invokes a model."""
    actor = require_integration_access(request)
    row, duplicate = archive_capture(payload, actor.label)
    return {"generation":public(row), "deduplicated":duplicate, "message":"Existing asset reused without another B2 copy." if duplicate else "Captured provider output into Recall memory."}


@app.post("/api/v1/jobs/generate", status_code=status.HTTP_202_ACCEPTED)
def enqueue_generation(payload: GenerateRequest, request: Request) -> dict[str, Any]:
    actor = require_generation_access(request)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = {"job_id": job_id, "status": "queued", "created": now(), "prompt": payload.prompt, "parent_gen_id": payload.parent_gen_id, "actor": actor.label, "request": payload.model_dump()}
    store().save_job(job)
    submit_generation_job(job_id, payload, actor.label)
    return {"job_id": job_id, "status": "queued", "poll": f"/api/v1/jobs/{job_id}"}


@app.post("/api/v1/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_generation_job(job_id: str, request: Request) -> dict[str, Any]:
    previous = store().job(job_id)
    if not previous:
        raise HTTPException(404, "generation job not found")
    if previous.get("status") not in {"failed", "interrupted"}:
        raise HTTPException(409, "only failed or interrupted jobs may be retried")
    if not previous.get("request"):
        raise HTTPException(409, "this legacy job has no recoverable request snapshot")
    actor = require_generation_access(request)
    try:
        payload = GenerateRequest.model_validate(previous["request"])
    except ValidationError as exc:
        raise HTTPException(409, "stored job request is no longer valid; create a new generation request") from exc
    retry_id = f"job_{uuid.uuid4().hex[:12]}"
    retry = {"job_id": retry_id, "status": "queued", "created": now(), "prompt": payload.prompt, "parent_gen_id": payload.parent_gen_id, "actor": actor.label, "kind": "retry", "retry_of": job_id, "request": payload.model_dump()}
    store().save_job(retry)
    submit_generation_job(retry_id, payload, actor.label)
    return {"job_id": retry_id, "status": "queued", "retry_of": job_id, "poll": f"/api/v1/jobs/{retry_id}"}

@app.get("/api/v1/jobs/{job_id}")
def generation_job(job_id: str) -> dict[str, Any]:
    recover_stale_jobs()
    job = store().job(job_id)
    if not job:
        raise HTTPException(404, "generation job not found")
    if job.get("generation_id"):
        generation_row = store().generation(job["generation_id"])
        if generation_row:
            job = {**job, "generation": public(generation_row)}
        else:
            job = {**job, "status": "failed", "error": "generation record is missing from storage"}
    return job

@app.post("/api/v1/generate")
@app.post("/api/generate")
def generate(payload: GenerateRequest, request: Request) -> dict[str, Any]:
    actor = require_generation_access(request)
    try:
        row = RecallPipeline(store()).generate(
            prompt=payload.prompt,
            model=payload.model or config.RECALL_MODEL,
            params=payload.params,
            tags=payload.tags,
            parent_id=payload.parent_gen_id,
            intent=clean_intent(payload.intent.model_dump()),
        )
        event("generate", gen_id=row["gen_id"], actor=actor.label, model=row["model"], cost_usd=row.get("cost_usd"))
        return public(row)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        event("generate_failed", actor=actor.label, reason=str(exc)[:180])
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/v1/library")
@app.get("/api/library")
def library(q: str = "", tag: str = "", model: str = "", limit: int = 48, offset: int = 0) -> dict[str, Any]:
    limit = min(max(1, limit), 100)
    offset = max(0, offset)
    needle, wanted, model_filter = q.casefold().strip(), tag.casefold().strip(), model.casefold().strip()
    rows = store().generations()
    rows = [row for row in rows if not needle or needle in (row.get("prompt", "") + " " + " ".join(row.get("tags", []))).casefold()]
    rows = [row for row in rows if not wanted or wanted in [value.casefold() for value in row.get("tags", [])]]
    rows = [row for row in rows if not model_filter or model_filter in row.get("model", "").casefold()]
    return {"items": [public(row) for row in rows[offset: offset + limit]], "total": len(rows), "limit": limit, "offset": offset}


@app.get("/api/v1/gen/{gen_id}")
@app.get("/api/gen/{gen_id}")
def generation(gen_id: str) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    children = [item["gen_id"] for item in store().generations() if item.get("parent_gen_id") == gen_id]
    return {**public(row), "children": children}


@app.get("/api/v1/gen/{gen_id}/lineage")
def lineage(gen_id: str) -> dict[str, Any]:
    rows = {item["gen_id"]: item for item in store().generations()}
    row = rows.get(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    ancestors: list[dict[str, Any]] = []
    current = row
    seen: set[str] = set()
    while current.get("parent_gen_id") and current["parent_gen_id"] not in seen:
        parent_id = current["parent_gen_id"]
        seen.add(parent_id)
        parent = rows.get(parent_id)
        if not parent:
            break
        ancestors.append(public(parent))
        current = parent
    descendants = [public(item) for item in rows.values() if item.get("parent_gen_id") == gen_id]
    return {"generation": public(row), "ancestors": list(reversed(ancestors)), "children": descendants}


@app.get("/api/v1/gen/{gen_id}/verify")
def verify(gen_id: str) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    expected = row.get("asset", {}).get("sha256")
    actual: str | None = None
    asset_error: str | None = None
    try:
        data = store().get(row["asset"]["b2_key"])
        actual = hashlib.sha256(data).hexdigest()
    except Exception as exc:
        asset_error = str(exc)[:160]
    asset_matches = bool(expected and actual and hmac.compare_digest(actual, expected))
    manifest_recorded = bool(row.get("raw_manifest_key"))
    manifest_present = False
    manifest_verified = False
    manifest_error: str | None = None
    canonical_hash = row.get("genblaze", {}).get("canonical_hash")
    if manifest_recorded:
        try:
            raw_manifest = json.loads(store().get(row["raw_manifest_key"]))
            manifest_present = True
            if row.get("provenance_kind") != "external_capture":
                from genblaze import parse_manifest
                manifest = parse_manifest(raw_manifest)
                manifest_verified = bool(manifest.verify())
                canonical_hash = manifest.canonical_hash
        except Exception as exc:
            manifest_error = str(exc)[:160]
    externally_verified = asset_matches and manifest_present and row.get("provenance_kind") == "external_capture"
    return {
        "generation_id": gen_id,
        "asset_sha256": actual,
        "stored_sha256": expected,
        "asset_hash_matches": asset_matches,
        "asset_error": asset_error,
        "manifest_key_recorded": manifest_recorded,
        "manifest_present_on_b2": manifest_present,
        "manifest_verified": manifest_verified,
        "manifest_error": manifest_error,
        "canonical_manifest_hash": canonical_hash,
        "status": "verified" if asset_matches and manifest_verified else ("externally_captured_asset_verified" if externally_verified else "attention_required"),
        "provenance_kind": row.get("provenance_kind", "genblaze"),
    }


@app.get("/api/v1/gen/{gen_id}/evidence")
def evidence_bundle(gen_id: str) -> dict[str, Any]:
    """Portable, public-safe evidence for an archived generation."""
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    return {
        "schema": "recall-evidence/v1",
        "generated_at": now(),
        "generation": public(row),
        "integrity": verify(gen_id),
        "lineage": lineage(gen_id),
        "verification_instructions": [
            "Fetch the asset_url and compute SHA-256; compare it with integrity.stored_sha256.",
            "Fetch raw_manifest_url and inspect the external capture record." if row.get("provenance_kind") == "external_capture" else "Fetch raw_manifest_url and run genblaze.parse_manifest(...).verify().",
            "Use the lineage record to inspect parent/child provenance.",
        ],
        "note": "This bundle intentionally omits service credentials and semantic embedding vectors.",
    }

@app.post("/api/v1/reuse-feedback")
def reuse_feedback(payload: FeedbackRequest, request: Request) -> dict[str, Any]:
    actor = require_generation_access(request, consume_public_quota=False)
    receipt = store().receipt(payload.receipt_id)
    if not receipt:
        raise HTTPException(404, "reuse receipt not found")
    candidate = receipt.get("candidate") or {}
    if not candidate.get("generation_id"):
        raise HTTPException(409, "this receipt has no candidate to calibrate")
    feedback = {"feedback_id":f"fb_{uuid.uuid4().hex[:12]}", "created":now(), "actor":actor.label, "receipt_id":payload.receipt_id, "verdict":payload.verdict, "note":payload.note, "candidate_gen_id":candidate["generation_id"], "prompt_commitment_hmac_sha256":receipt["prompt_commitment_hmac_sha256"], "policy_version":receipt.get("policy_version")}
    store().record_feedback(feedback)
    event("reuse_feedback", gen_id=candidate["generation_id"], actor=actor.label, verdict=payload.verdict, receipt_id=payload.receipt_id)
    return {"status":"recorded", "feedback_id":feedback["feedback_id"], "message":"Future checks will honor this exact request/candidate rejection." if payload.verdict in {"too_similar", "never_suggest"} else "Feedback recorded for workspace calibration."}


@app.get("/api/v1/receipts/{receipt_id}")
def receipt(receipt_id: str) -> dict[str, Any]:
    value = store().receipt(receipt_id)
    if not value:
        raise HTTPException(404, "reuse receipt not found")
    return value


@app.get("/api/v1/receipts/{receipt_id}/verify")
def verify_reuse_receipt(receipt_id: str) -> dict[str, Any]:
    values = store().receipts()
    index = next((i for i, value in enumerate(values) if value.get("receipt_id") == receipt_id), None)
    if index is None:
        raise HTTPException(404, "reuse receipt not found")
    return {**verify_receipt(values[index], values[index - 1] if index else None), "object_lock_retention_days": config.B2_LOCK_DAYS, "storage": store().mode}


@app.post("/api/v1/gen/{gen_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun_recipe(gen_id: str, request: Request) -> dict[str, Any]:
    original = store().generation(gen_id)
    if not original:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request)
    if original.get("provenance_kind") == "external_capture":
        raise HTTPException(409, "external captures have no Genblaze recipe to re-run; create a tracked fork instead")
    payload = GenerateRequest(
        prompt=original["prompt"], model=original["model"], params=original.get("params", {}),
        tags=[*original.get("tags", [])[:19], "rerun"], parent_gen_id=gen_id, intent=IntentProfile(**original.get("intent", {})),
    )
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = {"job_id": job_id, "status": "queued", "created": now(), "prompt": payload.prompt, "parent_gen_id": gen_id, "actor": actor.label, "kind": "rerun", "source_generation_id": gen_id, "request": payload.model_dump()}
    store().save_job(job)
    submit_generation_job(job_id, payload, actor.label)
    return {"job_id": job_id, "status": "queued", "kind": "rerun", "source_generation_id": gen_id, "poll": f"/api/v1/jobs/{job_id}", "note": "This is a new paid Genblaze run using the original stored settings. Exact retrieval remains the free default."}

@app.get("/api/v1/gen/{gen_id}/replay-recipe")
@app.get("/api/gen/{gen_id}/replay-recipe")
def replay_recipe(gen_id: str) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    external = row.get("provenance_kind") == "external_capture"
    return {"generation": public(row), "manifest_url": store().url(row["raw_manifest_key"]) if row.get("raw_manifest_key") else None, "command": None if external else "genblaze replay manifest.json", "note": "External captures preserve provider provenance but have no Genblaze recipe to replay." if external else "Replay creates a new paid provider run; retrieve is Recall's exact free default."}


@app.post("/api/v1/gen/{gen_id}/reproduce")
@app.post("/api/gen/{gen_id}/reproduce")
def reproduce(gen_id: str, request: Request) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request, consume_public_quota=False)
    accounting_recorded = event("reproduce", gen_id=gen_id, actor=actor.label, avoided_cost_usd=row.get("cost_usd"))
    return {"generation": public(row), "message": "Exact stored asset retrieved - no new generation charge.", "avoided_cost_usd": row.get("cost_usd"), "accounting_recorded":accounting_recorded}


@app.post("/api/v1/gen/{gen_id}/fork")
@app.post("/api/gen/{gen_id}/fork")
def fork(gen_id: str, payload: ForkRequest, request: Request) -> dict[str, Any]:
    parent = store().generation(gen_id)
    if not parent:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request)
    try:
        row = RecallPipeline(store()).generate(prompt=payload.prompt, model=parent["model"], params={**parent.get("params", {}), **payload.params}, tags=parent.get("tags", []), parent_id=gen_id, intent=clean_intent(payload.intent.model_dump()) or parent.get("intent", {}))
        event("fork", gen_id=row["gen_id"], actor=actor.label, parent_gen_id=gen_id, cost_usd=row.get("cost_usd"))
        return public(row)
    except RuntimeError as exc:
        event("fork_failed", gen_id=gen_id, actor=actor.label, reason=str(exc)[:180])
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/v1/gen/{gen_id}/approve")
@app.post("/api/gen/{gen_id}/approve")
def approve(gen_id: str, request: Request) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request, consume_public_quota=False)
    approval = store().approve(row)
    row["approval"] = approval
    row["locked"] = approval["status"] == "locked"
    if row["locked"]:
        row["approved_asset"] = approval["asset"]
    store().save_generation(row)
    event("approve", gen_id=gen_id, actor=actor.label, locked=row["locked"])
    return public(row)


def _savings_key(active_store: RecallStore) -> tuple[str, int]:
    return (_workspace_context.get() or "root", id(active_store))


def _copy_savings(metrics: dict[str, Any]) -> dict[str, Any]:
    copied = dict(metrics)
    copied["savings_by_asset"] = dict(metrics.get("savings_by_asset", {}))
    return copied


def _calculate_savings(active_store: RecallStore) -> dict[str, Any]:
    rows, events = active_store.generations(), active_store.events()
    priced_rows = [row for row in rows if row.get("cost_usd") is not None]
    reproductions = [item for item in events if item.get("kind") == "reproduce"]
    priced_reproductions = [item for item in reproductions if item.get("avoided_cost_usd") is not None]
    by_asset: dict[str, float] = {}
    for item in priced_reproductions:
        by_asset[item.get("gen_id", "unknown")] = by_asset.get(item.get("gen_id", "unknown"), 0.0) + float(item["avoided_cost_usd"])
    total_spent = round(sum(float(row["cost_usd"]) for row in priced_rows), 4)
    total_saved = round(sum(float(item["avoided_cost_usd"]) for item in priced_reproductions), 4)
    return {
        "total_spent": total_spent,
        "total_saved": total_saved,
        "savings_multiple": round(total_saved / total_spent, 2) if total_spent else None,
        "count_reproduced": len(reproductions),
        "count_generated": len(rows),
        "unpriced_generations": len(rows) - len(priced_rows),
        "unpriced_reproductions": len(reproductions) - len(priced_reproductions),
        "savings_by_asset": {key: round(value, 4) for key, value in by_asset.items()},
    }


def _refresh_savings(active_store: RecallStore, key: tuple[str, int]) -> None:
    try:
        metrics = _calculate_savings(active_store)
        with _savings_cache_lock:
            _savings_cache[key] = (time.monotonic(), metrics)
    except Exception as exc:
        logger.warning("savings_refresh_failed error_type=%s", type(exc).__name__)
    finally:
        with _savings_cache_lock:
            _savings_refreshing.discard(key)


def _savings_snapshot(*, force: bool = False) -> dict[str, Any]:
    active_store = store()
    key = _savings_key(active_store)
    current = time.monotonic()
    with _savings_cache_lock:
        cached = _savings_cache.get(key)
        if cached and not force:
            if current - cached[0] >= _SAVINGS_REFRESH_SECONDS and key not in _savings_refreshing:
                _savings_refreshing.add(key)
                threading.Thread(target=_refresh_savings, args=(active_store, key), daemon=True, name="recall-savings-refresh").start()
            return _copy_savings(cached[1])
        _savings_refreshing.add(key)
    try:
        metrics = _calculate_savings(active_store)
        with _savings_cache_lock:
            _savings_cache[key] = (time.monotonic(), metrics)
        return _copy_savings(metrics)
    finally:
        with _savings_cache_lock:
            _savings_refreshing.discard(key)


def _apply_savings_event(kind: str, gen_id: str | None, extra: dict[str, Any]) -> None:
    if kind not in {"generate", "capture", "fork", "reproduce"}:
        return
    active_store = store()
    key = _savings_key(active_store)
    with _savings_cache_lock:
        cached = _savings_cache.get(key)
        if not cached:
            return
        metrics = _copy_savings(cached[1])
        if kind in {"generate", "capture", "fork"}:
            metrics["count_generated"] += 1
            cost = extra.get("cost_usd")
            if cost is None:
                metrics["unpriced_generations"] += 1
            else:
                metrics["total_spent"] = round(metrics["total_spent"] + float(cost), 4)
        else:
            metrics["count_reproduced"] += 1
            avoided = extra.get("avoided_cost_usd")
            if avoided is None:
                metrics["unpriced_reproductions"] += 1
            else:
                metrics["total_saved"] = round(metrics["total_saved"] + float(avoided), 4)
                asset_id = gen_id or "unknown"
                by_asset = metrics["savings_by_asset"]
                by_asset[asset_id] = round(by_asset.get(asset_id, 0.0) + float(avoided), 4)
        metrics["savings_multiple"] = round(metrics["total_saved"] / metrics["total_spent"], 2) if metrics["total_spent"] else None
        _savings_cache[key] = (time.monotonic(), metrics)



@app.get("/api/v1/savings")
@app.get("/api/savings")
def savings() -> dict[str, Any]:
    return _savings_snapshot()


@app.get("/api/v1/integration")
def integration() -> dict[str, Any]:
    return {
        "name": "Recall API",
        "version": "v1",
        "openapi": "/openapi.json",
        "authentication": "Use X-Recall-Key or Authorization: Bearer <key> for protected automation. The hosted demo also provides a tightly rate-limited public lane.",
        "flows": {
            "workspace": "POST /api/v1/workspaces (one-time workspace key; requires X-Recall-Key)",
            "reuse_check": "POST /api/v1/reuse-check",
            "feedback": "POST /api/v1/reuse-feedback",
            "capture": "POST /api/v1/capture",
            "generate": "POST /api/v1/generate",
            "retrieve": "POST /api/v1/gen/{id}/reproduce",
            "fork": "POST /api/v1/gen/{id}/fork",
            "verify": "GET /api/v1/gen/{id}/verify",
            "approve": "POST /api/v1/gen/{id}/approve",
        },
    }


@app.get("/api/object/{key:path}")
def object_file(key: str, expires: int = 0, signature: str = "") -> Response:
    if root_store().mode != "local":
        raise HTTPException(404, "object proxy is available only in local mode")
    current = int(dt.datetime.now(dt.timezone.utc).timestamp())
    expected = hmac.new(config.RECALL_RECEIPT_SECRET.encode(), f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
    if expires < current or expires > current + 3600 or not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(403, "object URL is invalid or expired")
    try:
        return Response(root_store().get(key), media_type=mimetypes.guess_type(key)[0] or "application/octet-stream")
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "object not found")


@app.get("/gen/{gen_id}", response_class=HTMLResponse)
def certificate_page(gen_id: str) -> str:
    if not store().generation(gen_id):
        raise HTTPException(404, "generation not found")
    return (FRONTEND / "certificate.html").read_text(encoding="utf-8")

@app.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    try:
        metrics = savings()
    except Exception:
        logger.exception("Landing metrics could not be rendered")
        for token in ("{{RECALL_TOTAL_SPENT}}", "{{RECALL_TOTAL_SAVED}}", "{{RECALL_ASSET_COUNT}}", "{{RECALL_SAVINGS_MULTIPLE}}"):
            html = html.replace(token, "Unavailable")
        return html
    replacements = {
        "{{RECALL_TOTAL_SPENT}}": f"${metrics['total_spent']:.2f}",
        "{{RECALL_TOTAL_SAVED}}": f"${metrics['total_saved']:.2f}",
        "{{RECALL_ASSET_COUNT}}": str(metrics["count_generated"]),
        "{{RECALL_SAVINGS_MULTIPLE}}": f"{metrics['savings_multiple']:.2f}x" if metrics["savings_multiple"] is not None else "Not available",
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html


@app.get("/app", response_class=HTMLResponse)
def app_page() -> str:
    return (FRONTEND / "app.html").read_text(encoding="utf-8")
