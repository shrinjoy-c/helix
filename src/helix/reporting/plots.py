"""Plotly comparison charts built directly from `BenchmarkGroupSummary`
objects - no numbers are recomputed here, only visualized.
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go

from helix.benchmarking.metrics import BenchmarkGroupSummary


def _bar_with_error(summaries: list[BenchmarkGroupSummary], attr: str, title: str, y_title: str) -> go.Figure:
    by_dataset: dict[str, list[BenchmarkGroupSummary]] = {}
    for s in summaries:
        by_dataset.setdefault(s.dataset_id, []).append(s)

    fig = go.Figure()
    for dataset_id, group in sorted(by_dataset.items()):
        group = sorted(group, key=lambda s: s.pipeline_name)
        metric = [getattr(s, attr) for s in group]
        fig.add_trace(
            go.Bar(
                name=dataset_id,
                x=[s.pipeline_name for s in group],
                y=[m.mean for m in metric],
                error_y=dict(type="data", array=[m.std for m in metric]),
            )
        )
    fig.update_layout(
        title=title, yaxis_title=y_title, xaxis_title="pipeline", barmode="group",
        template="plotly_white",
    )
    return fig


def build_f1_comparison(summaries: list[BenchmarkGroupSummary]) -> go.Figure:
    return _bar_with_error(summaries, "f1", "F1 by configuration (mean +/- std across repeats)", "F1")


def build_precision_recall_comparison(summaries: list[BenchmarkGroupSummary]) -> go.Figure:
    fig = go.Figure()
    by_dataset: dict[str, list[BenchmarkGroupSummary]] = {}
    for s in summaries:
        by_dataset.setdefault(s.dataset_id, []).append(s)
    for dataset_id, group in sorted(by_dataset.items()):
        group = sorted(group, key=lambda s: s.pipeline_name)
        fig.add_trace(go.Scatter(
            x=[s.recall.mean for s in group], y=[s.precision.mean for s in group],
            mode="markers+text", text=[s.pipeline_name for s in group], textposition="top center",
            name=dataset_id,
        ))
    fig.update_layout(
        title="Precision vs. recall by configuration", xaxis_title="recall", yaxis_title="precision",
        xaxis=dict(range=[0, 1.05]), yaxis=dict(range=[0, 1.05]), template="plotly_white",
    )
    return fig


def build_runtime_comparison(summaries: list[BenchmarkGroupSummary]) -> go.Figure:
    return _bar_with_error(summaries, "runtime_seconds", "Runtime by configuration (mean +/- std)", "seconds")


def build_cost_comparison(summaries: list[BenchmarkGroupSummary]) -> go.Figure:
    fig = go.Figure()
    by_dataset: dict[str, list[BenchmarkGroupSummary]] = {}
    for s in summaries:
        if s.estimated_cost_usd is not None:
            by_dataset.setdefault(s.dataset_id, []).append(s)
    for dataset_id, group in sorted(by_dataset.items()):
        group = sorted(group, key=lambda s: s.pipeline_name)
        fig.add_trace(go.Bar(
            name=dataset_id, x=[s.pipeline_name for s in group],
            y=[s.estimated_cost_usd.mean for s in group],
        ))
    fig.update_layout(
        title="Estimated cost by configuration (USD; 0 for MockProvider)", yaxis_title="USD",
        xaxis_title="pipeline", barmode="group", template="plotly_white",
    )
    return fig


def save_all_plots(summaries: list[BenchmarkGroupSummary], output_dir: str | Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures = {
        "f1_comparison.html": build_f1_comparison(summaries),
        "precision_recall.html": build_precision_recall_comparison(summaries),
        "runtime_comparison.html": build_runtime_comparison(summaries),
        "cost_comparison.html": build_cost_comparison(summaries),
    }
    paths = {}
    for filename, fig in figures.items():
        path = output_dir / filename
        fig.write_html(str(path), include_plotlyjs="cdn")
        paths[filename] = path
    return paths
