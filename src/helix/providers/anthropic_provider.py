"""Real Anthropic-backed provider.

Requires the `anthropic` package (`pip install -e ".[providers]"`) and an
`ANTHROPIC_API_KEY` environment variable. A Claude Code / Claude Ultra
subscription does NOT provide this - it is a separate, pay-as-you-go API
key. Not imported by default anywhere in `helix` so that the rest of the
system has zero hard dependency on the Anthropic SDK or network access.
"""

from __future__ import annotations

import os

from helix.providers.base import LLMProvider, ProviderResponse

# Pricing as of this writing, USD per million tokens. Update if pricing
# changes; this is best-effort cost *estimation*, not a billing record.
_PRICING_PER_MILLION_TOKENS = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
}
_DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = _DEFAULT_MODEL, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the optional extra
            raise ImportError(
                "AnthropicProvider requires the 'anthropic' package: pip install -e '.[providers]'"
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Note: a Claude Code/Claude Ultra subscription does "
                "not provide programmatic API credits - a separate API key is required."
            )
        self.model = model
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        pricing = _PRICING_PER_MILLION_TOKENS.get(self.model)
        cost = (
            (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
            if pricing
            else 0.0
        )
        return ProviderResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            stop_reason=response.stop_reason or "end_turn",
        )
