"""Strategist Agent: examines the (public, redacted) dataset metadata
and proposes a matching strategy, stated assumptions, and QC checks.
Never sees ground truth - only `dataset_metadata.json`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.orchestration.messages import MethodProposal
from helix.providers.base import ProviderResponse

_SYSTEM = (
    "You are the Strategist agent in a computational biology research team. You examine "
    "dataset metadata (never ground truth) and propose a sequence-matching approach, your "
    "assumptions, and quality-control checks. Respond with only JSON."
)


class ProposeRequest(BaseModel):
    dataset_metadata: dict[str, Any]
    research_objective: str


class StrategistAgent(Agent):
    role = "strategist"

    def propose_approach(
        self, dataset_metadata: dict[str, Any], research_objective: str
    ) -> tuple[MethodProposal, ProviderResponse]:
        request = ProposeRequest(dataset_metadata=dataset_metadata, research_objective=research_objective)
        return call_structured(self.provider, _SYSTEM, "strategist_propose", request, MethodProposal)
