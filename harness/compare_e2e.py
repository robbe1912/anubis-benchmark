#!/usr/bin/env python3
"""Compare two E2E benchmark result directories (with-Anubis vs without-Anubis).

Reads:
    <with-dir>/audit.jsonl       scanner verdicts + warnings (with-mode only)
    <with-dir>/agent_output.jsonl   opencode events + token usage
    <with-dir>/metadata.json     build outcome, routing state
    <without-dir>/agent_output.jsonl
    <without-dir>/metadata.json

Emits a YAML report comparing:
    - audit warnings count (with only — without should be 0)
    - audit entries by model (proves ollama traffic was scanned)
    - build outcome in each mode
    - token usage in each mode
    - delta in tokens (with vs without) — scanner adds zero tokens

Usage:
    python harness/compare_e2e.py \\
        --with-dir    results-e2e/task-012-...-with \\
        --without-dir results-e2e/task-012-...-without \\
        --output      results-e2e/task-012-comparison.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_metadata(run_dir: Path) -> dict:
    p = run_dir / "metadata.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_audit(audit_path: Path) -> dict:
    """Re-tally the audit.jsonl in case metadata.json is stale."""
    counts = {
        "total": 0,
        "by_model": {},
        "by_verdict": {},
        "with_warnings": 0,
        "warning_samples": [],
    }
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
        n = len(warnings) if isinstance(warnings, list) else (
            warnings if isinstance(warnings, int) else 0
        )
        if n > 0:
            counts["with_warnings"] += 1
            if len(counts["warning_samples"]) < 5 and isinstance(warnings, list):
                counts["warning_samples"].extend(warnings[:2])
    counts["warning_samples"] = counts["warning_samples"][:5]
    return counts


def _load_tokens(agent_log: Path) -> dict:
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


def _yaml_dump(data: Any, indent: int = 0) -> str:
    """Tiny YAML emitter — handles dict/list/str/int/float/bool/None.

    Good enough for the report shape we produce; avoids a PyYAML dep so the
    script runs in a bare python3 install.
    """
    pad = "  " * indent
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.append(_yaml_dump(v, indent + 1))
            elif isinstance(v, list):
                out.append(f"{pad}{k}: []")
            elif isinstance(v, bool):
                out.append(f"{pad}{k}: {'true' if v else 'false'}")
            elif v is None:
                out.append(f"{pad}{k}: null")
            elif isinstance(v, str) and ("\n" in v or len(v) > 80):
                # Use block scalar for long/multiline strings.
                escaped = v.replace('"', '\\"')
                out.append(f'{pad}{k}: "{escaped}"')
            else:
                out.append(f"{pad}{k}: {v}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                out.append(f"{pad}-")
                out.append(_yaml_dump(item, indent + 1))
            elif isinstance(item, bool):
                out.append(f"{pad}- {'true' if item else 'false'}")
            elif item is None:
                out.append(f"{pad}- null")
            elif isinstance(item, str):
                escaped = item.replace('"', '\\"')
                out.append(f'{pad}- "{escaped}"')
            else:
                out.append(f"{pad}- {item}")
    else:
        out.append(f"{pad}{data}")
    return "\n".join(out)


def compare(with_dir: Path, without_dir: Path) -> dict:
    with_meta = _load_metadata(with_dir)
    without_meta = _load_metadata(without_dir)

    with_audit = _load_audit(with_dir / "audit.jsonl")
    without_audit = _load_audit(without_dir / "audit.jsonl")

    with_tokens = _load_tokens(with_dir / "agent_output.jsonl")
    without_tokens = _load_tokens(without_dir / "agent_output.jsonl")

    with_audit_meta = with_meta.get("audit", {}) if with_meta else with_audit
    without_audit_meta = without_meta.get("audit", {}) if without_meta else without_audit

    task_id = with_meta.get("task_id") or without_dir.name
    model = with_meta.get("agent_model") or "unknown"

    # Sanity invariants the report should make explicit:
    #   with-mode MUST have ollama routing enabled after enable
    #   without-mode MUST have audit total == 0 (daemon never saw the traffic)
    with_routing = bool((with_meta.get("routing_before") or {})
                        .get("ollama_routed")) or with_audit["total"] > 0
    without_audit_empty = without_audit["total"] == 0

    report = {
        "task_id": task_id,
        "model": model,
        "with_dir": str(with_dir),
        "without_dir": str(without_dir),
        "invariants": {
            # Routing was actually enabled at some point during the with-run.
            "ollama_routing_observed": with_routing,
            # Without-mode produced no scanner audit (proves baseline isolation).
            "without_audit_empty": without_audit_empty,
        },
        "audit_with": {
            "total_entries": with_audit["total"],
            "with_warnings": with_audit["with_warnings"],
            "by_model": with_audit["by_model"],
            "by_verdict": with_audit["by_verdict"],
            "samples": with_audit["warning_samples"],
        },
        "audit_without": {
            "total_entries": without_audit["total"],
        },
        "build": {
            "with": {
                "label": with_meta.get("build_result"),
                "exit": with_meta.get("build_exit"),
            },
            "without": {
                "label": without_meta.get("build_result"),
                "exit": without_meta.get("build_exit"),
            },
        },
        "tokens": {
            "with": with_tokens,
            "without": without_tokens,
            # Scanner cost is independent of the agent — both should match
            # within model sampling noise. Large deltas hint at a routing leak.
            "delta_input": with_tokens["input"] - without_tokens["input"],
            "delta_output": with_tokens["output"] - without_tokens["output"],
        },
        "durations": {
            "with_seconds": with_meta.get("duration_seconds"),
            "without_seconds": without_meta.get("duration_seconds"),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="compare_e2e",
        description="Compare with-Anubis vs without-Anubis E2E benchmark runs.",
    )
    p.add_argument("--with-dir", "-WithDir", dest="with_dir", required=True,
                   help="Result dir from a with-mode run.")
    p.add_argument("--without-dir", "-WithoutDir", dest="without_dir", required=True,
                   help="Result dir from a without-mode run.")
    p.add_argument("--output", "-Output", dest="output", default="",
                   help="Write YAML to this path (default: stdout).")
    args = p.parse_args(argv)

    with_dir = Path(args.with_dir)
    without_dir = Path(args.without_dir)
    if not with_dir.is_dir():
        print(f"[compare] ERROR: with-dir not a directory: {with_dir}", file=sys.stderr)
        sys.exit(2)
    if not without_dir.is_dir():
        print(f"[compare] ERROR: without-dir not a directory: {without_dir}", file=sys.stderr)
        sys.exit(2)

    report = compare(with_dir, without_dir)
    yaml_text = _yaml_dump(report)

    if args.output:
        Path(args.output).write_text(yaml_text + "\n", encoding="utf-8")
        print(f"[compare] wrote {args.output}")
    else:
        print(yaml_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
