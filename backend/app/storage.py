"""Recall's B2 system of record: assets, manifests, event ledger, and approvals."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any
from .config import config

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

class RecallStore:
    def __init__(self) -> None:
        self._remote = None
        if config.has_b2:
            from genblaze_s3 import S3StorageBackend
            self._remote = S3StorageBackend.for_backblaze(config.B2_BUCKET, region=config.B2_REGION, key_id=config.B2_KEY_ID, app_key=config.B2_APP_KEY, preflight=True)
        config.LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def mode(self) -> str: return "b2" if self._remote else "local"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", *, lock_days: int | None = None) -> dict[str, Any]:
        if self._remote:
            kwargs: dict[str, Any] = {"content_type": content_type}
            if lock_days:
                try:
                    from genblaze import ObjectLockConfig
                    retain = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=lock_days)
                    kwargs["object_lock"] = ObjectLockConfig(retain_until=retain, mode="GOVERNANCE")
                except Exception:
                    pass
            try: self._remote.put(key, data, **kwargs)
            except TypeError: self._remote.put(key, data)
        else:
            target = self._local_path(key); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        return {"b2_key": key, "sha256": sha256_hex(data), "bytes": len(data)}

    def get(self, key: str) -> bytes:
        if self._remote:
            value = self._remote.get(key)
            return bytes(value if isinstance(value, (bytes, bytearray)) else getattr(value, "data", value))
        return self._local_path(key).read_bytes()

    def url(self, key: str) -> str:
        if self._remote:
            value = self._remote.presigned_get_url(key, expires_in=3600)
            return value if isinstance(value, str) else getattr(value, "url", str(value))
        return f"/api/object/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        if self._remote:
            page = self._remote.list(prefix, max_keys=1000); out=[]
            for item in getattr(page, "entries", []) or []:
                key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else None)
                if key: out.append(key)
            return out
        root=self._local_path(prefix)
        return [] if not root.exists() else [p.relative_to(config.LOCAL_DATA_DIR).as_posix() for p in root.rglob("*") if p.is_file()]

    def save_generation(self, generation: dict[str, Any]) -> None: self.put(f"recall/index/runs/{generation['gen_id']}.json", json.dumps(generation, indent=2).encode(), "application/json")
    def generation(self, gen_id: str) -> dict[str, Any] | None:
        try: return json.loads(self.get(f"recall/index/runs/{gen_id}.json"))
        except FileNotFoundError: return None
    def generations(self) -> list[dict[str, Any]]:
        out=[]
        for key in self.list_keys("recall/index/runs"):
            try: out.append(json.loads(self.get(key)))
            except (OSError, json.JSONDecodeError): pass
        return sorted(out, key=lambda row: row.get("created", ""), reverse=True)
    def record_event(self, event: dict[str, Any]) -> None: self.put(f"recall/index/events/{event['event_id']}.json", json.dumps(event).encode(), "application/json")
    def save_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"recall/ledger/{receipt['receipt_id']}.json", json.dumps(receipt, indent=2).encode(), "application/json", lock_days=config.B2_LOCK_DAYS)
    def receipt(self, receipt_id: str) -> dict[str, Any] | None:
        try: return json.loads(self.get(f"recall/ledger/{receipt_id}.json"))
        except FileNotFoundError: return None
    def receipts(self) -> list[dict[str, Any]]:
        out=[]
        for key in self.list_keys("recall/ledger"):
            try: out.append(json.loads(self.get(key)))
            except (OSError, json.JSONDecodeError): pass
        return sorted(out, key=lambda row: row.get("created", ""))
    def save_job(self, job: dict[str, Any]) -> None: self.put(f"recall/index/jobs/{job['job_id']}.json", json.dumps(job, indent=2).encode(), "application/json")
    def job(self, job_id: str) -> dict[str, Any] | None:
        try: return json.loads(self.get(f"recall/index/jobs/{job_id}.json"))
        except FileNotFoundError: return None
    def jobs(self) -> list[dict[str, Any]]:
        out=[]
        for key in self.list_keys("recall/index/jobs"):
            try: out.append(json.loads(self.get(key)))
            except (OSError, json.JSONDecodeError): pass
        return sorted(out, key=lambda row: row.get("created", ""), reverse=True)
    def events(self) -> list[dict[str, Any]]:
        out=[]
        for key in self.list_keys("recall/index/events"):
            try: out.append(json.loads(self.get(key)))
            except (OSError, json.JSONDecodeError): pass
        return out
    def approve(self, row: dict[str, Any]) -> dict[str, Any]:
        data=self.get(row["asset"]["b2_key"]); content_type=row["asset"].get("content_type", "application/octet-stream")
        ext=row["asset"]["b2_key"].rsplit(".",1)[-1]
        key=f"recall/approved/{row['gen_id']}/final.{ext}"
        try:
            locked=self.put(key,data,content_type,lock_days=config.B2_LOCK_DAYS)
            return {"status":"locked" if self._remote else "local-approved", "asset":{**locked,"content_type":content_type},"retention_days":config.B2_LOCK_DAYS}
        except Exception as exc:
            return {"status":"requires-object-lock", "reason":str(exc)[:180]}
    def _local_path(self,key:str)->Path:
        safe=Path(key)
        if safe.is_absolute() or ".." in safe.parts: raise ValueError("invalid storage key")
        return config.LOCAL_DATA_DIR/safe