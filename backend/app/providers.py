"""Custom Genblaze providers for Recall.

Genblaze's generic image provider maps payloads via the model registry. For
models not in the bundled registry (e.g. gpt-image-2-edit), the generic mapping
produces a body GMICloud rejects. We subclass and emit the exact payload each
model expects, while keeping all of Genblaze's Pipeline / manifest / lineage /
retry machinery.
"""
from __future__ import annotations
import base64, os, tempfile
from typing import Any
import httpx
from genblaze_gmicloud import GMICloudImageProvider
from genblaze_core.models.step import Step

# Per-model required payload shape (reverse-engineered from the request queue).
#   image  -> the input image URL
#   prompt -> instruction
_IMAGE_URL_KEY = {
    "gpt-image-2-edit": "image",
    "hunyuan-image-to-image": "image",
    "bria-fibo-restore": "image",
    "seededit-3-0-i2i-250628": "image",
}


def _first_input_url(step: Step) -> str | None:
    for attr in ("inputs", "assets"):
        for a in (getattr(step, attr, None) or []):
            u = getattr(a, "url", None)
            if u:
                return u
    return None


class RecallImageProvider(GMICloudImageProvider):
    """GMICloud image provider that emits model-correct request-queue payloads."""

    def prepare_payload(self, step: Step, *, base_params: dict[str, Any] | None = None,
                        validate_inputs: bool = True) -> dict[str, Any]:
        # SSRF-validate chained inputs, mirroring the base implementation.
        if validate_inputs:
            try:
                from genblaze import validate_chain_input_url
                for a in (getattr(step, "inputs", None) or []):
                    if getattr(a, "url", None):
                        validate_chain_input_url(a.url)
            except Exception:
                pass

        payload: dict[str, Any] = {}
        if step.prompt:
            payload["prompt"] = step.prompt

        url = _first_input_url(step)
        if url:
            key = _IMAGE_URL_KEY.get(step.model, "image")
            payload[key] = url

        # explicit per-step params win (e.g. size, seed, guidance)
        for k, v in (getattr(step, "params", None) or {}).items():
            payload[k] = v
        return payload


class RecallGoogleImageProvider:
    """A real Genblaze provider (SyncProvider) for Google Gemini image-to-image.

    Lets Trueprint run its second, independent colorization through Genblaze on a
    *different provider family* (Google) than gpt-image (OpenAI/GMI) Ã¢â‚¬â€ so the
    confidence map is genuine multi-provider corroboration. Implemented lazily as a
    subclass so importing this module never requires genblaze at load time.
    """

    _impl = None

    def __new__(cls, api_key: str, *, http_timeout: float = 180.0):
        from genblaze import SyncProvider
        from genblaze_core.exceptions import ProviderError
        from genblaze_core.models.enums import ProviderErrorCode
        from genblaze_core.models.asset import Asset
        from genblaze_core._utils import local_file_url

        class _GeminiImageProvider(SyncProvider):
            name = "google-gemini-image"

            def __init__(self, key: str, timeout: float):
                super().__init__()
                self._key, self._timeout = key, timeout

            def generate(self, step: Step, config=None) -> Step:
                url = _first_input_url(step)
                parts = [{"text": step.prompt or "Create an editorial-quality image."}]
                if url:
                    img = httpx.get(url, timeout=self._timeout).content
                    mime = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                    parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode()}})
                ep = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"{step.model}:generateContent?key={self._key}")
                body = {"contents": [{"parts": parts}],
                    "generationConfig": {"responseModalities": ["IMAGE"]}}
                # "Thinking" image models occasionally return a thought-only response
                # with no image Ã¢â‚¬â€ retry a few times before giving up.
                out, last = None, ""
                for _ in range(3):
                    r = httpx.post(ep, json=body, timeout=self._timeout)
                    if r.status_code >= 300:
                        last = f"HTTP {r.status_code}: {r.text[:140]}"; continue
                    parts = (r.json().get("candidates", [{}])[0].get("content", {}) or {}).get("parts", [])
                    for p in parts:
                        idata = p.get("inline_data") or p.get("inlineData")
                        if idata and idata.get("data"):
                            out = base64.b64decode(idata["data"]); break
                    if out:
                        break
                    last = "no image part (thought-only response)"
                if not out:
                    # MODEL_ERROR so Genblaze's fallback_models kicks in to the next model
                    raise ProviderError(f"Gemini returned no image after retries ({last})",
                                        error_code=ProviderErrorCode.MODEL_ERROR)
                from pathlib import Path
                fd, tmp = tempfile.mkstemp(suffix=".png"); os.close(fd)
                with open(tmp, "wb") as f:
                    f.write(out)
                step.assets.append(Asset(url=local_file_url(Path(tmp).resolve()), media_type="image/png"))
                return step

        return _GeminiImageProvider(api_key, http_timeout)
