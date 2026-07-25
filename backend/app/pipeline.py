"""Live Genblaze generation, native B2 persistence, and provenance capture."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
import httpx
from .config import config
from .storage import RecallStore, now
from .semantic import embed

def _manifest_document(manifest: Any) -> dict[str, Any]:
    if manifest is None: return {}
    for method in ("model_dump", "to_dict", "dict"):
        value=getattr(manifest, method, None)
        if callable(value):
            try: return value(mode="json") if method=="model_dump" else value()
            except Exception: pass
    try: return json.loads(json.dumps(manifest, default=lambda value: getattr(value, "__dict__", str(value))))
    except Exception: return {"unserializable": str(manifest)}

def manifest_summary(manifest: Any, parent_run_id: str | None) -> dict[str, Any]:
    run=getattr(manifest,"run",None)
    return {"run_id":getattr(run,"run_id",None),"canonical_hash":getattr(manifest,"canonical_hash",None),"parent_run_id":getattr(run,"parent_run_id",None) or parent_run_id,"schema_version":getattr(manifest,"schema_version",None)}

class RecallPipeline:
    def __init__(self,store:RecallStore)->None: self.store=store
    def generate(self,*,prompt:str,model:str,params:dict[str,Any],tags:list[str],parent_id:str|None=None)->dict[str,Any]:
        gen_id=f"gen_{uuid.uuid4().hex[:12]}"; parent_run_id=None
        if parent_id:
            parent=self.store.generation(parent_id)
            if not parent: raise ValueError("parent generation not found")
            parent_run_id=parent.get("genblaze",{}).get("run_id") or parent_id
        output, summary, raw_manifest=self._run_genblaze(prompt,model,params,parent_run_id)
        if output is None: raise RuntimeError("Live generation did not return an asset. Nothing was archived; check provider access or model support.")
        extension,content_type=self._image_format(output)
        asset=self.store.put(f"recall/assets/{gen_id}/output.{extension}",output,content_type); asset["content_type"]=content_type
        raw_key=f"recall/genblaze-manifests/{gen_id}.json"; self.store.put(raw_key,json.dumps(raw_manifest,indent=2,default=str).encode(),"application/json")
        recipe={"generation":gen_id,"created":now(),"prompt":prompt,"model":model,"params":params,"provider":config.RECALL_PROVIDER,"genblaze":summary,"raw_manifest_key":raw_key}
        manifest_key=f"recall/manifests/{gen_id}.json"; self.store.put(manifest_key,json.dumps(recipe,indent=2).encode(),"application/json")
        cost=summary.get("cost_usd") if summary else None
        vector=embed(prompt)
        semantic={"model":config.GOOGLE_EMBEDDING_MODEL,"embedding":vector} if vector else None
        row={"gen_id":gen_id,"created":recipe["created"],"modality":"image","prompt":prompt,"provider":config.RECALL_PROVIDER,"model":model,"params":params,"tags":tags,"genblaze":summary,"asset":asset,"manifest_key":manifest_key,"raw_manifest_key":raw_key,"cost_usd":float(cost) if cost is not None else None,"parent_gen_id":parent_id,"locked":False,"approval":None,"semantic":semantic}
        self.store.save_generation(row); return row
    @staticmethod
    def _image_format(data:bytes)->tuple[str,str]:
        if data.startswith(b"\xff\xd8\xff"): return "jpg","image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"): return "png","image/png"
        if data.startswith(b"RIFF") and data[8:12]==b"WEBP": return "webp","image/webp"
        return "bin","application/octet-stream"
    @staticmethod
    def _read_asset(url:str)->bytes:
        if url.startswith("file:"):
            path=unquote(urlparse(url).path)
            if path.startswith("/") and len(path)>2 and path[2]==":": path=path[1:]
            return Path(path).read_bytes()
        response=httpx.get(url,timeout=120); response.raise_for_status(); return response.content
    def _sink(self):
        if self.store.mode!="b2" or not config.RECALL_NATIVE_SINK: return None
        from genblaze_core import ObjectStorageSink,KeyStrategy
        from genblaze_s3 import S3StorageBackend
        backend=S3StorageBackend.for_backblaze(config.B2_BUCKET,region=config.B2_REGION,key_id=config.B2_KEY_ID,app_key=config.B2_APP_KEY,preflight=True)
        return ObjectStorageSink(backend,prefix="recall/genblaze",key_strategy=KeyStrategy.HIERARCHICAL)
    def _run_genblaze(self,prompt:str,model:str,params:dict[str,Any],parent_run_id:str|None)->tuple[bytes|None,dict[str,Any],dict[str,Any]]:
        if not config.has_generation_provider:
            return None,{},{}
        errors:list[str]=[]
        for attempt in range(1, config.RECALL_GENERATION_RETRIES + 1):
            try:
                import genblaze as g
                from .providers import RecallImageProvider,RecallGoogleImageProvider
                if config.RECALL_PROVIDER=="google":
                    provider=RecallGoogleImageProvider(api_key=config.GOOGLE_API_KEY)
                else:
                    provider=RecallImageProvider(api_key=config.GMI_API_KEY,base_url=config.GMI_IMAGE_BASE_URL)
                pipeline=g.Pipeline("recall-generate").step(
                    provider,model=model,prompt=prompt,modality=g.Modality.IMAGE,params=params,
                    fallback_models=config.RECALL_FALLBACK_MODELS or None,
                )
                result=pipeline.run(sink=self._sink(),timeout=300,raise_on_failure=False)
                manifest=getattr(result,"manifest",None)
                raw=_manifest_document(manifest)
                summary=manifest_summary(manifest,parent_run_id)
                summary["attempt"] = attempt
                summary["fallback_models"] = config.RECALL_FALLBACK_MODELS
                for step in getattr(getattr(result,"run",None),"steps",[]) or []:
                    for asset in getattr(step,"assets",[]) or []:
                        if getattr(asset,"url",None):
                            reported=getattr(step,"cost_usd",None) or getattr(step,"cost",None)
                            summary["cost_usd"]=float(reported) if reported is not None else (float(config.RECALL_MODEL_COST_USD) if config.RECALL_MODEL_COST_USD else None)
                            summary["price_source"]="provider" if reported is not None else ("configured_model_price" if config.RECALL_MODEL_COST_USD else "unknown")
                            summary["native_asset_url"]=asset.url
                            return self._read_asset(asset.url),summary,raw
                errors.append(f"attempt {attempt}: provider returned no image asset")
            except Exception as exc:
                errors.append(f"attempt {attempt}: {str(exc)[:180]}")
        return None,{"error":" | ".join(errors),"attempts":config.RECALL_GENERATION_RETRIES},{}