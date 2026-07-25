from __future__ import annotations

import hashlib
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import config
from .pipeline import RecallPipeline
from .reuse import rank
from .security import require_generation_access
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
    allow_headers=["Content-Type", "Authorization", "X-Recall-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


_store: RecallStore | None = None
_jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix='recall-generation')


def store() -> RecallStore:
    global _store
    if _store is None:
        _store = RecallStore()
    return _store


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)
    parent_gen_id: str | None = None


class ReuseRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class ForkRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    params: dict[str, Any] = Field(default_factory=dict)


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
        row = RecallPipeline(store()).generate(prompt=payload.prompt, model=payload.model or config.RECALL_MODEL, params=payload.params, tags=payload.tags, parent_id=payload.parent_gen_id)
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
    return {
        "recommendation": "reuse" if matches and matches[0]["score"] >= 0.7 else "generate",
        "matches": [{**public(item["generation"]), "similarity": item["score"], "match_type": item["match"]} for item in matches],
        "note": "Near matches are suggestions; Recall never reuses an asset without an explicit user action.",
    }


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
        "status": "verified" if actual == expected and manifest_verified else "attention_required",
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

@app.post("/api/v1/gen/{gen_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun_recipe(gen_id: str, request: Request) -> dict[str, Any]:
    original = store().generation(gen_id)
    if not original:
        raise HTTPException(404, "generation not found")
    actor = require_generation_access(request)
    payload = GenerateRequest(
        prompt=original["prompt"], model=original["model"], params=original.get("params", {}),
        tags=[*original.get("tags", []), "rerun"], parent_gen_id=gen_id,
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
        row = RecallPipeline(store()).generate(prompt=payload.prompt, model=parent["model"], params={**parent.get("params", {}), **payload.params}, tags=parent.get("tags", []), parent_id=gen_id)
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
            "reuse_check": "POST /api/v1/reuse-check",
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