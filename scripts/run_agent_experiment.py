"""End-to-end demonstration of the HELIX Release 0.1 agent orchestration layer.

Runs the same synthetic dataset through both the single-agent and
multi-agent workflows, using the deterministic MockProvider (free, offline,
reproducible - see helix/providers/mock_provider.py). Prints each agent's
distinct contribution (event log) and persists an ExperimentRecord per run.

To use a real model instead of the mock policy engine, set ANTHROPIC_API_KEY
and swap `MockProvider()` below for
`AnthropicProvider()` (requires `pip install -e ".[providers]"`).

Run with:
    python scripts/run_agent_experiment.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from helix.biology.simulation import write_dataset
from helix.config.models import (
    BackgroundDiversity,
    DatasetGenerationConfig,
    ErrorModel,
    ExperimentRecord,
    TargetSpec,
)
from helix.experiments.store import ExperimentStore
from helix.orchestration.orchestrator import OrchestratorResult, run_multi_agent, run_single_agent
from helix.providers.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
SANDBOX_ROOT = REPO_ROOT / "experiments" / "results" / "_sandbox"
DB_PATH = REPO_ROOT / "experiments" / "results" / "helix.db"

RESEARCH_OBJECTIVE = (
    "Determine whether the reference target is present in this sample and, if so, estimate "
    "its abundance, while minimizing false positive detections."
)


def _scenario() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="agent_demo_moderate_indels",
        seed=77,
        description="Moderate target abundance with a nonzero indel rate - exercises the "
        "Reviewer's ability to catch the Implementation Agent's initial, indel-blind code.",
        sequence_length=45,
        read_depth=4000,
        targets=[TargetSpec(target_id="target_a", abundance=0.08, present=True)],
        n_background_sequences=100,
        background_diversity=BackgroundDiversity.MEDIUM,
        error_model=ErrorModel(substitution_rate=0.01, insertion_rate=0.004, deletion_rate=0.004),
        duplicate_rate=0.1,
    )


def _print_event_log(result: OrchestratorResult) -> None:
    for event in result.event_log:
        print(f"  [{event.sequence:02d}] {event.actor:>22s} | {event.event_type:<20s} | {event.message}")


def _persist(
    result: OrchestratorResult,
    config: DatasetGenerationConfig,
    workflow: str,
    store: ExperimentStore,
    provider_name: str,
) -> None:
    record = ExperimentRecord(
        experiment_id=f"exp_{workflow}_{uuid.uuid4().hex[:10]}",
        dataset_id=config.dataset_id,
        pipeline_name=workflow,
        status=result.state.status,
        dataset_config=config,
        matching_config=result.state.matching_config,
        analysis_result=result.state.execution_results[-1].analysis_result if result.state.execution_results else None,
        evaluation_metrics=result.metrics,
        runtime_seconds=result.state.runtime_seconds,
        created_at=datetime.now(timezone.utc).isoformat(),
        iteration_count=result.state.iteration,
        token_usage=result.state.token_usage,
        estimated_cost_usd=result.state.estimated_cost_usd,
        event_log=[e.model_dump() for e in result.event_log],
        provider=provider_name,
        model=result.state.model_name,
    )
    store.save(record)
    print(f"  Saved experiment record: {record.experiment_id}")


def main() -> None:
    config = _scenario()
    paths = write_dataset(config, DATASETS_DIR)
    dataset_dir = paths["reads_fastq"].parent
    provider = MockProvider()

    with ExperimentStore(DB_PATH) as store:
        print("\n=== Single-agent workflow ===")
        single = run_single_agent(
            f"exp_single_{uuid.uuid4().hex[:8]}", dataset_dir, RESEARCH_OBJECTIVE, provider,
            SANDBOX_ROOT, max_iterations=3, timeout_seconds=30.0,
        )
        _print_event_log(single)
        print(
            f"  status={single.state.status} iterations={single.state.iteration} "
            f"tokens={single.state.token_usage} precision={single.metrics.precision if single.metrics else None} "
            f"recall={single.metrics.recall if single.metrics else None}"
        )
        _persist(single, config, "single_agent", store, provider.name)

        print("\n=== Multi-agent workflow ===")
        multi = run_multi_agent(
            f"exp_multi_{uuid.uuid4().hex[:8]}", dataset_dir, RESEARCH_OBJECTIVE, provider,
            SANDBOX_ROOT, max_iterations=3, timeout_seconds=30.0,
        )
        _print_event_log(multi)
        print(
            f"  status={multi.state.status} iterations={multi.state.iteration} "
            f"tokens={multi.state.token_usage} precision={multi.metrics.precision if multi.metrics else None} "
            f"recall={multi.metrics.recall if multi.metrics else None}"
        )
        if multi.state.review_findings:
            rejected = [f for f in multi.state.review_findings if not f.approved]
            print(f"  Reviewer rejected {len(rejected)}/{len(multi.state.review_findings)} code version(s); "
                  f"issues on first rejection (if any): {rejected[0].issues if rejected else 'none'}")
        if multi.state.disagreements:
            d = multi.state.disagreements[0]
            print(f"  Experiment Designer resolved a disagreement: {d.topic} -> {d.resolution} ({d.rationale})")
        _persist(multi, config, "multi_agent", store, provider.name)

        print(f"\nAll experiments persisted to: {DB_PATH}")
        print(f"Experiment IDs: {store.list_ids()}")


if __name__ == "__main__":
    main()
