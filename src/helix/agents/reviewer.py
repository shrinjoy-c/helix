"""Independent Reviewer Agent: inspects generated code and does not
simply approve it. Combines a real static-analysis security gate (banned
patterns like `eval`, `subprocess`, raw sockets - checked by plain string
inspection, not by asking a model) with a model/rule-driven qualitative
review of the method and code. The security gate always runs, regardless
of which provider backs the qualitative portion, and alone can block
approval.
"""

from __future__ import annotations

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.orchestration.messages import CodeArtifact, ReviewFindings
from helix.providers.base import ProviderResponse

_SYSTEM = (
    "You are the Independent Reviewer agent in a computational biology research team. You "
    "inspect generated code and proposed methods for logical errors, missed edge cases, and "
    "unsupported assumptions. You do not automatically approve another agent's work. Respond "
    "with only JSON."
)

_BANNED_PATTERNS = (
    "eval(",
    "exec(",
    "os.system",
    "subprocess",
    "socket.",
    "import requests",
    "urllib.request",
    "__import__",
)


class ReviewRequest(BaseModel):
    code: str


def static_security_scan(code: str) -> list[str]:
    """Deterministic, always-on check independent of any provider."""
    return [f"Disallowed pattern found in generated code: `{p}`" for p in _BANNED_PATTERNS if p in code]


class ReviewerAgent(Agent):
    role = "reviewer"

    def review(self, code_artifact: CodeArtifact) -> tuple[ReviewFindings, ProviderResponse]:
        request = ReviewRequest(code=code_artifact.code)
        findings, response = call_structured(self.provider, _SYSTEM, "reviewer_review", request, ReviewFindings)

        # The security gate is non-negotiable and independent of the provider's own judgment:
        # even if a provider's qualitative review misses something, the deterministic scan can
        # still block approval.
        extra_violations = [v for v in static_security_scan(code_artifact.code) if v not in findings.security_violations]
        if extra_violations:
            findings = findings.model_copy(
                update={
                    "security_violations": findings.security_violations + extra_violations,
                    "approved": False,
                }
            )
        return findings, response
