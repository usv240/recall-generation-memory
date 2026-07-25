"""Provider-neutral, bring-your-own-key generation memory for Recall.

Provider credentials remain in the caller process; Recall receives completed media only.
"""
from __future__ import annotations
import base64, json, math
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

class RecallRelayError(RuntimeError):
    """Safe, actionable network/API failure raised by the Relay client."""
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class RelayResult:
    status: str
    generation: dict[str, Any]
    receipt: dict[str, Any]
    media: bytes | None = None

class RecallRelay:
    @staticmethod
    def _cost(cost_usd: float | None) -> float | None:
        if cost_usd is None:
            return None
        if isinstance(cost_usd, bool) or not math.isfinite(float(cost_usd)) or float(cost_usd) < 0:
            raise ValueError("cost_usd must be a finite, non-negative number")
        return float(cost_usd)

    def __init__(self, recall_url: str, *, recall_key: str | None = None, gemini_key: str | None = None, openai_key: str | None = None, workspace_id: str | None = None, workspace_key: str | None = None, timeout: float = 180.0, max_media_bytes: int = 18_874_368) -> None:
        self.recall_url=recall_url.rstrip("/")
        self.recall_key=recall_key
        self.gemini_key=gemini_key
        self.openai_key=openai_key
        self.workspace_id=workspace_id
        self.workspace_key=workspace_key
        self.timeout=timeout
        self.max_media_bytes=max_media_bytes
        parsed = urlsplit(self.recall_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc: raise ValueError("recall_url must be an absolute HTTP(S) URL")
        self._recall_origin = (parsed.scheme.casefold(), parsed.netloc.casefold())
        if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or timeout <= 0: raise ValueError("timeout must be a finite positive number")
        if isinstance(max_media_bytes, bool) or max_media_bytes < 1: raise ValueError("max_media_bytes must be positive")
        if bool(workspace_id) != bool(workspace_key): raise ValueError("workspace_id and workspace_key must be supplied together")

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        target = urlsplit(url)
        is_recall = (target.scheme.casefold(), target.netloc.casefold()) == self._recall_origin
        workspace_headers = {"X-Recall-Workspace":self.workspace_id, "X-Recall-Workspace-Key":self.workspace_key} if self.workspace_id and is_recall else {}
        request=Request(url, data=json.dumps(body, allow_nan=False).encode(), headers={"Content-Type":"application/json", "User-Agent":"recall-relay/0.1", **workspace_headers, **(headers or {})})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(32_000_001)
            if len(raw) > 32_000_000:
                raise RecallRelayError("Response exceeded the Relay client's 32 MB safety limit")
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RecallRelayError("Service returned an invalid JSON response") from exc
            if not isinstance(value, dict):
                raise RecallRelayError("Service returned an unexpected JSON response")
            return value
        except HTTPError as exc:
            raw = exc.read(2048).decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except json.JSONDecodeError:
                detail = raw
            raise RecallRelayError(f"Request failed with HTTP {exc.code}: {str(detail)[:500]}", status=exc.code) from None
        except URLError as exc:
            raise RecallRelayError(f"Request could not reach {request.host}: {exc.reason}") from None

    def _check(self, prompt: str, tags: list[str], intent: dict[str, str]) -> dict[str, Any]:
        return self._post(self.recall_url+"/api/v1/reuse-check", {"prompt":prompt,"tags":tags,"intent":intent})

    def _retrieve(self, generation_id: str) -> dict[str, Any]:
        headers={"X-Recall-Key":self.recall_key} if self.recall_key else {}
        return self._post(self.recall_url+f"/api/v1/gen/{generation_id}/reproduce", {}, headers)

    def _reuse_result(self, gate: dict[str, Any]) -> RelayResult:
        match = gate["matches"][0]
        retrieved = self._retrieve(match["gen_id"])
        return RelayResult("reused", retrieved.get("generation", match), gate["receipt"])

    def _capture(self, *, prompt: str, media_base64: str, media_type: str, provider: str, model: str, tags: list[str], intent: dict[str, str], params: dict[str, Any], cost_usd: float | None) -> dict[str, Any]:
        headers={"X-Recall-Key":self.recall_key} if self.recall_key else {}
        return self._post(self.recall_url+"/api/v1/capture", {"prompt":prompt,"media_base64":media_base64,"media_type":media_type,"provider":provider,"model":model,"tags":tags,"intent":intent,"params":params,"cost_usd":cost_usd}, headers)

    def generate_with(self, prompt: str, generator: Callable[[str], bytes], *, provider: str, model: str, media_type: str="image/png", tags: list[str] | None=None, intent: dict[str, str] | None=None, params: dict[str, Any] | None=None, cost_usd: float | None=None) -> RelayResult:
        """Reuse first, then run any caller-owned media generator only on a safe miss.

        ``generator`` executes in the caller's process, so its provider credentials never
        transit Recall. It must return the completed media bytes for archival.
        """
        tags, intent, params = tags or [], intent or {}, params or {}
        cost_usd = self._cost(cost_usd)
        if not provider.strip() or not model.strip(): raise ValueError("provider and model are required")
        if media_type.casefold() not in {"image/png", "image/jpeg", "image/webp", "video/mp4", "audio/mpeg", "audio/wav"}: raise ValueError("media_type is not supported by Recall")
        gate = self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse":
            return self._reuse_result(gate)
        media = generator(prompt)
        if not isinstance(media, bytes) or not media:
            raise TypeError("generator must return non-empty media bytes")
        if len(media) > self.max_media_bytes:
            raise ValueError(f"generated media exceeds max_media_bytes ({self.max_media_bytes})")
        encoded = base64.b64encode(media).decode()
        captured = self._capture(prompt=prompt, media_base64=encoded, media_type=media_type, provider=provider, model=model, tags=tags, intent=intent, params={**params, "relay":"custom-provider"}, cost_usd=cost_usd)
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)

    def generate_gemini(self, prompt: str, *, model: str="gemini-3.1-flash-image", tags: list[str] | None=None, intent: dict[str, str] | None=None, response_format: dict[str, Any] | None=None, cost_usd: float | None=None) -> RelayResult:
        tags, intent = tags or [], intent or {}
        cost_usd = self._cost(cost_usd)
        gate=self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse":
            return self._reuse_result(gate)
        if not self.gemini_key:
            raise RuntimeError("No reusable result; supply gemini_key to create and capture a new asset.")
        interaction=self._post("https://generativelanguage.googleapis.com/v1beta/interactions", {"model":model,"input":prompt, **({"response_format":response_format} if response_format else {})}, {"x-goog-api-key":self.gemini_key})
        image=(interaction.get("output_image") or {}).get("data")
        if not image: raise RuntimeError("Gemini returned no output_image data; inspect the interaction response for an unsupported mode.")
        try:
            media=base64.b64decode(image, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Provider returned invalid base64 media data.") from exc
        if len(media) > self.max_media_bytes: raise RuntimeError("Provider media exceeds max_media_bytes and was not uploaded to Recall.")
        captured=self._capture(prompt=prompt, media_base64=image, media_type=(interaction.get("output_image") or {}).get("mime_type", "image/png"), provider="google", model=model, tags=tags, intent=intent, params={"relay":"gemini-interactions"}, cost_usd=cost_usd)
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)

    def generate_openai(self, prompt: str, *, model: str="gpt-image-1", tags: list[str] | None=None, intent: dict[str, str] | None=None, size: str | None=None, cost_usd: float | None=None) -> RelayResult:
        """Use the caller's OpenAI key locally; Recall never receives that key."""
        tags, intent = tags or [], intent or {}
        cost_usd = self._cost(cost_usd)
        gate=self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse": return self._reuse_result(gate)
        if not self.openai_key: raise RuntimeError("No reusable result; supply openai_key to create and capture a new asset.")
        body={"model":model,"prompt":prompt,"response_format":"b64_json"}
        if size: body["size"]=size
        result=self._post("https://api.openai.com/v1/images/generations", body, {"Authorization":f"Bearer {self.openai_key}"})
        image=((result.get("data") or [{}])[0]).get("b64_json")
        if not image: raise RuntimeError("OpenAI returned no b64_json image data.")
        try:
            media=base64.b64decode(image, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Provider returned invalid base64 media data.") from exc
        if len(media) > self.max_media_bytes: raise RuntimeError("Provider media exceeds max_media_bytes and was not uploaded to Recall.")
        captured=self._capture(prompt=prompt, media_base64=image, media_type="image/png", provider="openai", model=model, tags=tags, intent=intent, params={"relay":"openai-images", **({"size":size} if size else {})}, cost_usd=cost_usd)
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)
