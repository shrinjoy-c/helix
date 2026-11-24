"""Shared agent plumbing: every agent calls its provider through
`call_structured`, which is the one place prompts are built and typed
responses are parsed. Individual agent classes never hand-parse free text.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from helix.providers.base import LLMProvider, ProviderResponse

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class AgentCallError(RuntimeError):
    """Raised when a provider's response cannot be parsed as the expected output model."""


def build_task_prompt(task_type: str, payload: BaseModel, output_model: type[BaseModel] | None = None) -> str:
    """Builds the prompt every agent sends its provider.

    Critically, this embeds the *output* schema's exact field names (via
    `output_model.model_json_schema()`), not just a generic "match the
    schema" instruction. This was found to matter in practice: tested live
    against a local Ollama model (qwen3:4b), omitting the output schema
    produced plausible-sounding JSON under *different* field names
    (`sequence_matching_approach` instead of `approach_summary`, etc.) -
    conceptually on-task, but unparseable as the required Pydantic model.
    Smaller/local models apparently need the exact contract spelled out;
    larger hosted models may have been more forgiving, but no one had
    actually exercised this path with a real model before. MockProvider's
    `_extract_task` only ever reads the *first* ```json fence (the input
    payload, emitted before the schema below), so this is safe to add
    without touching mock behavior - verified by the existing test suite.
    """
    sections = [
        f"TASK_TYPE: {task_type}",
        "Input:",
        f"```json\n{payload.model_dump_json()}\n```",
    ]
    if output_model is not None:
        sections.append(
            "Respond with only a single JSON object matching exactly this schema (the same "
            "field names and types - do not rename or omit any field) - no prose, no markdown fences:"
        )
        sections.append(f"```json\n{json.dumps(output_model.model_json_schema())}\n```")
    else:
        sections.append(
            "Respond with only a single JSON object matching the requested schema - no prose, no markdown fences."
        )
    return "\n".join(sections)


def _extract_json_text(text: str) -> str:
    """Best-effort recovery of a JSON object from a real model's response.

    MockProvider and well-behaved APIs return exactly the JSON asked for, so
    this is never exercised for them. Real models - especially smaller local
    ones - routinely ignore "no prose/no fences" instructions: they wrap the
    answer in a ```json fence, add a sentence before or after it, or (for a
    reasoning model) emit a <think>...</think> block first. This recovers the
    JSON object in each of those cases; it does not attempt to fix malformed
    JSON itself.
    """
    text = _THINK_BLOCK_RE.sub("", text).strip()
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def call_structured(
    provider: LLMProvider,
    system: str,
    task_type: str,
    payload: BaseModel,
    output_model: type[T],
) -> tuple[T, ProviderResponse]:
    prompt = build_task_prompt(task_type, payload, output_model)
    response = provider.complete(system, prompt)
    try:
        parsed = output_model.model_validate_json(response.text)
    except ValidationError:
        try:
            parsed = output_model.model_validate_json(_extract_json_text(response.text))
        except ValidationError as exc:
            raise AgentCallError(
                f"{provider.name} provider response for task_type={task_type!r} did not match "
                f"{output_model.__name__}: {exc}\nraw response: {response.text[:500]!r}"
            ) from exc
    return parsed, response


class Agent:
    role: str = "base"

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
