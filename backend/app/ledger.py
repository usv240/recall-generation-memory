"""Tamper-evident, privacy-preserving records of Recall reuse decisions."""
from __future__ import annotations
import hashlib, hmac, json, uuid
from typing import Any
from .config import config
from .storage import now

SCHEMA = "recall-reuse-receipt/v1"

def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def prompt_commitment(prompt: str) -> str:
    return hmac.new(config.RECALL_RECEIPT_SECRET.encode(), prompt.encode(), hashlib.sha256).hexdigest()

def create_receipt(*, prompt: str, intent: dict[str, str], recommendation: str, reason: str, blockers: list[dict[str, str]], match: dict[str, Any] | None, previous_receipt_hash: str | None) -> dict[str, Any]:
    asset = (match or {}).get("generation", {}).get("asset", {})
    body = {
        "schema": SCHEMA,
        "receipt_id": f"rr_{uuid.uuid4().hex[:16]}",
        "created": now(),
        "policy_version": "intent-firewall/v1",
        "prompt_commitment_hmac_sha256": prompt_commitment(prompt),
        "requested_intent": intent,
        "recommendation": recommendation,
        "reason": reason,
        "blockers": blockers,
        "candidate": None if not match else {
            "generation_id": match["generation"].get("gen_id"), "asset_sha256": asset.get("sha256"),
            "similarity": match.get("score"), "match_type": match.get("match"),
            "candidate_intent": match["generation"].get("intent", {}), "avoided_cost_usd": match["generation"].get("cost_usd"),
        },
        "previous_receipt_hash": previous_receipt_hash,
    }
    return {**body, "receipt_hash": digest(body)}

def verify_receipt(receipt: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    hash_valid = hmac.compare_digest(receipt.get("receipt_hash", ""), digest(body))
    chain_valid = previous is None or receipt.get("previous_receipt_hash") == previous.get("receipt_hash")
    return {"receipt_id": receipt.get("receipt_id"), "receipt_hash": receipt.get("receipt_hash"), "hash_valid": hash_valid, "chain_valid": chain_valid, "status": "verified" if hash_valid and chain_valid else "attention_required"}
