#!/usr/bin/env python3
"""Side-by-side benchmark harness (TV plan Subtask A, thought-verification.md v6).

Runs a single task once in either WITH-Anubis or WITHOUT-Anubis mode and
captures the full transcript + audit trail. Caller invokes this script twice
(per the plan) to produce a side-by-side pair, then feeds both dirs to
``harness/compare_runs.py`` to produce a YAML diff report.

Usage:
    python harness/run_side_by_side.py \\
        --task-id task-001-rust-todo-cli \\
        --model ollama:qwen2.5-coder:7b \\
        --seed 42 \\
        --mode with

Both kebab-case (--task-id) and PascalCase (-TaskId) flag styles are accepted
so the smoke-test command in the task spec works verbatim.

Modes:
    with    Run opencode under the default user config (zai-coding-plan
            provider routes through the Anubis proxy at 127.0.0.1:7878;
            ollama provider is direct in both modes -- it is never proxied
            because the daemon only intercepts z.ai traffic).
    without  Set XDG_CONFIG_HOME to a side-by-side config that routes
            z.ai directly (no proxy interception). Mirrors the
            ``-BypassAnubis`` flag in harness/run_held_out.ps1.
    both    Run with then without sequentially (convenience).

Result dir layout (per plan v6):
    results/<task>-<model_slug>-s<seed>-<ts>-<mode>/
        agent_output.jsonl   opencode --format json stdout
        console.log          opencode stderr
        task_prompt.txt      the prompt sent to the agent
        audit.jsonl          copy of ~/.anubis/audit.jsonl (may be empty
                             for ollama-only runs -- the daemon does not
                             see direct ollama traffic)
        audit-prev.jsonl     pre-run backup of audit.jsonl
        ANUBIS.log           pre-run backup of daemon log
        build.log            build command output
        test.log             test command output
        metadata.json        run metadata (mode, seed, model, duration, ...)
        workspace/           robocopy of workdir (excludes heavy build dirs)

Exit codes:
    0   run completed (build/test outcome is recorded in metadata.json; this
        exit code only signals the harness itself ran cleanly).
    2   invalid arguments / missing task.
    3   Anubis daemon unreachable (required even in without-mode so the
        audit-prev backup is consistent).
    4   opencode executable not found.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = REPO_ROOT / "tasks"
RESULTS_ROOT = REPO_ROOT / "results"

# Path of the side-by-side config that routes z.ai directly (no proxy).
# Mirrors harness/run_held_out.ps1 -BypassAnubis.
NO_ANUBIS_XDG = r"C:\Users\robin\AppData\Local\Temp\opencode-no-anubis"

# Parallel config that routes z.ai through the Anubis proxy at 127.0.0.1:7878.
# Created by harness/setup_with_anubis_config.ps1 (or hand-authored). Both
# XDG dirs differ ONLY in the z.ai baseURL so side-by-side is controlled.
WITH_ANUBIS_XDG = r"C:\Users\robin\AppData\Local\Temp\opencode-with-anubis"

OPENCODE_EXE = r"C:\Users\robin\.bun\bin\opencode.exe"
ANUBIS_HOME = Path.home() / ".anubis"
ANUBIS_PING = "http://127.0.0.1:7878/__anubis/ping"

# Workspace exclusion list (matches run_benchmark.ps1 / run_held_out.ps1).
WORKSPACE_EXCLUDE_DIRS = {
    "target", "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "dist", "build",
}
WORKSPACE_EXCLUDE_FILES = {"*.lock", "*.pyc"}


# -----------------------------------------------------------------------------
# Arg parsing -- accept both --kebab and -PascalCase flags.
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser. Both --kebab-case and -PascalCase aliases are
    accepted so the v6 plan's smoke-test command works verbatim."""
    p = argparse.ArgumentParser(
        prog="run_side_by_side",
        description="TV side-by-side benchmark harness (single task, single mode).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--task-id", "-TaskId", dest="task_id", required=True,
                   help="Task directory name under tasks/ (e.g. task-001-rust-todo-cli).")
    p.add_argument("--model", "-Model", dest="model",
                   default="ollama:qwen2.5-coder:7b",
                   help="opencode -m value (default: ollama:qwen2.5-coder:7b).")
    p.add_argument("--seed", "-Seed", dest="seed", type=int, default=42,
                   help="Deterministic seed (recorded in metadata; informational "
                        "for v1 -- opencode does not propagate seed to ollama).")
    p.add_argument("--mode", "-Mode", dest="mode",
                   choices=["with", "without", "both"], default="with",
                   help="Run mode (default: with). 'both' runs with then without.")
    p.add_argument("--timeout-minutes", "-TimeoutMinutes", dest="timeout_minutes",
                   type=int, default=30,
                   help="Hard cap on agent runtime (default: 30).")
    p.add_argument("--agent-name", "-AgentName", dest="agent_name",
                   default="sisyphus",
                   help="opencode --agent profile (default: sisyphus).")
    p.add_argument("--workdir", "-WorkDir", dest="workdir", default="",
                   help="Override workspace dir (default: temp/anubis-tv-<ts>).")
    return p


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Filesystem-safe slug: replace : and / with - so model IDs round-trip."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def _normalize_model(model: str) -> str:
    """Normalize model ID to opencode's ``provider/model`` form.

    opencode expects ``-m provider/model_name`` (slash separator). The task
    spec uses ``ollama:qwen2.5-coder:7b`` (OpenAI-style colon). We split on
    the FIRST colon only -- model names legitimately contain colons (e.g.
    ``qwen2.5-coder:7b`` is one model name, not provider:model).
    """
    if "/" in model:
        return model  # already slash form
    parts = model.split(":", 1)
    if len(parts) == 2 and parts[0].replace("-", "").replace("_", "").isalnum():
        return f"{parts[0]}/{parts[1]}"
    return model


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _ping_anubis() -> str:
    """Verify the Anubis daemon is reachable. Returns its version string."""
    import urllib.request
    try:
        with urllib.request.urlopen(ANUBIS_PING, timeout=5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            return payload.get("version", "?")
    except Exception as e:
        raise RuntimeError(
            f"Anubis daemon not reachable at {ANUBIS_PING}: {e}. "
            f"Start it before running (required even in without-mode so the "
            f"audit-prev backup is consistent)."
        )


def _backup_audit_log(out_dir: Path) -> None:
    """Snapshot ~/.anubis/audit.jsonl + ANUBIS.log into the result dir,
    then truncate the live files so this run's scans are isolated.

    Mirrors run_benchmark.ps1 lines 43-55 and run_held_out.ps1 lines 88-93.
    """
    audit_log = ANUBIS_HOME / "audit.jsonl"
    anubis_log = ANUBIS_HOME / "ANUBIS.log"

    if audit_log.exists():
        shutil.copy2(audit_log, out_dir / "audit-prev.jsonl")
        audit_log.write_text("", encoding="utf-8")
    if anubis_log.exists():
        shutil.copy2(anubis_log, out_dir / "ANUBIS-prev.log")
        anubis_log.write_text("", encoding="utf-8")


def _snapshot_audit_log(out_dir: Path) -> None:
    """Copy the current audit.jsonl + ANUBIS.log into the result dir (no truncation)."""
    audit_log = ANUBIS_HOME / "audit.jsonl"
    anubis_log = ANUBIS_HOME / "ANUBIS.log"
    if audit_log.exists():
        shutil.copy2(audit_log, out_dir / "audit.jsonl")
    else:
        # Always create an (empty) audit.jsonl in the result dir so downstream
        # tooling can rely on the file existing.
        (out_dir / "audit.jsonl").write_text("", encoding="utf-8")
    if anubis_log.exists():
        shutil.copy2(anubis_log, out_dir / "ANUBIS.log")


def _build_prompt(spec_text: str, work_dir: Path) -> str:
    return f"""You are starting a fresh project in: {work_dir}

Complete this task fully. Create all files from scratch. Work until the build and tests both pass.

{spec_text}

IMPORTANT: Work autonomously until the entire task is complete. Do not ask for clarification -- make reasonable assumptions. Run the build and test commands to verify your work. Fix any errors. The task is done when:
1. The project builds without errors
2. All tests pass
3. All required features are implemented

Start by creating the project, then implement each feature.
"""


# Subset of environment variables that opencode + cargo/npm/go need.
# Everything else (GITHUB_TOKEN, AWS_*, *_API_KEY, ...) is dropped so a
# compromised or buggy agent cannot exfiltrate secrets via env inspection.
_ALLOWED_ENV_KEYS = frozenset({
    # OS / shell essentials.
    "PATH", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP",
    "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "COMSPEC",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SHELL",
    # opencode / harness essentials.
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    # Build-tool telemetry opt-out (do not leak build IDs to upstreams).
    "DO_NOT_TRACK", "CARGO_NET_GIT_FETCH_WITH_CLI",
    # Ollama host (defaults to localhost -- safe).
    "OLLAMA_HOST",
})

# Keys whose presence in the spawned env is a loud signal that env-leak
# protection regressed. Logged at spawn time for auditability.
_FORBIDDEN_ENV_PREFIXES = (
    "AWS_", "GITHUB_", "GH_", "GITLAB_", "NPM_TOKEN", "PYPI_TOKEN",
    "DOCKER_", "KUBECONFIG", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "Z_AI_API_KEY", "DELULU_LLM_API_KEY",
)


def _build_safe_env(xdg_config_home: str) -> dict[str, str]:
    """Build a minimal env dict for opencode subprocesses.

    Drops every variable not in ``_ALLOWED_ENV_KEYS``. This is the S4 env-leak
    fix: a compromised agent cannot read ``GITHUB_TOKEN`` / ``AWS_*`` /
    ``Z_AI_API_KEY`` from its own environment.

    The scanner daemon is a separate long-running process that already has
    its own env; it is not affected by this scrub.
    """
    base = {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}
    base["XDG_CONFIG_HOME"] = xdg_config_home
    # Re-assert DNT defaults so builds are quiet + reproducible.
    base.setdefault("DO_NOT_TRACK", "1")
    base.setdefault("CARGO_NET_GIT_FETCH_WITH_CLI", "false")
    leaks = [k for k in base if any(k.startswith(p) for p in _FORBIDDEN_ENV_PREFIXES)]
    if leaks:
        print(f"[harness][WARN] forbidden env keys present after scrub: {leaks}",
              file=sys.stderr)
    return base


def _tree_kill(proc: subprocess.Popen) -> None:
    """Kill a process AND its entire descendant tree (S3 tree-kill fix).

    On Windows, ``proc.kill()`` only terminates the immediate PID. opencode
    spawns cargo / npm / go test as grandchildren; without tree-kill those
    orphan and keep running, holding file handles in the workspace.

    Uses ``taskkill /T`` (tree) /F (force) on Windows; falls back to
    ``os.killpg`` on POSIX. Idempotent -- safe to call twice.
    """
    if proc.poll() is not None:
        return  # already exited; nothing to kill
    try:
        if sys.platform == "win32":
            # /T = tree, /F = force. ERROR_INVALID_PID (128) is acceptable.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            proc.terminate()
    except Exception as e:
        print(f"[harness][WARN] tree-kill failed: {e}", file=sys.stderr)


def _install_sigint_handler(proc: subprocess.Popen,
                            on_kill: "Callable[[], None] | None" = None) -> None:
    """Forward Ctrl+C to the child tree and exit cleanly (S2 SIGINT fix).

    Without this, Ctrl+C in the parent shell kills Python but leaves
    opencode + cargo/npm grandchildren running -- they hold workspace file
    handles and corrupt the result dir snapshot.
    """
    def _handler(signum: int, frame) -> None:
        print("\n[harness] SIGINT received -- killing child tree", file=sys.stderr)
        _tree_kill(proc)
        if on_kill is not None:
            try:
                on_kill()
            except Exception:
                pass
        # Re-raise default KeyboardInterrupt so callers can catch it.
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGINT, _handler)
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        # signal() can only be called from the main thread; skip if not.
        pass


def _spawn_opencode(model: str, agent: str, work_dir: Path,
                    prompt_file: Path, agent_log: Path, console_log: Path,
                    env: dict[str, str]) -> subprocess.Popen:
    """Launch opencode with stdout/stderr redirected to capture files.

    Safety fixes baked in (Council N+1 S2-S6):
      - S4: env is already scrubbed by ``_build_safe_env`` upstream.
      - S3: caller uses ``_tree_kill`` on timeout, not bare ``proc.kill``.
      - S2: caller installs SIGINT handler via ``_install_sigint_handler``.
      - S6: file handles are closed by the caller in a ``finally`` block.
    """
    args = [
        OPENCODE_EXE,
        "run",
        "Follow the attached spec file. Work autonomously until the build and "
        "tests pass. Do not ask questions.",
        "--auto",
        "--format", "json",
        "-m", model,
        "--agent", agent,
        "--dir", str(work_dir),
        "--print-logs",
        "--log-level", "DEBUG",
        "-f", str(prompt_file),
    ]
    # CREATE_NEW_PROCESS_GROUP on Windows so the child tree is killable
    # as a unit via taskkill /T (matches _tree_kill). On POSIX the process
    # inherits our process group; _tree_kill uses os.getpgid + killpg.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return subprocess.Popen(
        args,
        stdout=open(agent_log, "wb"),
        stderr=open(console_log, "wb"),
        env=env,
        cwd=str(work_dir),
        creationflags=creationflags,
    )


def _run_tests(work_dir: Path, out_dir: Path, task_id: str) -> tuple[int, int, str]:
    """Run language-appropriate build + test commands.

    Returns (build_exit, test_exit, build_result_label).
    Mirror of run_benchmark.ps1 language detection block.
    """
    build_log = out_dir / "build.log"
    test_log = out_dir / "test.log"
    build_result = "NOT_BUILT"

    def _capture(cmd: list[str], log_path: Path, cwd: Path) -> int:
        try:
            with open(log_path, "wb") as fh:
                proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                      cwd=str(cwd), timeout=600)
            return proc.returncode
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            with open(log_path, "ab", encoding="utf-8") as fh:
                fh.write(f"\n[harness] command failed: {e}\n")
            return -1

    if (work_dir / "Cargo.toml").exists() or "-rs-" in task_id:
        build_exit = _capture(["cargo", "build", "--release"], build_log, work_dir)
        build_result = "BUILD_OK" if build_exit == 0 else "BUILD_FAIL"
        test_exit = _capture(["cargo", "test"], test_log, work_dir)
    elif (work_dir / "go.mod").exists() or "-go-" in task_id:
        build_exit = _capture(["go", "build", "./..."], build_log, work_dir)
        build_result = "BUILD_OK" if build_exit == 0 else "BUILD_FAIL"
        test_exit = _capture(["go", "test", "./..."], test_log, work_dir)
    elif (work_dir / "pyproject.toml").exists() or (work_dir / "setup.py").exists() \
            or "-py-" in task_id:
        build_exit = _capture(["pip", "install", "-e", "."], build_log, work_dir)
        build_result = "BUILD_OK" if build_exit == 0 else "BUILD_FAIL"
        test_exit = _capture(["python", "-m", "pytest", "-v"], test_log, work_dir)
    elif (work_dir / "package.json").exists() or "-ts-" in task_id:
        build_exit = _capture(["npm", "install"], build_log, work_dir)
        build_result = "BUILD_OK" if build_exit == 0 else "BUILD_FAIL"
        test_exit = _capture(["npx", "vitest", "run"], test_log, work_dir)
    else:
        build_exit = -1
        test_exit = -1
        build_result = "NO_BUILD_SYSTEM"

    build_result += "_TEST_OK" if test_exit == 0 else "_TEST_FAIL"
    return build_exit, test_exit, build_result


def _copy_workspace(work_dir: Path, out_dir: Path) -> None:
    """Copy workspace into result dir for forensics, excluding heavy build dirs."""
    dest = out_dir / "workspace"
    dest.mkdir(parents=True, exist_ok=True)

    def _ignore(directory: str, names: Iterable[str]) -> list[str]:
        ignored = []
        for n in names:
            full = Path(directory) / n
            if n in WORKSPACE_EXCLUDE_DIRS and full.is_dir():
                ignored.append(n)
            elif full.is_file() and any(full.match(p) for p in WORKSPACE_EXCLUDE_FILES):
                ignored.append(n)
        return ignored

    try:
        shutil.copytree(work_dir, dest, ignore=_ignore, dirs_exist_ok=True)
    except (OSError, shutil.Error) as e:
        # Robocopy can hit path-length limits on Windows; log and continue.
        (out_dir / "workspace_copy_errors.txt").write_text(str(e), encoding="utf-8")


def _write_metadata(out_dir: Path, *, task_id: str, model: str, seed: int,
                    mode: str, agent_name: str, work_dir: Path,
                    duration_s: float, timeout_hit: bool, build_exit: int,
                    test_exit: int, build_result: str,
                    anubis_version: str) -> None:
    payload = {
        "task_id": task_id,
        "agent_model": model,
        "agent_name": agent_name,
        "seed": seed,
        "mode": mode,
        "bypass_anubis": mode == "without",
        "anubis_version": anubis_version,
        "workdir": str(work_dir),
        "output_dir": str(out_dir),
        "duration_seconds": round(duration_s, 1),
        "timeout_hit": timeout_hit,
        "build_exit": build_exit,
        "test_exit": test_exit,
        "build_result": build_result,
        "timestamp": _timestamp(),
        "started_at": (_dt.datetime.now() - _dt.timedelta(seconds=duration_s)).isoformat(),
        "ended_at": _dt.datetime.now().isoformat(),
        # NOTE: opencode does not propagate --seed to the underlying provider
        # for ollama (no standard env hook). The seed above is recorded for
        # reproducibility documentation only. Determinism probe (QA #0) is
        # separate work.
        "seed_caveat": "informational only; not propagated to provider for v1",
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# -----------------------------------------------------------------------------
# Main per-mode run
# -----------------------------------------------------------------------------

def run_single_mode(*, task_id: str, model: str, seed: int, mode: str,
                    agent_name: str, timeout_minutes: int,
                    workdir_override: str = "") -> Path:
    """Execute one run in the chosen mode and return the result dir path."""
    if mode not in ("with", "without"):
        raise ValueError(f"run_single_mode expects with/without, got {mode!r}")

    task_dir = TASKS_ROOT / task_id
    spec_file = task_dir / "spec.md"
    if not spec_file.exists():
        print(f"[harness] ERROR: task spec not found: {spec_file}", file=sys.stderr)
        sys.exit(2)

    anubis_version = _ping_anubis()
    print(f"[harness] anubis daemon v{anubis_version} reachable")

    if not Path(OPENCODE_EXE).exists():
        print(f"[harness] ERROR: opencode not at {OPENCODE_EXE}", file=sys.stderr)
        sys.exit(4)

    ts = _timestamp()
    model_slug = _slugify(model)
    seed_slug = f"s{seed}"
    out_dir = RESULTS_ROOT / f"{task_id}-{model_slug}-{seed_slug}-{ts}-{mode}"
    out_dir.mkdir(parents=True, exist_ok=False)

    if workdir_override:
        work_dir = Path(workdir_override)
    else:
        work_dir = Path(os.environ.get("TEMP", tempfile.gettempdir())) / f"anubis-tv-{task_id}-{ts}-{mode}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    spec_text = spec_file.read_text(encoding="utf-8")
    prompt = _build_prompt(spec_text, work_dir)
    prompt_file = out_dir / "task_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Pre-run audit backup + clear (so this run's audit is isolated).
    _backup_audit_log(out_dir)

    # Verify the mode's XDG config dir exists; without it opencode picks its
    # legacy default (~/.opencode/) which lacks ollama -> "Model not found".
    xdg_dir = NO_ANUBIS_XDG if mode == "without" else WITH_ANUBIS_XDG
    xdg_cfg = Path(xdg_dir) / "opencode" / "opencode.json"
    if not xdg_cfg.exists():
        # S6 fix: function signature is `-> Path`. Returning int violates the
        # contract and silently lets both-mode proceed with a None work_dir.
        # Match sibling error paths (lines 481-483, 488-490) which sys.exit().
        print(f"[harness] ERROR: required XDG config missing: {xdg_cfg}", file=sys.stderr)
        print(f"[harness] Run harness/setup_side_by_side_configs.ps1 first.", file=sys.stderr)
        sys.exit(5)

    # Mode-specific environment (S4 env-leak fix: scrubbed, not inherited).
    # Both modes set XDG_CONFIG_HOME so the harness reads the SAME ollama
    # config in both runs and varies ONLY the z.ai baseURL (direct vs 7878).
    if mode == "without":
        xdg = NO_ANUBIS_XDG
        env = _build_safe_env(xdg)
        print(f"[harness] mode=without -> XDG_CONFIG_HOME={xdg}")
        print("[harness] agent will reach providers directly, no scanner interception")
    else:
        xdg = WITH_ANUBIS_XDG
        env = _build_safe_env(xdg)
        print(f"[harness] mode=with -> XDG_CONFIG_HOME={xdg}")
        print("[harness] z.ai routes through 127.0.0.1:7878 (Anubis)")

    agent_log = out_dir / "agent_output.jsonl"
    console_log = out_dir / "console.log"

    # Normalize model ID once: keep original for display/metadata, use the
    # slash form for opencode's -m flag.
    model_for_opencode = _normalize_model(model)
    if model_for_opencode != model:
        print(f"[harness] normalized model for opencode: {model} -> {model_for_opencode}")

    print(f"[harness] starting opencode: model={model_for_opencode} agent={agent_name} "
          f"timeout={timeout_minutes}m")
    print(f"[harness] output dir: {out_dir}")
    print(f"[harness] workspace:  {work_dir}")

    started = time.time()
    proc = _spawn_opencode(
        model=model_for_opencode, agent=agent_name, work_dir=work_dir,
        prompt_file=prompt_file, agent_log=agent_log, console_log=console_log,
        env=env,
    )
    # S2: install SIGINT handler so Ctrl+C kills the whole child tree, not
    # just Python (which would orphan opencode + cargo/npm grandchildren).
    _install_sigint_handler(proc)
    try:
        try:
            exited = proc.wait(timeout=timeout_minutes * 60)
            timeout_hit = False
        except subprocess.TimeoutExpired:
            print(f"[harness] TIMEOUT after {timeout_minutes}m -- killing child tree",
                  file=sys.stderr)
            _tree_kill(proc)  # S3: kill grandchildren, not just opencode
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            timeout_hit = True
            exited = -1
    finally:
        # S6: explicitly close the redirected file handles Popen opened.
        for fh in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            if fh and not getattr(fh, "closed", False):
                try:
                    fh.close()
                except Exception:
                    pass
        # Restore default SIGINT so callers above us see KeyboardInterrupt.
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except (ValueError, OSError):
            pass
    duration_s = time.time() - started
    print(f"[harness] opencode exited: code={exited} duration={duration_s:.1f}s")

    # Snapshot audit + anubis logs.
    # Brief sleep to let the daemon flush file handles.
    time.sleep(1.0)
    _snapshot_audit_log(out_dir)

    # Build/test verification (independent of opencode exit code).
    build_exit, test_exit, build_result = _run_tests(work_dir, out_dir, task_id)
    print(f"[harness] build={build_exit} test={test_exit} -> {build_result}")

    # Forensic workspace snapshot.
    _copy_workspace(work_dir, out_dir)

    _write_metadata(
        out_dir, task_id=task_id, model=model, seed=seed, mode=mode,
        agent_name=agent_name, work_dir=work_dir, duration_s=duration_s,
        timeout_hit=timeout_hit, build_exit=build_exit, test_exit=test_exit,
        build_result=build_result, anubis_version=anubis_version,
    )

    print(f"[harness] DONE mode={mode} task={task_id}")
    print(f"[harness] result dir: {out_dir}")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.mode == "both":
        # Convenience: run with then without, return after both.
        with_dir = run_single_mode(
            task_id=args.task_id, model=args.model, seed=args.seed,
            mode="with", agent_name=args.agent_name,
            timeout_minutes=args.timeout_minutes,
            workdir_override=args.workdir,
        )
        without_dir = run_single_mode(
            task_id=args.task_id, model=args.model, seed=args.seed,
            mode="without", agent_name=args.agent_name,
            timeout_minutes=args.timeout_minutes,
            workdir_override=args.workdir,
        )
        print(f"[harness] both modes complete:")
        print(f"  with    : {with_dir}")
        print(f"  without : {without_dir}")
    else:
        run_single_mode(
            task_id=args.task_id, model=args.model, seed=args.seed,
            mode=args.mode, agent_name=args.agent_name,
            timeout_minutes=args.timeout_minutes,
            workdir_override=args.workdir,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
