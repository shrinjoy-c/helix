"""Experiment Designer Agent: turns a (possibly contested) method proposal
into concrete, executable parameters, explicitly recording any tradeoff
between stricter matching (fewer false positives) and more permissive
matching (fewer false negatives under higher error rates) rather than
silently picking one side.
"""

from __future__ import annotations

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.orchestration.messages import MatchingDecision, MethodProposal
from helix.providers.base import ProviderResponse

_SYSTEM = (
    "You are the Experiment Designer agent in a computational biology research team. You "
    "resolve disagreements between other agents' recommendations into concrete, justified "
    "experiment parameters. Respond with only JSON."
)


class ResolveRequest(BaseModel):
    strategist_max_mismatches: int
    collapse_duplicates: bool
    min_count: int
    total_error_rate: float


class ExperimentDesignerAgent(Agent):
    role = "experiment_designer"

    def resolve_matching_config(
        self, proposal: MethodProposal, total_error_rate: float
    ) -> tuple[MatchingDecision, ProviderResponse]:
        request = ResolveRequest(
            strategist_max_mismatches=proposal.recommended_max_mismatches,
            collapse_duplicates=proposal.recommended_collapse_duplicates,
            min_count=proposal.recommended_min_count,
            total_error_rate=total_error_rate,
        )
        return call_structured(self.provider, _SYSTEM, "experiment_designer_resolve", request, MatchingDecision)
