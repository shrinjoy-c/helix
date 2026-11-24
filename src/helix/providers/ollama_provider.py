"""Local Ollama backend - no API key, no network egress beyond localhost
(or wherever the Ollama daemon is reachable). Uses Ollama's native
`/api/chat` endpoint rather than its OpenAI-compatibility shim, since the
native endpoint's token-count fields (`prompt_eval_count`/`eval_count`)
are what Ollama actually reports.

This is the cheapest way to exercise the *real* agent/provider code path
(prompting, response parsing, retries) without spending money on a
hosted API - useful for development and for anyone without a funded
Anthropic/OpenAI key.
"""

from __future__ import annotations

import requests

from helix.providers.base import LLMProvider, ProviderResponse

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 240.0,
        min_predict_tokens: int = 8192,
        num_ctx: int = 16384,
    ) -> None:
        """
        `min_predict_tokens`: a reasoning-capable local model (e.g. qwen3) can
        spend its entire token budget on a <think> block and never reach the
        actual answer if that budget is too small - reproduced directly
        against a live Ollama instance during development: a trivial prompt
        completed in ~400 tokens; a real agent prompt (with its full input
        payload and output schema) needed ~3700 of an 8192 budget to finish
        with done_reason="stop", while a 1024 budget produced empty content
        with done_reason="length" (truncated mid-thought). `complete()`'s own
        `max_tokens` is raised to at least this floor rather than honored
        literally, specifically to avoid that failure mode.

        `num_ctx`: Ollama's default context window (observed at 4096 for a
        freshly-pulled model, via `ollama ps`/the running llama-server's `-c`
        flag) can be smaller than `min_predict_tokens` alone, before the
        prompt even counts against it - requesting more output tokens than
        fit in the context window is not just wasted, it produced a request
        that never returned (a read timeout) during development, rather than
        a clean truncation. Passed as a per-request option, which Ollama
        honors by reconfiguring context size for the call (may reload the
        model once). Keep well under the model's own max (`ollama show
        <model>` reports it) - 16384 is comfortably inside qwen3's 262144.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.min_predict_tokens = min_predict_tokens
        self.num_ctx = num_ctx

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {
                        "num_predict": max(max_tokens, self.min_predict_tokens),
                        "num_ctx": self.num_ctx,
                    },
                },
                timeout=self.timeout_seconds,
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url} - is `ollama serve` running? "
                f"(run `ollama list` to check reachable models)"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama did not respond within {self.timeout_seconds}s. Reasoning models can be "
                f"slow on modest hardware - try a smaller model, a larger timeout_seconds, or check "
                f"`ollama ps` isn't already busy with another request."
            ) from exc
        response.raise_for_status()
        data = response.json()

        text = data.get("message", {}).get("content", "")
        input_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)
        return ProviderResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=0.0,  # local inference - no metered cost
            stop_reason="stop" if data.get("done") else "unknown",
        )
