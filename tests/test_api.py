"""Tests for the FastAPI backend: endpoint behavior, job polling,
dataset redaction, and error handling. Each test gets its own isolated
app (tmp_path-scoped datasets/sandbox/db) via `create_app`.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from helix.api.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "datasets", tmp_path / "sandbox", tmp_path / "test.db")
    return TestClient(app)


def _dataset_payload(dataset_id: str = "ds1", **overrides) -> dict:
    payload = {
        "dataset_id": dataset_id,
        "seed": 42,
        "sequence_length": 35,
        "read_depth": 600,
        "targets": [{"target_id": "t1", "abundance": 0.1, "present": True}],
        "n_background_sequences": 15,
        "error_model": {"substitution_rate": 0.005, "insertion_rate": 0.0, "deletion_rate": 0.0},
    }
    payload.update(overrides)
    return payload


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_pipelines_includes_all_configurations(client):
    names = {p["name"] for p in client.get("/pipelines").json()}
    assert names == {
        "deterministic_baseline", "single_agent", "sequential_multi_agent",
        "multi_agent_with_verification", "parallel_multi_agent",
        "ablation_no_reviewer", "ablation_no_experiment_designer", "ablation_no_verification_loop",
    }


# --- datasets ---------------------------------------------------------


def test_create_dataset_response_never_leaks_ground_truth(client):
    response = client.post("/datasets", json=_dataset_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] == "ds1"
    serialized = str(body)
    assert "abundance" not in serialized
    assert '"present"' not in serialized


def test_list_datasets_returns_created_dataset(client):
    client.post("/datasets", json=_dataset_payload("ds1"))
    client.post("/datasets", json=_dataset_payload("ds2"))
    ids = {d["dataset_id"] for d in client.get("/datasets").json()}
    assert ids == {"ds1", "ds2"}


def test_get_dataset_by_id(client):
    client.post("/datasets", json=_dataset_payload("ds1"))
    response = client.get("/datasets/ds1")
    assert response.status_code == 200
    assert response.json()["dataset_id"] == "ds1"


def test_get_unknown_dataset_returns_404(client):
    assert client.get("/datasets/does_not_exist").status_code == 404


# --- experiments --------------------------------------------------------


def test_launch_experiment_with_unknown_pipeline_returns_400(client):
    client.post("/datasets", json=_dataset_payload())
    response = client.post("/experiments", json={"dataset_id": "ds1", "pipeline_name": "not_a_real_pipeline"})
    assert response.status_code == 400


def test_launch_and_poll_experiment_completes(client):
    client.post("/datasets", json=_dataset_payload())
    launch = client.post(
        "/experiments",
        json={"dataset_id": "ds1", "pipeline_name": "single_agent", "max_iterations": 3, "timeout_seconds": 30},
    )
    assert launch.status_code == 200
    job = _wait_for_job(client, launch.json()["job_id"])
    assert job["status"] == "completed"
    assert len(job["experiment_ids"]) == 1

    experiment = client.get(f"/experiments/{job['experiment_ids'][0]}").json()
    assert experiment["status"] == "completed"
    assert experiment["evaluation_metrics"]["precision"] == pytest.approx(1.0)


def test_launch_experiment_against_missing_dataset_fails_the_job_not_the_request(client):
    launch = client.post("/experiments", json={"dataset_id": "nope", "pipeline_name": "single_agent"})
    assert launch.status_code == 200  # request itself is accepted
    job = _wait_for_job(client, launch.json()["job_id"])
    assert job["status"] == "failed"
    assert "nope" in job["error"]


def test_launch_experiment_with_ollama_provider_unreachable_fails_the_job_cleanly(client):
    """Doesn't require a live Ollama instance: pointed at a port nothing
    listens on, to prove the provider selection actually reaches
    OllamaProvider (and its clear "could not reach" error) through the
    full API path, not just unit-tested in isolation."""
    client.post("/datasets", json=_dataset_payload())
    launch = client.post(
        "/experiments",
        json={
            "dataset_id": "ds1", "pipeline_name": "single_agent",
            "provider": "ollama", "base_url": "http://localhost:19999",
        },
    )
    assert launch.status_code == 200
    job = _wait_for_job(client, launch.json()["job_id"])
    assert job["status"] == "failed"
    assert "Could not reach Ollama" in job["error"]


def test_launch_experiment_with_openai_compatible_missing_key_fails_the_job_cleanly(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client.post("/datasets", json=_dataset_payload())
    launch = client.post(
        "/experiments",
        json={"dataset_id": "ds1", "pipeline_name": "single_agent", "provider": "openai_compatible"},
    )
    job = _wait_for_job(client, launch.json()["job_id"])
    assert job["status"] == "failed"
    assert "No API key found" in job["error"]


def test_launch_experiment_rejects_unknown_provider_name(client):
    client.post("/datasets", json=_dataset_payload())
    response = client.post(
        "/experiments",
        json={"dataset_id": "ds1", "pipeline_name": "single_agent", "provider": "not_a_real_provider"},
    )
    assert response.status_code == 422  # rejected by Pydantic's Literal validation, never reaches a job


def test_experiments_list_filters_by_dataset_and_pipeline(client):
    client.post("/datasets", json=_dataset_payload())
    for pipeline in ("deterministic_baseline", "single_agent"):
        launch = client.post("/experiments", json={"dataset_id": "ds1", "pipeline_name": pipeline})
        _wait_for_job(client, launch.json()["job_id"])

    all_experiments = client.get("/experiments").json()
    assert len(all_experiments) == 2

    filtered = client.get("/experiments", params={"pipeline_name": "single_agent"}).json()
    assert len(filtered) == 1
    assert filtered[0]["pipeline_name"] == "single_agent"


def test_get_unknown_experiment_returns_404(client):
    assert client.get("/experiments/does_not_exist").status_code == 404


def test_get_unknown_job_returns_404(client):
    assert client.get("/jobs/does_not_exist").status_code == 404


# --- benchmark sweeps ----------------------------------------------------


def test_launch_benchmark_sweep_and_poll_result(client):
    client.post("/datasets", json=_dataset_payload())
    launch = client.post(
        "/benchmarks",
        json={
            "dataset_ids": ["ds1"], "pipeline_names": ["deterministic_baseline", "single_agent"],
            "n_repeats": 2, "max_iterations": 3, "timeout_seconds": 30,
        },
    )
    job = _wait_for_job(client, launch.json()["job_id"], timeout=20.0)
    assert job["status"] == "completed"
    assert len(job["experiment_ids"]) == 2 * 2  # 2 pipelines x 2 repeats

    result = client.get(f"/benchmarks/{job['job_id']}").json()
    assert result["result"] is not None
    pipeline_names = {s["pipeline_name"] for s in result["result"]["summaries"]}
    assert pipeline_names == {"deterministic_baseline", "single_agent"}
    for s in result["result"]["summaries"]:
        assert s["n_runs"] == 2


def test_benchmark_result_before_completion_has_null_result(client):
    client.post("/datasets", json=_dataset_payload())
    launch = client.post(
        "/benchmarks", json={"dataset_ids": ["ds1"], "pipeline_names": ["single_agent"], "n_repeats": 1}
    )
    job_id = launch.json()["job_id"]
    immediate = client.get(f"/benchmarks/{job_id}").json()
    # whether or not it has already finished by the time we ask depends on
    # scheduling, but the contract (null result while not completed) must hold
    if immediate["job"]["status"] != "completed":
        assert immediate["result"] is None
    _wait_for_job(client, job_id)
    final = client.get(f"/benchmarks/{job_id}").json()
    assert final["result"] is not None


def test_benchmark_sweep_against_unknown_dataset_fails_the_job(client):
    launch = client.post(
        "/benchmarks", json={"dataset_ids": ["never_created"], "pipeline_names": ["single_agent"]}
    )
    job = _wait_for_job(client, launch.json()["job_id"])
    assert job["status"] == "failed"
    assert "never_created" in job["error"]


# --- restart survival ---------------------------------------------------
#
# A fresh create_app() call against the same datasets_dir/db_path simulates
# a server restart: a brand-new in-memory job registry and dataset-config
# cache, but the same files/database on disk.


def test_benchmark_sweep_works_for_a_dataset_created_in_a_previous_server_process(tmp_path):
    datasets_dir, sandbox_root, db_path = tmp_path / "datasets", tmp_path / "sandbox", tmp_path / "test.db"
    first_server = TestClient(create_app(datasets_dir, sandbox_root, db_path))
    first_server.post("/datasets", json=_dataset_payload())

    second_server = TestClient(create_app(datasets_dir, sandbox_root, db_path))
    launch = second_server.post(
        "/benchmarks", json={"dataset_ids": ["ds1"], "pipeline_names": ["single_agent"], "n_repeats": 1}
    )
    job = _wait_for_job(second_server, launch.json()["job_id"])
    assert job["status"] == "completed"
    assert len(job["experiment_ids"]) == 1


def test_job_and_experiment_status_survive_a_restart(tmp_path):
    datasets_dir, sandbox_root, db_path = tmp_path / "datasets", tmp_path / "sandbox", tmp_path / "test.db"
    first_server = TestClient(create_app(datasets_dir, sandbox_root, db_path))
    first_server.post("/datasets", json=_dataset_payload())
    launch = first_server.post("/experiments", json={"dataset_id": "ds1", "pipeline_name": "single_agent"})
    job_id = launch.json()["job_id"]
    completed_job = _wait_for_job(first_server, job_id)
    experiment_id = completed_job["experiment_ids"][0]

    second_server = TestClient(create_app(datasets_dir, sandbox_root, db_path))
    assert second_server.get(f"/jobs/{job_id}").json()["status"] == "completed"
    assert second_server.get(f"/experiments/{experiment_id}").json()["experiment_id"] == experiment_id
