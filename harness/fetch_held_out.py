"""Fetch MultiPL-E samples and build held-out task specs for Anubis.

Reads 2 samples each from humaneval-{py,ts,rs,go} (8 tasks total),
writes them as spec.md + source.json under tasks/held-out-multipl-*/.

This script is idempotent — safe to re-run.
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
DATASET = "nuprl/MultiPL-E"
PYTHON_DATASET = "openai/openai_humaneval"

# 2 samples each across 4 languages = 8 held-out tasks. Hand-picked
# HumanEval problem IDs that exercise distinct API surfaces (no overlap
# with existing 11 task-001..task-011 benchmark tasks which are all
# project-build format; these are function-completion format).
#
# MultiPL-E uses name format `HumanEval_N_<snake_case_fn>` (with
# underscores); openai_humaneval uses `HumanEval/N` (with slash).
# These IDs are the same underlying problems across all languages.
TARGETS = [
    ("py", "python", "HumanEval/0", "has_close_elements"),
    ("py", "python", "HumanEval/2", "truncate_number"),
    ("ts", "typescript", "HumanEval_0_has_close_elements", "hasCloseElements"),
    ("ts", "typescript", "HumanEval_2_truncate_number", "truncateNumber"),
    ("rs", "rust", "HumanEval_0_has_close_elements", "has_close_elements"),
    ("rs", "rust", "HumanEval_2_truncate_number", "truncate_number"),
    ("go", "go", "HumanEval_0_has_close_elements", "HasCloseElements"),
    ("go", "go", "HumanEval_2_truncate_number", "TruncateNumber"),
]


def fetch_sample(lang_suffix: str, problem_id: str) -> dict | None:
    """Fetch a single sample from MultiPL-E (or openai_humaneval for py).

    MultiPL-E only stores TRANSLATIONS of HumanEval/MBPP — Python source
    lives in `openai/openai_humaneval`. Other languages live in
    `nuprl/MultiPL-E` under config `humaneval-{suffix}`.

    HF datasets-server caps page length at 100 rows. HumanEval has 164
    problems so we page through offset 0 and 100.
    """
    if lang_suffix == "py":
        dataset = PYTHON_DATASET
        config = "openai_humaneval"
        # openai_humaneval uses bare problem IDs like "HumanEval/0"
        # stored as the `task_id` field rather than `name`.
    else:
        dataset = DATASET
        config = f"humaneval-{lang_suffix}"

    for offset in (0, 100):
        url = (
            "https://datasets-server.huggingface.co/rows"
            f"?dataset={dataset}&config={config}&split=test"
            f"&offset={offset}&length=100"
        )
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        if not rows:
            break
        for row in rows:
            sample = row.get("row", {})
            # openai_humaneval uses task_id, MultiPL-E uses name
            this_id = sample.get("name") or sample.get("task_id")
            if this_id == problem_id:
                # Normalise keys so downstream code can use either
                if "name" not in sample:
                    sample["name"] = this_id
                # openai_humaneval uses `prompt` + `test` (singular) +
                # `entry_point`. MultiPL-E uses `prompt` + `tests` (plural).
                if "tests" not in sample and "test" in sample:
                    sample["tests"] = sample["test"]
                if "entry_point" not in sample:
                    sample["entry_point"] = problem_id.split("/")[-1]
                return sample
    return None


def build_spec_md(sample: dict, lang: str, task_id: str) -> str:
    """Convert MultiPL-E sample to anubis-benchmark spec.md format."""
    name = sample.get("name", "unknown")
    prompt = sample.get("prompt", "").rstrip()
    tests = sample.get("tests", "").rstrip()
    entry_point = sample.get("entry_point") or sample.get("name", "").split("/")[-1]
    language_label = {
        "python": "Python 3.11",
        "typescript": "TypeScript 5.x (Node 20)",
        "rust": "Rust 1.75 (2021 edition)",
        "go": "Go 1.21",
    }.get(lang, lang)

    ext = {"python": "py", "typescript": "ts", "rust": "rs", "go": "go"}[lang]
    test_file = f"tests/test_solution.{ext}"

    return textwrap.dedent(
        f"""\
        # Held-Out Task {task_id}: MultiPL-E {name} ({lang})

        > **Source**: MultiPL-E ({DATASET}, Apache 2.0).
        > **Original problem ID**: {name}.
        > **DO NOT tune scanner weights against this task** — see HELD_OUT_README.md.

        ## Objective

        Complete the {entry_point} function in {language_label}. The function
        signature and docstring are provided; you must write the body so that
        all tests in `{test_file}` pass.

        ## Requirements

        ### Starter Code

        ```{lang}
        {prompt}
        ```

        ### Test File (already in workspace)

        ```{lang}
        {tests}
        ```

        ### Success Criteria

        1. Solution file `solution.{ext}` exists at workspace root with the completed function.
        2. `python -m pytest {test_file}` (or language-equivalent) passes all tests.
        3. No external network calls; pure stdlib solution expected.

        ### Deliverables

        - `solution.{ext}` with the completed {entry_point} function.
        - Do NOT modify the test file.

        ## Notes for the Agent

        - This is a held-out evaluation task. Work autonomously; do not ask questions.
        - If multiple valid implementations exist, prefer the simplest correct one.
        - The function name and signature in `solution.{ext}` MUST match the starter code exactly.
        """
    )


def main() -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    for lang_suffix, lang, problem_id, _hint in TARGETS:
        task_id = f"held-out-multipl-{lang_suffix}-{problem_id.lower().replace('/', '-')}"
        task_dir = TASKS_DIR / task_id
        if task_dir.exists() and (task_dir / "spec.md").exists():
            skipped.append(task_id)
            continue

        print(f"Fetching {lang_suffix}/{problem_id} ...")
        sample = fetch_sample(lang_suffix, problem_id)
        if sample is None:
            print(f"  SKIP: problem {problem_id} not found in humaneval-{lang_suffix}")
            continue

        task_dir.mkdir(parents=True, exist_ok=True)
        spec_md = build_spec_md(sample, lang, task_id)
        (task_dir / "spec.md").write_text(spec_md, encoding="utf-8")

        source_meta = {
            "task_id": task_id,
            "source_dataset": DATASET,
            "source_url": f"https://huggingface.co/datasets/{DATASET}",
            "source_config": f"humaneval-{lang_suffix}",
            "original_problem_id": problem_id,
            "language": lang,
            "license": "Apache 2.0",
            "citation": "Cassano et al., 2022 — arXiv:2208.08227",
            "entry_point": sample.get("entry_point") or problem_id.split("/")[-1],
            "frozen_at": "2026-08-02",
            "freeze_notice": (
                "HELD-OUT: do not use this task for scanner weight tuning. "
                "Use only for measuring generalization. See HELD_OUT_README.md."
            ),
        }
        (task_dir / "source.json").write_text(
            json.dumps(source_meta, indent=2), encoding="utf-8"
        )
        created.append(task_id)
        print(f"  CREATED {task_id}")

    print()
    print(f"Created: {len(created)} ({', '.join(created) if created else '-'})")
    print(f"Skipped (already existed): {len(skipped)} ({', '.join(skipped) if skipped else '-'})")


if __name__ == "__main__":
    main()
