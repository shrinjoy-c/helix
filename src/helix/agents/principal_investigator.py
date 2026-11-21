"""Principal Investigator Agent: decomposes the research objective into
assigned tasks, and - at the end of a run - reviews whether the result
actually addresses the original objective.
"""

from __future__ import annotations

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.orchestration.messages import PIFinalReview, ResearchPlan
from helix.providers.base import ProviderResponse

_SYSTEM = (
    "You are the Principal Investigator agent in a computational biology research team. "
    "You decompose research objectives into tasks for specialist agents, and judge whether "
    "final results address the original objective. Respond with only JSON."
)


class DecomposeRequest(BaseModel):
    research_objective: str
    dataset_id: str


class FinalReviewRequest(BaseModel):
    research_objective: str
    validation_passed: bool
    iterations_used: int
    max_iterations: int


class PrincipalInvestigatorAgent(Agent):
    role = "principal_investigator"

    def decompose_objective(self, research_objective: str, dataset_id: str) -> tuple[ResearchPlan, ProviderResponse]:
        request = DecomposeRequest(research_objective=research_objective, dataset_id=dataset_id)
        return call_structured(self.provider, _SYSTEM, "pi_decompose", request, ResearchPlan)

    def review_final_results(
        self, research_objective: str, validation_passed: bool, iterations_used: int, max_iterations: int
    ) -> tuple[PIFinalReview, ProviderResponse]:
        request = FinalReviewRequest(
            research_objective=research_objective,
            validation_passed=validation_passed,
            iterations_used=iterations_used,
            max_iterations=max_iterations,
        )
        return call_structured(self.provider, _SYSTEM, "pi_review", request, PIFinalReview)
