from __future__ import annotations

import base64
import hashlib
import json
import sys
from io import BytesIO
from urllib.error import HTTPError
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.reuse import rank
from backend.app.storage import RecallStore
from backend.app.config import config
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
    payload = {"prompt":"outside workflow hero", "media_base64":base64.b64encode(b"external media bytes").decode(), "media_type":"image/png", "provider":"bring-your-own", "model":"model-x", "cost_usd":0.12}
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
