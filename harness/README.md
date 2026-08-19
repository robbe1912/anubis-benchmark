# TV Side-by-Side Benchmark Harness

Python harness for the Thought Verification (TV) side-by-side benchmark
described in `thought-verification.md` v6 (Side-by-Side Benchmark Methodology,
Metis-fixed). Lives in the `anubis-benchmark` repo per Metis B3 coordination
rule; nothing here touches the `groundwire` scanner codebase.

## What it does

Runs a task spec twice — once with Anubis+TV intercepting LLM traffic, once
without — captures both transcripts side-by-side, and emits a YAML comparison
report. Ground-truth labels (Subtask C) plug into the same report via
`labels.jsonl`.

## Layout

```
anubis-benchmark/
├── harness/
│   ├── run_side_by_side.py           # primary entrypoint
│   ├── compare_runs.py               # YAML diff report
│   ├── replay_transcript.py          # deterministic rescan replay
│   └── setup_side_by_side_configs.ps1# bootstrap XDG-swapped opencode configs
├── evaluation/
│   └── tv_labels.py                  # CLI labeling (extract/judge/alpha)
├── lib/
│   ├── __init__.py
│   ├── stats.py                      # cluster_bootstrap, wilson, alpha, neyman
│   └── test_stats.py                 # 15 pytest tests
└── results/<task>-<model>-<seed>-<ts>-{with,without}/
    ├── agent_output.jsonl            # opencode agent events
    ├── audit.jsonl                   # Anubis audit entries (empty in without)
    ├── audit-prev.jsonl              # pre-run backup of ~/.anubis/audit.jsonl
    ├── ANUBIS.log                    # scanner log (empty in without)
    ├── ANUBIS-prev.log               # pre-run backup
    ├── console.log                   # opencode stderr
    ├── build.log / test.log          # language-detected build/test outputs
    ├── metadata.json                 # run metadata
    ├── task_prompt.txt               # spec fed to the agent
    └── workspace/                    # snapshot of agent's workdir
```

## Prerequisites

- Python 3.12+
- Ollama 0.32+ with `qwen2.5-coder:7b` pulled (Qwen-only for v1 — Metis H2)
- Anubis daemon running on `http://127.0.0.1:7878` (only required for
  `with`-mode; `without`-mode skips the daemon ping)
- opencode.exe discoverable at `C:\Users\robin\.bun\bin\opencode.exe`
- `pip install krippendorff numpy pyyaml`

## First-time setup

The harness varies the `zai-coding-plan` provider baseURL between
`with`/`without` modes. Bootstrap both XDG-swapped opencode configs from your
canonical config:

```powershell
python harness/run_side_by_side.py --help           # smoke-check argparse
.\harness\setup_side_by_side_configs.ps1 `
    -SourceConfig  C:\Users\robin\.config\opencode\opencode.json `
    -AnubisBaseUrl http://127.0.0.1:7878 `
    -DirectZaiBaseUrl https://api.z.ai/api/coding/paas/v4
```

This writes:
- `$env:TEMP\opencode-with-anubis\opencode\opencode.json` (z.ai → 7878)
- `$env:TEMP\opencode-no-anubis\opencode\opencode.json` (z.ai → direct)

Both inherit the rest of your config (plugins, ollama models, MCPs, etc.).

## Usage

### Run one mode

```powershell
python harness/run_side_by_side.py `
    -TaskId task-001-rust-todo-cli `
    -Model ollama:qwen2.5-coder:7b `
    -Seed  42 `
    -Mode  with
```

Flags accept both `--kebab-case` and `-PascalCase` aliases for compatibility
with the existing PowerShell harness scripts (`run_benchmark.ps1`,
`run_held_out.ps1`). `--mode both` runs `with` then `without` sequentially.

### Compare two result dirs

```powershell
python harness/compare_runs.py `
    -WithDir    results/task-001-...-with `
    -WithoutDir results/task-001-...-without `
    -Output     results/tv-smoke-comparison.yaml
```

Without `-Output`, the YAML goes to stdout. Recall/precision/f1/
silent_fn_delta remain `null` until `labels.jsonl` exists in the run dir
(Subtask C produces this via `evaluation/tv_labels.py`).

### Replay deterministically

```powershell
python harness/replay_transcript.py `
    -Input results/<run>/agent_output.jsonl `
    -Output results/<run>/replay.jsonl
```

Re-derives per-event warnings from `agent_output.jsonl` paired with
`audit.jsonl`. Byte-identical across runs (no wall-clock timestamps in
output). Satisfies QA gate #10 without touching
`groundwire/packages/daemon-rs/tests/held_out_rescan.rs` (Metis B3).

### Labeling CLI

```powershell
# Extract candidate claims with auto-classified type
python evaluation/tv_labels.py extract -RunDir results/<run>

# LLM-judge each claim (uses Z_AI_API_KEY env)
python evaluation/tv_labels.py judge `
    -RunDir results/<run> `
    -Labeler glm-5.2-strict `
    -SystemPrompt strict-fact-checker

# Compute Krippendorff α across multiple labelers
python evaluation/tv_labels.py alpha `
    -Labels results/<run>/labels.glm-5.2-strict.jsonl `
            results/<run>/labels.human.jsonl `
    -Level nominal
```

## Deviations from plan v6

Recorded per the plan's MUST-DO requirement.

1. **Python harness, not a wrapper around `run_held_out.ps1`.** The plan
   suggested reusing `run_held_out.ps1 -BypassAnubis`, but that script
   requires `tasks/<id>/workspace_template/` (held-out format) and
   `task-001-rust-todo-cli` does not have one. `run_side_by_side.py` is a
   Python reimplementation of `run_benchmark.ps1` (the spec-driven runner)
   with the XDG-swap from `run_held_out.ps1` grafted in.

2. **Both modes swap XDG.** The plan only swaps for `without`-mode. We also
   swap for `with`-mode (`opencode-with-anubis` config dir) so that the only
   variable between runs is the `zai-coding-plan.options.baseURL`
   (`127.0.0.1:7878` vs `https://api.z.ai/...`). This keeps ollama traffic
   identical in both modes and prevents plugin/agent drift from the user's
   main config from leaking into the result.

3. **Model normalization.** Plan uses `ollama:qwen2.5-coder:7b` (OpenAI-style
   colon). opencode expects `provider/model` (slash). `_normalize_model`
   translates the first colon into a slash if no slash is present, preserving
   model names that themselves contain colons (e.g. `qwen2.5-coder:7b`).

4. **`metadata.json.seed_caveat`.** Plan v6 lists seed as a primary
   independent variable. The harness records `seed=42` in metadata but
   documents that it is **not currently propagated to the provider** (ollama
   does not expose a fixed seed knob via the OpenAI-compatible endpoint;
   z.ai does, but routing is not yet wired). v1 results should be read as
   "single-shot runs at seed 42 (informational)" — true seed-controlled
   replication is deferred to v2.

5. **`replay_transcript.py` omits wall-clock timestamps.** QA gate #10
   requires two consecutive runs to produce identical output. The meta row
   therefore contains only `input_path`, `audit_path`, `scanner_commit`,
   `event_count`, `audit_count` — all deterministic given the input.

6. **No new Rust modules, no `held_out_rescan.rs` extension, no audit schema
   changes.** Per Metis B2, B3, H1 — all stats and replay logic live in
   Python (`lib/stats.py`, `harness/replay_transcript.py`). The harness
   reads `audit.jsonl` purely as input.

7. **Doccano replaced by `evaluation/tv_labels.py`.** Plan v6 explicitly
   drops Doccano; we ship a CLI labeler with `extract` (auto-classify),
   `judge` (LLM adjudication with strict/skeptical system prompts per B4),
   and `alpha` (Krippendorff inter-rater agreement).

## QA gate coverage

| Gate | Status | Where |
|------|--------|-------|
| #0 determinism probe | n/a (Subtask C) | — |
| #1 harness smoke (`with`) | ✅ exit 0 | this README |
| #2 baseline capture gate (`without`) | ✅ exit 0, audit empty | this README |
| #3 comparison report gate | ✅ YAML has `metrics.recall/precision/f1/silent_fn_delta` keys (null until labeled) | `compare_runs.py` |
| #4 Krippendorff α gate | ✅ `test_alpha_above_threshold` | `lib/test_stats.py` |
| #5 cluster bootstrap gate | ✅ `test_cluster_bootstrap_ci` | `lib/test_stats.py` |
| #6 e2e MVP | deferred — needs Subtask B corpus tasks | — |
| #7 adaptive stopping | ✅ `test_adaptive_stopping_converges` | `lib/test_stats.py` |
| #8 cost | manual — $0 Ollama, cloud validation <$1 per run | — |
| #9 power doc | deferred — out of Subtask A scope | — |
| #10 replay determinism | ✅ two consecutive runs produce identical SHA-256 | `replay_transcript.py` |

## Notes on the smoke run

The v1 smoke run on `task-001-rust-todo-cli` with `ollama:qwen2.5-coder:7b`
shows the harness plumbing works (both modes exit 0, audit captured in
`with`-mode and empty in `without`-mode, YAML report is valid), but the
7B model produces no files in 20 seconds — the autonomous loop quits after
one step. This is a model-capability limitation, not a harness bug. For
non-zero agent output use `zai-coding-plan/glm-5.2` (the existing default
in `run_benchmark.ps1`).
