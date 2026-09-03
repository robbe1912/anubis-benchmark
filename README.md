# anubis-benchmark

The benchmark that killed [Anubis](https://github.com/robbe1912/anubis-public) — a hallucination detector for coding agents.

## What's here

- **`corpus/hard_tasks/`** — 25 hard-distribution tasks (novel/unfamiliar SDKs: sqlx, tRPC, gRPC, MediatR, Godot 4.x, pydantic v2, axum 0.7, …) with auditor-labeled ground truth: ~60 labeled real-hallucination instances and all 41 scanner warnings classified (solid / marginal / FP / corrupted). This is the scarce artifact — an honest, adversarially-audited hallucination benchmark.
- **`harness/`** — the run scripts: opencode→anubis-proxy→model wiring, offline `scan_transcript` replay, structural build checks.

## Why it exists

Anubis's detector was killed by a pre-registered gate on this corpus: **4 solid true positives out of 41 warnings (17% warning-level precision) against ~35 missed real hallucinations (10–15% recall).** Full story, methods, and the adversarial audit that corrected the author's own over-count 3–4×: [`docs/anubis-postmortem.md`](https://github.com/robbe1912/anubis-public/blob/main/docs/anubis-postmortem.md) in the parent repo.

## Use it

The labels are the point. If you're building a hallucination detector for coding agents, run it against this corpus and publish precision/recall on the same counting units — warning-level and defect-level. The 2026-08 baseline to beat is documented in the postmortem.

## License

MIT.
