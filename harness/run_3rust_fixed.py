#!/usr/bin/env python3
"""
Re-run GLM-5-Turbo benchmark on 3 Rust tasks with FIXED scanner.
Previous run showed 0 warnings. Now scanner has primary detection mode
+ expanded capture codes + macro detection.

For each task:
  1. WITHOUT Anubis: curl POST to direct z.ai API
  2. WITH Anubis:    curl POST to proxy at 127.0.0.1:7878
  3. Convert each .json to single-line .jsonl
  4. Run scan_transcript on each .jsonl
  5. Record warning counts

Outputs to results-e2e/<task>_without_fixed.{json,jsonl,scan.txt}
                     <task>_with_fixed.{json,jsonl,scan.txt}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

API_KEY = os.environ.get("ZAI_API_KEY", "")
DIRECT_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
PROXY_URL = "http://127.0.0.1:7878/chat/completions"
MODEL = "glm-5-turbo"
MAX_TOKENS = 8192
TEMPERATURE = 0.3
TIMEOUT = 180  # seconds per request

RESULTS_DIR = Path(r"E:\GitRepos\anubis-benchmark\results-e2e")
SCAN_BIN = Path(r"E:\GitRepos\groundwire\packages\daemon-rs\target\release\scan_transcript.exe")
CORPUS_ROOT = Path(r"E:\GitRepos\anubis-benchmark\corpus\hard_tasks")

TASKS = {
    "task-01-rust-sqlx": (
        "rust",
        "Create a Rust async CLI using sqlx with SQLite. Define a User struct with FromRow derive. "
        "Create functions: create_user, get_user_by_id, list_users. Use sqlx::query_as! macro. "
        "Run migrations from migrations/ directory."
    ),
    "task-11-rust-axum": (
        "rust",
        "Create a Rust async REST API with Axum 0.7. Implement CRUD endpoints for a Task entity "
        "(id, title, done). Use extractors (State, Path, Json) on handlers. Store shared state with "
        "Arc<RwLock<Vec<Task>>>. Implement custom AppError type that implements IntoResponse with "
        "proper status code mapping. Add CORS middleware using tower-http. Define a Router with "
        "`/api/tasks` routes (GET list, POST create, GET by id, PUT update, DELETE remove). Wire it "
        "into a Tokio runtime bound to 0.0.0.0:3000."
    ),
    "task-18-rust-traits-async": (
        "rust",
        "Create a Rust async web service with axum 0.8. Define a `Repository` trait with async methods "
        "(`async fn get_user(&self, id: &str) -> Result<Option<User>, AppError>; async fn list_users(&self) "
        "-> Result<Vec<User>, AppError>; async fn save_user(&self, user: &User) -> Result<(), AppError>`). "
        "Implement `PostgresRepo` (stubs using a `sqlx::PgPool` field, real sqlx queries) and `MemoryRepo` "
        "(uses `Arc<RwLock<HashMap<String, User>>>` internally). The trait MUST be usable as "
        "`Arc<dyn Repository>` for dynamic dispatch - handle object safety by boxing futures "
        "(`Pin<Box<dyn Future<Output = ...> + Send>>`) when needed, or use the `async-trait` crate. "
        "Build a tower middleware layer called `TimingLayer` that logs request duration; implement "
        "`tower::Service` for a wrapping `TimingMiddleware<S>` and call the inner service correctly. "
        "Define an `AppError` enum with thiserror derive (`#[derive(Error)]`) containing `NotFound(String)`, "
        "`Database(sqlx::Error)`, `InvalidInput(String)`, and implement `IntoResponse` for it mapping to "
        "HTTP 404 / 500 / 400 respectively with a JSON body. Wire it all into an axum `Router` with routes "
        "`GET /users`, `GET /users/:id`, `POST /users` and an `Arc<dyn Repository>` shared via "
        "`axum::extract::State`. Bind to `0.0.0.0:3000` using `axum::serve`. Emit fenced code blocks each "
        "prefixed with a `// File: path/to/file.rs` comment."
    ),
}


def call_api(url: str, prompt: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        return {
            "_error": f"HTTP {e.code} after {elapsed:.1f}s",
            "_body": e.read().decode("utf-8", errors="replace")[:2000],
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {"_error": f"{type(e).__name__}: {e} after {elapsed:.1f}s"}

    elapsed = time.time() - t0
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_error": "non-JSON response", "_body": raw[:2000]}
    # Stash metadata
    parsed["_elapsed_s"] = round(elapsed, 2)
    parsed["_raw_chars"] = len(raw)
    return parsed


def write_jsonl(resp: dict, path: Path) -> int:
    """Write single-line JSONL containing the OpenAI response shape.
    Strip our private _* keys first. Returns char count of content."""
    clean = {k: v for k, v in resp.items() if not k.startswith("_")}
    line = json.dumps(clean, ensure_ascii=False)
    path.write_text(line + "\n", encoding="utf-8")
    # Extract content for char count
    try:
        content = clean["choices"][0]["message"]["content"]
        return len(content)
    except (KeyError, IndexError):
        return 0


def run_scan(jsonl_path: Path, lang: str, project_root: Path) -> tuple[str, str]:
    """Returns (stdout_text, stderr_text)."""
    cmd = [
        str(SCAN_BIN),
        str(jsonl_path),
        "--lang", lang,
        "--project-root", str(project_root),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.stdout, proc.stderr


def parse_warnings(stdout_text: str) -> tuple[int, list]:
    """First line of stdout is JSON; pull warnings count + details."""
    if not stdout_text.strip():
        return -1, []
    first_line = stdout_text.strip().splitlines()[0]
    try:
        obj = json.loads(first_line)
    except json.JSONDecodeError:
        return -1, []
    return len(obj.get("warnings", [])), obj.get("warnings", [])


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = []

    for task_id, (lang, prompt) in TASKS.items():
        print(f"\n{'='*72}\n{task_id} ({lang})\n{'='*72}")
        project_root = CORPUS_ROOT / task_id
        # Sanity check
        if not project_root.exists():
            print(f"WARN: project_root {project_root} does not exist")

        for mode, url, label in [
            ("without", DIRECT_URL, "DIRECT (no Anubis)"),
            ("with", PROXY_URL, "PROXY (via Anubis)"),
        ]:
            print(f"\n--- {task_id} [{mode}] -> {label} ---")
            out_json = RESULTS_DIR / f"{task_id}_{mode}_fixed.json"
            out_jsonl = RESULTS_DIR / f"{task_id}_{mode}_fixed.jsonl"
            out_scan = RESULTS_DIR / f"{task_id}_{mode}_fixed_scan.txt"

            # Skip if scan already exists from a prior interrupted run
            if out_scan.exists() and out_jsonl.exists():
                existing_stdout = out_scan.read_text(encoding="utf-8").split("--- STDERR ---", 1)[0]
                n_warn, warnings = parse_warnings(existing_stdout)
                if n_warn >= 0:
                    print(f"  SKIP (cached): {n_warn} warnings from prior scan")
                    preview = [w if isinstance(w, str) else f"[{w.get('kind','?')}] {(w.get('message') or w.get('detail') or '')[:120]}" for w in warnings[:8]]
                    summary.append((task_id, mode, "OK_CACHED", n_warn, preview, warnings))
                    continue

            resp = call_api(url, prompt)
            if "_error" in resp:
                print(f"  API ERROR: {resp['_error']}")
                if "_body" in resp:
                    print(f"  body: {resp['_body'][:400]}")
                out_json.write_text(
                    json.dumps(resp, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                summary.append((task_id, mode, "API_ERROR", 0, [], []))
                continue

            elapsed = resp.get("_elapsed_s", "?")
            content_chars = write_jsonl(resp, out_jsonl)
            # Also dump the full pretty JSON for inspection
            out_json.write_text(
                json.dumps(resp, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  OK in {elapsed}s, content={content_chars} chars")

            # Scan
            stdout, stderr = run_scan(out_jsonl, lang, project_root)
            out_scan.write_text(
                stdout + "\n--- STDERR ---\n" + stderr,
                encoding="utf-8",
            )
            n_warn, warnings = parse_warnings(stdout)
            print(f"  scan: {n_warn} warnings")
            preview = []
            for w in warnings[:8]:
                if isinstance(w, str):
                    s = w[:140]
                else:
                    kind = w.get("kind") or w.get("type") or "?"
                    msg = (w.get("message") or w.get("detail") or "")[:120]
                    s = f"[{kind}] {msg}"
                preview.append(s)
                print(f"    {s}")
            summary.append((task_id, mode, "OK", n_warn, preview, warnings))

    # Print summary
    print(f"\n{'='*72}\nSUMMARY\n{'='*72}")
    print(f"{'task':<28} {'mode':<8} {'status':<10} {'warnings':<8}")
    for task_id, mode, status, n, _, _ in summary:
        print(f"{task_id:<28} {mode:<8} {status:<10} {n:<8}")

    # Write summary JSON
    summary_obj = [
        {
            "task": t,
            "mode": m,
            "status": s,
            "warnings_count": n,
            "warnings_preview": p,
            "warnings": w,
        }
        for (t, m, s, n, p, w) in summary
    ]
    (RESULTS_DIR / "summary_3rust_fixed.json").write_text(
        json.dumps(summary_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSummary written to {RESULTS_DIR / 'summary_3rust_fixed.json'}")


if __name__ == "__main__":
    main()
