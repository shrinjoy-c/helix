"""Resource-budget enforcement for an experiment run: iteration caps,
wall-clock timeouts, and cooperative cancellation.

Deliberately simple (no OS process group management - that lives in
`helix.execution.sandbox` for the untrusted code execution step itself).
This module bounds the *orchestration loop*, i.e. how many revision
cycles an experiment is allowed before it's forced to stop.
"""

from __future__ import annotations

import time


class ExperimentCancelled(Exception):
    """Raised when a cancellation token is tripped mid-run."""


class ExperimentTimedOut(Exception):
    """Raised when the overall experiment wall-clock budget is exceeded."""


class ExperimentBudgetExceeded(Exception):
    """Raised when a token or cost budget is exceeded mid-run."""


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise ExperimentCancelled("experiment was cancelled")


class IterationBudget:
    """Tracks iteration count and overall wall-clock time for one experiment."""

    def __init__(self, max_iterations: int, timeout_seconds: float) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.iteration = 0
        self._start = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - self.elapsed_seconds)

    def has_iterations_remaining(self) -> bool:
        return self.iteration < self.max_iterations

    def check_timeout(self) -> None:
        if self.elapsed_seconds > self.timeout_seconds:
            raise ExperimentTimedOut(
                f"experiment exceeded {self.timeout_seconds}s wall-clock budget "
                f"after {self.iteration} iteration(s)"
            )

    def start_iteration(self) -> int:
        self.check_timeout()
        self.iteration += 1
        return self.iteration


class TokenBudget:
    """Optional hard cap on cumulative token usage and/or estimated cost for
    one experiment. Unset fields (the default) are not enforced at all -
    that's how a caller opts out, since not every run should be bounded
    this way. Previously this resource was only ever tracked, never capped;
    this is what turns tracking into an actual stop condition."""

    def __init__(self, max_tokens: int | None = None, max_cost_usd: float | None = None) -> None:
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd

    def _violation(self, total_tokens: int, total_cost_usd: float) -> str | None:
        if self.max_tokens is not None and total_tokens > self.max_tokens:
            return f"exceeded its {self.max_tokens}-token budget ({total_tokens} used)"
        if self.max_cost_usd is not None and total_cost_usd > self.max_cost_usd:
            return f"exceeded its ${self.max_cost_usd:.4f} cost budget (${total_cost_usd:.6f} estimated)"
        return None

    def check(self, total_tokens: int, total_cost_usd: float) -> None:
        violation = self._violation(total_tokens, total_cost_usd)
        if violation:
            raise ExperimentBudgetExceeded(f"experiment {violation}")

    def is_exceeded(self, total_tokens: int, total_cost_usd: float) -> bool:
        return self._violation(total_tokens, total_cost_usd) is not None
