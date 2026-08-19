#!/usr/bin/env python3
"""E2E benchmark runner: routes Ollama traffic through the Anubis proxy and
captures the full transcript + audit trail for a single task.

Unlike ``run_side_by_side.py`` (which swaps XDG configs to vary the
``zai-coding-plan`` baseURL), this harness uses the Anubis CLI
``harness enable opencode ollama`` to rewrite the **ollama** provider's
``baseURL`` to ``127.0.0.1:7878`` and inject the ``x-anubis-target`` header
so the proxy forwards traffic to the real Ollama daemon at
``127.0.0.1:11434``. The proxy scans the response and writes entries to
``~/.anubis/audit.jsonl`` with ``model=qwen2.5-coder:7b`` (or whatever
Ollama model is selected).

Usage:
    python harness/run_e2e_benchmark.py --task-id task-012-rusqlite-refinery
    python harness/run_e2e_benchmark.py --all
    python harness/run_e2e_benchmark.py --task-id task-013-fastapi-websocket --mode without

Result dir layout:
    results-e2e/<task>-<model_slug>-<ts>-<mode>/
        agent_output.jsonl   opencode --format json stdout
        console.log          opencode stderr
        task_prompt.txt      the prompt sent to the agent
        audit.jsonl          snapshot of ~/.anubis/audit.jsonl (empty in without)
        audit-prev.jsonl     pre-run backup of ~/.anubis/audit.jsonl
        ANUBIS.log           snapshot of scanner log
        ANUBIS-prev.log      pre-run backup
        build.log / test.log language-detected build/test outputs
        metadata.json        run metadata
        routing-state.json   pre/post routing state (proves enable/disable ran)
        workspace/           snapshot of agent workdir (excludes build dirs)

Exit codes:
    0   run completed (build/test outcome recorded in metadata.json).
    2   invalid arguments / missing task.
    3   Anubis daemon unreachable.
    4   opencode executable not found.
    5   anubis CLI not on PATH.
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
# Benchmark tasks live under corpus/benchmark_tasks/ (new e2e corpus).
# Fall back to tasks/ if the legacy dir is the only one present.
TASKS_ROOT = REPO_ROOT / "corpus" / "benchmark_tasks"
TASKS_LEGACY_ROOT = REPO_ROOT / "tasks"
RESULTS_ROOT = REPO_ROOT / "results-e2e"

OPENCODE_EXE = r"C:\Users\robin\.bun\bin\opencode.exe"
ANUBIS_HOME = Path.home() / ".anubis"
ANUBIS_PING = "http://127.0.0.1:7878/__anubis/ping"
ANUBIS_CLI = shutil.which("anubis") or r"C:\Users\robin\AppData\Local\anubis\anubis.exe"

WORKSPACE_EXCLUDE_DIRS = {
    "target", "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "dist", "build",
}
WORKSPACE_EXCLUDE_FILES = {"*.lock", "*.pyc"}

HARNESS_ID = "opencode"
PROVIDER_ID = "ollama"


# -----------------------------------------------------------------------------
# Arg parsing
# -----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_e2e_benchmark",
        description="E2E benchmark: routes Ollama through Anubis and captures transcripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--task-id", "-TaskId", dest="task_id", default="",
                   help="Task directory name under corpus/benchmark_tasks/. "
                        "Required unless --all is set.")
    p.add_argument("--all", dest="run_all", action="store_true",
                   help="Run every task under corpus/benchmark_tasks/. "
                        "Excludes legacy task-001..011 in tasks/.")
    p.add_argument("--model", "-Model", dest="model",
                   default="ollama:qwen2.5-coder:7b",
                   help="opencode -m value (default: ollama:qwen2.5-coder:7b).")
    p.add_argument("--mode", "-Mode", dest="mode",
                   choices=["with", "without", "both"], default="with",
                   help="Run mode (default: with). 'both' runs with then without.")
    p.add_argument("--timeout-minutes", "-TimeoutMinutes", dest="timeout_minutes",
                   type=int, default=30,
                   help="Hard cap on agent runtime (default: 30).")
    p.add_argument("--agent-name", "-AgentName", dest="agent_name",
                   default="",
                   help="opencode --agent profile. Empty = opencode's default "
                        "agent (recommended — 'sisyphus' is not a valid agent "
                        "name in opencode 1.18.x and triggers a fallback).")
    p.add_argument("--workdir", "-WorkDir", dest="workdir", default="",
                   help="Override workspace dir (default: temp/anubis-e2e-<ts>).")
    return p


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def _normalize_model(model: str) -> str:
    """Convert 'ollama:qwen2.5-coder:7b' to 'ollama/qwen2.5-coder:7b'."""
    if "/" in model:
        return model
    parts = model.split(":", 1)
    if len(parts) == 2 and parts[0].replace("-", "").replace("_", "").isalnum():
        return f"{parts[0]}/{parts[1]}"
    return model


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _ping_anubis() -> str:
    import urllib.request
    try:
        with urllib.request.urlopen(ANUBIS_PING, timeout=5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            return payload.get("version", "?")
    except Exception as e:
        raise RuntimeError(
            f"Anubis daemon not reachable at {ANUBIS_PING}: {e}. "
            f"Start it before running."
        )


def _anubis_cli(args: list[str]) -> tuple[int, str]:
    """Run the anubis CLI with args; returns (exit_code, combined_output).

    Captures as raw bytes then decodes as UTF-8 — the ``harness list`` output
    contains ``→`` and ``✓`` which Windows' default codepage (cp1252) mangles,
    breaking the routing-state parser.
    """
    cmd = [ANUBIS_CLI] + args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=False, timeout=30,
        )
    except FileNotFoundError:
        print(f"[harness] ERROR: anubis CLI not at {ANUBIS_CLI}", file=sys.stderr)
        sys.exit(5)
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    out = raw.decode("utf-8", errors="replace")
    return proc.returncode, out


def _capture_routing_state() -> dict:
    """Run `anubis harness list` and parse the routing state for opencode/ollama.

    Output format (per src/bin/anubis.rs::run_harness)::

        opencode  (1/4)  <config_path>
          ✓ zai-coding-plan → http://127.0.0.1:7878
            ollama → [not routed]

    We rely on the ASCII sentinel ``[not routed]`` rather than the ``✓`` glyph
    (which is robust to any future codepage mangling).
    """
    code, out = _anubis_cli(["harness", "list"])
    state = {"raw": out, "ollama_routed": None, "providers": []}
    in_opencode = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("opencode "):
            in_opencode = True
            continue
        if in_opencode and re.match(r"^[a-z-]+\s+\(", s):
            # Hit the next harness block (claude-code, codex, etc.)
            in_opencode = False
            continue
        if not in_opencode:
            continue
        # Provider line. If it contains '[not routed]' the provider is direct;
        # otherwise it is routed through the proxy and the target URL follows
        # the arrow.
        if "[not routed]" in s:
            # Strip the leading '✓ ' or spaces, then take the provider name
            # as the first token before the arrow.
            before_arrow = s.split("→")[0].strip().lstrip("✓").strip()
            name = before_arrow.split()[0] if before_arrow else "?"
            state["providers"].append({"name": name, "routed": False, "target": None})
            if name == PROVIDER_ID:
                state["ollama_routed"] = False
        else:
            parts = s.split("→", 1)
            if len(parts) != 2:
                continue
            name_field = parts[0].strip().lstrip("✓").strip()
            name = name_field.split()[0] if name_field else "?"
            target = parts[1].strip()
            state["providers"].append({"name": name, "routed": True, "target": target})
            if name == PROVIDER_ID:
                state["ollama_routed"] = True
                state["ollama_target"] = target
    return state


def _ensure_ollama_routing(enabled: bool) -> None:
    """Idempotently enable or disable opencode::ollama routing via anubis CLI."""
    state = _capture_routing_state()
    already = state.get("ollama_routed") is True
    if enabled and already:
        print(f"[harness] opencode::{PROVIDER_ID} already routed "
              f"(target={state.get('ollama_target')})")
        return
    if not enabled and not already:
        print(f"[harness] opencode::{PROVIDER_ID} already direct")
        return
    action = "enable" if enabled else "disable"
    code, out = _anubis_cli(["harness", action, HARNESS_ID, PROVIDER_ID])
    if code != 0:
        print(f"[harness][WARN] `anubis harness {action} {HARNESS_ID} {PROVIDER_ID}` "
              f"exited {code}: {out.strip()}", file=sys.stderr)
    else:
        print(f"[harness] {action}d {HARNESS_ID}::{PROVIDER_ID}")


def _backup_audit_log(out_dir: Path) -> None:
    """Snapshot ~/.anubis/audit.jsonl + ANUBIS.log, then truncate the live files."""
    audit_log = ANUBIS_HOME / "audit.jsonl"
    anubis_log = ANUBIS_HOME / "ANUBIS.log"
    if audit_log.exists():
        shutil.copy2(audit_log, out_dir / "audit-prev.jsonl")
        audit_log.write_text("", encoding="utf-8")
    if anubis_log.exists():
        shutil.copy2(anubis_log, out_dir / "ANUBIS-prev.log")
        anubis_log.write_text("", encoding="utf-8")


def _snapshot_audit_log(out_dir: Path) -> None:
    """Copy the current audit.jsonl + ANUBIS.log into the result dir."""
    audit_log = ANUBIS_HOME / "audit.jsonl"
    anubis_log = ANUBIS_HOME / "ANUBIS.log"
    if audit_log.exists():
        shutil.copy2(audit_log, out_dir / "audit.jsonl")
    else:
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


def _build_safe_env() -> dict[str, str]:
    """Environment for opencode subprocesses.

    Earlier versions of this harness stripped the env to a minimal allow-list
    (matching ``run_side_by_side.py``). That crashes opencode 1.18.x's native
    runtime (STATUS_STACK_BUFFER_OVERRUN, exit 0xC0000409) because the
    oh-my-openagent plugin / Bun runtime depend on env vars outside the
    allow-list. We now inherit the parent env verbatim and only force
    DO_NOT_TRACK=1 (analytics opt-out) plus CARGO_NET_GIT_FETCH_WITH_CLI
    (avoids interactive git prompts during cargo builds).
    """
    env = dict(os.environ)
    env["DO_NOT_TRACK"] = "1"
    env["CARGO_NET_GIT_FETCH_WITH_CLI"] = "false"
    return env


def _tree_kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
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
    def _handler(signum: int, frame) -> None:
        print("\n[harness] SIGINT received -- killing child tree", file=sys.stderr)
        _tree_kill(proc)
        if on_kill is not None:
            try:
                on_kill()
            except Exception:
                pass
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGINT, _handler)
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass


def _spawn_opencode(model: str, agent: str, work_dir: Path,
                    prompt_file: Path, agent_log: Path, console_log: Path,
                    env: dict[str, str]) -> subprocess.Popen:
    args = [
        OPENCODE_EXE,
        "run",
        "Follow the attached spec file. Work autonomously until the build and "
        "tests pass. Do not ask questions.",
        "--auto",
        "--format", "json",
        "-m", model,
        "--dir", str(work_dir),
        "--print-logs",
        "--log-level", "DEBUG",
        "-f", str(prompt_file),
    ]
    if agent:
        # Only pass --agent when explicitly set; an invalid name makes
        # opencode fall back to the default with a warning.
        args += ["--agent", agent]
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


def _run_build(work_dir: Path, out_dir: Path, task_id: str) -> tuple[int, str]:
    """Run the language-appropriate build command. Returns (exit_code, label)."""
    build_log = out_dir / "build.log"

    def _capture(cmd: list[str], cwd: Path) -> int:
        try:
            with open(build_log, "wb") as fh:
                proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                      cwd=str(cwd), timeout=600)
            return proc.returncode
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            with open(build_log, "ab", encoding="utf-8") as fh:
                fh.write(f"\n[harness] build command failed: {e}\n")
            return -1

    # Verification per the task spec (lighter than run_side_by_side's full test
    # matrix — these are the success criteria the task author specified).
    if (work_dir / "Cargo.toml").exists() or "-rs-" in task_id:
        rc = _capture(["cargo", "build", "--release"], work_dir)
        return rc, "BUILD_OK" if rc == 0 else "BUILD_FAIL"
    if (work_dir / "pyproject.toml").exists() or (work_dir / "setup.py").exists() \
            or "-py-" in task_id or "-fastapi-" in task_id:
        # task-013 verifies via `python -c "import main"`
        rc = _capture(["python", "-c", "import main"], work_dir)
        return rc, "IMPORT_OK" if rc == 0 else "IMPORT_FAIL"
    if (work_dir / "package.json").exists() or "-ts-" in task_id or "-drizzle-" in task_id:
        if not (work_dir / "node_modules").exists():
            install_rc = _capture(["npm", "install"], work_dir)
            if install_rc != 0:
                return install_rc, "NPM_INSTALL_FAIL"
        rc = _capture(["npx", "tsc", "--noEmit"], work_dir)
        return rc, "TSC_OK" if rc == 0 else "TSC_FAIL"
    return -1, "NO_BUILD_SYSTEM"


def _copy_workspace(work_dir: Path, out_dir: Path) -> None:
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
        (out_dir / "workspace_copy_errors.txt").write_text(str(e), encoding="utf-8")


def _count_audit_warnings(audit_path: Path) -> dict:
    """Tally audit entries by model and verdict/warning presence.

    The audit.jsonl schema (src/stats.rs) is one JSON object per line with at
    least: ``model``, ``verdict`` (allow/block/etc.), and ``warnings`` (list).
    Missing keys are tolerated so the harness doesn't break on schema drift.
    """
    counts = {"total": 0, "by_model": {}, "with_warnings": 0, "by_verdict": {}}
    if not audit_path.exists():
        return counts
    for line in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        counts["total"] += 1
        model = row.get("model") or row.get("provider") or "unknown"
        counts["by_model"][model] = counts["by_model"].get(model, 0) + 1
        verdict = row.get("verdict") or "unknown"
        counts["by_verdict"][verdict] = counts["by_verdict"].get(verdict, 0) + 1
        warnings = row.get("warnings") or row.get("claims") or []
        if isinstance(warnings, list) and len(warnings) > 0:
            counts["with_warnings"] += 1
        elif isinstance(warnings, int) and warnings > 0:
            counts["with_warnings"] += 1
    return counts


def _count_tokens(agent_log: Path) -> dict:
    """Sum input/output tokens across all JSONL events in agent_output.jsonl."""
    totals = {"input": 0, "output": 0, "events": 0, "tool_calls": 0}
    if not agent_log.exists():
        return totals
    for line in agent_log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["events"] += 1
        # OpenCode event shapes vary; try the common token locations.
        # opencode 1.18.x emits step-finish events with tokens nested under
        # ``part.tokens`` (input/output/total/reasoning/cache).
        usage = (
            ev.get("usage")
            or ev.get("tokens")
            or (ev.get("message", {}) or {}).get("usage")
            or (ev.get("part", {}) or {}).get("tokens")
            or {}
        )
        if isinstance(usage, dict):
            totals["input"] += int(usage.get("input_tokens")
                                    or usage.get("prompt_tokens")
                                    or usage.get("input") or 0)
            totals["output"] += int(usage.get("output_tokens")
                                     or usage.get("completion_tokens")
                                     or usage.get("output") or 0)
        if ev.get("type") == "tool" or ev.get("tool") or ev.get("toolName"):
            totals["tool_calls"] += 1
    return totals


def _write_metadata(out_dir: Path, *, task_id: str, model: str, mode: str,
                    agent_name: str, work_dir: Path, duration_s: float,
                    timeout_hit: bool, build_exit: int, build_label: str,
                    anubis_version: str, audit_summary: dict,
                    token_summary: dict, routing_before: dict,
                    routing_after: dict) -> None:
    payload = {
        "task_id": task_id,
        "agent_model": model,
        "agent_name": agent_name,
        "mode": mode,
        "routed_through_anubis": mode == "with",
        "anubis_version": anubis_version,
        "workdir": str(work_dir),
        "output_dir": str(out_dir),
        "duration_seconds": round(duration_s, 1),
        "timeout_hit": timeout_hit,
        "build_exit": build_exit,
        "build_result": build_label,
        "audit": audit_summary,
        "tokens": token_summary,
        "routing_before": routing_before,
        "routing_after": routing_after,
        "timestamp": _timestamp(),
        "started_at": (_dt.datetime.now() - _dt.timedelta(seconds=duration_s)).isoformat(),
        "ended_at": _dt.datetime.now().isoformat(),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# -----------------------------------------------------------------------------
# Per-mode run
# -----------------------------------------------------------------------------

def _resolve_task_dir(task_id: str) -> Path:
    """Look under corpus/benchmark_tasks/ first, then legacy tasks/."""
    for root in (TASKS_ROOT, TASKS_LEGACY_ROOT):
        candidate = root / task_id / "spec.md"
        if candidate.exists():
            return root / task_id
    raise FileNotFoundError(
        f"task spec not found: {task_id}/spec.md under "
        f"{TASKS_ROOT} or {TASKS_LEGACY_ROOT}"
    )


def run_single_mode(*, task_id: str, model: str, mode: str,
                    agent_name: str, timeout_minutes: int,
                    workdir_override: str = "") -> Path:
    if mode not in ("with", "without"):
        raise ValueError(f"run_single_mode expects with/without, got {mode!r}")

    task_dir = _resolve_task_dir(task_id)
    spec_file = task_dir / "spec.md"

    anubis_version = _ping_anubis()
    print(f"[harness] anubis daemon v{anubis_version} reachable")

    if not Path(OPENCODE_EXE).exists():
        print(f"[harness] ERROR: opencode not at {OPENCODE_EXE}", file=sys.stderr)
        sys.exit(4)

    ts = _timestamp()
    model_slug = _slugify(model)
    out_dir = RESULTS_ROOT / f"{task_id}-{model_slug}-{ts}-{mode}"
    out_dir.mkdir(parents=True, exist_ok=False)

    if workdir_override:
        work_dir = Path(workdir_override)
    else:
        work_dir = Path(os.environ.get("TEMP", tempfile.gettempdir())) / f"anubis-e2e-{task_id}-{ts}-{mode}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Capture routing state BEFORE we touch it.
    routing_before = _capture_routing_state()
    (out_dir / "routing-state-before.json").write_text(
        json.dumps(routing_before, indent=2), encoding="utf-8"
    )

    # Set routing for this mode. With-mode: route ollama through 7878.
    # Without-mode: ensure ollama is direct so we have a clean baseline.
    _ensure_ollama_routing(enabled=(mode == "with"))

    spec_text = spec_file.read_text(encoding="utf-8")
    prompt = _build_prompt(spec_text, work_dir)
    prompt_file = out_dir / "task_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Pre-run audit backup + clear so this run's scans are isolated.
    _backup_audit_log(out_dir)

    env = _build_safe_env()
    agent_log = out_dir / "agent_output.jsonl"
    console_log = out_dir / "console.log"

    model_for_opencode = _normalize_model(model)
    if model_for_opencode != model:
        print(f"[harness] normalized model: {model} -> {model_for_opencode}")

    print(f"[harness] mode={mode} starting opencode: model={model_for_opencode} "
          f"agent={agent_name} timeout={timeout_minutes}m")
    print(f"[harness] output dir: {out_dir}")
    print(f"[harness] workspace:  {work_dir}")

    def _restore_routing() -> None:
        """Always restore the pre-run routing state, even on timeout/SIGINT."""
        try:
            _ensure_ollama_routing(enabled=bool(routing_before.get("ollama_routed")))
            after = _capture_routing_state()
            (out_dir / "routing-state-after.json").write_text(
                json.dumps(after, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[harness][WARN] routing restore failed: {e}", file=sys.stderr)

    started = time.time()
    proc = _spawn_opencode(
        model=model_for_opencode, agent=agent_name, work_dir=work_dir,
        prompt_file=prompt_file, agent_log=agent_log, console_log=console_log,
        env=env,
    )
    _install_sigint_handler(proc, on_kill=_restore_routing)
    try:
        try:
            exited = proc.wait(timeout=timeout_minutes * 60)
            timeout_hit = False
        except subprocess.TimeoutExpired:
            print(f"[harness] TIMEOUT after {timeout_minutes}m -- killing child tree",
                  file=sys.stderr)
            _tree_kill(proc)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            timeout_hit = True
            exited = -1
    finally:
        for fh in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            if fh and not getattr(fh, "closed", False):
                try:
                    fh.close()
                except Exception:
                    pass
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except (ValueError, OSError):
            pass
        # Always restore routing, regardless of how opencode exited.
        _restore_routing()

    duration_s = time.time() - started
    print(f"[harness] opencode exited: code={exited} duration={duration_s:.1f}s")

    # Brief sleep so the daemon flushes audit + log handles.
    time.sleep(1.0)
    _snapshot_audit_log(out_dir)

    build_exit, build_label = _run_build(work_dir, out_dir, task_id)
    print(f"[harness] build={build_exit} -> {build_label}")

    audit_summary = _count_audit_warnings(out_dir / "audit.jsonl")
    token_summary = _count_tokens(agent_log)
    print(f"[harness] audit: {audit_summary}")
    print(f"[harness] tokens: {token_summary}")

    routing_after = {}
    after_path = out_dir / "routing-state-after.json"
    if after_path.exists():
        routing_after = json.loads(after_path.read_text(encoding="utf-8"))

    _copy_workspace(work_dir, out_dir)

    _write_metadata(
        out_dir, task_id=task_id, model=model, mode=mode,
        agent_name=agent_name, work_dir=work_dir, duration_s=duration_s,
        timeout_hit=timeout_hit, build_exit=build_exit, build_label=build_label,
        anubis_version=anubis_version, audit_summary=audit_summary,
        token_summary=token_summary, routing_before=routing_before,
        routing_after=routing_after,
    )

    print(f"[harness] DONE mode={mode} task={task_id}")
    print(f"[harness] result dir: {out_dir}")
    return out_dir


def _discover_all_tasks() -> list[str]:
    """List task dir names under corpus/benchmark_tasks/."""
    if not TASKS_ROOT.exists():
        return []
    return sorted(
        p.name for p in TASKS_ROOT.iterdir()
        if p.is_dir() and (p / "spec.md").exists()
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.run_all:
        task_ids = _discover_all_tasks()
        if not task_ids:
            print(f"[harness] ERROR: no tasks under {TASKS_ROOT}", file=sys.stderr)
            sys.exit(2)
    else:
        if not args.task_id:
            print("[harness] ERROR: --task-id or --all required", file=sys.stderr)
            sys.exit(2)
        task_ids = [args.task_id]

    for tid in task_ids:
        try:
            if args.mode == "both":
                with_dir = run_single_mode(
                    task_id=tid, model=args.model, mode="with",
                    agent_name=args.agent_name, timeout_minutes=args.timeout_minutes,
                    workdir_override=args.workdir,
                )
                without_dir = run_single_mode(
                    task_id=tid, model=args.model, mode="without",
                    agent_name=args.agent_name, timeout_minutes=args.timeout_minutes,
                    workdir_override=args.workdir,
                )
                print(f"[harness] both modes complete for {tid}:")
                print(f"  with    : {with_dir}")
                print(f"  without : {without_dir}")
            else:
                run_single_mode(
                    task_id=tid, model=args.model, mode=args.mode,
                    agent_name=args.agent_name, timeout_minutes=args.timeout_minutes,
                    workdir_override=args.workdir,
                )
        except FileNotFoundError as e:
            print(f"[harness] ERROR: {e}", file=sys.stderr)
            sys.exit(2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
