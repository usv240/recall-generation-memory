"""Bring-your-own-key Recall relay for Gemini image generation.

Your Gemini key is read locally and is sent only to Google. Recall receives it never.
"""
from __future__ import annotations
import base64, json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

@dataclass
class RelayResult:
    status: str
    generation: dict[str, Any]
    receipt: dict[str, Any]
    media: bytes | None = None

class RecallRelay:
    def __init__(self, recall_url: str, *, recall_key: str | None = None, gemini_key: str | None = None) -> None:
        self.recall_url=recall_url.rstrip("/")
        self.recall_key=recall_key
        self.gemini_key=gemini_key

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        request=Request(url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json", **(headers or {})})
        return json.load(urlopen(request, timeout=180))

    def generate_gemini(self, prompt: str, *, model: str="gemini-3.1-flash-image", tags: list[str] | None=None, intent: dict[str, str] | None=None, response_format: dict[str, Any] | None=None) -> RelayResult:
        tags, intent = tags or [], intent or {}
        gate=self._post(self.recall_url+"/api/v1/reuse-check", {"prompt":prompt,"tags":tags,"intent":intent})
        if gate["recommendation"] == "reuse":
            return RelayResult("reused", gate["matches"][0], gate["receipt"])
        if not self.gemini_key:
            raise RuntimeError("No reusable result; supply gemini_key to create and capture a new asset.")
        interaction=self._post("https://generativelanguage.googleapis.com/v1beta/interactions", {"model":model,"input":prompt, **({"response_format":response_format} if response_format else {})}, {"x-goog-api-key":self.gemini_key})
        image=(interaction.get("output_image") or {}).get("data")
        if not image: raise RuntimeError("Gemini returned no output_image data; inspect the interaction response for an unsupported mode.")
        media=base64.b64decode(image)
        headers={"X-Recall-Key":self.recall_key} if self.recall_key else {}
        captured=self._post(self.recall_url+"/api/v1/capture", {"prompt":prompt,"media_base64":image,"media_type":(interaction.get("output_image") or {}).get("mime_type", "image/png"),"provider":"google","model":model,"tags":tags,"intent":intent,"params":{"relay":"gemini-interactions"}}, headers)
        return RelayResult("generated_and_captured", captured["generation"], gate["receipt"], media)
