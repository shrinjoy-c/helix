"""Combined lookup of every pipeline configuration (baselines + ablations)
by name, for the API layer. Lives here rather than in
`helix.benchmarking` to avoid a circular import (ablations.py already
imports from baselines.py).
"""

from __future__ import annotations

from helix.benchmarking.ablations import ABLATION_CONFIGURATIONS
from helix.benchmarking.baselines import BASELINE_CONFIGURATIONS, PipelineSpec

ALL_PIPELINE_SPECS: list[PipelineSpec] = BASELINE_CONFIGURATIONS + ABLATION_CONFIGURATIONS
PIPELINE_REGISTRY: dict[str, PipelineSpec] = {spec.name: spec for spec in ALL_PIPELINE_SPECS}
