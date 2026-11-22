"""Aggregation of repeated benchmark runs into means and variability.

Per the project's fairness principle: never report a single best run as
"the" result. `aggregate_runs` always reports mean + standard deviation
(0.0, not omitted, when only one run exists - distinguishable via `n`)
across every (dataset_id, pipeline_name) group it finds.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel
from scipy import stats as scipy_stats

from helix.config.models import ExperimentRecord


class MetricSummary(BaseModel):
    mean: float
    std: float
    n: int


class BenchmarkGroupSummary(BaseModel):
    dataset_id: str
    pipeline_name: str
    n_runs: int
    success_rate: float

    precision: MetricSummary
    recall: MetricSummary
    f1: MetricSummary
    false_positive_rate: MetricSummary
    false_negative_rate: MetricSummary
    abundance_mae: MetricSummary | None

    runtime_seconds: MetricSummary
    iteration_count: MetricSummary | None
    total_tokens: MetricSummary | None
    estimated_cost_usd: MetricSummary | None


def _summarize(values: list[float]) -> MetricSummary:
    if not values:
        return MetricSummary(mean=0.0, std=0.0, n=0)
    mean = statistics.mean(values)
    std = statistics.stdev(values) if len(values) >= 2 else 0.0
    return MetricSummary(mean=mean, std=std, n=len(values))


def aggregate_runs(records: list[ExperimentRecord]) -> list[BenchmarkGroupSummary]:
    groups: dict[tuple[str, str], list[ExperimentRecord]] = defaultdict(list)
    for r in records:
        groups[(r.dataset_id, r.pipeline_name)].append(r)

    summaries: list[BenchmarkGroupSummary] = []
    for (dataset_id, pipeline_name), group in groups.items():
        n_runs = len(group)
        success_rate = sum(1 for r in group if r.status == "completed") / n_runs

        scored = [r for r in group if r.evaluation_metrics is not None]
        precision = _summarize([r.evaluation_metrics.precision for r in scored])
        recall = _summarize([r.evaluation_metrics.recall for r in scored])
        f1 = _summarize([r.evaluation_metrics.f1 for r in scored])
        fpr = _summarize([r.evaluation_metrics.false_positive_rate for r in scored])
        fnr = _summarize([r.evaluation_metrics.false_negative_rate for r in scored])

        abundance_values = [
            r.evaluation_metrics.abundance_mae for r in scored if r.evaluation_metrics.abundance_mae is not None
        ]
        abundance_mae = _summarize(abundance_values) if abundance_values else None

        runtime = _summarize([r.runtime_seconds for r in group])

        iteration_values = [r.iteration_count for r in group if r.iteration_count is not None]
        iteration_count = _summarize([float(v) for v in iteration_values]) if iteration_values else None

        token_values = [
            float(r.token_usage["input_tokens"] + r.token_usage["output_tokens"])
            for r in group
            if r.token_usage is not None
        ]
        total_tokens = _summarize(token_values) if token_values else None

        cost_values = [r.estimated_cost_usd for r in group if r.estimated_cost_usd is not None]
        estimated_cost = _summarize(cost_values) if cost_values else None

        summaries.append(
            BenchmarkGroupSummary(
                dataset_id=dataset_id,
                pipeline_name=pipeline_name,
                n_runs=n_runs,
                success_rate=success_rate,
                precision=precision,
                recall=recall,
                f1=f1,
                false_positive_rate=fpr,
                false_negative_rate=fnr,
                abundance_mae=abundance_mae,
                runtime_seconds=runtime,
                iteration_count=iteration_count,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost,
            )
        )
    return summaries


_METRIC_EXTRACTORS = {
    "precision": lambda r: r.evaluation_metrics.precision,
    "recall": lambda r: r.evaluation_metrics.recall,
    "f1": lambda r: r.evaluation_metrics.f1,
    "runtime_seconds": lambda r: r.runtime_seconds,
}
MetricName = Literal["precision", "recall", "f1", "runtime_seconds"]


class ComparisonResult(BaseModel):
    """Two-sample comparison of one metric between two configurations on
    the same dataset, across their repeated runs."""

    dataset_id: str
    metric: str
    pipeline_a: str
    pipeline_b: str
    mean_a: float
    mean_b: float
    n_a: int
    n_b: int
    t_statistic: float | None
    p_value: float | None
    note: str = ""


def compare_configurations(
    records_a: list[ExperimentRecord],
    records_b: list[ExperimentRecord],
    metric: MetricName = "f1",
) -> ComparisonResult:
    """Welch's t-test (unequal variances, no assumption of equal sample
    size) between two configurations' repeated runs on the same metric.

    Requires at least 2 runs per side to compute a p-value; with fewer,
    returns means only and explains why in `note`. This is the project's
    stated commitment to "report means and variability, not just the
    best run" made literal: comparing single runs is explicitly refused.
    """
    extractor = _METRIC_EXTRACTORS[metric]
    scored_a = [r for r in records_a if r.evaluation_metrics is not None or metric == "runtime_seconds"]
    scored_b = [r for r in records_b if r.evaluation_metrics is not None or metric == "runtime_seconds"]
    values_a = [extractor(r) for r in scored_a]
    values_b = [extractor(r) for r in scored_b]

    dataset_ids = {r.dataset_id for r in records_a + records_b}
    pipeline_names_a = {r.pipeline_name for r in records_a}
    pipeline_names_b = {r.pipeline_name for r in records_b}
    if len(dataset_ids) != 1:
        raise ValueError("compare_configurations requires both record sets to share one dataset_id")

    mean_a = statistics.mean(values_a) if values_a else 0.0
    mean_b = statistics.mean(values_b) if values_b else 0.0

    if len(values_a) < 2 or len(values_b) < 2:
        return ComparisonResult(
            dataset_id=dataset_ids.pop(),
            metric=metric,
            pipeline_a=",".join(pipeline_names_a),
            pipeline_b=",".join(pipeline_names_b),
            mean_a=mean_a, mean_b=mean_b, n_a=len(values_a), n_b=len(values_b),
            t_statistic=None, p_value=None,
            note="Fewer than 2 runs on at least one side - cannot compute a significance test; "
            "re-run with n_repeats >= 2 to get one.",
        )

    if statistics.pvariance(values_a) == 0.0 and statistics.pvariance(values_b) == 0.0:
        # Welch's t-test is undefined (0/0) when both samples have zero variance -
        # expected with the deterministic MockProvider, not a bug. Report means
        # without a fabricated or NaN significance result.
        return ComparisonResult(
            dataset_id=dataset_ids.pop(),
            metric=metric,
            pipeline_a=",".join(pipeline_names_a),
            pipeline_b=",".join(pipeline_names_b),
            mean_a=mean_a, mean_b=mean_b, n_a=len(values_a), n_b=len(values_b),
            t_statistic=None, p_value=None,
            note="Both samples have zero variance (e.g. a deterministic provider) - a t-test is "
            "mathematically undefined here, not merely insignificant.",
        )

    t_stat, p_value = scipy_stats.ttest_ind(values_a, values_b, equal_var=False)
    return ComparisonResult(
        dataset_id=dataset_ids.pop(),
        metric=metric,
        pipeline_a=",".join(pipeline_names_a),
        pipeline_b=",".join(pipeline_names_b),
        mean_a=mean_a, mean_b=mean_b, n_a=len(values_a), n_b=len(values_b),
        t_statistic=float(t_stat), p_value=float(p_value),
    )
