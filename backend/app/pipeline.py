"""Live Genblaze generation, native B2 persistence, and provenance capture."""
from __future__ import annotations
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
import httpx
from .config import config
from .storage import RecallStore, now
from .semantic import embed
from .media import image_dhash

def _safe_error(value: Any, limit: int = 180) -> str:
    text = re.sub(r"https?://[^\s]+", "[redacted-url]", str(value))
    return text[:limit]

def _manifest_document(manifest: Any) -> dict[str, Any]:
    if manifest is None: return {}
    for method in ("model_dump", "to_dict", "dict"):
        value=getattr(manifest, method, None)
        if callable(value):
            try: return value(mode="json") if method=="model_dump" else value()
            except Exception: pass
    try: return json.loads(json.dumps(manifest, default=lambda value: getattr(value, "__dict__", str(value))))
    except Exception: return {"unserializable": str(manifest)}

def seal_manifest(raw_manifest: dict[str, Any], asset: dict[str, Any], content_type: str) -> tuple[dict[str, Any], str | None, bool]:
    """Attach the durable output hash, then recanonicalize the Genblaze manifest."""
    try:
        from genblaze import parse_manifest
        manifest = parse_manifest(raw_manifest)
        for step in getattr(manifest.run, "steps", []) or []:
            for output in getattr(step, "assets", []) or []:
                if not getattr(output, "sha256", None):
                    output.sha256 = asset["sha256"]
                    output.size_bytes = asset["bytes"]
                    output.media_type = content_type
        manifest.canonical_hash = manifest.compute_hash()
        sealed = _manifest_document(manifest)
        return sealed, manifest.canonical_hash, bool(manifest.verify())
    except Exception:
        return raw_manifest, None, False
def manifest_summary(manifest: Any, parent_run_id: str | None) -> dict[str, Any]:
    run=getattr(manifest,"run",None)
    return {"run_id":getattr(run,"run_id",None),"canonical_hash":getattr(manifest,"canonical_hash",None),"parent_run_id":getattr(run,"parent_run_id",None) or parent_run_id,"schema_version":getattr(manifest,"schema_version",None)}

class RecallPipeline:
    def __init__(self,store:RecallStore)->None: self.store=store
    def generate(self,*,prompt:str,model:str|None,params:dict[str,Any],tags:list[str],provider:str|None=None,parent_id:str|None=None,intent:dict[str, Any]|None=None,modality:str="image",fallback_provider:str|None=None)->dict[str,Any]:
        modality_name=modality.casefold()
        if modality_name not in {"image","video"}:
            raise ValueError(f"unsupported generation modality: {modality_name}")
        provider_name=(provider or ("gmi" if modality_name=="video" else config.default_generation_provider)).casefold()
        if provider_name not in {"google","gmi"}:
            raise ValueError(f"unsupported generation provider: {provider_name}")
        if not config.provider_is_configured(provider_name,modality_name):
            raise RuntimeError(f"{provider_name} {modality_name} generation is not configured")
        model=model or config.default_model_for(provider_name,modality_name)
        normalized_fallback=fallback_provider.casefold() if fallback_provider else None
        if normalized_fallback==provider_name:
            raise ValueError("fallback_provider must differ from provider")
        if normalized_fallback and not config.provider_is_configured(normalized_fallback,modality_name):
            raise RuntimeError(f"{normalized_fallback} {modality_name} fallback is not configured")
        effective_params=dict(params)
        if modality_name=="video":
            effective_params.setdefault("duration",3)
            effective_params.setdefault("resolution","480p")
            effective_params.setdefault("aspect_ratio","16:9")
        gen_id=f"gen_{uuid.uuid4().hex[:12]}"; parent_run_id=None
        if parent_id:
            parent=self.store.generation(parent_id)
            if not parent: raise ValueError("parent generation not found")
            parent_run_id=parent.get("genblaze",{}).get("run_id") or parent_id
        requested_provider,requested_model=provider_name,model
        output,summary,raw_manifest=self._run_genblaze(prompt,model,effective_params,parent_run_id,provider_name,modality_name)
        routing={"requested_provider":requested_provider,"requested_model":requested_model,"fallback_provider":normalized_fallback,"fallback_used":False,"attempted_providers":[requested_provider]}
        if output is None and normalized_fallback:
            next_provider=normalized_fallback
            primary_error=str(summary.get("error","provider returned no asset"))[:300]
            fallback_model=config.default_model_for(next_provider,modality_name)
            output,fallback_summary,raw_manifest=self._run_genblaze(prompt,fallback_model,effective_params,parent_run_id,next_provider,modality_name)
            routing.update({"fallback_used":True,"primary_error":primary_error,"fallback_model":fallback_model,"attempted_providers":[requested_provider,next_provider]})
            summary=fallback_summary
            provider_name,model=next_provider,fallback_model
        summary["routing"]=routing
        if output is None:
            detail = str(summary.get("error", "provider returned no asset"))[:300]
            raise RuntimeError(f"Live generation did not return an asset. Nothing was archived: {detail}")
        limit=config.RECALL_MAX_GENERATED_VIDEO_BYTES if modality_name=="video" else config.RECALL_MAX_GENERATED_MEDIA_BYTES
        if len(output)>limit: raise RuntimeError("Generated media exceeds the configured archive limit; nothing was stored.")
        extension,content_type=self._media_format(output,modality_name)
        asset=self.store.put(f"recall/assets/{gen_id}/output.{extension}",output,content_type); asset["content_type"]=content_type
        media_fingerprint=image_dhash(output) if modality_name=="image" else None
        raw_manifest,canonical_hash,manifest_verified=seal_manifest(raw_manifest,asset,content_type)
        if canonical_hash:
            summary["canonical_hash"]=canonical_hash
        summary["manifest_verified"]=manifest_verified
        raw_key=f"recall/genblaze-manifests/{gen_id}.json"; self.store.put(raw_key,json.dumps(raw_manifest,indent=2,default=str).encode(),"application/json")
        recipe={"generation":gen_id,"created":now(),"modality":modality_name,"prompt":prompt,"model":model,"params":effective_params,"provider":provider_name,"routing":routing,"genblaze":summary,"raw_manifest_key":raw_key}
        manifest_key=f"recall/manifests/{gen_id}.json"; self.store.put(manifest_key,json.dumps(recipe,indent=2).encode(),"application/json")
        cost=summary.get("cost_usd") if summary else None
        vector=embed(prompt)
        semantic={"model":config.GOOGLE_EMBEDDING_MODEL,"embedding":vector} if vector else None
        row={"gen_id":gen_id,"created":recipe["created"],"modality":modality_name,"prompt":prompt,"provider":provider_name,"model":model,"params":effective_params,"tags":tags,"genblaze":summary,"asset":asset,"manifest_key":manifest_key,"raw_manifest_key":raw_key,"cost_usd":float(cost) if cost is not None else None,"parent_gen_id":parent_id,"intent":intent or {},"media_fingerprint":media_fingerprint,"locked":False,"approval":None,"semantic":semantic}
        self.store.save_generation(row); return row
    @staticmethod
    def _media_format(data:bytes,modality:str)->tuple[str,str]:
        if modality=="video":
            if len(data)>=12 and data[4:8]==b"ftyp": return "mp4","video/mp4"
            return "bin","application/octet-stream"
        if data[:3]==bytes.fromhex("ffd8ff"): return "jpg","image/jpeg"
        if data[:8]==bytes.fromhex("89504e470d0a1a0a"): return "png","image/png"
        if data.startswith(b"RIFF") and data[8:12]==b"WEBP": return "webp","image/webp"
        return "bin","application/octet-stream"
    def _read_asset(self,url:str,limit:int|None=None)->bytes:
        limit=limit or config.RECALL_MAX_GENERATED_MEDIA_BYTES
        parsed = urlparse(url)
        trusted_hosts = {f"s3.{config.B2_REGION}.backblazeb2.com"}
        if config.B2_S3_ENDPOINT:
            trusted_hosts.add(urlparse(config.B2_S3_ENDPOINT).hostname or "")
        bucket_prefix = f"/{config.B2_BUCKET}/"
        if self.store.mode == "b2" and parsed.scheme == "https" and parsed.hostname in trusted_hosts and parsed.path.startswith(bucket_prefix):
            physical_key = unquote(parsed.path[len(bucket_prefix):])
            if self.store.workspace_id:
                workspace_prefix = f"recall/workspaces/{self.store.workspace_id}/"
                if not physical_key.startswith(workspace_prefix):
                    raise RuntimeError("Native sink returned an asset outside the active workspace")
                logical_key = "recall/" + physical_key.removeprefix(workspace_prefix)
            else:
                logical_key = physical_key
            data = self.store.get(logical_key)
            if len(data) > limit:
                raise RuntimeError("Provider asset exceeds the configured archive limit")
            return data
        if url.startswith("file:"):
            path=unquote(urlparse(url).path)
            if path.startswith("/") and len(path)>2 and path[2]==":": path=path[1:]
            source = Path(path)
            if source.stat().st_size > limit:
                raise RuntimeError("Provider asset exceeds the configured archive limit")
            return source.read_bytes()
        chunks: list[bytes] = []
        total = 0
        with httpx.stream("GET", url, timeout=120) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    raise RuntimeError("Provider asset exceeds the configured archive limit")
                chunks.append(chunk)
        return b"".join(chunks)
    def _sink(self):
        if self.store.mode!="b2" or not config.RECALL_NATIVE_SINK: return None
        from genblaze_core import ObjectStorageSink,KeyStrategy
        from genblaze_s3 import S3StorageBackend
        backend=S3StorageBackend.for_backblaze(config.B2_BUCKET,region=config.B2_REGION,key_id=config.B2_KEY_ID,app_key=config.B2_APP_KEY,preflight=True)
        sink_prefix = f"recall/workspaces/{self.store.workspace_id}/genblaze" if self.store.workspace_id else "recall/genblaze"
        return ObjectStorageSink(backend,prefix=sink_prefix,key_strategy=KeyStrategy.HIERARCHICAL)
    def _run_genblaze(self,prompt:str,model:str,params:dict[str,Any],parent_run_id:str|None,provider_name:str,modality:str)->tuple[bytes|None,dict[str,Any],dict[str,Any]]:
        if not config.provider_is_configured(provider_name,modality):
            return None,{},{}
        errors:list[str]=[]
        for attempt in range(1,config.RECALL_GENERATION_RETRIES+1):
            try:
                import genblaze as g
                from .providers import RecallImageProvider,RecallGoogleImageProvider
                if modality=="video":
                    from genblaze_gmicloud import GMICloudVideoProvider
                    provider=GMICloudVideoProvider(api_key=config.GMI_API_KEY,base_url=config.GMI_IMAGE_BASE_URL)
                    genblaze_modality=g.Modality.VIDEO
                elif provider_name=="google":
                    provider=RecallGoogleImageProvider(api_key=config.GOOGLE_API_KEY)
                    genblaze_modality=g.Modality.IMAGE
                else:
                    provider=RecallImageProvider(api_key=config.GMI_API_KEY,base_url=config.GMI_IMAGE_BASE_URL)
                    genblaze_modality=g.Modality.IMAGE
                fallback_models=config.fallback_models_for(provider_name,modality)
                pipeline=g.Pipeline("recall-generate").step(
                    provider,model=model,prompt=prompt,modality=genblaze_modality,params=params,
                    fallback_models=fallback_models or None,
                )
                native_sink=self._sink()
                result=pipeline.run(sink=native_sink,timeout=600 if modality=="video" else 300,raise_on_failure=False)
                manifest=getattr(result,"manifest",None)
                raw=_manifest_document(manifest)
                summary=manifest_summary(manifest,parent_run_id)
                summary["attempt"]=attempt
                summary["modality"]=modality
                summary["native_b2_sink"]=native_sink is not None
                summary["fallback_models"]=fallback_models
                steps=getattr(getattr(result,"run",None),"steps",[]) or []
                for step in steps:
                    for asset in getattr(step,"assets",[]) or []:
                        if getattr(asset,"url",None):
                            reported=getattr(step,"cost_usd",None)
                            if reported is None:
                                reported=getattr(step,"cost",None)
                            provider_cost=None
                            if reported is not None:
                                try:
                                    candidate=float(reported)
                                    provider_cost=candidate if math.isfinite(candidate) and candidate>=0 else None
                                except (TypeError,ValueError):
                                    provider_cost=None
                            configured_cost=config.model_cost_for(provider_name,model,modality)
                            summary["cost_usd"]=provider_cost if provider_cost is not None else configured_cost
                            summary["price_source"]="provider" if provider_cost is not None else ("configured_model_price" if configured_cost is not None else "unknown")
                            limit=config.RECALL_MAX_GENERATED_VIDEO_BYTES if modality=="video" else config.RECALL_MAX_GENERATED_MEDIA_BYTES
                            return self._read_asset(asset.url,limit),summary,raw
                diagnostics=[f"{getattr(step,'status','unknown')}: {_safe_error(getattr(step,'error','') or getattr(step,'error_code','') or 'no asset')}" for step in steps]
                errors.append(f"attempt {attempt}: provider returned no {modality} asset"+(" ("+" | ".join(diagnostics)+")" if diagnostics else ""))
            except Exception as exc:
                errors.append(f"attempt {attempt}: {_safe_error(exc)}")
        return None,{"error":" | ".join(errors),"attempts":config.RECALL_GENERATION_RETRIES,"modality":modality},{}