"""Run all held-out tasks sequentially and aggregate scanner results.

Each task is spawned via harness/run_held_out.ps1 with a 15-min timeout.
After all tasks complete, this script writes:
  - held-out-summary.json  : per-task pass/fail + warning counts
  - held-out-all-warnings.csv : concatenated unique_warnings across runs
  - held-out-report.md     : human-readable markdown report

Policy: HELD_OUT_README.md forbids using these results to tune scanner
weights/thresholds/skip-lists. This script is READ-ONLY analysis.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
RESULTS_DIR = REPO_ROOT / "results"
HARNESS = REPO_ROOT / "harness" / "run_held_out.ps1"

TIMEOUT_MIN = 12
AGENT_MODEL = "zai-coding-plan/glm-4.7"


def list_tasks() -> list[str]:
    return sorted(
        d.name for d in TASKS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("held-out-multipl-")
    )


def run_task(task_id: str) -> Path | None:
    """Invoke run_held_out.ps1 for one task. Return result dir on success."""
    print(f"\n=== {task_id} ===", flush=True)
    cmd = [
        "powershell", "-NoProfile", "-File",
        str(HARNESS),
        "-TaskId", task_id,
        "-AgentModel", AGENT_MODEL,
        "-TimeoutMinutes", str(TIMEOUT_MIN),
    ]
    start = time.time()
    try:
        # Allow PowerShell its own timeout enforcement; cap wall at TIMEOUT+2m.
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=(TIMEOUT_MIN + 2) * 60,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(f"  ORCHESTRATOR KILL: orchestrator timeout {(TIMEOUT_MIN + 2)}m exceeded")
        return None

    elapsed = time.time() - start
    print(f"  elapsed: {elapsed:.0f}s", flush=True)
    if result.returncode != 0:
        print(f"  PS exit={result.returncode}", flush=True)
        print(f"  stderr tail: {(result.stderr or '')[-400:]}", flush=True)

    # Find the most recent result dir for this task.
    candidates = sorted(
        RESULTS_DIR.glob(f"{task_id}-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        print(f"  no result dir found")
        return None
    out = candidates[0]
    print(f"  result dir: {out.name}", flush=True)
    return out


def collect_task_summary(task_id: str, result_dir: Path) -> dict:
    """Pull metadata.json + unique_warnings.txt into a summary row."""
    meta_path = result_dir / "metadata.json"
    warn_path = result_dir / "unique_warnings.txt"
    audit_path = result_dir / "audit.jsonl"

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    warnings = []
    if warn_path.exists():
        warnings = [
            line.strip()
            for line in warn_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # Rough audit.jsonl scan-event count (entries with warnings array non-empty).
    scan_events = 0
    warning_total = 0
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ws = ev.get("warnings") or []
            scan_events += 1
            if ws:
                warning_total += len(ws)

    return {
        "task_id": task_id,
        "language": _lang_of(task_id),
        "problem": _problem_of(task_id),
        "result_dir": result_dir.name,
        "timeout_hit": meta.get("timeout_hit"),
        "build_exit": meta.get("build_exit", -1),
        "test_exit": meta.get("test_exit", -1),
        "unique_warnings_count": len(warnings),
        "unique_warnings": warnings,
        "scan_events": scan_events,
        "warning_total": warning_total,
    }


def _lang_of(task_id: str) -> str:
    if "-py-" in task_id: return "python"
    if "-rs-" in task_id: return "rust"
    if "-ts-" in task_id: return "typescript"
    if "-go-" in task_id: return "go"
    return "unknown"


def _problem_of(task_id: str) -> str:
    # task IDs look like held-out-multipl-{lang}-{problem_id}
    parts = task_id.split("-", 3)
    return parts[3] if len(parts) >= 4 else "unknown"


def main() -> int:
    tasks = list_tasks()
    print(f"found {len(tasks)} held-out tasks", flush=True)
    for t in tasks:
        print(f"  - {t}")

    summaries: list[dict] = []
    for task_id in tasks:
        result_dir = run_task(task_id)
        if result_dir is None:
            print(f"  ! no result dir; skipping aggregation")
            continue
        s = collect_task_summary(task_id, result_dir)
        summaries.append(s)
        # Progress checkpoint after each task so partial runs are usable.
        _write_outputs(summaries)

    _write_outputs(summaries, final=True)
    print("\n=== summary ===")
    print(f"  tasks completed: {len(summaries)}")
    total_warn = sum(s["unique_warnings_count"] for s in summaries)
    print(f"  total unique warnings: {total_warn}")
    print(f"  report: {REPO_ROOT / 'held-out-report.md'}")
    return 0


def _write_outputs(summaries: list[dict], final: bool = False) -> None:
    if not summaries:
        return

    # JSON
    (REPO_ROOT / "held-out-summary.json").write_text(
        json.dumps(summaries, indent=2),
        encoding="utf-8",
    )

    # CSV
    csv_path = REPO_ROOT / "held-out-all-warnings.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "language", "problem", "warning"])
        for s in summaries:
            for warn in s["unique_warnings"]:
                w.writerow([s["task_id"], s["language"], s["problem"], warn])

    # Markdown report
    md = ["# Held-Out Corpus Report", ""]
    md.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    md.append("")
    md.append("**FROZEN — DO NOT tune scanner weights against this corpus.**")
    md.append("See [HELD_OUT_README.md](./HELD_OUT_README.md).")
    md.append("")
    md.append("## Per-Task Summary")
    md.append("")
    md.append("| Task | Lang | Timeout | Build | Test | Unique Warnings |")
    md.append("|---|---|---|---|---|---|")
    for s in summaries:
        md.append(
            f"| {s['task_id']} | {s['language']} | "
            f"{'yes' if s['timeout_hit'] else 'no'} | "
            f"{s['build_exit']} | {s['test_exit']} | "
            f"{s['unique_warnings_count']} |"
        )
    md.append("")
    md.append("## Aggregated Metrics")
    md.append("")
    total_warn = sum(s["unique_warnings_count"] for s in summaries)
    completed = len(summaries)
    passing = sum(1 for s in summaries if s["test_exit"] == 0)
    md.append(f"- Tasks completed: **{completed}**")
    md.append(f"- Tasks passing tests: **{passing} / {completed}**")
    md.append(f"- Total unique scanner warnings: **{total_warn}**")
    md.append("")
    md.append("## False Positive Analysis")
    md.append("")
    md.append("Each warning below was produced by the scanner on agent output")
    md.append("during a held-out task. Classify each as TP (genuine hallucination)")
    md.append("or FP (legitimate code flagged). DO NOT use this analysis to")
    md.append("adjust scanner weights — only to document the gap between")
    md.append("DELULU (dev) and held-out (generalization) metrics.")
    md.append("")
    for s in summaries:
        if not s["unique_warnings"]:
            continue
        md.append(f"### {s['task_id']}")
        md.append("")
        for w in s["unique_warnings"]:
            md.append(f"- {w}")
        md.append("")
    (REPO_ROOT / "held-out-report.md").write_text(
        "\n".join(md), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
