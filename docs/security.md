# Security: Execution Sandbox

## Honest scope statement

`helix.execution.sandbox.RestrictedSandbox` is a **restricted subprocess**, not a
hardened container sandbox. This document says plainly what it does and does not
protect against, per the project's own non-negotiable rule: never claim a subprocess
is a secure sandbox.

## What it provides

1. **A fresh, per-execution temporary working directory**, created under a
   configurable root and removed after use (`keep_artifacts=True` disables cleanup,
   for debugging only).
2. **Only the public dataset files are copied in** (`reads.fastq`,
   `reference_panel.fasta`) - `_ground_truth/` is never copied, and no path the
   subprocess is given points to it. Verified by
   `tests/test_security.py::test_ground_truth_is_not_reachable_from_the_sandbox`.
3. **A wall-clock execution timeout** (`subprocess.run(..., timeout=...)`); a
   timed-out process is treated as a failed execution, never left running.
4. **An allow-listed environment** - only `PATH`, `PYTHONPATH`, `LANG`, `LC_ALL`,
   `SYSTEMROOT` are passed through if present; nothing else, including API keys or
   credentials in the orchestrator's own environment, reaches the subprocess. Verified
   by `tests/test_security.py::test_sandbox_scrubs_secrets_from_environment`.
5. **Best-effort POSIX resource limits** (`RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NPROC`,
   `RLIMIT_CORE`) applied via `preexec_fn` before the child process execs. Best-effort
   because `resource.setrlimit` can fail silently on some platforms/permission
   configurations - failures are caught and ignored rather than crashing the sandbox,
   which means these limits are a mitigation, not a guarantee.
6. **An import-time guard** that blocks `socket`, `requests`, `urllib.request`,
   `http.client`, `ftplib`, `smtplib`, `subprocess`, and `multiprocessing` (exact
   module or its submodules only - e.g. blocking `urllib.request` does not block
   `urllib.parse`, which `pathlib` needs internally; see the regression test
   `test_sandbox_does_not_block_unrelated_stdlib_imports`). This is an
   application-level deterrent, trivially bypassable by anyone willing to read
   CPython's import machinery and work around `builtins.__import__` - it stops
   accidental or unsophisticated network/shell use, not a deliberately adversarial
   script.

## What it does NOT provide

- **No kernel-level isolation.** No namespaces, no seccomp filtering, no chroot, no
  cgroup enforcement beyond the `resource` module's best-effort limits. The executed
  code shares the host's filesystem permissions and Python interpreter.
- **No network isolation at the OS level.** The import guard blocks the *modules*
  used to reach the network from pure Python, but does not block, e.g., a compiled
  extension module making raw syscalls, or exploiting a library that reaches the
  network through a path that isn't one of the blocked names.
- **No defense against a deliberately adversarial, sophisticated attacker.** This
  sandbox is designed for the failure modes that actually show up when an LLM (or a
  rule-based stand-in) writes a bioinformatics analysis script and occasionally gets
  something wrong or does something lazy (infinite loop, accidentally imports
  `requests`, reads an unexpected path) - not for safely running code from an
  untrusted or hostile party.

## Do not

Do not expose this sandbox to the public internet, to untrusted end users, or treat
it as sufficient isolation for code from a source you don't already trust at the
level of "a research collaborator's draft script." Docker-based (or equivalent
OS-level) isolation with dropped capabilities and no network namespace is planned
before any such use case - see the Release 1.0 "Better sandboxing and observability"
item in the roadmap. No such hardening exists in this repository as of Release 0.1.

## Where this is enforced today

All of the above lives in `src/helix/execution/sandbox.py`
(`RestrictedSandbox.run_python`) and is exercised by `tests/test_security.py`. Every
piece of generated code that runs anywhere in HELIX - whether from `MockProvider` or
a real model - goes through this one path; there is no second, less-restricted
execution route anywhere in the codebase.
