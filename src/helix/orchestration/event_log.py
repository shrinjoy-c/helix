"""Append-only event log for a single experiment run.

This is the record an Agent Observatory UI (future release) would render:
every agent invocation, review verdict, execution attempt, and
orchestrator decision, in order, with enough payload to reconstruct what
happened without re-running anything.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "task_assigned",
    "agent_invoked",
    "method_proposed",
    "disagreement_resolved",
    "code_written",
    "code_reviewed",
    "code_executed",
    "verification_run",
    "verifier_guidance",
    "iteration_complete",
    "revision_requested",
    "ablation_applied",
    "parallel_branch_explored",
    "parallel_branch_selected",
    "experiment_cancelled",
    "experiment_timed_out",
    "experiment_budget_exceeded",
    "experiment_completed",
    "experiment_failed",
]


class EventLogEntry(BaseModel):
    sequence: int
    event_type: EventType
    actor: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EventLog:
    """Append-only: entries can be read and exported, never mutated or removed."""

    def __init__(self) -> None:
        self._entries: list[EventLogEntry] = []

    def append(self, event_type: EventType, actor: str, message: str, **payload: Any) -> EventLogEntry:
        entry = EventLogEntry(
            sequence=len(self._entries), event_type=event_type, actor=actor, message=message, payload=payload
        )
        self._entries.append(entry)
        return entry

    def to_list(self) -> list[EventLogEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)
