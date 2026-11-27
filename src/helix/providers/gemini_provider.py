"""Real Google Gemini-backed provider, via the native Generative Language
REST API (not the OpenAI-compatibility shim Gemini also exposes, and not
the `google-generativeai` SDK) - this keeps the same "no extra install"
property Ollama/OpenAICompatible already have: `requests` is a core
dependency, not an optional extra, so this provider needs nothing beyond
a GEMINI_API_KEY to use.

Implemented against the documented REST contract; not yet run end-to-end
against a live key in this repository (no funded Google AI key was used
to build this project) - treat it as implemented-but-unverified, the same
honest status `AnthropicProvider` carried until it was actually exercised.
See docs/agent_design.md's "Real providers" section for what live testing
against Ollama actually found, and why that caution is warranted here too.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from helix.providers.base import LLMProvider, ProviderResponse

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.0-flash"

# Pricing as of this writing, USD per million tokens - approximate, update
# if pricing changes. Unknown models fall back to $0.0 (meaning "unknown",
# not "free"), same convention as AnthropicProvider/OpenAICompatibleProvider.
_PRICING_PER_MILLION_TOKENS = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        api_key_env: str = "GEMINI_API_KEY",
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 120.0,
    ) -> None:
        key = api_key or os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(
                f"No API key found. Set the {api_key_env} environment variable, or pass api_key= "
                "directly. Get one at https://aistudio.google.com/apikey."
            )
        self.model = model
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        url = f"{self.base_url}/models/{self.model}:generateContent"

        try:
            response = requests.post(
                url, params={"key": self.api_key}, json=payload, timeout=self.timeout_seconds
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"Could not reach {self.base_url} - check network access.") from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(f"{self.base_url} did not respond within {self.timeout_seconds}s.") from exc
        response.raise_for_status()
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            # No candidate at all usually means the prompt or response was
            # blocked by Gemini's safety filters - fail clearly rather than
            # raise a confusing IndexError on an empty list.
            feedback = data.get("promptFeedback", {})
            raise RuntimeError(f"Gemini returned no candidates (promptFeedback={feedback!r}).")

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        finish_reason = candidate.get("finishReason", "unknown")

        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        pricing = _PRICING_PER_MILLION_TOKENS.get(self.model)
        cost = (
            (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
            if pricing
            else 0.0
        )

        return ProviderResponse(
            text=text,
            model=data.get("modelVersion", self.model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            stop_reason=finish_reason,
        )
