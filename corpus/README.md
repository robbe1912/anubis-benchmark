# TV Benchmark corpus

Pre-authored task specs for the thought-verification side-by-side benchmark
(thought-verification.md v6 Appendix D). Each spec is a self-contained
prompt + verification recipe — independent of the older `tasks/task-NNN-*`
held-out tasks used by the smoke test.

## Layout

```
corpus/tv_tasks/
  tv_task_01_rust_serde_custom.md       Obscure-API
  tv_task_02_pydantic_annotated.md      Obscure-API
  tv_task_03_react_usetransition.md     Obscure-API
  tv_task_04_ts_zod_discriminated.md    Obscure-API
  tv_task_05_go_context_timeout.md      Obscure-API
  tv_task_06_python_async_itertools.md  Obscure-API
  tv_task_07_python_typing_39.md        Version-locked
  tv_task_08_rust_edition_2021.md       Version-locked
  tv_task_09_react_16_no_hooks.md       Version-locked
  tv_task_10_moment_js_parsezone.md     Version-locked
  tv_task_11_node_20_fetch.md           Version-locked
  tv_task_12_seed_known_hallucinations.md  Synthetic-seed (E2E MVP gate)
```

## Categories

| Category | Count | Forces |
|----------|------:|--------|
| Obscure-API | 6 | Specific API recall (rarely-used signatures, `serialize_with`, `useTransition`, `discriminatedUnion`, `parseZone`, ...) |
| Version-locked | 5 | Version recall (Python 3.9 vs 3.10, React 16 vs 18, Rust edition 2021 vs let-else, moment.js 2.30, Node 20 fetch) |
| Synthetic-seed | 1 | Planted hallucinations (3 known misleading hints) — the QA #6 E2E MVP gate |

## Spec shape

Each `tv_task_NN_<name>.md` contains:

1. **Objective** — what the agent must build.
2. **Prompt (verbatim)** — the exact string fed to the agent (used as the
   transcript seed for both `with-anubis` and `without-anubis` runs).
3. **Expected duration** — by backend (GLM-5.2 vs Qwen-7B stress).
4. **Expected hallucination types** — the categories the TV layer must
   detect (capability_claim / version_claim / citation / tool_call_lie).
5. **Docker verification command** — canonical reproducible build/test.
6. **Success Criteria** — machine-checkable acceptance conditions.
7. **Hallucination seed density** — expected FP/FN rate per backend.

## Running

Use `harness/run_side_by_side.py --task corpus/tv_tasks/tv_task_NN_<name>.md`
(Subtask A harness). The transcript + audit JSONL land in the standard
`runs/<task_id>/<seed>/{with,without}-anubis/` layout. Then
`evaluation/tv_labels.py batch --transcript <agent_output.jsonl>` produces
labels per claim with the two divergent labelers + optional human auditor.
