from __future__ import annotations

import base64
import hashlib
import json
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.reuse import rank
from backend.app.storage import RecallStore
from backend.app.config import config


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
    client = TestClient(main.app)
    payload = {"prompt":"outside workflow hero", "media_base64":base64.b64encode(b"external media bytes").decode(), "media_type":"image/png", "provider":"bring-your-own", "model":"model-x"}
    first = client.post("/api/v1/capture", json=payload)
    assert first.status_code == 200
    assert first.json()["deduplicated"] is False
    generation = first.json()["generation"]
    assert generation["provenance_kind"] == "external_capture"
    duplicate = client.post("/api/v1/capture", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    checked = client.get(f"/api/v1/gen/{generation['gen_id']}/verify").json()
    assert checked["status"] == "externally_captured_asset_verified"


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
