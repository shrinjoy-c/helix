"""Shared agent plumbing: every agent calls its provider through
`call_structured`, which is the one place prompts are built and typed
responses are parsed. Individual agent classes never hand-parse free text.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from helix.providers.base import LLMProvider, ProviderResponse

T = TypeVar("T", bound=BaseModel)


class AgentCallError(RuntimeError):
    """Raised when a provider's response cannot be parsed as the expected output model."""


def build_task_prompt(task_type: str, payload: BaseModel) -> str:
    return (
        f"TASK_TYPE: {task_type}\n"
        f"```json\n{payload.model_dump_json()}\n```\n"
        "Respond with only a single JSON object matching the requested schema - no prose, no markdown fences."
    )


def call_structured(
    provider: LLMProvider,
    system: str,
    task_type: str,
    payload: BaseModel,
    output_model: type[T],
) -> tuple[T, ProviderResponse]:
    prompt = build_task_prompt(task_type, payload)
    response = provider.complete(system, prompt)
    try:
        parsed = output_model.model_validate_json(response.text)
    except ValidationError as exc:
        raise AgentCallError(
            f"{provider.name} provider response for task_type={task_type!r} did not match "
            f"{output_model.__name__}: {exc}"
        ) from exc
    return parsed, response


class Agent:
    role: str = "base"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
