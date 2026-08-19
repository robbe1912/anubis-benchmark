"""Aggregate held-out MultiPL-E results into a single report.

Picks the most recent successful (timeout_hit=False) run per task,
produces held-out-report.md + held-out-summary.json + held-out-all-warnings.csv.

POLICY: This script is read-only on scanner weights. Per HELD_OUT_README.md,
findings here MUST NOT be used to tune compute_risk_score, skip-lists,
introspection rules, or any scanner parameter. The corpus exists to
measure generalization, not to drive it.
"""
from __future__ import annotations
import csv
import json
import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / 'results'
OUT_SUMMARY = REPO / 'held-out-summary.json'
OUT_CSV = REPO / 'held-out-all-warnings.csv'
OUT_REPORT = REPO / 'held-out-report.md'

LANG_OF = {
    'py': 'python', 'ts': 'typescript', 'rs': 'rust', 'go': 'go',
}

def parse_task(dir_name: str) -> tuple[str, str, str]:
    # held-out-multipl-{lang}-{problem_id}-YYYYMMDD-HHMMSS
    m = re.match(r'held-out-multipl-(py|ts|rs|go)-(.+?)-(\d{8}-\d{6})$', dir_name)
    if not m:
        return ('', '', '')
    lang_short, problem, _ts = m.groups()
    return (LANG_OF[lang_short], problem, lang_short)

def pick_runs() -> list[dict]:
    """Most recent successful (timeout_hit=false, agent_output exists) run per task."""
    by_task: dict[tuple[str, str], dict] = {}
    for d in sorted(RESULTS.glob('held-out-multipl-*')):
        if not d.is_dir():
            continue
        lang, problem, _ = parse_task(d.name)
        if not lang:
            continue
        agent_out = d / 'agent_output.jsonl'
        meta = d / 'metadata.json'
        if not agent_out.exists() or not meta.exists():
            continue
        try:
            # PowerShell Set-Content emits UTF-8 BOM; utf-8-sig strips it transparently.
            m = json.loads(meta.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        if m.get('timeout_hit', True):
            continue  # skip timed-out runs
        key = (lang, problem)
        prev = by_task.get(key)
        # prefer newer mtime
        if prev is None or d.stat().st_mtime > prev['_mtime']:
            warnings_file = d / 'unique_warnings.txt'
            warnings = []
            if warnings_file.exists():
                # Anubis evaluate.ps1 writes via PowerShell Out-File which defaults to UTF-16 LE.
                # Try utf-8-sig first (covers plain UTF-8 + BOM), fall back to utf-16.
                raw = warnings_file.read_bytes()
                for enc in ('utf-8-sig', 'utf-16', 'utf-16le', 'cp1252'):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = raw.decode('utf-8', errors='replace')
                warnings = [line.strip() for line in text.splitlines() if line.strip()]
            event_count = 0
            with agent_out.open(encoding='utf-8') as f:
                for _ in f:
                    event_count += 1
            by_task[key] = {
                'task_dir': str(d.relative_to(REPO)),
                'language': lang,
                'problem': problem,
                'warnings': warnings,
                'warning_count': len(warnings),
                'agent_events': event_count,
                'timeout_hit': m.get('timeout_hit'),
                'build_exit': m.get('build_exit'),
                'test_exit': m.get('test_exit'),
                'agent_model': m.get('agent_model', 'unknown'),
                '_mtime': d.stat().st_mtime,
            }
    return sorted(by_task.values(), key=lambda r: (r['language'], r['problem']))

def write_csv(rows: list[dict]) -> None:
    with OUT_CSV.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['language', 'problem', 'task_dir', 'warning'])
        for r in rows:
            if r['warnings']:
                for warning in r['warnings']:
                    w.writerow([r['language'], r['problem'], r['task_dir'], warning])
            else:
                w.writerow([r['language'], r['problem'], r['task_dir'], ''])

def write_report(rows: list[dict]) -> None:
    total = len(rows)
    flagged = sum(1 for r in rows if r['warning_count'] > 0)
    total_warnings = sum(r['warning_count'] for r in rows)
    by_lang: dict[str, dict] = {}
    for r in rows:
        lang = r['language']
        d = by_lang.setdefault(lang, {'total': 0, 'flagged': 0, 'warnings': 0})
        d['total'] += 1
        if r['warning_count'] > 0:
            d['flagged'] += 1
        d['warnings'] += r['warning_count']

    lines = []
    lines.append('# Held-out MultiPL-E Generalization Report')
    lines.append('')
    lines.append(f'Generated: {datetime.utcnow().isoformat(timespec="seconds")}Z')
    lines.append('')
    lines.append('## Freeze Policy (READ BEFORE READING THIS REPORT)')
    lines.append('')
    lines.append('**DO NOT tune scanner weights, thresholds, skip-lists, or introspection')
    lines.append('rules against this corpus.** See `HELD_OUT_README.md`. This corpus')
    lines.append('exists to measure generalization, not to drive it. Any change that')
    lines.append('references warning text or counts from these runs is forbidden.')
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append(f'- Tasks scanned: **{total}**')
    lines.append(f'- Tasks with >=1 warning: **{flagged}** ({flagged/total*100:.1f}%)')
    lines.append(f'- Total warnings: **{total_warnings}**')
    lines.append(f'- All warnings are FPs by definition (golden = correct agent code)')
    lines.append('')
    lines.append('## Per-language Breakdown')
    lines.append('')
    lines.append('| Language | Tasks | Flagged | Warnings |')
    lines.append('|----------|-------|---------|----------|')
    for lang in sorted(by_lang):
        d = by_lang[lang]
        lines.append(f"| {lang} | {d['total']} | {d['flagged']} | {d['warnings']} |")
    lines.append('')
    lines.append('## Per-task Detail')
    lines.append('')
    lines.append('| Task | Warnings | Build | Test | Timeout |')
    lines.append('|------|----------|-------|------|---------|')
    for r in rows:
        build = 'n/a' if r['build_exit'] in (-1, None) else f"exit {r['build_exit']}"
        test = 'n/a' if r['test_exit'] in (-1, None) else f"exit {r['test_exit']}"
        timeout = 'yes' if r['timeout_hit'] else 'no'
        lines.append(
            f"| `{r['language']}/{r['problem']}` | {r['warning_count']} | {build} | {test} | {timeout} |"
        )
    lines.append('')
    lines.append('## All Warnings (verbatim)')
    lines.append('')
    any_warnings = False
    for r in rows:
        if not r['warnings']:
            continue
        any_warnings = True
        lines.append(f"### `{r['language']}/{r['problem']}` ({r['warning_count']})")
        lines.append('')
        lines.append(f'Run dir: `{r["task_dir"]}`')
        lines.append('')
        for w in r['warnings']:
            lines.append(f'- {w}')
        lines.append('')
    if not any_warnings:
        lines.append('_No warnings emitted on any held-out task._')
        lines.append('')

    OUT_REPORT.write_text('\n'.join(lines), encoding='utf-8')

def main() -> None:
    rows = pick_runs()
    if not rows:
        raise SystemExit('No successful held-out runs found under results/')
    OUT_SUMMARY.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    write_csv(rows)
    write_report(rows)
    print(f'Wrote {OUT_SUMMARY}')
    print(f'Wrote {OUT_CSV}')
    print(f'Wrote {OUT_REPORT}')
    print(f'Tasks: {len(rows)}')
    flagged = sum(1 for r in rows if r['warning_count'] > 0)
    total_warnings = sum(r['warning_count'] for r in rows)
    print(f'Flagged tasks: {flagged}/{len(rows)}')
    print(f'Total warnings: {total_warnings}')

if __name__ == '__main__':
    main()
