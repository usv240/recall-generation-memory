from __future__ import annotations

import base64
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.reuse import rank
from backend.app.storage import RecallStore
from backend.app.config import config
from backend.app.ledger import verify_receipt
from backend.app.security import limiter
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
import recall_relay as relay_module
from recall_relay import RecallRelay, RecallRelayError


class MemoryStore:
    mode = "b2"
    def __init__(self):
        self.objects = {}
        self._events = []
        self._feedback = []
        self._workspaces = {}
    def put(self, key, data, content_type="application/octet-stream", **kwargs):
        self.objects[key] = data
        return {"b2_key": key, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    def get(self, key): return self.objects[key]
    def url(self, key): return f"https://example.test/{key}"
    def list_keys(self, prefix): return [key for key in self.objects if key.startswith(prefix)]
    def save_generation(self, row): self.objects[f"recall/index/runs/{row['gen_id']}.json"] = json.dumps(row).encode()
    def generation(self, gen_id):
        value = self.objects.get(f"recall/index/runs/{gen_id}.json")
        return json.loads(value) if value else None
    def generations(self):
        return sorted([json.loads(value) for key, value in self.objects.items() if key.startswith("recall/index/runs/")], key=lambda row: row["created"], reverse=True)
    def record_event(self, row): self._events.append(row)
    def save_receipt(self, row):
        key=f"recall/ledger/{row['receipt_id']}.json"; self.objects[key]=json.dumps(row).encode()
        return {"b2_key":key, "sha256":hashlib.sha256(self.objects[key]).hexdigest(), "bytes":len(self.objects[key])}
    def receipt(self, receipt_id):
        value=self.objects.get(f"recall/ledger/{receipt_id}.json")
        return json.loads(value) if value else None
    def receipts(self):
        return sorted([json.loads(value) for key, value in self.objects.items() if key.startswith("recall/ledger/")], key=lambda row: row["created"])
    def save_job(self, row): self.objects[f"recall/index/jobs/{row['job_id']}.json"] = json.dumps(row).encode()
    def jobs(self):
        return [json.loads(value) for key, value in self.objects.items() if key.startswith("recall/index/jobs/")]
    def job(self, job_id):
        value = self.objects.get(f"recall/index/jobs/{job_id}.json")
        return json.loads(value) if value else None
    def events(self): return self._events
    def record_feedback(self, row): self._feedback.append(row)
    def feedback(self): return self._feedback
    def save_workspace(self, row): self._workspaces[row["workspace_id"]] = row
    def workspace(self, workspace_id): return self._workspaces.get(workspace_id)
    def approve(self, row): return {"status":"locked", "asset": row["asset"], "retention_days":30}


def sample(store):
    asset = store.put("recall/assets/gen_demo/output.jpg", b"demo", "image/jpeg")
    row = {"gen_id":"gen_demo","created":"2026-01-01T00:00:00+00:00","prompt":"blue launch hero","model":"gemini-image","provider":"google","params":{},"tags":["hero","blue"],"asset":asset,"manifest_key":"recall/manifests/gen_demo.json","raw_manifest_key":"recall/genblaze-manifests/gen_demo.json","genblaze":{"canonical_hash":"abc"},"cost_usd":0.067,"parent_gen_id":None,"locked":False,"approval":None}
    store.put(row["raw_manifest_key"], b"{}", "application/json")
    store.put(row["manifest_key"], b"{}", "application/json")
    store.save_generation(row)
    return row


def test_reuse_exact_match_is_explicit():
    rows = [{"prompt":"Blue launch hero", "tags":[], "created":"2026"}]
    result = rank("blue launch hero", [], rows)
    assert result[0]["match"] == "exact"
    assert result[0]["score"] == 1.0


def test_integrity_and_free_retrieval_are_end_to_end(monkeypatch):
    memory = MemoryStore(); sample(memory); main._store = memory
    client = TestClient(main.app)
    verified = client.get("/api/v1/gen/gen_demo/verify")
    assert verified.status_code == 200
    assert verified.json()["asset_hash_matches"] is True
    assert verified.json()["manifest_present_on_b2"] is True
    retrieved = client.post("/api/v1/gen/gen_demo/reproduce")
    assert retrieved.status_code == 200
    assert retrieved.json()["avoided_cost_usd"] == 0.067
    totals = client.get("/api/v1/savings").json()
    assert totals["total_saved"] == 0.067

def test_generation_job_records_completion(monkeypatch):
    memory = MemoryStore(); row = sample(memory); main._store = memory
    monkeypatch.setattr(main._jobs, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(main.RecallPipeline, "generate", lambda self, **kwargs: row)
    client = TestClient(main.app)
    created = client.post("/api/v1/jobs/generate", json={"prompt":"new hero", "tags":["hero"]})
    assert created.status_code == 202
    job = client.get(created.json()["poll"])
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json()["generation_id"] == "gen_demo"

def test_intent_firewall_blocks_similar_but_wrong_brand_and_receipt_verifies():
    memory = MemoryStore(); sample(memory); main._store = memory
    client = TestClient(main.app)
    allowed = client.post("/api/v1/reuse-check", json={"prompt":"blue launch hero", "tags":["hero"]})
    assert allowed.status_code == 200
    assert allowed.json()["recommendation"] == "reuse"
    receipt_id = allowed.json()["receipt"]["receipt_id"]
    assert client.get(f"/api/v1/receipts/{receipt_id}/verify").json()["status"] == "verified"
    blocked = client.post("/api/v1/reuse-check", json={"prompt":"blue launch hero", "intent":{"brand":"Different Brand"}})
    assert blocked.status_code == 200
    assert blocked.json()["recommendation"] == "generate"
    assert blocked.json()["blockers"][0]["field"] == "brand"


def test_external_capture_deduplicates_and_is_honest_about_provenance(monkeypatch):
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(main, "embed", lambda prompt: None)
    monkeypatch.setattr(config, "RECALL_API_KEYS", ["test-integration-key"])
    client = TestClient(main.app)
    payload = {"prompt":"outside workflow hero", "media_base64":base64.b64encode(b"\x89PNG\r\n\x1a\nexternal media bytes").decode(), "media_type":"image/png", "provider":"bring-your-own", "model":"model-x", "cost_usd":0.12}
    headers = {"X-Recall-Key":"test-integration-key"}
    first = client.post("/api/v1/capture", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["deduplicated"] is False
    generation = first.json()["generation"]
    assert generation["provenance_kind"] == "external_capture"
    assert generation["cost_usd"] == 0.12
    assert generation["cost_source"] == "caller_reported"
    duplicate = client.post("/api/v1/capture", json=payload, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    checked = client.get(f"/api/v1/gen/{generation['gen_id']}/verify").json()
    assert checked["status"] == "externally_captured_asset_verified"
    retrieved = client.post(f"/api/v1/gen/{generation['gen_id']}/reproduce").json()
    assert retrieved["avoided_cost_usd"] == 0.12
    assert client.get("/api/v1/savings").json()["total_saved"] == 0.12


def test_feedback_prevents_the_same_rejected_prompt_candidate_pair(monkeypatch):
    memory = MemoryStore(); sample(memory); main._store = memory
    client = TestClient(main.app)
    payload = {"prompt":"blue launch hero"}
    recommendation = client.post("/api/v1/reuse-check", json=payload).json()
    response = client.post("/api/v1/reuse-feedback", json={"receipt_id":recommendation["receipt"]["receipt_id"], "verdict":"never_suggest"})
    assert response.status_code == 200
    retried = client.post("/api/v1/reuse-check", json=payload).json()
    assert retried["recommendation"] == "generate"
    assert retried["blockers"][0]["reason"] == "previously_rejected"


def test_workspace_store_is_prefix_isolated_locally(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "B2_KEY_ID", None)
    monkeypatch.setattr(config, "B2_APP_KEY", None)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    alpha, beta = RecallStore("ws-alpha"), RecallStore("ws-beta")
    alpha.put("recall/index/runs/only-alpha.json", b"alpha", "application/json")
    beta.put("recall/index/runs/only-beta.json", b"beta", "application/json")
    assert alpha.get("recall/index/runs/only-alpha.json") == b"alpha"
    assert "recall/index/runs/only-alpha.json" in alpha.list_keys("recall/index/runs")
    assert "recall/index/runs/only-alpha.json" not in beta.list_keys("recall/index/runs")


def test_openai_relay_does_not_call_provider_on_reuse(monkeypatch):
    relay = RecallRelay("https://recall.example", openai_key="local-only-key", workspace_id="ws-example", workspace_key="workspace-secret")
    calls = []
    def fake_post(url, body, headers=None):
        calls.append(url)
        if url.endswith("/reuse-check"):
            return {"recommendation":"reuse", "matches":[{"gen_id":"gen_existing","asset_url":"https://asset.example"}], "receipt":{"receipt_id":"rr_existing"}}
        return {"generation":{"gen_id":"gen_existing","asset_url":"https://asset.example"}, "avoided_cost_usd":0.25}
    monkeypatch.setattr(relay, "_post", fake_post)
    result = relay.generate_openai("same creative request")
    assert result.status == "reused"
    assert calls == ["https://recall.example/api/v1/reuse-check", "https://recall.example/api/v1/gen/gen_existing/reproduce"]


def test_custom_relay_skips_provider_on_reuse(monkeypatch):
    relay = RecallRelay("https://recall.example", workspace_id="ws-example", workspace_key="workspace-secret")
    calls = []
    def fake_post(url, body, headers=None):
        calls.append(url)
        if url.endswith("/reuse-check"):
            return {"recommendation":"reuse", "matches":[{"gen_id":"gen_existing"}], "receipt":{"receipt_id":"rr_existing"}}
        return {"generation":{"gen_id":"gen_existing"}, "avoided_cost_usd":0.42}
    monkeypatch.setattr(relay, "_post", fake_post)
    invoked = []
    result = relay.generate_with("same creative request", lambda prompt: invoked.append(prompt) or b"new-media", provider="custom", model="model-x")
    assert result.status == "reused"
    assert invoked == []
    assert calls[-1].endswith("/gen/gen_existing/reproduce")


def test_custom_relay_captures_only_after_safe_miss(monkeypatch):
    relay = RecallRelay("https://recall.example", workspace_id="ws-example", workspace_key="workspace-secret")
    calls = []
    def fake_post(url, body, headers=None):
        calls.append((url, body))
        if url.endswith("/reuse-check"):
            return {"recommendation":"generate", "matches":[], "receipt":{"receipt_id":"rr_miss"}}
        return {"generation":{"gen_id":"cap_new", "asset_url":"https://asset.example"}}
    monkeypatch.setattr(relay, "_post", fake_post)
    result = relay.generate_with("new creative request", lambda prompt: b"provider-media", provider="custom", model="model-x", tags=["launch"], cost_usd=0.42)
    assert result.status == "generated_and_captured"
    assert result.media == b"provider-media"
    assert calls[1][1]["provider"] == "custom"
    assert calls[1][1]["params"]["relay"] == "custom-provider"
    assert calls[1][1]["cost_usd"] == 0.42

def test_external_capture_rejects_public_uploads(monkeypatch):
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(config, "RECALL_API_KEYS", ["test-integration-key"])
    client = TestClient(main.app)
    payload = {"prompt":"unauthorized upload", "media_base64":base64.b64encode(b"bytes").decode()}
    response = client.post("/api/v1/capture", json=payload)
    assert response.status_code == 401
    assert memory.generations() == []

def test_custom_relay_rejects_invalid_cost_before_provider():
    relay = RecallRelay("https://recall.example", workspace_id="ws-example", workspace_key="workspace-secret")
    invoked = []
    try:
        relay.generate_with("new request", lambda prompt: invoked.append(prompt) or b"bytes", provider="custom", model="model-x", cost_usd=-0.01)
    except ValueError as exc:
        assert "cost_usd" in str(exc)
    else:
        raise AssertionError("negative cost should be rejected")
    assert invoked == []

def test_relay_http_error_is_actionable_and_secret_safe(monkeypatch):
    relay = RecallRelay("https://recall.example", recall_key="never-print-this-key")
    def fail(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(b'{"detail":"Invalid Recall integration key"}'))
    monkeypatch.setattr(relay_module, "urlopen", fail)
    try:
        relay._check("test prompt", [], {})
    except RecallRelayError as exc:
        assert exc.status == 401
        assert "Invalid Recall integration key" in str(exc)
        assert "never-print-this-key" not in str(exc)
    else:
        raise AssertionError("HTTP errors must become RecallRelayError")

def test_background_job_preserves_private_workspace_scope(monkeypatch):
    root, workspace = MemoryStore(), MemoryStore()
    workspace_id, workspace_key = "ws-private", "private-workspace-key"
    root.save_workspace({"workspace_id":workspace_id, "key_sha256":hashlib.sha256(workspace_key.encode()).hexdigest()})
    main._store = root
    monkeypatch.setitem(main._workspace_stores, workspace_id, workspace)

    def submit_without_context(fn, *args):
        marker = main._workspace_context.set(None)
        try:
            return fn(*args)
        finally:
            main._workspace_context.reset(marker)

    def generate_for_workspace(pipeline, **kwargs):
        assert pipeline.store is workspace
        return sample(workspace)

    monkeypatch.setattr(main._jobs, "submit", submit_without_context)
    monkeypatch.setattr(main.RecallPipeline, "generate", generate_for_workspace)
    headers = {"X-Recall-Workspace":workspace_id, "X-Recall-Workspace-Key":workspace_key}
    client = TestClient(main.app)
    created = client.post("/api/v1/jobs/generate", json={"prompt":"private launch asset"}, headers=headers)
    assert created.status_code == 202
    job = client.get(created.json()["poll"], headers=headers)
    assert job.json()["status"] == "completed"
    assert workspace.generation("gen_demo") is not None
    assert root.generation("gen_demo") is None
    assert root.job(created.json()["job_id"]) is None


def test_object_lock_request_never_falls_back_to_unlocked_write():
    class RejectingRemote:
        def __init__(self): self.calls = []
        def get(self, key): return b"approved-bytes"
        def put(self, key, data, **kwargs):
            self.calls.append(kwargs)
            if "object_lock" in kwargs:
                raise TypeError("unsupported object lock")

    target = object.__new__(RecallStore)
    target.workspace_id = None
    target._remote = RejectingRemote()
    row = {"gen_id":"gen_lock", "asset":{"b2_key":"recall/assets/gen_lock/output.png", "content_type":"image/png"}}
    result = target.approve(row)
    assert result["status"] == "requires-object-lock"
    assert len(target._remote.calls) <= 1


def test_b2_listing_paginates_and_rejects_repeated_tokens():
    class PagedRemote:
        def __init__(self): self.tokens = []
        def list(self, prefix, *, max_keys, continuation_token):
            self.tokens.append(continuation_token)
            if continuation_token is None:
                return SimpleNamespace(entries=[SimpleNamespace(key="recall/index/runs/a.json")], next_token="page-2")
            return SimpleNamespace(entries=[{"key":"recall/index/runs/b.json"}], next_token=None)

    target = object.__new__(RecallStore)
    target.workspace_id = None
    target._remote = PagedRemote()
    assert target.list_keys("recall/index/runs") == ["recall/index/runs/a.json", "recall/index/runs/b.json"]
    assert target._remote.tokens == [None, "page-2"]

    class BrokenRemote:
        def list(self, prefix, *, max_keys, continuation_token):
            return SimpleNamespace(entries=[], next_token="same-token")
    target._remote = BrokenRemote()
    try:
        target.list_keys("recall/index/runs")
    except RuntimeError as exc:
        assert "repeated continuation token" in str(exc)
    else:
        raise AssertionError("repeated pagination tokens must fail closed")


def test_receipt_chain_remains_linear_under_concurrent_checks():
    memory = MemoryStore(); sample(memory); main._store = memory
    client = TestClient(main.app)
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda index: client.post("/api/v1/reuse-check", json={"prompt":f"blue launch hero {index}"}), range(12)))
    assert all(response.status_code == 200 for response in responses)
    receipts = memory.receipts()
    assert len(receipts) == 12
    for index, receipt in enumerate(receipts):
        previous = receipts[index - 1] if index else None
        assert verify_receipt(receipt, previous)["status"] == "verified"


def test_savings_ignores_unrelated_unpriced_events():
    memory = MemoryStore(); sample(memory); main._store = memory
    memory.record_event({"kind":"generate", "gen_id":"gen_demo"})
    memory.record_event({"kind":"approve", "gen_id":"gen_demo"})
    memory.record_event({"kind":"reproduce", "gen_id":"gen_demo", "avoided_cost_usd":None})
    result = TestClient(main.app).get("/api/v1/savings").json()
    assert result["count_reproduced"] == 1
    assert result["unpriced_reproductions"] == 1


def test_capture_and_params_validation_fail_before_work(monkeypatch):
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(config, "RECALL_API_KEYS", ["validation-key"])
    client = TestClient(main.app)
    headers = {"X-Recall-Key":"validation-key"}
    unsupported = client.post("/api/v1/capture", headers=headers, json={"prompt":"unsupported media", "media_base64":base64.b64encode(b"data").decode(), "media_type":"text/html"})
    assert unsupported.status_code == 422
    mismatch = client.post("/api/v1/capture", headers=headers, json={"prompt":"mislabeled media", "media_base64":base64.b64encode(b"not a png").decode(), "media_type":"image/png"})
    assert mismatch.status_code == 415
    secret = client.post("/api/v1/jobs/generate", headers=headers, json={"prompt":"unsafe params", "params":{"nested":{"api_key":"must-not-store"}}})
    assert secret.status_code == 422
    long_tag = client.post("/api/v1/reuse-check", json={"prompt":"tag validation", "tags":["x" * 81]})
    assert long_tag.status_code == 422
    assert memory.generations() == []


def test_public_reuse_checks_are_rate_limited(monkeypatch):
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(config, "RECALL_PUBLIC_REUSE_CHECKS_PER_HOUR", 1)
    with limiter._lock:
        limiter._hits.pop("reuse:198.51.100.77", None)
    client = TestClient(main.app)
    headers = {"X-Forwarded-For":"198.51.100.77"}
    assert client.post("/api/v1/reuse-check", headers=headers, json={"prompt":"first public check"}).status_code == 200
    assert client.post("/api/v1/reuse-check", headers=headers, json={"prompt":"second public check"}).status_code == 429


def test_integrity_endpoint_reports_missing_asset_without_500():
    memory = MemoryStore(); sample(memory); main._store = memory
    del memory.objects["recall/assets/gen_demo/output.jpg"]
    response = TestClient(main.app).get("/api/v1/gen/gen_demo/verify")
    assert response.status_code == 200
    assert response.json()["status"] == "attention_required"
    assert response.json()["asset_hash_matches"] is False
    assert response.json()["asset_error"]


def test_security_headers_cover_clickjacking_csp_and_https():
    memory = MemoryStore(); main._store = memory
    response = TestClient(main.app).get("/api/health", headers={"X-Forwarded-Proto":"https"})
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_relay_never_sends_recall_workspace_credentials_to_provider(monkeypatch):
    relay = RecallRelay("https://recall.example", workspace_id="ws-example", workspace_key="workspace-secret")
    seen = []
    def respond(request, timeout):
        seen.append(dict(request.header_items()))
        return BytesIO(b'{}')
    monkeypatch.setattr(relay_module, "urlopen", respond)
    relay._post("https://provider.example/generate", {"prompt":"hello"})
    relay._post("https://recall.example/api/v1/reuse-check", {"prompt":"hello"})
    provider_headers = {key.casefold():value for key,value in seen[0].items()}
    recall_headers = {key.casefold():value for key,value in seen[1].items()}
    assert "x-recall-workspace-key" not in provider_headers
    assert recall_headers["x-recall-workspace-key"] == "workspace-secret"


def test_relay_rejects_oversized_media_before_capture(monkeypatch):
    relay = RecallRelay("https://recall.example", workspace_id="ws-example", workspace_key="workspace-secret", max_media_bytes=4)
    monkeypatch.setattr(relay, "_check", lambda prompt, tags, intent: {"recommendation":"generate", "receipt":{}})
    monkeypatch.setattr(relay, "_capture", lambda **kwargs: (_ for _ in ()).throw(AssertionError("capture should not run")))
    try:
        relay.generate_with("new request", lambda prompt: b"12345", provider="custom", model="model-x")
    except ValueError as exc:
        assert "max_media_bytes" in str(exc)
    else:
        raise AssertionError("oversized media must fail before upload")


def test_frontend_has_no_dead_end_reuse_gate_or_encoding_regression():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    source = (frontend / "app.html").read_text(encoding="utf-8")
    assert "Generate a new asset" in source
    assert "scheduleLoad()" in source
    assert 'loading="lazy"' in source
    assert "mediaPreview" in source and "<video" in source and "<audio" in source
    assert "GENERATE AGAIN" in source and "$0.00 model cost" in source
    assert "Retrieve exact original" in source and "Paid recipe replay" in source
    landing = (frontend / "index.html").read_text(encoding="utf-8")
    assert "A folder waits until after you remember" in landing
    assert "fetch('/api/v1/savings')" in landing
    assert not any(marker in source for marker in ("Ã", "â", "Â", "�"))
    for page in frontend.glob("*.html"):
        page_source = page.read_text(encoding="utf-8")
        assert 0x2014 not in map(ord, page_source)
        assert not any(
            0x1F300 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF
            for char in page_source
        )

def test_exact_reuse_match_skips_external_embedding_call(monkeypatch):
    import backend.app.reuse as reuse_module
    monkeypatch.setattr(reuse_module, "embed", lambda prompt: (_ for _ in ()).throw(AssertionError("exact matches should not call embeddings")))
    rows = [{"prompt":"Exact campaign asset", "created":"2026-01-01", "tags":[]}]
    result = reuse_module.rank("exact campaign asset", [], rows)
    assert result[0]["match"] == "exact"
    assert result[0]["semantic_score"] is None

def test_full_generation_queue_rejects_without_leaving_stale_job(monkeypatch):
    class NoSlots:
        def acquire(self, blocking=False): return False
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(main, "_job_slots", NoSlots())
    client = TestClient(main.app)
    response = client.post("/api/v1/jobs/generate", json={"prompt":"queue pressure request"})
    assert response.status_code == 503
    jobs = memory.jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "rejected"
    assert "no provider call" in jobs[0]["error"]


def test_event_write_failure_does_not_make_paid_generation_retryable(monkeypatch):
    class EventFailingStore(MemoryStore):
        def record_event(self, row): raise OSError("temporary event write failure")
    memory = EventFailingStore(); row = sample(memory); main._store = memory
    monkeypatch.setattr(main._jobs, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(main.RecallPipeline, "generate", lambda self, **kwargs: row)
    client = TestClient(main.app)
    created = client.post("/api/v1/jobs/generate", json={"prompt":"durably archived generation"})
    job = client.get(created.json()["poll"]).json()
    assert job["status"] == "completed"
    assert "analytics event" in job["warning"]


def test_relay_rejects_malformed_success_response(monkeypatch):
    relay = RecallRelay("https://recall.example")
    monkeypatch.setattr(relay_module, "urlopen", lambda request, timeout: BytesIO(b"not-json"))
    try:
        relay._post("https://recall.example/api/v1/reuse-check", {"prompt":"hello"})
    except RecallRelayError as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("malformed service responses must be normalized")

def test_workspace_auth_storage_outage_is_safe_503_not_server_error():
    class UnavailableWorkspaceStore(MemoryStore):
        def workspace(self, workspace_id): raise OSError("B2 unavailable")
    main._store = UnavailableWorkspaceStore()
    response = TestClient(main.app).get("/api/v1/library", headers={"X-Recall-Workspace":"ws-private", "X-Recall-Workspace-Key":"secret"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Workspace authentication is temporarily unavailable."

def test_local_object_urls_are_expiring_and_tamper_evident(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "B2_KEY_ID", None)
    monkeypatch.setattr(config, "B2_APP_KEY", None)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    local = RecallStore()
    local.put("recall/assets/private/output.png", b"private-bytes", "image/png")
    main._store = local
    client = TestClient(main.app)
    signed_url = local.url("recall/assets/private/output.png")
    assert client.get(signed_url).content == b"private-bytes"
    assert client.get(signed_url.replace("signature=", "signature=bad")).status_code == 403
    assert client.get("/api/object/recall/assets/private/output.png").status_code == 403

def test_stale_job_reconciles_durable_generation_event_before_retry():
    memory = MemoryStore(); sample(memory); main._store = memory
    memory.save_job({"job_id":"job_recover", "status":"running", "created":"2020-01-01T00:00:00+00:00", "request":{"prompt":"already completed"}})
    memory.record_event({"event_id":"evt_recover", "kind":"generate", "job_id":"job_recover", "gen_id":"gen_demo"})
    assert main.recover_stale_jobs() == 1
    recovered = memory.job("job_recover")
    assert recovered["status"] == "completed"
    assert recovered["generation_id"] == "gen_demo"


def test_external_capture_cannot_claim_genblaze_recipe_rerun(monkeypatch):
    memory = MemoryStore(); main._store = memory
    monkeypatch.setattr(main, "embed", lambda prompt: None)
    monkeypatch.setattr(config, "RECALL_API_KEYS", ["capture-key"])
    client = TestClient(main.app)
    media = b"\x89PNG\r\n\x1a\nexternal"
    captured = client.post("/api/v1/capture", headers={"X-Recall-Key":"capture-key"}, json={"prompt":"external source", "media_base64":base64.b64encode(media).decode(), "media_type":"image/png"}).json()["generation"]
    response = client.post(f"/api/v1/gen/{captured['gen_id']}/rerun", headers={"X-Recall-Key":"capture-key"})
    assert response.status_code == 409
    recipe = client.get(f"/api/v1/gen/{captured['gen_id']}/replay-recipe").json()
    assert recipe["command"] is None
    assert "no Genblaze recipe" in recipe["note"]
