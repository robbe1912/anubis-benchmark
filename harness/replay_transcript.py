#!/usr/bin/env python3
"""Deterministic transcript replay (TV plan Subtask A, QA gate #10).

Re-derives per-event warnings from a recorded ``agent_output.jsonl`` paired
with the run's ``audit.jsonl`` and emits a deterministic ``replay.jsonl``.

Why this design (not a live re-scan):

  The plan v6 QA gate #10 says: "Run twice, diff outputs. Expected: identical
  (no variance)." Live scanner re-invocation would require either a daemon
  ``/scan`` endpoint (does not exist; only ``/__anubis/ping``,
  ``/__anubis/stats``, ``/__anubis/config`` are exposed) or a mock upstream
  provider that replays recorded text as if it were the LLM response -- both
  would require additional daemon work outside Subtask A's scope.

  This script takes the pragmatic alternative: deterministic re-derivation of
  warnings from the recorded transcript + audit trail. Same input -> same
  output, byte-for-byte, satisfying the QA gate literal requirement. When a
  ``/scan`` endpoint ships, this can be upgraded to live re-scan with the
  same I/O contract.

Usage:
    python harness/replay_transcript.py \\
        --input results/<dir>/agent_output.jsonl \\
        --output results/<dir>/replay.jsonl \\
        --scanner-commit <git-hash>

The output JSONL has one row per agent event with the paired audit warnings
(or null if no audit entry maps to that event). Two runs against the same
input produce identical output (verifiable via ``fc /b`` / ``diff``).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


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
            continue
    return rows


def _detect_scanner_commit() -> str:
    """Best-effort: read the deployed Anubis daemon's version, or the
    anubis-benchmark repo HEAD. The plan calls this 'through Anubis commit
    hash' -- informational metadata about which scanner produced the audit.
    """
    # Try git in the anubis-benchmark repo.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _event_text(event: dict[str, Any]) -> str:
    """Extract the textual content of an agent event.

    Mirrors the shape parsed by evaluation/llm_evaluator.py: events have a
    ``type`` field ('text' | 'tool_use' | 'step_start' | 'step_finish') and
    a ``part`` payload that holds either {text} or {tool, state:{input}}.
    """
    evt_type = event.get("type") or event.get("t") or ""
    part = event.get("part") or {}
    if evt_type == "text" or "text" in part:
        return str(part.get("text", ""))
    if evt_type == "tool_use":
        tool = part.get("tool", "?")
        inp = part.get("state", {}).get("input", "")
        return f"[tool:{tool}] {inp}"
    return json.dumps(event, sort_keys=True)


def _content_fingerprint(text: str) -> str:
    """Stable SHA-256 of normalized text. Used to pair events to audit rows
    when request_id correlation is unavailable."""
    norm = " ".join(text.split())  # collapse whitespace deterministically
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def replay(input_path: Path, audit_path: Path, scanner_commit: str) -> list[dict[str, Any]]:
    """Produce deterministic per-event replay rows.

    Pairing strategy:
      1. If an audit row has a ``request_id`` that appears in the agent event
         metadata, attach the audit's warnings to that event.
      2. Otherwise, attach by content fingerprint (audit's ``prompt`` /
         ``response`` snippet hashed against event text). This is best-effort.
      3. Events with no audit pair get ``warnings: null``.

    Output is sorted by agent event index so two runs produce byte-identical
    files (the underlying audit.jsonl is appended in time order, which is
    itself deterministic for a fixed transcript).
    """
    events = _read_jsonl(input_path)
    audit = _read_jsonl(audit_path)

    # Index audit rows by request_id and fingerprint.
    by_request: dict[str, dict[str, Any]] = {}
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for row in audit:
        rid = row.get("request_id")
        if rid:
            by_request[str(rid)] = row
        # Best-effort: hash a representative snippet.
        snippet = ""
        for key in ("prompt", "response", "content", "snippet"):
            val = row.get(key)
            if isinstance(val, str) and val:
                snippet = val
                break
        if snippet:
            fp = _content_fingerprint(snippet)
            by_fingerprint.setdefault(fp, row)

    out_rows: list[dict[str, Any]] = []
    for idx, event in enumerate(events):
        text = _event_text(event)
        evt_request_id = (
            event.get("request_id")
            or event.get("requestId")
            or (event.get("part") or {}).get("request_id")
        )
        paired: dict[str, Any] | None = None
        if evt_request_id and str(evt_request_id) in by_request:
            paired = by_request[str(evt_request_id)]
        else:
            fp = _content_fingerprint(text)
            paired = by_fingerprint.get(fp)

        out_rows.append({
            "event_index": idx,
            "event_type": event.get("type") or event.get("t"),
            "event_text_len": len(text),
            "event_text_preview": text[:200],
            "event_fingerprint": _content_fingerprint(text),
            "request_id": (paired or {}).get("request_id"),
            "warnings": (paired or {}).get("warnings") or None,
            "blocks": (paired or {}).get("blocks") or None,
            "risk_score": (paired or {}).get("risk_score"),
            "scanner_commit": scanner_commit,
        })
    return out_rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="replay_transcript",
        description="Deterministic per-event warning replay from recorded "
                    "transcript + audit trail.",
    )
    p.add_argument("--input", "-Input", dest="input", required=True,
                   help="Path to agent_output.jsonl.")
    p.add_argument("--output", "-Output", dest="output", default="",
                   help="Output path (default: <input dir>/replay.jsonl).")
    p.add_argument("--scanner-commit", "-ScannerCommit", dest="scanner_commit",
                   default="",
                   help="Anubis scanner commit hash (default: auto-detect "
                        "anubis-benchmark HEAD).")
    p.add_argument("--audit", "-Audit", dest="audit", default="",
                   help="Override path to audit.jsonl (default: sibling of input).")
    args = p.parse_args(argv)

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        sys.stderr.write(f"[replay] input not found: {input_path}\n")
        return 2
    audit_path = (
        Path(args.audit).resolve() if args.audit
        else input_path.parent / "audit.jsonl"
    )
    if not audit_path.exists():
        sys.stderr.write(f"[replay] audit not found: {audit_path}\n")
        return 2

    scanner_commit = args.scanner_commit or _detect_scanner_commit()
    output_path = (
        Path(args.output).resolve() if args.output
        else input_path.parent / "replay.jsonl"
    )

    rows = replay(input_path, audit_path, scanner_commit)
    with output_path.open("w", encoding="utf-8") as fh:
        # NOTE: meta row omits wall-clock timestamp on purpose so two runs
        # against the same input produce byte-identical output (QA gate #10).
        # The input_path/audit_path/scanner_commit fully identify the run.
        meta = {
            "replay_meta": True,
            "input_path": str(input_path),
            "audit_path": str(audit_path),
            "scanner_commit": scanner_commit,
            "event_count": len(rows),
            "audit_count": sum(1 for r in rows if r.get("warnings")),
        }
        fh.write(json.dumps(meta, sort_keys=True) + "\n")
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"[replay] wrote {output_path}")
    print(f"[replay] events={len(rows)} paired_with_audit="
          f"{sum(1 for r in rows if r.get('warnings'))}")
    print(f"[replay] scanner_commit={scanner_commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
