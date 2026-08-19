# Parallel launcher removed (Council N+1 S1)

`run_held_out_parallel.ps1` was removed because concurrent invocations of
`run_held_out.ps1` corrupt the singleton audit log at
`~/.anubis/audit.jsonl`.

## Why it breaks

`run_held_out.ps1` lines 88-93:

```powershell
if (Test-Path $AuditLog) {
    $Backup = Join-Path $OutputDir 'audit-prev.jsonl'
    Copy-Item $AuditLog $Backup -Force
    Remove-Item $AuditLog -Force
}
```

Under N concurrent runs:

1. Task A: `Copy-Item` audit.jsonl → A/audit-prev.jsonl, `Remove-Item` audit.jsonl
2. Task B (mid-step): tries to copy a file A just removed → either fails or
   captures partial data
3. Task A's agent starts appending to a freshly-created audit.jsonl
4. Task B removes that file mid-append → Task A's audits are lost
5. Final `Copy-Item $AuditLog` (line 165) races with the other task's
   `Remove-Item` step

The daemon's `AUDIT_LOCK` (audit.rs:106) only serializes writes within a
single process — it does nothing for the harness's external copy+remove
on the same file.

## What's safe

Use sequential single-task runs:

```powershell
foreach ($task in @('task-001-rust-todo-cli','task-002-python-notes-cli')) {
    .\harness\run_held_out.ps1 -TaskId $task -TimeoutMinutes 10
}
```

Or use `harness/run_side_by_side.py` (Subtask A) which runs one task at a
time per dir, so concurrent audit access never happens.

## Future: re-enable parallel safely

Requires per-task audit isolation. Two viable paths:

1. **Scanner-side env var.** Add `ANUBIS_HOME` (or `ANUBIS_AUDIT_PATH`)
   override in `lib.rs::dirs_home()` so each task can redirect to its own
   `~/.anubis/` directory. ~1 hour scanner change. Then the parallel
   launcher can set `$env:ANUBIS_HOME = $WorkDir/.anubis` per task.

2. **Run-id header.** Add `X-Anubis-Run-Id` request header that the daemon
   uses to partition audit entries. Larger scanner change; lets all tasks
   share one daemon instance.

Until one of those lands, parallel benchmarking is unsafe.
