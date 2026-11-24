"""Demonstrates HELIX with a real local model via Ollama, instead of the
default deterministic MockProvider.

Requires `ollama serve` running locally with a model pulled, e.g.:
    ollama pull qwen3:4b

Reasoning-capable local models need a large token budget - OllamaProvider
defaults to an 8192-token floor for exactly this reason (see its
docstring, and docs/agent_design.md's "Real providers" section for what
was actually found when this was first tested: a 1024-token budget
produced empty responses because the model spent its whole budget on a
<think> block). Expect each agent call to take tens of seconds to a few
minutes depending on your hardware - this is real inference, not the
millisecond-scale MockProvider.

Run with:
    python scripts/run_with_ollama.py [model]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from helix.biology.simulation import write_dataset
from helix.config.models import DatasetGenerationConfig, ErrorModel, TargetSpec
from helix.orchestration.orchestrator import run_single_agent
from helix.providers.ollama_provider import OllamaProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
SANDBOX_ROOT = REPO_ROOT / "experiments" / "results" / "_sandbox"


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b"

    config = DatasetGenerationConfig(
        dataset_id="ollama_demo",
        seed=11,
        description="Small, easy dataset - kept small deliberately so a local model's "
        "slower inference doesn't make this demo take forever.",
        sequence_length=40,
        read_depth=500,
        targets=[TargetSpec(target_id="target_a", abundance=0.1, present=True)],
        n_background_sequences=15,
        error_model=ErrorModel(substitution_rate=0.005, insertion_rate=0.0, deletion_rate=0.0),
    )
    paths = write_dataset(config, DATASETS_DIR)
    dataset_dir = paths["reads_fastq"].parent

    print(f"Using Ollama model: {model}")
    print("This will take a while - local inference, not MockProvider's milliseconds.\n")
    # A longer per-call timeout than OllamaProvider's own default: code-
    # generation-length replies were observed taking longer than 240-300s on
    # at least one development machine (see docs/agent_design.md) - this is
    # not a guarantee every call finishes within 600s either, just a more
    # generous budget for a demo script than the library default.
    provider = OllamaProvider(model=model, timeout_seconds=600.0)

    start = time.monotonic()
    result = run_single_agent(
        "ollama_demo_exp", dataset_dir,
        "Determine whether the reference target is present and, if so, estimate its abundance.",
        provider, SANDBOX_ROOT, max_iterations=2, timeout_seconds=900.0,
    )
    elapsed = time.monotonic() - start

    print(f"\nFinished in {elapsed:.1f}s")
    print(f"status={result.state.status} iterations={result.state.iteration}")
    print(f"token usage: {result.state.token_usage}")
    if result.state.proposed_methods:
        print(f"\nStrategist Agent proposed: {result.state.proposed_methods[0].approach_summary}")
    if result.metrics:
        print(
            f"\nprecision={result.metrics.precision:.2f} recall={result.metrics.recall:.2f} "
            f"f1={result.metrics.f1:.2f}"
        )
    else:
        print("\nNo metrics - execution did not reach verification. Check the event log below.")

    print("\nEvent log:")
    for event in result.event_log:
        print(f"  [{event.sequence:02d}] {event.actor:>14s} | {event.event_type:<16s} | {event.message}")


if __name__ == "__main__":
    main()
