"""Ablation configurations (F): each removes exactly one coordination
mechanism from the full multi-agent system
(`baselines.MULTI_AGENT_WITH_VERIFICATION`), isolating that mechanism's
contribution per H5 ("removing specific agents will reveal which roles
contribute meaningfully").

Note that "remove the verification/revision loop" is not redefined here:
it is exactly `baselines.SEQUENTIAL_MULTI_AGENT` (configuration C), since
that configuration *is* the full system with only
`enable_verification_loop=False` - defining a second, identical spec
under a different name would just be duplication.
"""

from __future__ import annotations

from helix.benchmarking.baselines import PipelineSpec, SEQUENTIAL_MULTI_AGENT

NO_REVIEWER = PipelineSpec(name="ablation_no_reviewer", kind="multi_agent", enable_reviewer=False)
NO_EXPERIMENT_DESIGNER = PipelineSpec(
    name="ablation_no_experiment_designer", kind="multi_agent", enable_experiment_designer=False
)
NO_VERIFICATION_LOOP = SEQUENTIAL_MULTI_AGENT.model_copy(update={"name": "ablation_no_verification_loop"})
NO_VERIFIER_AGENT = PipelineSpec(name="ablation_no_verifier_agent", kind="multi_agent", enable_verifier_agent=False)

ABLATION_CONFIGURATIONS: list[PipelineSpec] = [
    NO_REVIEWER,
    NO_EXPERIMENT_DESIGNER,
    NO_VERIFICATION_LOOP,
    NO_VERIFIER_AGENT,
]
