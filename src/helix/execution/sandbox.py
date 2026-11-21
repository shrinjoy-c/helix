"""Restricted execution environment for agent-written Python code.

Honest scope statement (see docs/security.md): this is a **restricted
subprocess**, not a hardened container sandbox. It provides:

- a fresh, experiment-scoped temporary working directory, cleaned up after use;
- only the public dataset files copied in (ground truth is never copied here,
  and is not reachable via any path the subprocess is given);
- a wall-clock execution timeout;
- an allow-listed environment (no inherited API keys, credentials, or
  arbitrary host env vars);
- best-effort POSIX resource limits (CPU time, address space, process count);
- an import-time guard that blocks common networking/dangerous modules from
  the executed code.

It does **not** provide kernel-level isolation (no namespaces, no seccomp, no
chroot). A sufficiently motivated piece of code sharing the host's Python
interpreter could still escape these controls. Do not run untrusted code
through this sandbox and treat it as safe to expose to the public internet;
Docker-based (or equivalent) isolation is planned before any such use (see
docs/security.md).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

_BLOCKED_IMPORTS = (
    "socket",
    "requests",
    "urllib.request",
    "http.client",
    "ftplib",
    "smtplib",
    "subprocess",
    "multiprocessing",
)

_GUARD_PREAMBLE = f"""
import builtins as _builtins
_BLOCKED = {_BLOCKED_IMPORTS!r}
_real_import = _builtins.__import__

def _guarded_import(name, *args, **kwargs):
    for blocked in _BLOCKED:
        if name == blocked or name.startswith(blocked + "."):
            raise ImportError(f"import of {{name!r}} is blocked in the HELIX execution sandbox")
    return _real_import(name, *args, **kwargs)

_builtins.__import__ = _guarded_import
""".strip()

_ALLOWED_ENV_KEYS = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "SYSTEMROOT")


class SandboxResult(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    runtime_seconds: float = 0.0
    output_files: dict[str, str] = Field(default_factory=dict)


def _restricted_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ALLOWED_ENV_KEYS if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


def _set_resource_limits(cpu_seconds: int, memory_bytes: int):
    def _apply():
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except Exception:
            pass  # best-effort only; not available/permitted on all platforms

    return _apply


class RestrictedSandbox:
    def __init__(
        self,
        workdir_root: str | Path,
        timeout_seconds: float = 15.0,
        cpu_seconds: int = 10,
        memory_mb: int = 512,
        keep_artifacts: bool = False,
    ) -> None:
        self.workdir_root = Path(workdir_root)
        self.workdir_root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.cpu_seconds = cpu_seconds
        self.memory_bytes = memory_mb * 1024 * 1024
        self.keep_artifacts = keep_artifacts

    def run_python(
        self,
        code: str,
        input_files: dict[str, Path] | None = None,
        output_filenames: tuple[str, ...] = ("result.json",),
    ) -> SandboxResult:
        workdir = Path(tempfile.mkdtemp(prefix="helix_exec_", dir=self.workdir_root))
        try:
            for dest_name, src_path in (input_files or {}).items():
                shutil.copy2(src_path, workdir / dest_name)

            script_path = workdir / "analysis.py"
            script_path.write_text(_GUARD_PREAMBLE + "\n\n" + code)

            preexec = _set_resource_limits(self.cpu_seconds, self.memory_bytes) if os.name == "posix" else None

            start = time.monotonic()
            timed_out = False
            try:
                completed = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=workdir,
                    env=_restricted_env(),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    preexec_fn=preexec,
                )
                stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout or ""
                stderr = (exc.stderr or "") + "\n[sandbox] execution timed out"
                exit_code = -1
            runtime = time.monotonic() - start

            output_files = {}
            for name in output_filenames:
                out_path = workdir / name
                if out_path.exists():
                    output_files[name] = out_path.read_text()

            return SandboxResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=timed_out,
                runtime_seconds=runtime,
                output_files=output_files,
            )
        finally:
            if not self.keep_artifacts:
                shutil.rmtree(workdir, ignore_errors=True)
