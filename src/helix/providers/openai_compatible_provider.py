"""Generic provider for any service implementing the OpenAI chat-completions
wire format - which, as of this writing, includes OpenAI itself, Groq,
Together AI, Fireworks, DeepSeek, Azure OpenAI, and most self-hosted
inference servers (vLLM, LM Studio, text-generation-webui, and Ollama's
own `/v1` compatibility endpoint). One class, parameterized by
`base_url` + `api_key_env`, covers all of them - "any API key" is a
matter of pointing this at the right URL and environment variable, not
writing a new provider per vendor.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from helix.providers.base import LLMProvider, ProviderResponse

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def _is_local(base_url: str) -> bool:
    return any(host in base_url for host in ("localhost", "127.0.0.1", "0.0.0.0"))


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_seconds: float = 120.0,
        json_mode: bool = True,
        pricing_per_million_tokens: Optional[dict[str, float]] = None,
    ) -> None:
        """
        `pricing_per_million_tokens`: optional {"input": x, "output": y} in USD.
        Cost estimation is impossible in general for an arbitrary endpoint - if
        omitted, `estimated_cost_usd` is always 0.0, meaning "unknown", not
        "free". Supply it yourself for whichever model/vendor you're pointing at.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.json_mode = json_mode
        self.pricing = pricing_per_million_tokens

        key = api_key or os.environ.get(api_key_env)
        if not key and not _is_local(self.base_url):
            raise RuntimeError(
                f"No API key found. Set the {api_key_env} environment variable, or pass "
                f"api_key= directly, or point base_url at a local server that doesn't need one."
            )
        self.api_key = key
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post_chat_completion(self, payload: dict) -> requests.Response:
        return requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._post_chat_completion(payload)
            if response.status_code == 400 and self.json_mode:
                # Not every OpenAI-compatible server supports response_format -
                # retry once without it rather than failing outright.
                payload.pop("response_format")
                response = self._post_chat_completion(payload)
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"Could not reach {self.base_url} - is the server running?") from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(f"{self.base_url} did not respond within {self.timeout_seconds}s.") from exc
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        text = choice["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        cost = 0.0
        if self.pricing:
            cost = (
                input_tokens * self.pricing.get("input", 0.0) + output_tokens * self.pricing.get("output", 0.0)
            ) / 1_000_000

        return ProviderResponse(
            text=text,
            model=data.get("model", self.model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            stop_reason=choice.get("finish_reason") or "unknown",
        )
