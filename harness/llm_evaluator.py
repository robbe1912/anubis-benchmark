#!/usr/bin/env python3
"""
LLM evaluator for Anubis benchmark transcripts.

Reads a result dir (agent_output.jsonl + unique_warnings.txt + metadata.json),
sends the agent transcript to GLM-4.7 for hallucination identification, then
matches scanner warnings against LLM-identified hallucinations to compute
precision/recall/F1.

Usage:
    python harness/llm_evaluator.py --result-dir results/held-out-multipl-py-humaneval-0-20260802-123917
    python harness/llm_evaluator.py --results-root results --pattern 'held-out-*'
    python harness/llm_evaluator.py --results-root results --pattern 'task-*' --limit 5

Env vars:
    Z_AI_API_KEY (required) — bearer token for api.z.ai
    EVALUATOR_MODEL (optional, default 'glm-5.2') — judge model (best reasoning)
    EVALUATOR_BASE_URL (optional, default 'https://api.z.ai/api/coding/paas/v4')
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class AgentEvent:
    type: str
    text: str = ""
    tool: str = ""
    tool_input: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Hallucination:
    """An LLM-identified hallucination in the agent transcript."""
    kind: str  # import | method | variable | parameter | function | class | other
    symbol: str  # the hallucinated name (e.g. 'response.parseBody', 'task_id')
    location: str  # event index or 'unknown'
    description: str  # why LLM thinks it's hallucinated


@dataclass
class WarningClassification:
    warning: str
    verdict: str  # TP | FP | UNCERTAIN
    matched_hallucination: str | None = None
    reason: str = ""


@dataclass
class TaskEvaluation:
    result_dir: str
    task_id: str = ""
    bypass_anubis: bool = False
    # Agent transcript
    event_count: int = 0
    text_event_count: int = 0
    tool_event_count: int = 0
    transcript_chars: int = 0
    # LLM judge output
    total_hallucinations: int = 0
    hallucinations: list[Hallucination] = field(default_factory=list)
    # Scanner output
    scanner_warnings: list[str] = field(default_factory=list)
    classifications: list[WarningClassification] = field(default_factory=list)
    # Derived metrics
    true_positives: int = 0
    false_positives: int = 0
    uncertain: int = 0
    recall: float = 0.0  # caught / total
    precision: float = 0.0  # TP / (TP + FP)
    f1: float = 0.0
    # Latency
    judge_latency_ms: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def parse_events(jsonl_path: Path) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    if not jsonl_path.exists():
        return events
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = obj.get("type", "")
        part = obj.get("part", {}) or {}
        text = ""
        tool = ""
        tool_input = ""
        if etype == "text":
            text = part.get("text", "") or ""
        elif etype == "tool_use":
            tool = part.get("tool", "") or ""
            state = part.get("state", {}) or {}
            tool_input = json.dumps(state.get("input", state))[:2000]
        events.append(AgentEvent(
            type=etype,
            text=text,
            tool=tool,
            tool_input=tool_input,
            raw={"type": etype, "tool": tool},
        ))
    return events


def render_transcript(events: list[AgentEvent], char_cap: int = 24000) -> str:
    """Render events into a compact transcript for the LLM judge.

    Skips step_start/step_finish noise. Caps each event's content to keep
    total prompt under ~24K chars (≈6K tokens, leaves headroom for response).
    """
    out: list[str] = []
    total = 0
    for i, e in enumerate(events):
        if e.type in ("step_start", "step_finish"):
            continue
        if e.type == "text" and e.text:
            chunk = f"[{i}] LLM: {e.text}"
        elif e.type == "tool_use":
            chunk = f"[{i}] TOOL({e.tool}): {e.tool_input}"
        else:
            continue
        if total + len(chunk) > char_cap:
            remaining = char_cap - total
            if remaining > 200:
                chunk = chunk[:remaining] + "...[truncated]"
                out.append(chunk)
            break
        out.append(chunk)
        total += len(chunk)
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# Scanner output parsing
# ---------------------------------------------------------------------------


def parse_scanner_warnings(warnings_path: Path) -> list[str]:
    """Read scanner warnings. Prefers `rescan-warnings.txt` (current scanner
    output from held_out_rescan.rs) over `unique_warnings.txt` (original
    benchmark run with whatever scanner version was live at the time).
    """
    rescan = warnings_path.parent / "rescan-warnings.txt"
    paths_to_try = [rescan, warnings_path] if rescan.exists() else [warnings_path]
    if not warnings_path.exists() and not rescan.exists():
        return []
    raw = b""
    for p in paths_to_try:
        if p.exists():
            raw = p.read_bytes()
            break
    # PowerShell Out-File may emit UTF-16 LE BOM; try multiple encodings.
    for enc in ("utf-8-sig", "utf-16", "utf-16le", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_metadata(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    raw = meta_path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-16le", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# LLM judge call
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are a code hallucination evaluator. You inspect agent transcripts (LLM responses + tool calls) and identify hallucinations: invented APIs, methods, imports, variables, parameters, classes, or function signatures that don't exist in the referenced library/framework/language.

Examples of hallucinations:
- response.parseBody() — fetch Response has .json()/.text()/.blob()/.arrayBuffer() but not .parseBody()
- java.util.Vector.add(item, index) — Vector.add is single-arg or (index, item), not (item, index)
- import { useState } from 'react-router' — useState is from 'react', not 'react-router'
- HashMap.entry(key, default) — entry() returns an Entry handle, doesn't take a default
- fn process_data(data: Vec<u8>) -> Result<Processed, ProcessingError> — if ProcessingError isn't defined anywhere

Examples of NON-hallucinations (do NOT flag):
- Code that compiles and uses real APIs correctly
- Variables defined in scope (function params, let bindings, etc.)
- Standard library calls with correct arity
- User-defined types/classes/functions visible in the transcript

Output STRICT JSON. Schema:
{
  "hallucinations": [
    {
      "kind": "import" | "method" | "variable" | "parameter" | "function" | "class" | "other",
      "symbol": "<the hallucinated name>",
      "location": "<event index from [N] marker, or 'unknown'>",
      "description": "<one-sentence reason>"
    }
  ]
}

If the transcript has no hallucinations, output {"hallucinations": []}.
"""


def call_judge(transcript: str, api_key: str, model: str, base_url: str) -> tuple[list[Hallucination], int]:
    """Call GLM-4.7 to identify hallucinations in transcript. Returns (list, latency_ms)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"AGENT TRANSCRIPT:\n\n{transcript}"},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    latency_ms = int((time.monotonic() - started) * 1000)
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    # Extract JSON from response (may be wrapped in ```json fences).
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return [], latency_ms
    hallucinations = []
    for h in parsed.get("hallucinations", []):
        hallucinations.append(Hallucination(
            kind=str(h.get("kind", "other")),
            symbol=str(h.get("symbol", "")),
            location=str(h.get("location", "unknown")),
            description=str(h.get("description", "")),
        ))
    return hallucinations, latency_ms


# ---------------------------------------------------------------------------
# Classification: match scanner warnings against LLM-identified hallucinations
# ---------------------------------------------------------------------------


def extract_warning_symbol(warning: str) -> str:
    """Extract the symbol name from a scanner warning line.

    Examples:
        'forge: hallucinated-method: `response.parseBody` — ...' -> 'response.parseBody'
        'cached-hallucination: HashMap.hashCode not in cached symbols' -> 'HashMap.hashCode'
        'Hallucinated API: default_path() (did you mean default_port?)' -> 'default_path'
    """
    # Try backtick-quoted symbol first (FORGE format).
    m = re.search(r"`([^`]+)`", warning)
    if m:
        return m.group(1)
    # Hallucinated API: <name>( ...
    m = re.search(r"Hallucinated API:\s+(\S+?)\(", warning)
    if m:
        return m.group(1)
    # cached-hallucination: <name> ...
    m = re.search(r"cached-hallucination:\s+(\S+)", warning)
    if m:
        return m.group(1).rstrip(":,")
    return warning[:80]


def classify_warning(warning: str, hallucinations: list[Hallucination]) -> WarningClassification:
    sym = extract_warning_symbol(warning).lower()
    # Strip module/receiver prefixes for matching (e.g. 'response.parseBody' -> 'parsebody').
    bare = sym.rsplit(".", 1)[-1]
    for h in hallucinations:
        h_sym = h.symbol.lower()
        h_bare = h_sym.rsplit(".", 1)[-1]
        if sym == h_sym or bare == h_bare or bare in h_sym.split(".") or h_bare in sym.split("."):
            return WarningClassification(
                warning=warning,
                verdict="TP",
                matched_hallucination=h.symbol,
                reason=f"matches LLM hallucination '{h.symbol}' ({h.kind})",
            )
    # No match — likely FP.
    return WarningClassification(
        warning=warning,
        verdict="FP",
        matched_hallucination=None,
        reason="no matching LLM-identified hallucination",
    )


# ---------------------------------------------------------------------------
# Per-task evaluation
# ---------------------------------------------------------------------------


def evaluate_task(result_dir: Path, api_key: str, model: str, base_url: str) -> TaskEvaluation:
    ev = TaskEvaluation(result_dir=str(result_dir))
    events = parse_events(result_dir / "agent_output.jsonl")
    meta = parse_metadata(result_dir / "metadata.json")
    ev.task_id = meta.get("task_id", result_dir.name)
    ev.bypass_anubis = bool(meta.get("bypass_anubis", False))
    ev.event_count = len(events)
    ev.text_event_count = sum(1 for e in events if e.type == "text")
    ev.tool_event_count = sum(1 for e in events if e.type == "tool_use")

    transcript = render_transcript(events)
    ev.transcript_chars = len(transcript)

    if not transcript.strip():
        ev.error = "empty transcript"
        return ev

    try:
        hallucinations, latency = call_judge(transcript, api_key, model, base_url)
    except urllib.error.HTTPError as e:
        ev.error = f"judge HTTP {e.code}: {e.reason}"
        return ev
    except urllib.error.URLError as e:
        ev.error = f"judge URL error: {e.reason}"
        return ev
    ev.hallucinations = hallucinations
    ev.total_hallucinations = len(hallucinations)
    ev.judge_latency_ms = latency

    ev.scanner_warnings = parse_scanner_warnings(result_dir / "unique_warnings.txt")
    ev.classifications = [classify_warning(w, hallucinations) for w in ev.scanner_warnings]
    ev.true_positives = sum(1 for c in ev.classifications if c.verdict == "TP")
    ev.false_positives = sum(1 for c in ev.classifications if c.verdict == "FP")
    ev.uncertain = sum(1 for c in ev.classifications if c.verdict == "UNCERTAIN")

    # Recall = caught / total. With 0 total, recall is undefined (report as N/A).
    if ev.total_hallucinations > 0:
        ev.recall = ev.true_positives / ev.total_hallucinations
    # Precision = TP / (TP + FP). With 0 warnings, precision is undefined.
    flagged = ev.true_positives + ev.false_positives
    if flagged > 0:
        ev.precision = ev.true_positives / flagged
    if ev.precision + ev.recall > 0:
        ev.f1 = 2 * ev.precision * ev.recall / (ev.precision + ev.recall)
    return ev


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def aggregate(evaluations: list[TaskEvaluation]) -> dict[str, Any]:
    n = len(evaluations)
    total_hallu = sum(e.total_hallucinations for e in evaluations)
    total_tp = sum(e.true_positives for e in evaluations)
    total_fp = sum(e.false_positives for e in evaluations)
    total_warnings = sum(len(e.scanner_warnings) for e in evaluations)
    agg_recall = (total_tp / total_hallu) if total_hallu > 0 else None
    agg_precision = (total_tp / (total_tp + total_fp)) if (total_tp + total_fp) > 0 else None
    agg_f1 = (
        2 * agg_precision * agg_recall / (agg_precision + agg_recall)
        if (agg_precision is not None and agg_recall is not None
            and agg_precision + agg_recall > 0)
        else None
    )
    # Split by bypass mode.
    with_anubis = [e for e in evaluations if not e.bypass_anubis]
    without_anubis = [e for e in evaluations if e.bypass_anubis]

    def sub(items: list[TaskEvaluation]) -> dict[str, Any]:
        if not items:
            return {"n": 0}
        h = sum(e.total_hallucinations for e in items)
        tp = sum(e.true_positives for e in items)
        fp = sum(e.false_positives for e in items)
        return {
            "n": len(items),
            "total_hallucinations": h,
            "caught_hallucinations": tp,
            "false_positives": fp,
            "recall": (tp / h) if h > 0 else None,
            "precision": (tp / (tp + fp)) if (tp + fp) > 0 else None,
        }

    return {
        "n": n,
        "total_hallucinations": total_hallu,
        "total_warnings": total_warnings,
        "true_positives": total_tp,
        "false_positives": total_fp,
        "recall": agg_recall,
        "precision": agg_precision,
        "f1": agg_f1,
        "with_anubis": sub(with_anubis),
        "without_anubis": sub(without_anubis),
    }


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def write_report(evaluations: list[TaskEvaluation], agg: dict[str, Any], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# LLM Evaluator Report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Tasks evaluated: **{agg['n']}**")
    lines.append(f"- Total hallucinations (LLM judge): **{agg['total_hallucinations']}**")
    lines.append(f"- Total scanner warnings: **{agg['total_warnings']}**")
    lines.append(f"- True positives (caught): **{agg['true_positives']}**")
    lines.append(f"- False positives: **{agg['false_positives']}**")
    r = agg["recall"]
    p = agg["precision"]
    f1 = agg["f1"]
    lines.append(f"- Recall: **{r:.4f}**" if r is not None else "- Recall: N/A")
    lines.append(f"- Precision: **{p:.4f}**" if p is not None else "- Precision: N/A")
    lines.append(f"- F1: **{f1:.4f}**" if f1 is not None else "- F1: N/A")
    lines.append("")

    def fmt_sub(label: str, sub: dict[str, Any]) -> None:
        lines.append(f"### {label}")
        lines.append("")
        if sub.get("n", 0) == 0:
            lines.append("_No tasks in this group._")
            lines.append("")
            return
        lines.append(f"- Tasks: {sub['n']}")
        lines.append(f"- Hallucinations: {sub['total_hallucinations']}")
        lines.append(f"- Caught: {sub['caught_hallucinations']}")
        lines.append(f"- False positives: {sub['false_positives']}")
        r2 = sub.get("recall")
        p2 = sub.get("precision")
        lines.append(f"- Recall: {r2:.4f}" if r2 is not None else "- Recall: N/A")
        lines.append(f"- Precision: {p2:.4f}" if p2 is not None else "- Precision: N/A")
        lines.append("")

    fmt_sub("WITH anubis feedback", agg["with_anubis"])
    fmt_sub("WITHOUT anubis feedback (bypass)", agg["without_anubis"])

    lines.append("## Per-task detail")
    lines.append("")
    lines.append("| Task | Bypass | Events | Hallu | Caught | FP | Recall | Precision |")
    lines.append("|------|--------|--------|-------|--------|----|--------|-----------|")
    for e in evaluations:
        bypass = "yes" if e.bypass_anubis else "no"
        r_str = f"{e.recall:.2f}" if e.total_hallucinations > 0 else "N/A"
        denom = e.true_positives + e.false_positives
        p_str = f"{e.precision:.2f}" if denom > 0 else "N/A"
        lines.append(
            f"| `{e.task_id}` | {bypass} | {e.text_event_count}t/{e.tool_event_count}tool | "
            f"{e.total_hallucinations} | {e.true_positives} | {e.false_positives} | {r_str} | {p_str} |"
        )
    lines.append("")

    # Sample of FPs (top 5) for manual review.
    fps = [(e.task_id, c.warning, c.reason) for e in evaluations for c in e.classifications if c.verdict == "FP"]
    if fps:
        lines.append("## Sample false positives (first 10)")
        lines.append("")
        for tid, w, reason in fps[:10]:
            lines.append(f"- `{tid}`: {w}")
            lines.append(f"  - {reason}")
        lines.append("")

    # Sample of uncaught hallucinations (top 10).
    caught_syms = {(e.task_id, c.matched_hallucination) for e in evaluations for c in e.classifications if c.verdict == "TP"}
    uncaught = []
    for e in evaluations:
        for h in e.hallucinations:
            if (e.task_id, h.symbol) not in caught_syms:
                uncaught.append((e.task_id, h.kind, h.symbol, h.description))
    if uncaught:
        lines.append("## Uncaught hallucinations (first 10)")
        lines.append("")
        for tid, kind, sym, desc in uncaught[:10]:
            lines.append(f"- `{tid}` ({kind}): `{sym}` — {desc}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def discover_dirs(results_root: Path, pattern: str, limit: int | None = None) -> list[Path]:
    dirs = sorted(d for d in results_root.glob(pattern) if d.is_dir())
    if limit:
        dirs = dirs[:limit]
    return dirs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result-dir", help="Single result dir to evaluate")
    ap.add_argument("--results-root", default="results", help="Root containing result dirs (default: results)")
    ap.add_argument("--pattern", default="held-out-*", help="Glob pattern for result dirs")
    ap.add_argument("--limit", type=int, default=None, help="Max dirs to evaluate")
    ap.add_argument("--out", default="llm-eval-report.md", help="Output markdown report path")
    ap.add_argument("--json-out", default="llm-eval-summary.json", help="Output JSON summary path")
    args = ap.parse_args()

    api_key = os.environ.get("Z_AI_API_KEY")
    if not api_key:
        sys.stderr.write("ERROR: Z_AI_API_KEY env var required\n")
        return 2
    model = os.environ.get("EVALUATOR_MODEL", "glm-5.2")
    base_url = os.environ.get("EVALUATOR_BASE_URL", "https://api.z.ai/api/coding/paas/v4")

    if args.result_dir:
        dirs = [Path(args.result_dir)]
    else:
        dirs = discover_dirs(Path(args.results_root), args.pattern, args.limit)

    if not dirs:
        sys.stderr.write(f"ERROR: no result dirs matched pattern '{args.pattern}' in {args.results_root}\n")
        return 2

    sys.stderr.write(f"Evaluating {len(dirs)} task(s) with {model}...\n")
    evaluations: list[TaskEvaluation] = []
    for i, d in enumerate(dirs, 1):
        sys.stderr.write(f"[{i}/{len(dirs)}] {d.name}\n")
        ev = evaluate_task(d, api_key, model, base_url)
        if ev.error:
            sys.stderr.write(f"  ERROR: {ev.error}\n")
        else:
            sys.stderr.write(
                f"  events={ev.event_count} hallu={ev.total_hallucinations} "
                f"tp={ev.true_positives} fp={ev.false_positives} "
                f"recall={ev.recall:.2f} prec={ev.precision:.2f} "
                f"latency={ev.judge_latency_ms}ms\n"
            )
        evaluations.append(ev)

    agg = aggregate(evaluations)

    out_path = Path(args.out)
    write_report(evaluations, agg, out_path)
    sys.stderr.write(f"\nWrote {out_path}\n")

    json_path = Path(args.json_out)
    payload = {
        "aggregate": agg,
        "tasks": [asdict(e) for e in evaluations],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    sys.stderr.write(f"Wrote {json_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
