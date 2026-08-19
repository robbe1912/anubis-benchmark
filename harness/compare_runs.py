#!/usr/bin/env python3
"""Side-by-side comparison report generator (TV plan Subtask A).

Reads two result dirs produced by ``harness/run_side_by_side.py`` and emits a
YAML diff report keyed per the structure documented in
thought-verification.md v6 (Side-by-Side -> Comparison report).

Usage:
    python harness/compare_runs.py \\
        --with-dir    results/task-001-...-s42-20260802-101530-with \\
        --without-dir results/task-001-...-s42-20260802-102515-without \\
        --output      comparison.yaml

The report contains:
- Per-run summaries (duration, requests, build/test status, warning counts,
  warning type breakdown, total tokens).
- A metrics block: build_delta, duration_delta, api_requests_delta,
  tokens_delta -- these are computable from raw runs.
- Recall / precision / f1 / silent_fn_delta are emitted as ``null`` until
  labeler files (``labels.<labeler>.jsonl``) and a ground-truth file
  (``labels.ground-truth.jsonl``) exist in the run dir. Computed via
  ``lib.labeling`` (majority_vote + classify_outcomes) — schema-unified
  across all ``tv_labels.py`` subcommands (extract/judge/batch).
- A ground_truth block (per-mode true_positives / false_positives /
  false_negatives / total_prose_claims / krippendorff_alpha) is emitted
  when labeler + ground-truth files are present.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("compare_runs.py requires PyYAML: pip install pyyaml\n")
    raise


# Warning message prefixes mirrored from evaluation/evaluate.ps1.
# Each classifier returns (category, tv_type) where category is the high-level
# bucket (e.g. "code_hallucination", "logic", "scope") and tv_type is the
# fine-grained label used in the report's tv_warning_types map.
WARNING_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^cached-hallucination:"), "code_hallucination", "cached_hallucination"),
    (re.compile(r"^forge:\s*hallucinated-"), "code_hallucination", "forge_hallucinated_symbol"),
    (re.compile(r"^forge:\s*Hallucinated API:"), "code_hallucination", "forge_hallucinated_api"),
    (re.compile(r"^forge:"), "code_hallucination", "forge_other"),
    (re.compile(r"^Hallucinated API:"), "code_hallucination", "hallucinated_api"),
    (re.compile(r"^Unverified API:"), "code_hallucination", "unverified_api"),
    (re.compile(r"^hallucinated-(method|import|variable|function):"),
     "code_hallucination", "hallucinated_symbol"),
    (re.compile(r"^logic:"), "logic", "logic"),
    (re.compile(r"^scope-hallucination:"), "scope", "scope_hallucination"),
]


def classify_warning(message: str) -> tuple[str, str]:
    """Map a raw warning string to (category, tv_type).

    Default fall-through: ("other", "other") -- the warning still counts
    toward tv_warnings_total.
    """
    for pat, cat, tv_type in WARNING_PATTERNS:
        if pat.search(message):
            return cat, tv_type
    return "other", "other"


# -----------------------------------------------------------------------------
# Per-run summarization
# -----------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            # Skip malformed lines but count nothing.
            continue
    return rows


def _summarize_run(run_dir: Path) -> dict[str, Any]:
    meta_path = run_dir / "metadata.json"
    audit_entries = _read_jsonl(run_dir / "audit.jsonl")
    agent_entries = _read_jsonl(run_dir / "agent_output.jsonl")

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Aggregate warnings across all audit entries.
    # Audit warnings may be either plain strings (current daemon format) or
    # dicts with {message, kind, ...} (future schema). Handle both.
    warning_messages: list[str] = []
    for entry in audit_entries:
        for w in entry.get("warnings") or []:
            if isinstance(w, str):
                warning_messages.append(w)
            elif isinstance(w, dict):
                warning_messages.append(w.get("message") or w.get("kind") or json.dumps(w, sort_keys=True))
            else:
                warning_messages.append(str(w))
    by_tv_type: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    for msg in warning_messages:
        cat, tv_type = classify_warning(msg)
        by_category[cat] += 1
        by_tv_type[tv_type] += 1

    tokens_total = sum(int(e.get("total_tokens", 0) or 0) for e in audit_entries)
    api_requests = len(audit_entries)

    build_result = str(meta.get("build_result", "UNKNOWN"))
    return {
        "run_dir": str(run_dir),
        "run_id": meta.get("timestamp"),
        "task_id": meta.get("task_id"),
        "model": meta.get("agent_model"),
        "seed": meta.get("seed"),
        "mode": meta.get("mode"),
        "bypass_anubis": meta.get("bypass_anubis"),
        "duration_s": meta.get("duration_seconds"),
        "timeout_hit": meta.get("timeout_hit"),
        "build_result": build_result,
        "build_exit": meta.get("build_exit"),
        "test_exit": meta.get("test_exit"),
        "api_requests": api_requests,
        "agent_event_count": len(agent_entries),
        "tv_warnings_total": len(warning_messages),
        "tv_warnings_unique": len(set(warning_messages)),
        "tv_warning_types": dict(by_tv_type),
        "tv_warning_categories": dict(by_category),
        "code_hallucination_warnings": int(by_category.get("code_hallucination", 0)),
        "tokens_total": tokens_total,
        "anubis_version": meta.get("anubis_version"),
    }


def _parse_build_test(build_result: str) -> tuple[str, str]:
    """Split 'BUILD_OK_TEST_OK' into ('PASS', 'PASS'); 'FAIL'/'UNKNOWN' otherwise."""
    if not build_result or build_result == "UNKNOWN":
        return "UNKNOWN", "UNKNOWN"
    parts = build_result.split("_TEST_")
    build = "PASS" if parts[0] == "BUILD_OK" else "FAIL"
    if len(parts) > 1:
        test = "PASS" if parts[1] == "OK" else "FAIL"
    else:
        test = "UNKNOWN"
    return build, test


# -----------------------------------------------------------------------------
# Optional ground-truth block (computed from labeler files + ground truth)
# -----------------------------------------------------------------------------

# lib/ is a sibling of harness/ at the repo root. labeling.py imports
# `from lib.stats import ...` so we expose both the repo root (for `lib.*`)
# and lib/ itself (for direct `import labeling` / `import stats`).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB_DIR = _REPO_ROOT / "lib"
for _p in (str(_REPO_ROOT), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import labeling as L  # noqa: E402  (path-dependent import)


def _discover_labeler_files(run_dir: Path) -> tuple[list[Path], Path | None]:
    """Find per-labeler JSONL files and the ground-truth file in run_dir.

    Labeler files match ``labels.<labeler>.jsonl`` (excluding ground-truth).
    Ground truth is ``labels.ground-truth.jsonl`` (or the legacy
    ``ground-truth.jsonl``).
    """
    gt = run_dir / "labels.ground-truth.jsonl"
    if not gt.exists():
        gt = run_dir / "ground-truth.jsonl"
        if not gt.exists():
            gt = None
    labelers = sorted(
        p for p in run_dir.glob("labels.*.jsonl")
        if p.name not in {"labels.ground-truth.jsonl", "labels.report.yaml"}
    )
    return labelers, gt


def _load_ground_truth(run_dir: Path) -> dict[str, Any] | None:
    """Compute TP/FP/FN/TN from labeler files + ground truth via lib.labeling.

    Returns None if either is missing (recall/precision stay null per design).
    """
    labeler_files, gt_path = _discover_labeler_files(run_dir)
    if not labeler_files or gt_path is None:
        return None

    entries: list[L.LabelEntry] = []
    for path in labeler_files:
        entries.extend(L.load_labels(path))
    if not entries:
        return None

    consensus = L.majority_vote(entries)
    # Ground truth: claim_id -> is_hallucination (bool).
    gt_rows = _read_jsonl(gt_path)
    gt: dict[str, bool] = {}
    for row in gt_rows:
        claim_id = row.get("claim_id") or row.get("id")
        if claim_id is None:
            continue
        # Accept multiple schemas: explicit bool, string verdict, or 1/0.
        if "is_hallucination" in row:
            gt[str(claim_id)] = bool(row["is_hallucination"])
        else:
            verdict = (row.get("verdict") or row.get("ground_truth") or "").strip().lower()
            gt[str(claim_id)] = verdict in {
                "hallucination", "true", "tp", "1", "yes",
            }

    outcomes = L.classify_outcomes(consensus, gt)
    counts = Counter(o for _, o in outcomes)
    tp = counts.get(L.OUTCOME_TP, 0)
    fp = counts.get(L.OUTCOME_FP, 0)
    fn = counts.get(L.OUTCOME_FN, 0)
    tn = counts.get(L.OUTCOME_TN, 0)
    try:
        report = L.krippendorff_alpha_from_labels(entries)
        alpha = report.alpha
    except Exception:
        alpha = None
    return {
        "label_count": len(entries),
        "labeler_count": len(labeler_files),
        "ground_truth_count": len(gt),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "total_prose_claims": len(consensus),
        "krippendorff_alpha": alpha,
    }


def _safe_delta(a: Any, b: Any) -> Any:
    """Subtract b from a when both are numeric; else None."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a - b
    return None


def _compute_metrics(with_summary: dict[str, Any],
                     without_summary: dict[str, Any],
                     with_gt: dict[str, Any] | None,
                     without_gt: dict[str, Any] | None) -> dict[str, Any]:
    with_build, _ = _parse_build_test(with_summary.get("build_result", ""))
    without_build, _ = _parse_build_test(without_summary.get("build_result", ""))

    metrics: dict[str, Any] = {
        "build_delta": (
            1 if with_build == "PASS" and without_build != "PASS"
            else (-1 if with_build != "PASS" and without_build == "PASS" else 0)
        ),
        "duration_delta_s": _safe_delta(
            with_summary.get("duration_s"), without_summary.get("duration_s")
        ),
        "api_requests_delta": _safe_delta(
            with_summary.get("api_requests"),
            without_summary.get("api_requests"),
        ),
        "tokens_delta": _safe_delta(
            with_summary.get("tokens_total"),
            without_summary.get("tokens_total"),
        ),
        "tv_warnings_delta": _safe_delta(
            with_summary.get("tv_warnings_total"),
            without_summary.get("tv_warnings_total"),
        ),
        # Computed from labels.jsonl once Subtask C produces them.
        "recall": None,
        "recall_ci_wilson_95": None,
        "precision": None,
        "precision_ci_wilson_95": None,
        "f1": None,
        "silent_fn_delta": None,
    }

    if with_gt:
        tp = with_gt.get("true_positives", 0)
        fp = with_gt.get("false_positives", 0)
        fn = with_gt.get("false_negatives", 0)
        # Wilson 95% CIs (lib.stats.wilson_ci).
        try:
            from stats import wilson_ci  # type: ignore
            if (tp + fn) > 0:
                metrics["recall"] = tp / (tp + fn)
                metrics["recall_ci_wilson_95"] = wilson_ci(tp, tp + fn)
            if (tp + fp) > 0:
                metrics["precision"] = tp / (tp + fp)
                metrics["precision_ci_wilson_95"] = wilson_ci(tp, tp + fp)
        except Exception:
            if (tp + fn) > 0:
                metrics["recall"] = tp / (tp + fn)
            if (tp + fp) > 0:
                metrics["precision"] = tp / (tp + fp)
        if metrics["recall"] is not None and metrics["precision"] is not None \
                and (metrics["recall"] + metrics["precision"]) > 0:
            r, p = metrics["recall"], metrics["precision"]
            metrics["f1"] = 2 * r * p / (r + p)
        # Krippendorff α passthrough (informational; gate via lib.labeling).
        if with_gt.get("krippendorff_alpha") is not None:
            metrics["krippendorff_alpha"] = with_gt["krippendorff_alpha"]
        # Silent FN delta: claims labeled FN in the without-run that the
        # with-run caught (TP). Requires both labels to exist.
        if without_gt:
            metrics["silent_fn_delta"] = (
                without_gt.get("false_negatives", 0)
                - with_gt.get("false_negatives", 0)
            )
    return metrics


# -----------------------------------------------------------------------------
# YAML emission
# -----------------------------------------------------------------------------

def build_report(with_dir: Path, without_dir: Path) -> dict[str, Any]:
    with_summary = _summarize_run(with_dir)
    without_summary = _summarize_run(without_dir)
    with_gt = _load_ground_truth(with_dir)
    without_gt = _load_ground_truth(without_dir)
    metrics = _compute_metrics(with_summary, without_summary, with_gt, without_gt)

    report: dict[str, Any] = {
        "task_id": with_summary.get("task_id") or without_summary.get("task_id"),
        "generated_at": _dt.datetime.now().isoformat(),
        "with_anubis": with_summary,
        "without_anubis": without_summary,
        "metrics": metrics,
        "notes": [
            "recall/precision/f1/silent_fn_delta are null until labeler files "
            "(labels.<labeler>.jsonl) and labels.ground-truth.jsonl exist in "
            "the run dir. Computed via lib.labeling + lib.stats.",
            "ollama traffic is direct in both modes -- the daemon only "
            "intercepts z.ai calls -- so tv_warnings_total may be 0 even in "
            "'with' mode if the run only used an ollama model. Use a z.ai "
            "model for non-zero audit capture.",
        ],
    }
    if with_gt or without_gt:
        report["ground_truth"] = {
            "with_anubis": with_gt,
            "without_anubis": without_gt,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="compare_runs",
        description="Emit a YAML diff report from two side-by-side result dirs.",
    )
    p.add_argument("--with-dir", "-WithDir", dest="with_dir", required=True,
                   help="Result dir from a --mode with run.")
    p.add_argument("--without-dir", "-WithoutDir", dest="without_dir", required=True,
                   help="Result dir from a --mode without run.")
    p.add_argument("--output", "-Output", dest="output", default="",
                   help="Output YAML path (default: stdout).")
    args = p.parse_args(argv)

    with_dir = Path(args.with_dir).resolve()
    without_dir = Path(args.without_dir).resolve()
    if not with_dir.exists():
        sys.stderr.write(f"[compare] with-dir not found: {with_dir}\n")
        return 2
    if not without_dir.exists():
        sys.stderr.write(f"[compare] without-dir not found: {without_dir}\n")
        return 2

    report = build_report(with_dir, without_dir)
    yaml_text = yaml.safe_dump(report, sort_keys=False, allow_unicode=True,
                               default_flow_style=False, width=100)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml_text, encoding="utf-8")
        print(f"[compare] wrote {out_path}")
    else:
        sys.stdout.write(yaml_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
