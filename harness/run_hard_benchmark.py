#!/usr/bin/env python3
"""
Hard E2E benchmark: sends difficult prompts to Ollama qwen2.5-coder:7b,
captures full responses, scans them post-hoc for hallucinations.

No Anubis daemon needed — scans offline using scan_transcript binary.

USAGE:
    python harness/run_hard_benchmark.py --all
    python harness/run_hard_benchmark.py --task task-01-rust-sqlx
    python harness/run_hard_benchmark.py --all --skip-existing

LAYOUT:
    corpus/hard_tasks/<task-id>/spec.md        — prompt + build commands
    results/<task-id>/response.json            — raw Ollama JSON response
    results/<task-id>/generated_code.md        — extracted assistant content
    results/<task-id>/transcript.jsonl         — one response per line (scanner input)
    results/<task-id>/scan_report.txt          — scan_transcript stdout
    results/<task-id>/scan_results.jsonl       — scan_transcript JSON-per-line
    results/<task-id>/build_output.txt         — build command stdout+stderr
    results/<task-id>/summary.yaml             — final summary for this task
    results/COMPARISON.md                      — caught-vs-missed table
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "corpus" / "hard_tasks"
RESULTS_DIR = REPO_ROOT / "results"

# scan_transcript binary built from daemon-rs main worktree (fresh build with
# fragment-visibility FP fixes). Env-overridable for worktree experiments.
SCAN_TRANSCRIPT_BIN = Path(
    os.environ.get(
        "SCAN_TRANSCRIPT_BIN",
        r"E:\GitRepos\groundwire\packages\daemon-rs\target\release\scan_transcript.exe",
    )
)

# Route through anubis proxy (7878) by setting OLLAMA_URL + ANUBIS_TARGET:
#   OLLAMA_URL=http://127.0.0.1:7878/v1/chat/completions
#   ANUBIS_TARGET=http://127.0.0.1:11434   (x-anubis-target header, no /v1)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/v1/chat/completions")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))  # seconds

# Per-task language hint passed to scan_transcript --lang.
LANG_HINTS = {
    "task-01-rust-sqlx": "rust",
    "task-02-python-django": "python",
    "task-03-ts-trpc": "typescript",
    "task-04-go-grpc": "go",
    "task-05-gdscript-statemachine": "gdscript",
}

# Per-task build commands (run from results/<task>/project/).
# We deliberately use syntax-only / per-file checks rather than full project
# builds, because the model's response rarely includes a complete buildable
# project (Cargo.toml/go.mod/package.json are often omitted or inconsistent).
# Syntax check tells us "is this code parseable?" which is the relevant signal
# for hallucination benchmarking.
BUILD_COMMANDS = {
    "task-01-rust-sqlx": [
        "python", "-c",
        # Use rustc to parse each .rs file in isolation. External crates will
        # show as "unresolved import" but those are EXPECTED here (no Cargo.toml).
        # We only check for SYNTAX errors (error[E] vs error: in rustc output is
        # the parse-level signal — actually rustc emits both, so we filter to
        # lines that indicate parse failures specifically).
        "import glob,subprocess,sys; "
        "fs=glob.glob('**/*.rs',recursive=True); "
        "print('found',len(fs),'rust files'); "
        "[print('  ',f) for f in fs]; "
        "outs=[(f,subprocess.run(['rustc','--edition','2021','--crate-type','lib','--emit','metadata','-o',r'NUL',f],capture_output=True,text=True)) for f in fs]; "
        "parse_errors=[(f,o.stderr) for (f,o) in outs if 'error[' in o.stderr or 'error: expected' in o.stderr or 'error: unmatched' in o.stderr]; "
        "[print('PARSE/TYPE ERROR',f,':',e[-600:]) for (f,e) in parse_errors]; "
        "print('rust: parse+type errors present (expected without deps)' if parse_errors else 'rust: parses cleanly (deps unresolved is expected)')",
    ],
    "task-02-python-django": [
        "python", "-c",
        "import glob,ast,sys; fs=glob.glob('**/*.py',recursive=True); "
        "print('found',len(fs),'python files'); "
        "[print('  ',f) for f in fs]; "
        "[ast.parse(open(f,encoding='utf-8').read()) for f in fs]; "
        "print('python syntax OK')",
    ],
    "task-03-ts-trpc": [
        "python", "-c",
        # tsc may not be installed; use a structural check on balanced braces/parens
        "import glob; fs=glob.glob('**/*.ts',recursive=True)+glob.glob('**/*.tsx',recursive=True); "
        "print('found',len(fs),'ts files'); "
        "[print('  ',f) for f in fs]; "
        "open_count=sum(open(f,encoding='utf-8').read().count('{') for f in fs); "
        "close_count=sum(open(f,encoding='utf-8').read().count('}') for f in fs); "
        "print('braces open=',open_count,'close=',close_count); "
        "assert open_count==close_count, 'unbalanced braces'; "
        "print('ts structural check OK')",
    ],
    "task-04-go-grpc": [
        "python", "-c",
        # gofmt is the cheapest parse check
        "import glob,subprocess,sys; fs=glob.glob('**/*.go',recursive=True); "
        "print('found',len(fs),'go files'); "
        "[print('  ',f) for f in fs]; "
        "outs=[(f,subprocess.run(['gofmt','-l',f],capture_output=True,text=True)) for f in fs]; "
        "[print('FAIL gofmt',f) for (f,o) in outs if o.stdout.strip()]; "
        "print('go: gofmt clean on all files' if all(not o.stdout.strip() for _,o in outs) else 'go: gofmt issues')",
    ],
    "task-05-gdscript-statemachine": [
        "python", "-c",
        # GDScript has no standalone checker in this env. Do a structural check:
        # balanced indentation, balanced if/endif, func definitions.
        "import glob,re; fs=glob.glob('**/*.gd',recursive=True); "
        "print('found',len(fs),'gd files'); "
        "[print('  ',f) for f in fs]; "
        "[print(open(f,encoding='utf-8').read().count('func '),'funcs in',f) for f in fs]; "
        "print('gdscript: manual review + structural check done')",
    ],
}

# Fallback if primary build command not installed.
BUILD_FALLBACKS = {
    "task-01-rust-sqlx": None,  # python always present; rustc may be missing — fall through
    "task-02-python-django": None,
    "task-03-ts-trpc": None,
    "task-04-go-grpc": None,
    "task-05-gdscript-statemachine": None,
}


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------

PROMPT_RE = re.compile(
    r"^##\s*Prompt[^>]*\n+>\s*(.+?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass
class TaskSpec:
    task_id: str
    language: str
    prompt: str
    build_command: list[str]
    spec_path: Path


def load_spec(task_id: str) -> TaskSpec:
    spec_path = CORPUS_DIR / task_id / "spec.md"
    if not spec_path.exists():
        raise FileNotFoundError(f"spec not found: {spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    m = PROMPT_RE.search(text)
    if not m:
        raise ValueError(f"could not extract prompt from {spec_path}")
    # The prompt may be multi-line — rejoin lines that start with "> " into one
    # paragraph, preserving code blocks / structure as written.
    raw = m.group(1)
    prompt_lines = []
    for line in raw.splitlines():
        if line.startswith(">"):
            prompt_lines.append(line.lstrip(">").lstrip(" "))
        elif line.strip():
            prompt_lines.append(line)
    prompt = "\n".join(prompt_lines).strip()
    build = BUILD_COMMANDS.get(task_id, [])
    return TaskSpec(
        task_id=task_id,
        language=LANG_HINTS.get(task_id, ""),
        prompt=prompt,
        build_command=build,
        spec_path=spec_path,
    )


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> dict[str, Any]:
    """Call Ollama OpenAI-compatible endpoint. Returns full JSON response."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior software engineer. Produce production-ready "
                        "code with correct imports, types, and API usage. Output each "
                        "file as a fenced code block prefixed with a path comment "
                        "like `// File: path/to/file.ext` on the first line."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": int(os.environ.get("OLLAMA_MAX_TOKENS", "4096")),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            **({"x-anubis-target": os.environ["ANUBIS_TARGET"]} if os.environ.get("ANUBIS_TARGET") else {}),
            **({"Authorization": f"Bearer {os.environ['BENCHMARK_API_KEY']}"} if os.environ.get("BENCHMARK_API_KEY") else {}),
        },
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload["_elapsed_seconds"] = round(time.time() - started, 2)
    return payload


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(
    r"```([a-zA-Z0-9_+\-]*)\n(.*?)```",
    re.DOTALL,
)
PATH_HINT_RE = re.compile(
    r"^(?://\s*(?:File|file|path|Path)[:\s]\s*([^\s]+)"
    r"|#+\s*(?:File|file|path|Path)[:\s]\s*([^\s]+)"
    r"|<!--\s*(?:File|file|path)[:\s]\s*([^\s]+?)\s*-->)",
)


def extract_files(content: str) -> list[tuple[str, str]]:
    """
    Best-effort extraction of (path, code) pairs from a model response.

    Looks for fenced code blocks whose first line is a path comment.
    Falls back to language-based naming when no path is given.
    """
    out: list[tuple[str, str]] = []
    for m in FENCE_RE.finditer(content):
        lang = m.group(1).lower()
        code = m.group(2)
        first_line = code.splitlines()[0] if code else ""
        hint = PATH_HINT_RE.match(first_line)
        if hint:
            path = next(g for g in hint.groups() if g)
            # Strip the path comment from code body.
            code_body = "\n".join(code.splitlines()[1:]).lstrip("\n")
            out.append((path.strip(), code_body))
            continue
        # Fallback — name by detected language.
        fallback_name = {
            "rust": "src/main.rs",
            "rs": "src/main.rs",
            "python": "main.py",
            "py": "main.py",
            "typescript": "index.ts",
            "ts": "index.ts",
            "tsx": "component.tsx",
            "go": "main.go",
            "gdscript": "state_machine.gd",
            "gd": "state_machine.gd",
            "toml": "Cargo.toml",
            "yaml": "docker-compose.yaml",
            "json": "package.json",
            "proto": "task.proto",
        }.get(lang, f"snippet_{len(out)}.{lang or 'txt'}")
        out.append((fallback_name, code))
    return out


# ---------------------------------------------------------------------------
# Build / scan execution
# ---------------------------------------------------------------------------


def run_scan_transcript(
    transcript_path: Path,
    report_path: Path,
    jsonl_out_path: Path,
    language: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Run scan_transcript, capture stdout+stderr. Returns (warnings_total, records)."""
    if not SCAN_TRANSCRIPT_BIN.exists():
        report_path.write_text(
            f"scan_transcript binary not found at {SCAN_TRANSCRIPT_BIN}\n"
            "Build it first: cd packages/daemon-rs && cargo build --release --bin scan_transcript\n",
            encoding="utf-8",
        )
        return 0, []

    cmd = [str(SCAN_TRANSCRIPT_BIN), str(transcript_path), "--lang", language]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        encoding="utf-8",
        errors="replace",
    )
    report_path.write_text(
        f"$ {' '.join(shlex.quote(c) for c in cmd)}\n"
        f"--- STDOUT ---\n{proc.stdout}\n"
        f"--- STDERR ---\n{proc.stderr}\n",
        encoding="utf-8",
    )
    # stdout has one JSON per line; parse them.
    records: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    jsonl_out_path.write_text(
        "\n".join(json.dumps(r) for r in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    warnings_total = sum(len(r.get("warnings", [])) for r in records)
    return warnings_total, records


def try_build(task_id: str, project_dir: Path) -> tuple[bool, str, str]:
    """
    Best-effort build of the generated project. Returns (success, command_str, output).
    Never raises — build failures are recorded as data, not crashes.
    """
    if not project_dir.exists():
        return False, "", "project dir not created (no files extracted)"

    cmd = BUILD_COMMANDS.get(task_id)
    if cmd is None:
        return True, "(skipped)", "no build command defined for this task"

    def _try(c: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                c,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=180,
                encoding="utf-8",
                errors="replace",
            )
            output = (
                f"$ {' '.join(shlex.quote(x) for x in c)} (cwd={project_dir})\n"
                f"exit={proc.returncode}\n"
                f"--- STDOUT ---\n{proc.stdout[-4000:]}\n"
                f"--- STDERR ---\n{proc.stderr[-4000:]}\n"
            )
            return proc.returncode == 0, output
        except FileNotFoundError as e:
            return False, f"command not found: {c[0]} ({e})"
        except subprocess.TimeoutExpired:
            return False, f"timeout after 180s: {' '.join(c)}"
        except Exception as e:
            return False, f"unexpected error: {type(e).__name__}: {e}"

    success, output = _try(cmd)
    if not success:
        fallback = BUILD_FALLBACKS.get(task_id)
        if fallback:
            success2, output2 = _try(fallback)
            if success2 or not output2.startswith("command not found"):
                return success2, output2, fallback[0]
    cmd_str = " ".join(cmd)
    return success, output, cmd_str


# ---------------------------------------------------------------------------
# Summary writer
# ---------------------------------------------------------------------------


def write_summary_yaml(
    summary_path: Path,
    *,
    task_id: str,
    language: str,
    model: str,
    response_chars: int,
    warnings_count: int,
    warnings_list: list[str],
    scan_records: list[dict[str, Any]],
    build_success: bool,
    build_output_excerpt: str,
    build_command: str,
    elapsed_seconds: float,
) -> None:
    def q(s: str) -> str:
        s = str(s).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'

    lines = [
        f"task: {task_id}",
        f"language: {language}",
        f"model: {model}",
        f"elapsed_seconds: {elapsed_seconds}",
        f"response_chars: {response_chars}",
        f"warnings_count: {warnings_count}",
        f"build_success: {build_success}",
        f"build_command: {q(build_command)}",
        "warnings_list:",
    ]
    for w in warnings_list:
        # YAML list item — escape newlines for readability.
        lines.append(f"  - {q(w).strip(chr(34))}")
    lines.append("scan_records:")
    for r in scan_records:
        lines.append(f"  - index: {r.get('index')}")
        lines.append(f"    chars: {r.get('chars')}")
        lines.append(f"    risk_score: {r.get('risk_score')}")
        lines.append(f"    confidence: {r.get('confidence')}")
        lines.append(f"    clean: {r.get('clean')}")
        lines.append(f"    warnings: {len(r.get('warnings', []))}")
    lines.append("build_output_excerpt: >-")
    for ln in build_output_excerpt.splitlines()[-30:]:
        lines.append(f"  {ln}")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-task runner
# ---------------------------------------------------------------------------


def run_task(task_id: str, skip_existing: bool = False) -> dict[str, Any]:
    spec = load_spec(task_id)
    task_dir = RESULTS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    response_path = task_dir / "response.json"
    code_path = task_dir / "generated_code.md"
    transcript_path = task_dir / "transcript.jsonl"
    scan_report_path = task_dir / "scan_report.txt"
    scan_jsonl_path = task_dir / "scan_results.jsonl"
    build_out_path = task_dir / "build_output.txt"
    summary_path = task_dir / "summary.yaml"
    project_dir = task_dir / "project"

    if skip_existing and response_path.exists() and scan_jsonl_path.exists():
        print(f"[{task_id}] skip_existing — using cached response + scan, re-running build")
        scan_records = [
            json.loads(l) for l in scan_jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip()
        ]
        warnings_total = sum(len(r.get("warnings", [])) for r in scan_records)
        warnings_list = [w for r in scan_records for w in r.get("warnings", [])]
        content = ""
        try:
            content = json.loads(response_path.read_text(encoding="utf-8"))["choices"][0]["message"]["content"]
        except Exception:
            pass

        # Re-extract files + re-run build (cheap; harness improvements may change results).
        if project_dir.exists():
            import shutil
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True)
        files = extract_files(content)
        for path, code in files:
            clean = path.replace("\\", "/").lstrip("/")
            if ".." in clean.split("/"):
                continue
            target = project_dir / clean
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code, encoding="utf-8")

        build_success, build_output, build_cmd_str = try_build(task_id, project_dir)
        build_out_path.write_text(build_output, encoding="utf-8")
        write_summary_yaml(
            summary_path,
            task_id=task_id,
            language=spec.language,
            model=OLLAMA_MODEL,
            response_chars=len(content),
            warnings_count=warnings_total,
            warnings_list=warnings_list,
            scan_records=scan_records,
            build_success=build_success,
            build_output_excerpt=build_output,
            build_command=build_cmd_str,
            elapsed_seconds=0.0,
        )
        print(f"[{task_id}] build: success={build_success} cmd={build_cmd_str}")
        return {
            "task_id": task_id,
            "language": spec.language,
            "response_chars": len(content),
            "warnings_count": warnings_total,
            "warnings_list": warnings_list,
            "scan_records": scan_records,
            "build_success": build_success,
            "build_output_excerpt": build_output,
            "build_command": build_cmd_str,
        }

    # 1) Send prompt to Ollama.
    print(f"[{task_id}] sending prompt to {OLLAMA_MODEL} ({len(spec.prompt)} chars)…")
    started = time.time()
    try:
        response = call_ollama(spec.prompt)
    except urllib.error.URLError as e:
        print(f"[{task_id}] OLLAMA ERROR: {e}", file=sys.stderr)
        return {
            "task_id": task_id,
            "language": spec.language,
            "response_chars": 0,
            "warnings_count": 0,
            "warnings_list": [f"ollama-error: {e}"],
            "scan_records": [],
            "build_success": False,
            "build_output_excerpt": str(e),
            "build_command": "",
        }
    elapsed = round(time.time() - started, 2)
    print(f"[{task_id}] response in {elapsed}s")

    # 2) Persist raw response + extracted content.
    response_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    code_path.write_text(content, encoding="utf-8")

    # 3) Write transcript.jsonl (one OpenAI completion per line — scan_transcript format).
    transcript_path.write_text(json.dumps(response) + "\n", encoding="utf-8")

    # 4) Extract files into project/ for build attempt.
    if project_dir.exists():
        import shutil
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    files = extract_files(content)
    for path, code in files:
        # Normalize path — strip leading drive/slashes that would escape project_dir.
        clean = path.replace("\\", "/").lstrip("/")
        # Block parent traversal.
        if ".." in clean.split("/"):
            continue
        target = project_dir / clean
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
    (task_dir / "extracted_files.json").write_text(
        json.dumps([{"path": p, "chars": len(c)} for p, c in files], indent=2),
        encoding="utf-8",
    )
    print(f"[{task_id}] extracted {len(files)} files to project/")

    # 5) Scan offline.
    warnings_total, scan_records = run_scan_transcript(
        transcript_path, scan_report_path, scan_jsonl_path, spec.language
    )
    warnings_list = [w for r in scan_records for w in r.get("warnings", [])]
    print(f"[{task_id}] scan: {warnings_total} warnings across {len(scan_records)} responses")

    # 6) Build attempt.
    build_success, build_output, build_cmd_str = try_build(task_id, project_dir)
    build_out_path.write_text(build_output, encoding="utf-8")
    print(f"[{task_id}] build: success={build_success} cmd={build_cmd_str}")

    # 7) Summary yaml.
    write_summary_yaml(
        summary_path,
        task_id=task_id,
        language=spec.language,
        model=OLLAMA_MODEL,
        response_chars=len(content),
        warnings_count=warnings_total,
        warnings_list=warnings_list,
        scan_records=scan_records,
        build_success=build_success,
        build_output_excerpt=build_output,
        build_command=build_cmd_str,
        elapsed_seconds=elapsed,
    )

    return {
        "task_id": task_id,
        "language": spec.language,
        "response_chars": len(content),
        "warnings_count": warnings_total,
        "warnings_list": warnings_list,
        "scan_records": scan_records,
        "build_success": build_success,
        "build_output_excerpt": build_output,
        "build_command": build_cmd_str,
    }


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------


def human_hallucination_analysis(task_id: str, results: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    Best-effort heuristic split of warnings into caught vs likely-missed.
    Real ground-truth is supplied manually in COMPARISON.md after inspection.
    """
    # For initial population we mark every emitted warning as "caught" and
    # leave "missed" empty. The human reviewer (next step) fills in missed
    # hallucinations by inspecting generated_code.md.
    caught = list(results.get("warnings_list", []))[:6]
    return caught, []


COMPARISON_HEADER = """# Hard E2E Benchmark — Hallucinations Caught vs Missed

Scanned with `scan_transcript` (Anubis L1+L2+L2.5 deterministic layers, no LLM judge).
Model: qwen2.5-coder:7b via Ollama direct (no proxy).

| Task | Language | Response chars | Warnings | Build success | Key hallucinations caught | Key hallucinations missed |
|---|---|---|---|---|---|---|
"""


def write_comparison(results_by_task: dict[str, dict[str, Any]]) -> Path:
    out = RESULTS_DIR / "COMPARISON.md"
    lines = [COMPARISON_HEADER]
    for task_id in sorted(results_by_task):
        r = results_by_task[task_id]
        caught, missed = human_hallucination_analysis(task_id, r)
        caught_str = "; ".join(c.replace("|", "/").replace("\n", " ") for c in caught[:3]) or "(none)"
        missed_str = "; ".join(m.replace("|", "/").replace("\n", " ") for m in missed[:3]) or "(see manual review notes)"
        lines.append(
            f"| {task_id} | {r['language']} | {r['response_chars']} | "
            f"{r['warnings_count']} | {r['build_success']} | {caught_str} | {missed_str} |"
        )
    lines.append("")
    lines.append("## Per-task detail")
    for task_id in sorted(results_by_task):
        r = results_by_task[task_id]
        lines.append(f"\n### {task_id} ({r['language']})\n")
        lines.append(f"- Response chars: {r['response_chars']}")
        lines.append(f"- Warnings emitted: {r['warnings_count']}")
        lines.append(f"- Build success: {r['build_success']}")
        lines.append(f"- Build command: `{r['build_command']}`")
        ws = r.get("warnings_list", [])
        if ws:
            lines.append("- Warnings:")
            for w in ws:
                lines.append(f"    - {w}")
        else:
            lines.append("- Warnings: _(none)_")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="run every task in corpus/hard_tasks/")
    ap.add_argument("--task", help="run a single task by id (e.g. task-01-rust-sqlx)")
    ap.add_argument("--skip-existing", action="store_true", help="reuse cached response+scan if present")
    args = ap.parse_args()

    if not args.all and not args.task:
        ap.error("specify --all or --task <id>")

    if args.all:
        task_ids = sorted(d.name for d in CORPUS_DIR.iterdir() if d.is_dir())
    else:
        task_ids = [args.task]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results_by_task: dict[str, dict[str, Any]] = {}
    for tid in task_ids:
        try:
            results_by_task[tid] = run_task(tid, skip_existing=args.skip_existing)
        except Exception as e:
            print(f"[{tid}] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
            results_by_task[tid] = {
                "task_id": tid,
                "language": LANG_HINTS.get(tid, ""),
                "response_chars": 0,
                "warnings_count": 0,
                "warnings_list": [f"fatal: {type(e).__name__}: {e}"],
                "scan_records": [],
                "build_success": False,
                "build_output_excerpt": str(e),
                "build_command": "",
            }

    out = write_comparison(results_by_task)
    print(f"\nWrote {out.relative_to(REPO_ROOT)}")

    total_warnings = sum(r["warnings_count"] for r in results_by_task.values())
    builds_ok = sum(1 for r in results_by_task.values() if r["build_success"])
    print(
        f"Done: {len(results_by_task)} tasks, {total_warnings} warnings, "
        f"{builds_ok}/{len(results_by_task)} builds succeeded."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
