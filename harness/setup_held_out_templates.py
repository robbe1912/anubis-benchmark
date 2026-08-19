"""Generate workspace templates for held-out MultiPL-E tasks.

For each tasks/held-out-multipl-*/spec.md, parse the spec to extract:
- The starter code (prompt) for solution.{ext}
- The test code for tests/test_solution.{ext}

Writes them to tasks/<task_id>/workspace_template/ so the benchmark
harness can copy them into the agent's working directory before spawn.

This is necessary because the existing run_benchmark.ps1 expects a
project to be built from scratch; held-out tasks are function-
completion format where the test file is pre-placed.
"""
from __future__ import annotations

import re
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"

# Map MultiPL-E language label -> file extension + solution shebang/header
LANG_EXT = {
    "python": "py",
    "typescript": "ts",
    "rust": "rs",
    "go": "go",
}


def parse_spec(spec_text: str) -> dict:
    """Extract starter_code, test_code, language from spec.md text.

    The spec.md format produced by fetch_held_out.py has fenced code
    blocks under '### Starter Code' and '### Test File' headers.
    """
    # Find language from "Complete the X function in <Lang> ..."
    lang_match = re.search(
        r"Complete the \S+ function in ([A-Za-z0-9. ()-]+?)\.",
        spec_text,
    )
    lang_label = lang_match.group(1).strip() if lang_match else ""
    lang = {
        "Python 3.11": "python",
        "TypeScript 5.x (Node 20)": "typescript",
        "Rust 1.75 (2021 edition)": "rust",
        "Go 1.21": "go",
    }.get(lang_label, "")
    if not lang:
        # Fallback: scan the task_id from the source.json
        lang = lang_label.lower().split()[0] if lang_label else ""

    # Extract starter code block (under ### Starter Code)
    starter_match = re.search(
        r"### Starter Code\s*```(?:\w+)?\s*\n(.*?)```",
        spec_text,
        re.DOTALL,
    )
    starter_code = starter_match.group(1).rstrip() if starter_match else ""

    # Extract function name from starter code so we can substitute
    # `candidate(...)` in tests with the actual symbol. MultiPL-E
    # tests use `candidate` as a placeholder alias for the function.
    fn_name = ""
    if lang == "python":
        m = re.search(r"\bdef\s+([A-Za-z_]\w*)\s*\(", starter_code)
        if m:
            fn_name = m.group(1)
    elif lang == "rust":
        m = re.search(r"\bfn\s+([A-Za-z_]\w*)\s*\(", starter_code)
        if m:
            fn_name = m.group(1)
    elif lang == "go":
        m = re.search(r"\bfunc\s+([A-Za-z_]\w*)\s*\(", starter_code)
        if m:
            fn_name = m.group(1)
    elif lang == "typescript":
        # Could be `export function foo(` or `function foo(` or `const foo =`
        m = re.search(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", starter_code)
        if not m:
            m = re.search(r"\bconst\s+([A-Za-z_]\w*)\s*=", starter_code)
        if m:
            fn_name = m.group(1)

    # Extract test code block (under ### Test File)
    test_match = re.search(
        r"### Test File \(already in workspace\)\s*```(?:\w+)?\s*\n(.*?)```",
        spec_text,
        re.DOTALL,
    )
    test_code = test_match.group(1).rstrip() if test_match else ""

    return {
        "language": lang,
        "starter_code": starter_code,
        "test_code": test_code,
        "function_name": fn_name,
    }


def write_solution_file(starter_code: str, lang: str) -> str:
    """Adapt starter code into a solution file the agent completes.

    For all languages: starter is the function signature + docstring.
    The agent must fill in the body. We DO NOT add a placeholder body —
    the unclosed brace is the explicit "implement me" signal.

    For Rust the function is made `pub` so integration tests under
    tests/ can import it via `use solution::foo`.
    For Go we ensure `package main` is present so `go test` works.
    """
    if lang == "rust":
        # Promote fn to pub so integration tests can import from the lib
        # crate. Drop any inline fn main() — separate bin stub handles that.
        code = starter_code
        if not code.lstrip().startswith("pub "):
            code = code.replace("fn ", "pub fn ", 1)
        # Strip trailing inline main() if present
        code = re.sub(r"\n*fn\s+main\s*\(\s*\)\s*\{[^}]*\}\s*$", "", code)
        return code.rstrip() + "\n"
    if lang == "go":
        if not starter_code.startswith("package "):
            starter_code = "package main\n\n" + starter_code
        return starter_code.rstrip() + "\n"
    if lang == "python":
        return starter_code.rstrip() + "\n"
    if lang == "typescript":
        return starter_code.rstrip() + "\n"
    return starter_code


def _extract_rust_asserts(test_code: str) -> list[str]:
    """Pull assert_eq! lines out of MultiPL-E Rust test snippet.

    MultiPL-E Rust tests come as:
        }
        fn main() {
            let candidate = has_close_elements;
            assert_eq!(candidate(vec![...], 0.3), true);
            ...
        }

    We extract the assert_eq! lines and re-wrap as proper #[test] fns.
    """
    lines = []
    for line in test_code.splitlines():
        s = line.strip()
        if s.startswith("assert_eq!") or s.startswith("assert!"):
            lines.append(s)
    return lines


def _extract_go_asserts(test_code: str) -> list[str]:
    """Pull if-true-panic assertions out of MultiPL-E Go test snippet."""
    # MultiPL-E Go tests look like:
    #   if true {
    #       ... assertions like: if !(hasCloseElements(...)) { panic(...) }
    #   }
    # We extract the inner if-!(...) { panic(...) } lines.
    lines = []
    for line in test_code.splitlines():
        s = line.strip()
        if s.startswith("if !(") and "panic(" in s:
            lines.append(s)
        elif s.startswith("if !") and "panic(" in s:
            lines.append(s)
    return lines


def _extract_py_asserts(test_code: str) -> list[str]:
    """Pull assert lines out of MultiPL-E Python test snippet."""
    lines = []
    for line in test_code.splitlines():
        s = line.strip()
        if s.startswith("assert ") and "==" in s:
            lines.append(s)
    return lines


def _extract_ts_asserts(test_code: str) -> list[str]:
    """Pull assertion lines out of MultiPL-E TS test snippet.

    MultiPL-E TS tests use Node assert module:
        assert.deepEqual(candidate(...), expected)
        assert.strictEqual(candidate(...), expected)
    Older format used `if (JSON.stringify(fn(...)) !== JSON.stringify(...)) { throw ... }`
    or `console.assert(...)`. Extract all.
    """
    lines = []
    for line in test_code.splitlines():
        s = line.strip()
        if (
            s.startswith("assert.deepEqual")
            or s.startswith("assert.strictEqual")
            or s.startswith("assert.deepStrictEqual")
            or s.startswith("assert.equal")
            or s.startswith("console.assert")
            or (s.startswith("if (JSON.stringify(") and "throw" in s)
        ):
            lines.append(s)
    return lines


def write_test_file(test_code: str, lang: str, entry_point: str) -> str:
    """Adapt MultiPL-E test snippet into a runnable test file.

    MultiPL-E tests use a "candidate = fn" pattern with inline asserts
    inside main() (Rust/Go) or inside a check() function (Python). We
    re-wrap them as proper language-idiomatic test fns.
    """
    if lang == "python":
        asserts = _extract_py_asserts(test_code)
        if not asserts:
            return test_code  # Fallback: pass through unchanged
        # Replace `candidate(...)` with `{fn_name}(...)` — MultiPL-E tests
        # use `candidate` as a placeholder alias for the actual function.
        body_lines = [a.replace("candidate(", entry_point + "(") for a in asserts]
        body = "\n".join(f"    {a}" for a in body_lines)
        return (
            "import sys\n"
            "import os\n"
            "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
            "from solution import " + entry_point + "\n\n"
            "def test_" + entry_point + "():\n"
            + body + "\n"
        )
    if lang == "rust":
        asserts = _extract_rust_asserts(test_code)
        if not asserts:
            return test_code
        # Replace `candidate(...)` with `{fn_name}(...)`
        body_lines = []
        for a in asserts:
            body_lines.append("    " + a.replace("candidate(", entry_point + "("))
        body = "\n".join(body_lines)
        # Integration test in tests/ — import from the lib crate, not super.
        # The lib crate exposes the function as `pub fn`.
        return (
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    use solution::" + entry_point + ";\n\n"
            "    #[test]\n"
            "    fn test_" + entry_point + "() {\n"
            + body + "\n"
            "    }\n"
            "}\n"
        )
    if lang == "go":
        asserts = _extract_go_asserts(test_code)
        if not asserts:
            return test_code
        body = "\n".join(f"\t{a}" for a in asserts)
        return (
            "package main\n\n"
            "import \"testing\"\n\n"
            "func Test" + entry_point.title().replace("_", "") + "(t *testing.T) {\n"
            + body + "\n"
            "}\n"
        )
    if lang == "typescript":
        asserts = _extract_ts_asserts(test_code)
        if not asserts:
            return test_code
        # Replace `candidate(...)` with `{fn_name}(...)`. Import Node assert
        # (MultiPL-E TS tests use assert.deepEqual/strictEqual — vitest's
        # `expect` API doesn't match that shape 1:1, so we keep Node assert).
        body_lines = [a.replace("candidate(", entry_point + "(") for a in asserts]
        body = "\n".join(f"    {a}" for a in body_lines)
        return (
            "import { describe, it } from 'vitest';\n"
            "import assert from 'node:assert';\n"
            "import { " + entry_point + " } from '../solution';\n\n"
            "describe('" + entry_point + "', () => {\n"
            "  it('passes MultiPL-E assertions', () => {\n"
            + body + "\n"
            "  });\n"
            "});\n"
        )
    return test_code


def write_project_files(template_dir: Path, lang: str) -> None:
    """Write language-specific project scaffolding (Cargo.toml, etc.)."""
    if lang == "rust":
        # lib + bin layout so integration tests under tests/ can import
        # the function via `use solution::foo`. lib.rs holds the pub fn;
        # main.rs is a thin bin stub.
        (template_dir / "Cargo.toml").write_text(
            '[package]\n'
            'name = "solution"\n'
            'version = "0.1.0"\n'
            'edition = "2021"\n\n'
            '[lib]\n'
            'name = "solution"\n'
            'path = "src/lib.rs"\n\n'
            '[[bin]]\n'
            'name = "solution"\n'
            'path = "src/main.rs"\n',
            encoding="utf-8",
        )
        src_dir = template_dir / "src"
        src_dir.mkdir(exist_ok=True)
        # Move solution.rs → src/lib.rs; write thin main.rs bin stub.
        lib_path = src_dir / "lib.rs"
        sol_path = template_dir / "solution.rs"
        if sol_path.exists():
            lib_path.write_text(sol_path.read_text(encoding="utf-8"), encoding="utf-8")
            sol_path.unlink()
        (src_dir / "main.rs").write_text(
            "fn main() {\n"
            "    // Held-out task: main not required for evaluation.\n"
            "    // Tests live in tests/test_solution.rs and exercise the\n"
            "    // pub fns in src/lib.rs.\n"
            "}\n",
            encoding="utf-8",
        )
    elif lang == "go":
        (template_dir / "go.mod").write_text(
            "module solution\n\ngo 1.21\n",
            encoding="utf-8",
        )
    elif lang == "typescript":
        (template_dir / "package.json").write_text(
            '{\n'
            '  "name": "solution",\n'
            '  "version": "1.0.0",\n'
            '  "type": "module",\n'
            '  "scripts": {\n'
            '    "test": "vitest run"\n'
            '  },\n'
            '  "devDependencies": {\n'
            '    "vitest": "^1.0.0",\n'
            '    "typescript": "^5.0.0"\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        (template_dir / "tsconfig.json").write_text(
            '{\n'
            '  "compilerOptions": {\n'
            '    "target": "ES2022",\n'
            '    "module": "ESNext",\n'
            '    "moduleResolution": "bundler",\n'
            '    "strict": true,\n'
            '    "esModuleInterop": true,\n'
            '    "skipLibCheck": true\n'
            '  },\n'
            '  "include": ["*.ts", "tests/*.ts"]\n'
            '}\n',
            encoding="utf-8",
        )
    elif lang == "python":
        (template_dir / "pyproject.toml").write_text(
            '[project]\n'
            'name = "solution"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.11"\n\n'
            '[tool.pytest.ini_options]\n'
            'pythonpath = ["."]\n',
            encoding="utf-8",
        )


def main() -> None:
    created = []
    skipped = []
    failed = []

    for task_dir in sorted(TASKS_DIR.glob("held-out-multipl-*")):
        spec_path = task_dir / "spec.md"
        source_path = task_dir / "source.json"
        if not spec_path.exists():
            failed.append(f"{task_dir.name}: spec.md missing")
            continue

        spec_text = spec_path.read_text(encoding="utf-8")
        parsed = parse_spec(spec_text)
        lang = parsed["language"]
        if not lang or lang not in LANG_EXT:
            failed.append(f"{task_dir.name}: cannot determine language")
            continue

        ext = LANG_EXT[lang]
        # Prefer the function name parsed from the starter code — that's
        # the actual symbol the agent must implement. Fall back to the
        # source.json entry_point (which is often the dataset row ID like
        # HumanEval_0_has_close_elements, NOT the function name).
        entry_point = parsed.get("function_name") or "candidate"
        if not parsed.get("function_name") and source_path.exists():
            try:
                entry_point = json.loads(source_path.read_text(encoding="utf-8")).get(
                    "entry_point", entry_point
                )
            except Exception:
                pass

        template_dir = task_dir / "workspace_template"
        # Skip-check: Rust uses src/lib.rs, others use solution.{ext}
        sentinel = (
            template_dir / "src" / "lib.rs"
            if lang == "rust"
            else template_dir / f"solution.{ext}"
        )
        if template_dir.exists() and sentinel.exists():
            skipped.append(task_dir.name)
            continue

        template_dir.mkdir(parents=True, exist_ok=True)
        try:
            solution = write_solution_file(parsed["starter_code"], lang)
            test = write_test_file(parsed["test_code"], lang, entry_point)

            # For Rust, solution text goes to solution.rs first; the
            # write_project_files step then moves it to src/lib.rs.
            (template_dir / f"solution.{ext}").write_text(solution, encoding="utf-8")
            (template_dir / "tests").mkdir(exist_ok=True)
            (template_dir / "tests" / f"test_solution.{ext}").write_text(test, encoding="utf-8")
            write_project_files(template_dir, lang)
            created.append(task_dir.name)
        except Exception as e:
            failed.append(f"{task_dir.name}: {e}")
            # Rollback partial template
            if template_dir.exists():
                import shutil
                shutil.rmtree(template_dir, ignore_errors=True)

    print(f"Created: {len(created)}")
    for c in created:
        print(f"  + {c}")
    print(f"Skipped (already existed): {len(skipped)}")
    for s in skipped:
        print(f"  = {s}")
    print(f"Failed: {len(failed)}")
    for f in failed:
        print(f"  ! {f}")


if __name__ == "__main__":
    main()
