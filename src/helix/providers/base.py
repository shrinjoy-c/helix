"""Provider-independent model interface.

Every agent calls a `LLMProvider`, never a specific SDK. This lets the same
agent code run against `MockProvider` (free, deterministic, no network - the
default) or a real model backend (e.g. `AnthropicProvider`) by swapping one
object. See docs/agent_design.md for which agent behaviors are genuinely
model-driven vs. deterministic regardless of provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class ProviderResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0, default=0.0)
    stop_reason: str = "end_turn"


class LLMProvider(ABC):
    """Minimal interface every provider must implement.

    `complete` is intentionally the *only* method agents call - no
    provider-specific features (tool use, streaming, etc.) leak into the
    agent layer in this phase.
    """

    name: str = "base"

    @abstractmethod
    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        raise NotImplementedError
