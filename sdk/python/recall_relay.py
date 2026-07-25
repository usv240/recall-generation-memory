"""Bring-your-own-key Recall relay for Gemini image generation.

Your Gemini key is read locally and is sent only to Google. Recall receives it never.
"""
from __future__ import annotations
import base64, json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

@dataclass
class RelayResult:
    status: str
    generation: dict[str, Any]
    receipt: dict[str, Any]
    media: bytes | None = None

class RecallRelay:
    def __init__(self, recall_url: str, *, recall_key: str | None = None, gemini_key: str | None = None, openai_key: str | None = None, workspace_id: str | None = None, workspace_key: str | None = None) -> None:
        self.recall_url=recall_url.rstrip("/")
        self.recall_key=recall_key
        self.gemini_key=gemini_key
        self.openai_key=openai_key
        self.workspace_id=workspace_id
        self.workspace_key=workspace_key
        if bool(workspace_id) != bool(workspace_key): raise ValueError("workspace_id and workspace_key must be supplied together")

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        workspace_headers = {"X-Recall-Workspace":self.workspace_id, "X-Recall-Workspace-Key":self.workspace_key} if self.workspace_id else {}
        request=Request(url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json", **workspace_headers, **(headers or {})})
        return json.load(urlopen(request, timeout=180))

    def _check(self, prompt: str, tags: list[str], intent: dict[str, str]) -> dict[str, Any]:
        return self._post(self.recall_url+"/api/v1/reuse-check", {"prompt":prompt,"tags":tags,"intent":intent})

    def _capture(self, *, prompt: str, media_base64: str, media_type: str, provider: str, model: str, tags: list[str], intent: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
        headers={"X-Recall-Key":self.recall_key} if self.recall_key else {}
        return self._post(self.recall_url+"/api/v1/capture", {"prompt":prompt,"media_base64":media_base64,"media_type":media_type,"provider":provider,"model":model,"tags":tags,"intent":intent,"params":params}, headers)

    def generate_with(self, prompt: str, generator: Callable[[str], bytes], *, provider: str, model: str, media_type: str="image/png", tags: list[str] | None=None, intent: dict[str, str] | None=None, params: dict[str, Any] | None=None) -> RelayResult:
        """Reuse first, then run any caller-owned media generator only on a safe miss.

        ``generator`` executes in the caller's process, so its provider credentials never
        transit Recall. It must return the completed media bytes for archival.
        """
        tags, intent, params = tags or [], intent or {}, params or {}
        gate = self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse":
            return RelayResult("reused", gate["matches"][0], gate["receipt"])
        media = generator(prompt)
        if not isinstance(media, bytes) or not media:
            raise TypeError("generator must return non-empty media bytes")
        encoded = base64.b64encode(media).decode()
        captured = self._capture(prompt=prompt, media_base64=encoded, media_type=media_type, provider=provider, model=model, tags=tags, intent=intent, params={"relay":"custom-provider", **params})
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)
    def generate_gemini(self, prompt: str, *, model: str="gemini-3.1-flash-image", tags: list[str] | None=None, intent: dict[str, str] | None=None, response_format: dict[str, Any] | None=None) -> RelayResult:
        tags, intent = tags or [], intent or {}
        gate=self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse":
            return RelayResult("reused", gate["matches"][0], gate["receipt"])
        if not self.gemini_key:
            raise RuntimeError("No reusable result; supply gemini_key to create and capture a new asset.")
        interaction=self._post("https://generativelanguage.googleapis.com/v1beta/interactions", {"model":model,"input":prompt, **({"response_format":response_format} if response_format else {})}, {"x-goog-api-key":self.gemini_key})
        image=(interaction.get("output_image") or {}).get("data")
        if not image: raise RuntimeError("Gemini returned no output_image data; inspect the interaction response for an unsupported mode.")
        media=base64.b64decode(image)
        captured=self._capture(prompt=prompt, media_base64=image, media_type=(interaction.get("output_image") or {}).get("mime_type", "image/png"), provider="google", model=model, tags=tags, intent=intent, params={"relay":"gemini-interactions"})
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)

    def generate_openai(self, prompt: str, *, model: str="gpt-image-1", tags: list[str] | None=None, intent: dict[str, str] | None=None, size: str | None=None) -> RelayResult:
        """Use the caller's OpenAI key locally; Recall never receives that key."""
        tags, intent = tags or [], intent or {}
        gate=self._check(prompt, tags, intent)
        if gate["recommendation"] == "reuse": return RelayResult("reused", gate["matches"][0], gate["receipt"])
        if not self.openai_key: raise RuntimeError("No reusable result; supply openai_key to create and capture a new asset.")
        body={"model":model,"prompt":prompt,"response_format":"b64_json"}
        if size: body["size"]=size
        result=self._post("https://api.openai.com/v1/images/generations", body, {"Authorization":f"Bearer {self.openai_key}"})
        image=((result.get("data") or [{}])[0]).get("b64_json")
        if not image: raise RuntimeError("OpenAI returned no b64_json image data.")
        media=base64.b64decode(image)
        captured=self._capture(prompt=prompt, media_base64=image, media_type="image/png", provider="openai", model=model, tags=tags, intent=intent, params={"relay":"openai-images", **({"size":size} if size else {})})
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)
