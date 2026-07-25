from __future__ import annotations

import base64
import contextvars
import binascii
import hashlib
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
import mimetypes
import secrets
import hmac
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import config
from .pipeline import RecallPipeline
from .reuse import rank
from .policy import POLICY_VERSION, clean_intent, decision
from .ledger import create_receipt, verify_receipt, prompt_commitment
from .semantic import embed
from .media import image_dhash, sha256
from .security import require_generation_access, require_integration_access, require_private_api_key
from .storage import RecallStore, now

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
app = FastAPI(
    title="Recall API",
    version="1.0.0",
    description="A provenance-first reusable generation memory powered by Genblaze and Backblaze B2.",
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
    return response


_store: RecallStore | None = None
_workspace_stores: dict[str, RecallStore] = {}
_workspace_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("recall_workspace", default=None)
_jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix='recall-generation')


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
        _workspace_stores[workspace_id] = RecallStore(workspace_id)
    return _workspace_stores[workspace_id]


@app.middleware("http")
async def workspace_scope(request: Request, call_next: Any) -> Response:
    workspace_id = request.headers.get("x-recall-workspace", "").strip()
    if not workspace_id:
        return await call_next(request)
    token = request.headers.get("x-recall-workspace-key", "").strip()
    record = root_store().workspace(workspace_id)
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


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)
    parent_gen_id: str | None = None
    intent: IntentProfile = Field(default_factory=IntentProfile)


class ReuseRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    intent: IntentProfile = Field(default_factory=IntentProfile)


class ForkRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    params: dict[str, Any] = Field(default_factory=dict)
    intent: IntentProfile = Field(default_factory=IntentProfile)


class CaptureRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    media_base64: str = Field(min_length=4, max_length=28_000_000)
    media_type: str = Field(default="image/png", max_length=120)
    provider: str = Field(default="external", max_length=80)
    model: str = Field(default="external", max_length=160)
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)
    intent: IntentProfile = Field(default_factory=IntentProfile)
    cost_usd: float | None = Field(default=None, ge=0, le=100_000)


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
    data["asset_url"] = store().url(row["asset"]["b2_key"])
    data["manifest_url"] = store().url(row["manifest_key"])
    data["raw_manifest_url"] = store().url(row["raw_manifest_key"]) if row.get("raw_manifest_key") else None
    return data


def event(kind: str, *, gen_id: str | None = None, actor: str | None = None, **extra: Any) -> None:
    store().record_event({"event_id": f"evt_{uuid.uuid4().hex[:12]}", "created": now(), "kind": kind, "gen_id": gen_id, "actor": actor, **extra})


def _run_job(job_id: str, payload: GenerateRequest, actor: str) -> None:
    job = store().job(job_id) or {"job_id": job_id}
    job.update({"status": "running", "started": now()})
    store().save_job(job)
    try:
        row = RecallPipeline(store()).generate(prompt=payload.prompt, model=payload.model or config.RECALL_MODEL, params=payload.params, tags=payload.tags, parent_id=payload.parent_gen_id, intent=clean_intent(payload.intent.model_dump()))
        event("generate", gen_id=row["gen_id"], actor=actor, model=row["model"], cost_usd=row.get("cost_usd"), job_id=job_id)
        job.update({"status": "completed", "completed": now(), "generation_id": row["gen_id"]})
    except Exception as exc:
        event("generate_failed", actor=actor, job_id=job_id, reason=str(exc)[:180])
        job.update({"status": "failed", "completed": now(), "error": str(exc)[:240]})
    store().save_job(job)

def recover_stale_jobs() -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=config.RECALL_JOB_STALE_MINUTES)
    recovered = 0
    for job in store().jobs():
        if job.get("status") not in {"queued", "running"}:
            continue
        stamp = job.get("started") or job.get("created")
        try:
            created = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except Exception:
            continue
        if created < cutoff:
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
        "api_key_access": bool(config.RECALL_API_KEYS),
    }


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    if store().mode != "b2":
        raise HTTPException(503, "B2 archive is not configured")
    if not config.has_generation_provider:
        raise HTTPException(503, "Generation provider is not configured")
    recovered_jobs = recover_stale_jobs()
    try:
        store().list_keys("recall/index/")
    except Exception as exc:
        raise HTTPException(503, f"B2 archive check failed: {str(exc)[:120]}") from exc
    return {"ok": True, "checks": {"b2": "reachable", "generation_provider": "configured"}, "recovered_interrupted_jobs": recovered_jobs}


@app.post("/api/v1/reuse-check")
@app.post("/api/reuse-check")
def reuse_check(request: ReuseRequest) -> dict[str, Any]:
    matches = rank(request.prompt, request.tags, store().generations())
    intent = clean_intent(request.intent.model_dump())
    recommendation, blockers, reason = decision(matches, intent)
    if matches:
        candidate_id = matches[0]["generation"].get("gen_id")
        commitment = prompt_commitment(request.prompt)
        feedback = [item for item in store().feedback() if item.get("candidate_gen_id") == candidate_id and item.get("prompt_commitment_hmac_sha256") == commitment]
        if any(item.get("verdict") in {"too_similar", "never_suggest"} for item in feedback):
            recommendation, blockers, reason = "generate", [{"field":"user_feedback", "requested":"new generation", "candidate":candidate_id, "reason":"previously_rejected"}], "This exact request/candidate pair was previously rejected by the workspace."
    receipts = store().receipts()
    receipt = create_receipt(prompt=request.prompt, intent=intent, recommendation=recommendation, reason=reason, blockers=blockers, match=matches[0] if matches else None, previous_receipt_hash=receipts[-1]["receipt_hash"] if receipts else None)
    stored = store().save_receipt(receipt)
    event("reuse_assessed", gen_id=matches[0]["generation"].get("gen_id") if matches else None, recommendation=recommendation, receipt_id=receipt["receipt_id"], policy_version=POLICY_VERSION)
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
    if len(media) > 18 * 1024 * 1024:
        raise HTTPException(413, "captured media exceeds the 18 MB relay limit")
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
    _jobs.submit(_run_job, job_id, payload, actor.label)
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
    payload = GenerateRequest.model_validate(previous["request"])
    retry_id = f"job_{uuid.uuid4().hex[:12]}"
    retry = {"job_id": retry_id, "status": "queued", "created": now(), "prompt": payload.prompt, "parent_gen_id": payload.parent_gen_id, "actor": actor.label, "kind": "retry", "retry_of": job_id, "request": payload.model_dump()}
    store().save_job(retry)
    _jobs.submit(_run_job, retry_id, payload, actor.label)
    return {"job_id": retry_id, "status": "queued", "retry_of": job_id, "poll": f"/api/v1/jobs/{retry_id}"}

@app.get("/api/v1/jobs/{job_id}")
def generation_job(job_id: str) -> dict[str, Any]:
    recover_stale_jobs()
    job = store().job(job_id)
    if not job:
        raise HTTPException(404, "generation job not found")
    if job.get("generation_id"):
        job = {**job, "generation": public(store().generation(job["generation_id"]))}
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
    data = store().get(row["asset"]["b2_key"])
    actual = hashlib.sha256(data).hexdigest()
    expected = row["asset"].get("sha256")
    manifest_present = bool(row.get("raw_manifest_key"))
    manifest_verified = False
    manifest_error = None
    canonical_hash = row.get("genblaze", {}).get("canonical_hash")
    if manifest_present:
        try:
            from genblaze import parse_manifest
            raw_manifest = json.loads(store().get(row["raw_manifest_key"]))
            manifest = parse_manifest(raw_manifest)
            manifest_verified = bool(manifest.verify())
            canonical_hash = manifest.canonical_hash
        except Exception as exc:
            manifest_error = str(exc)[:160]
    return {
        "generation_id": gen_id,
        "asset_sha256": actual,
        "stored_sha256": expected,
        "asset_hash_matches": actual == expected,
        "manifest_present_on_b2": manifest_present,
        "manifest_verified": manifest_verified,
        "manifest_error": manifest_error,
        "canonical_manifest_hash": canonical_hash,
        "status": "verified" if actual == expected and manifest_verified else ("externally_captured_asset_verified" if actual == expected and row.get("provenance_kind") == "external_capture" else "attention_required"),
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
            "Fetch the asset_url and compute SHA-256; compare it with integrity.expected_sha256.",
            "Fetch raw_manifest_url and run genblaze.parse_manifest(...).verify().",
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
    payload = GenerateRequest(
        prompt=original["prompt"], model=original["model"], params=original.get("params", {}),
        tags=[*original.get("tags", []), "rerun"], parent_gen_id=gen_id, intent=IntentProfile(**original.get("intent", {})),
    )
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = {"job_id": job_id, "status": "queued", "created": now(), "prompt": payload.prompt, "parent_gen_id": gen_id, "actor": actor.label, "kind": "rerun", "source_generation_id": gen_id, "request": payload.model_dump()}
    store().save_job(job)
    _jobs.submit(_run_job, job_id, payload, actor.label)
    return {"job_id": job_id, "status": "queued", "kind": "rerun", "source_generation_id": gen_id, "poll": f"/api/v1/jobs/{job_id}", "note": "This is a new paid Genblaze run using the original stored settings. Exact retrieval remains the free default."}

@app.get("/api/v1/gen/{gen_id}/replay-recipe")
@app.get("/api/gen/{gen_id}/replay-recipe")
def replay_recipe(gen_id: str) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    return {"generation": public(row), "manifest_url": store().url(row["raw_manifest_key"]), "command": "genblaze replay manifest.json", "note": "Replay creates a new paid provider run; retrieve is Recall's exact free default."}


@app.post("/api/v1/gen/{gen_id}/reproduce")
@app.post("/api/gen/{gen_id}/reproduce")
def reproduce(gen_id: str, request: Request) -> dict[str, Any]:
    row = store().generation(gen_id)
    if not row:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request, consume_public_quota=False)
    event("reproduce", gen_id=gen_id, actor=actor.label, avoided_cost_usd=row.get("cost_usd"))
    return {"generation": public(row), "message": "Exact stored asset retrieved - no new generation charge.", "avoided_cost_usd": row.get("cost_usd")}


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
    row["locked"] = approval["status"] in {"locked", "local-approved"}
    if row["locked"]:
        row["approved_asset"] = approval["asset"]
    store().save_generation(row)
    event("approve", gen_id=gen_id, actor=actor.label, locked=row["locked"])
    return public(row)


@app.get("/api/v1/savings")
@app.get("/api/savings")
def savings() -> dict[str, Any]:
    rows, events = store().generations(), store().events()
    priced_rows = [row for row in rows if row.get("cost_usd") is not None]
    priced_events = [item for item in events if item.get("avoided_cost_usd") is not None]
    by_asset: dict[str, float] = {}
    for item in priced_events:
        by_asset[item.get("gen_id", "unknown")] = by_asset.get(item.get("gen_id", "unknown"), 0.0) + float(item["avoided_cost_usd"])
    return {
        "total_spent": round(sum(float(row["cost_usd"]) for row in priced_rows), 4),
        "total_saved": round(sum(float(item["avoided_cost_usd"]) for item in priced_events), 4),
        "count_reproduced": sum(item.get("kind") == "reproduce" for item in events),
        "count_generated": len(rows),
        "unpriced_generations": len(rows) - len(priced_rows),
        "unpriced_reproductions": len(events) - len(priced_events),
        "savings_by_asset": {key: round(value, 4) for key, value in by_asset.items()},
    }


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
def object_file(key: str) -> Response:
    try:
        return Response(store().get(key), media_type=mimetypes.guess_type(key)[0] or "application/octet-stream")
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "object not found")


@app.get("/gen/{gen_id}", response_class=HTMLResponse)
def certificate_page(gen_id: str) -> str:
    if not store().generation(gen_id):
        raise HTTPException(404, "generation not found")
    return (FRONTEND / "certificate.html").read_text(encoding="utf-8")

@app.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


@app.get("/app", response_class=HTMLResponse)
def app_page() -> str:
    return (FRONTEND / "app.html").read_text(encoding="utf-8")
