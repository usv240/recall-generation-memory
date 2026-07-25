"""No-provider-cost Genblaze ObjectStorageSink diagnostic for B2.
Run with B2_BUCKET, B2_REGION, B2_KEY_ID and B2_APP_KEY in the environment.
"""
from __future__ import annotations
import hashlib, os, tempfile
from pathlib import Path
from genblaze_core import ObjectStorageSink, KeyStrategy
from genblaze_core.models.asset import Asset
from genblaze_s3 import S3StorageBackend

payload=b"recall native sink diagnostic"
with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as file:
    file.write(payload); path=Path(file.name)
try:
    backend=S3StorageBackend.for_backblaze(os.environ["B2_BUCKET"], region=os.environ["B2_REGION"], key_id=os.environ["B2_KEY_ID"], app_key=os.environ["B2_APP_KEY"], preflight=True)
    sink=ObjectStorageSink(backend, prefix="recall/diagnostics/native-sink", key_strategy=KeyStrategy.HIERARCHICAL)
    result=sink.put_asset(Asset(url=path.as_uri(), media_type="application/octet-stream", sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)))
    print({"native_sink":"success", "url":result.url, "sha256":result.sha256})
finally:
    path.unlink(missing_ok=True)
