#!/usr/bin/env python3
"""Run 3 hard benchmark tasks: extract prompt -> proxy -> extract -> scan."""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "harness"
PROXY = "http://127.0.0.1:7878/v1/chat/completions"
API_KEY = os.environ.get("ZAI_API_KEY", "")
MODEL = "glm-5-turbo"
SCANNER = r"E:\GitRepos\groundwire\packages\daemon-rs\target\release\scan_transcript.exe"

TASKS = [
    ("task-21-go-concurrency", "go"),
    ("task-22-cpp-templates",  "cpp"),
    ("task-23-c-pointers",     "c"),
]

LANG_FOR_SCANNER = {
    "go": "go",
    "cpp": "cpp",
    "c":  "c",  # scanner may not accept 'c'; will pass anyway
}

SYSTEM_PROMPT = ("You are a senior software engineer. Produce production-ready code "
                 "with correct imports, types, and API usage. Output each file as a "
                 "fenced code block.")

def extract_prompt(spec_path: Path) -> str:
    raw = spec_path.read_text(encoding="utf-8")
    m = re.search(r"(?s)## Prompt.*?\n>(.*?)(?=\n##|\Z)", raw)
    if not m:
        raise RuntimeError(f"no prompt in {spec_path}")
    # Strip leading ">" from each line, collapse whitespace
    body = m.group(1)
    lines = [re.sub(r"^\s*>\s?", "", ln) for ln in body.splitlines()]
    return "\n".join(lines).strip()

def send_request(task_id: str, prompt: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 4096,
    }).encode("utf-8")
    req = urllib.request.Request(
        PROXY, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read().decode("utf-8", errors="replace")
    return {"status": r.status, "body": data, "elapsed": time.time() - t0}

def run_task(task_id: str, lang: str) -> dict:
    spec = REPO / "corpus" / "hard_tasks" / task_id / "spec.md"
    prompt = extract_prompt(spec)
    out_dir = REPO / "results" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "task_prompt.txt").write_text(prompt, encoding="utf-8")

    print(f"[{task_id}] prompt {len(prompt)} chars -> proxy", flush=True)
    resp = send_request(task_id, prompt)
    print(f"[{task_id}] response {len(resp['body'])} bytes in {resp['elapsed']:.1f}s", flush=True)

    resp_path = out_dir / "response.json"
    resp_path.write_text(resp["body"], encoding="utf-8")

    # extract
    transcript = out_dir / "transcript.jsonl"
    code_md    = out_dir / "generated_code.md"
    proc = subprocess.run(
        ["python", str(HARNESS / "extract_response.py"),
         str(resp_path), str(transcript), str(code_md)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"task": task_id, "error": "extract failed", "stderr": proc.stderr[-500:]}
    print(f"[{task_id}] extracted -> {code_md.stat().st_size} bytes", flush=True)

    # scan (no DELULU_FORGE_ONLY env)
    env = {**__import__("os").environ}
    env.pop("DELULU_FORGE_ONLY", None)
    scan_args = [SCANNER, str(transcript), "--project-root", str(REPO)]
    if lang in LANG_FOR_SCANNER:
        scan_args += ["--lang", LANG_FOR_SCANNER[lang]]
    print(f"[{task_id}] scanning ({lang})...", flush=True)
    sp = subprocess.run(scan_args, capture_output=True, text=True, env=env)
    (out_dir / "scan.log").write_text(sp.stdout + "\n--- STDERR ---\n" + sp.stderr, encoding="utf-8")

    # parse warnings from stdout JSON line
    warnings = []
    for ln in sp.stdout.splitlines():
        ln = ln.strip()
        if ln.startswith("{") and '"warnings"' in ln:
            try:
                obj = json.loads(ln)
                warnings.extend(obj.get("warnings", []))
            except json.JSONDecodeError:
                pass
    # also capture SUMMARY line from stderr (in case scanner writes there)
    print(f"[{task_id}] {len(warnings)} warnings", flush=True)
    return {"task": task_id, "lang": lang, "warnings": warnings,
            "returncode": sp.returncode, "stdout_tail": sp.stdout[-300:]}

def main():
    results = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_task, tid, lang): tid for tid, lang in TASKS}
        for f in futs:
            tid = futs[f]
            try:
                results[tid] = f.result()
            except Exception as e:
                results[tid] = {"task": tid, "error": f"{type(e).__name__}: {e}"}
    out = REPO / "results" / "task-21-23-scan-summary.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    # also dump just the warnings to a small file for classification
    for tid, r in results.items():
        if "warnings" in r:
            wfile = REPO / "results" / tid / "warnings.txt"
            wfile.write_text("\n".join(r["warnings"]), encoding="utf-8")
            print(f"  {tid}: {len(r['warnings'])} warnings -> {wfile}", flush=True)

if __name__ == "__main__":
    main()
