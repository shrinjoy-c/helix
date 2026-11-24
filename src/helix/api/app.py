"""FastAPI backend: the HTTP surface over HELIX's existing Python API.

Nothing in this module computes anything itself - every endpoint is a
thin wrapper that validates input, delegates to the same functions the
CLI scripts call (`write_dataset`, `run_pipeline`, `run_benchmark_sweep`,
`aggregate_runs`), and returns their real output. Agent runs and
benchmark sweeps are launched as background tasks and polled via
`/jobs/{job_id}` - a potentially slow run must never block the request
that launched it.

Dataset generation configs (which include target abundance/presence -
the ground truth) are kept server-side only, in an in-memory registry;
every dataset-facing response (`DatasetSummary`) is built strictly from
the redacted `dataset_metadata.json` the generator itself writes, never
from the full config. This mirrors the same trust boundary enforced
everywhere else in the codebase (see docs/reproducibility.md).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from helix.agents.verifier import AcceptanceCriteria
from helix.api.jobs import JobRecord, JobRegistry
from helix.api.registry import ALL_PIPELINE_SPECS, PIPELINE_REGISTRY
from helix.api.schemas import (
    BenchmarkSweepRequest,
    BenchmarkSweepResult,
    DatasetSummary,
    ExperimentLaunchRequest,
    PipelineInfo,
    ProviderSelection,
)
from helix.benchmarking.baselines import BenchmarkBudget, new_experiment_id, run_pipeline
from helix.benchmarking.experiments import run_benchmark_sweep
from helix.benchmarking.metrics import aggregate_runs
from helix.biology.simulation import write_dataset
from helix.config.models import DatasetGenerationConfig, ExperimentRecord
from helix.experiments.store import ExperimentStore
from helix.providers.base import LLMProvider
from helix.providers.mock_provider import MockProvider

logger = logging.getLogger("helix.api")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
DEFAULT_SANDBOX_ROOT = REPO_ROOT / "experiments" / "results" / "_sandbox"
DEFAULT_DB_PATH = REPO_ROOT / "experiments" / "results" / "helix.db"


def _build_provider(selection: ProviderSelection) -> LLMProvider:
    if selection.provider == "mock":
        return MockProvider()

    if selection.provider == "anthropic":
        from helix.providers.anthropic_provider import AnthropicProvider

        kwargs = {}
        if selection.model:
            kwargs["model"] = selection.model
        return AnthropicProvider(**kwargs)

    if selection.provider == "ollama":
        from helix.providers.ollama_provider import OllamaProvider

        kwargs = {}
        if selection.model:
            kwargs["model"] = selection.model
        if selection.base_url:
            kwargs["base_url"] = selection.base_url
        return OllamaProvider(**kwargs)

    if selection.provider == "openai_compatible":
        from helix.providers.openai_compatible_provider import OpenAICompatibleProvider

        kwargs = {}
        if selection.model:
            kwargs["model"] = selection.model
        if selection.base_url:
            kwargs["base_url"] = selection.base_url
        if selection.api_key_env:
            kwargs["api_key_env"] = selection.api_key_env
        return OpenAICompatibleProvider(**kwargs)

    raise ValueError(f"unknown provider: {selection.provider!r}")  # pragma: no cover - exhaustive Literal


def _read_dataset_metadata(datasets_dir: Path, dataset_id: str) -> Optional[dict]:
    path = datasets_dir / dataset_id / "dataset_metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def create_app(
    datasets_dir: str | Path = DEFAULT_DATASETS_DIR,
    sandbox_root: str | Path = DEFAULT_SANDBOX_ROOT,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> FastAPI:
    datasets_dir = Path(datasets_dir)
    sandbox_root = Path(sandbox_root)
    datasets_dir.mkdir(parents=True, exist_ok=True)

    store = ExperimentStore(db_path)
    jobs = JobRegistry()
    # Full generation configs (including ground truth), kept server-side only.
    # Lost on restart - a dataset generated in a previous server process can
    # still be read (public metadata) but not re-used in a new benchmark sweep
    # until regenerated. See docs/architecture.md for this known limitation.
    dataset_configs: dict[str, DatasetGenerationConfig] = {}

    app = FastAPI(title="HELIX API", version="0.1.0")
    # Permissive CORS: this is a local research tool, not a public multi-tenant
    # service - the dev frontend runs on a different port (Vite's dev proxy
    # makes this unnecessary in practice, but direct cross-origin access -
    # e.g. `vite preview`, or a differently-configured dev setup - still works).
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/pipelines", response_model=list[PipelineInfo])
    def list_pipelines() -> list[PipelineInfo]:
        return [
            PipelineInfo(
                name=spec.name, kind=spec.kind,
                enable_experiment_designer=spec.enable_experiment_designer,
                enable_reviewer=spec.enable_reviewer,
                enable_verification_loop=spec.enable_verification_loop,
            )
            for spec in ALL_PIPELINE_SPECS
        ]

    @app.post("/datasets", response_model=DatasetSummary)
    def create_dataset(config: DatasetGenerationConfig) -> DatasetSummary:
        write_dataset(config, datasets_dir)
        dataset_configs[config.dataset_id] = config
        metadata = _read_dataset_metadata(datasets_dir, config.dataset_id)
        return DatasetSummary(dataset_id=config.dataset_id, description=config.description, metadata=metadata)

    @app.get("/datasets", response_model=list[DatasetSummary])
    def list_datasets() -> list[DatasetSummary]:
        summaries = []
        for entry in sorted(datasets_dir.iterdir()):
            if not entry.is_dir():
                continue
            metadata = _read_dataset_metadata(datasets_dir, entry.name)
            if metadata is not None:
                summaries.append(
                    DatasetSummary(dataset_id=entry.name, description=metadata.get("description", ""), metadata=metadata)
                )
        return summaries

    @app.get("/datasets/{dataset_id}", response_model=DatasetSummary)
    def get_dataset(dataset_id: str) -> DatasetSummary:
        metadata = _read_dataset_metadata(datasets_dir, dataset_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id!r} not found")
        return DatasetSummary(dataset_id=dataset_id, description=metadata.get("description", ""), metadata=metadata)

    def _run_experiment_job(job_id: str, request: ExperimentLaunchRequest) -> None:
        jobs.mark_running(job_id)
        try:
            dataset_dir = datasets_dir / request.dataset_id
            if not dataset_dir.exists():
                raise ValueError(f"dataset {request.dataset_id!r} not found")
            spec = PIPELINE_REGISTRY.get(request.pipeline_name)
            if spec is None:
                raise ValueError(f"unknown pipeline {request.pipeline_name!r}")
            provider = _build_provider(request.provider_selection())
            dataset_config = dataset_configs.get(request.dataset_id)
            if dataset_config is None:
                # Reconstruct a minimal config just for record-keeping; the
                # deterministic baseline/agents only ever read dataset_dir anyway.
                dataset_config = DatasetGenerationConfig(dataset_id=request.dataset_id, seed=0)
            budget = BenchmarkBudget(
                max_iterations=request.max_iterations, timeout_seconds=request.timeout_seconds,
                max_branches=request.max_branches,
            )
            criteria = AcceptanceCriteria(**request.criteria.model_dump())
            experiment_id = new_experiment_id(spec.name)
            record = run_pipeline(
                spec=spec, experiment_id=experiment_id, dataset_dir=dataset_dir,
                dataset_config=dataset_config, provider=provider, sandbox_root=sandbox_root,
                budget=budget, criteria=criteria, research_objective=request.research_objective,
            )
            store.save(record)
            jobs.mark_completed(job_id, [record.experiment_id])
        except Exception as exc:  # pragma: no cover - defensive, reported via job status
            logger.exception("experiment job %s failed", job_id)
            jobs.mark_failed(job_id, str(exc))

    @app.post("/experiments", response_model=JobRecord)
    def launch_experiment(request: ExperimentLaunchRequest, background_tasks: BackgroundTasks) -> JobRecord:
        if request.pipeline_name not in PIPELINE_REGISTRY:
            raise HTTPException(status_code=400, detail=f"unknown pipeline {request.pipeline_name!r}")
        job = jobs.create("experiment")
        background_tasks.add_task(_run_experiment_job, job.job_id, request)
        return job

    @app.get("/experiments", response_model=list[ExperimentRecord])
    def list_experiments(
        dataset_id: Optional[str] = Query(default=None), pipeline_name: Optional[str] = Query(default=None)
    ) -> list[ExperimentRecord]:
        records = store.list_all()
        if dataset_id:
            records = [r for r in records if r.dataset_id == dataset_id]
        if pipeline_name:
            records = [r for r in records if r.pipeline_name == pipeline_name]
        return records

    @app.get("/experiments/{experiment_id}", response_model=ExperimentRecord)
    def get_experiment(experiment_id: str) -> ExperimentRecord:
        record = store.load(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"experiment {experiment_id!r} not found")
        return record

    def _run_benchmark_job(job_id: str, request: BenchmarkSweepRequest) -> None:
        jobs.mark_running(job_id)
        try:
            missing = [d for d in request.dataset_ids if d not in dataset_configs]
            if missing:
                raise ValueError(
                    f"dataset(s) {missing} have no server-side generation config - "
                    "they must be created via POST /datasets in this server process first"
                )
            unknown_pipelines = [p for p in request.pipeline_names if p not in PIPELINE_REGISTRY]
            if unknown_pipelines:
                raise ValueError(f"unknown pipeline(s): {unknown_pipelines}")

            configs = [dataset_configs[d] for d in request.dataset_ids]
            specs = [PIPELINE_REGISTRY[p] for p in request.pipeline_names]
            provider = _build_provider(request.provider_selection())
            criteria = AcceptanceCriteria(**request.criteria.model_dump())

            records = run_benchmark_sweep(
                configs, specs, provider, datasets_dir, sandbox_root, store,
                budget=request.to_budget(), criteria=criteria, n_repeats=request.n_repeats,
            )
            jobs.mark_completed(job_id, [r.experiment_id for r in records])
        except Exception as exc:  # pragma: no cover - defensive, reported via job status
            logger.exception("benchmark job %s failed", job_id)
            jobs.mark_failed(job_id, str(exc))

    @app.post("/benchmarks", response_model=JobRecord)
    def launch_benchmark(request: BenchmarkSweepRequest, background_tasks: BackgroundTasks) -> JobRecord:
        job = jobs.create("benchmark_sweep")
        background_tasks.add_task(_run_benchmark_job, job.job_id, request)
        return job

    @app.get("/jobs", response_model=list[JobRecord])
    def list_jobs() -> list[JobRecord]:
        return jobs.list()

    @app.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        return job

    @app.get("/benchmarks/{job_id}")
    def get_benchmark_result(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        if job.status != "completed":
            return {"job": job.model_dump(), "result": None}
        records = [store.load(eid) for eid in job.experiment_ids]
        records = [r for r in records if r is not None]
        summaries = aggregate_runs(records)
        result = BenchmarkSweepResult(job_id=job_id, experiment_ids=job.experiment_ids, summaries=summaries)
        return {"job": job.model_dump(), "result": result.model_dump()}

    return app


app = create_app()
