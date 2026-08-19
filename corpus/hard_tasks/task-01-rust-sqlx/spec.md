# Task 01 — Rust async SQLite with sqlx + migrations

## Prompt (sent to qwen2.5-coder:7b)

> Create a Rust async CLI using sqlx with SQLite. Define a User struct with FromRow derive. Create functions: create_user, get_user_by_id, list_users. Use sqlx::query_as! macro. Run migrations from migrations/ directory.

## Expected hallucinations

- Wrong sqlx API (sync vs async confusion — e.g. `sqlx::query` instead of `sqlx::query_as!`)
- Fabricated query macros
- Wrong FromRow derive patterns
- Made-up `Pool<Sqlite>` methods
- Invented `migrate!` macro paths

## Build

```
cargo build
```

## Project skeleton

`Cargo.toml` with `sqlx = { version = "0.7", features = ["runtime-tokio", "sqlite", "macros", "migrate"] }`.

A `migrations/` directory with one SQL file (`0001_init.sql`) that creates a `users` table so the model can rely on it.
