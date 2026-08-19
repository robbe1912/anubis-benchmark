# Task 18 — Rust async web service with axum 0.8 + tower + trait objects + thiserror

## Prompt (sent to glm-5-turbo)

> Create a Rust async web service with axum 0.8. Define a `Repository` trait with async methods (`async fn get_user(&self, id: &str) -> Result<Option<User>, AppError>; async fn list_users(&self) -> Result<Vec<User>, AppError>; async fn save_user(&self, user: &User) -> Result<(), AppError>`). Implement `PostgresRepo` (stubs using a `sqlx::PgPool` field, real sqlx queries) and `MemoryRepo` (uses `Arc<RwLock<HashMap<String, User>>>` internally). The trait MUST be usable as `Arc<dyn Repository>` for dynamic dispatch — handle object safety by boxing futures (`Pin<Box<dyn Future<Output = ...> + Send>>`) when needed, or use the `async-trait` crate. Build a tower middleware layer called `TimingLayer` that logs request duration; implement `tower::Service` for a wrapping `TimingMiddleware<S>` and call the inner service correctly. Define an `AppError` enum with thiserror derive (`#[derive(Error)]`) containing `NotFound(String)`, `Database(sqlx::Error)`, `InvalidInput(String)`, and implement `IntoResponse` for it mapping to HTTP 404 / 500 / 400 respectively with a JSON body. Wire it all into an axum `Router` with routes `GET /users`, `GET /users/:id`, `POST /users` and an `Arc<dyn Repository>` shared via `axum::extract::State`. Bind to `0.0.0.0:3000` using `axum::serve`. Emit fenced code blocks each prefixed with a `// File: path/to/file.rs` comment.

## Expected hallucinations

- Wrong axum 0.8 router API — `Router::new().route("/", handler)` instead of `.route("/", get(handler))`, missing `axum::routing::{get, post}`, `app.router(...)` instead of `Router::new().route(...)`, `axum::Server::bind` (0.6 API) instead of `axum::serve(listener, app)`
- Wrong tower `Service` trait — implementing `fn call(&self, ...)` instead of `fn call(&mut self, req)`; missing `poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>>`; inventing `Service::request()`, `Service::handle()`, `tower::Layer::service()` (correct: `layer(&self, inner: S) -> Self::Service`)
- Wrong thiserror syntax — `#[error("{0}")]` on tuple variants written as `#[err("...")]`, missing `#[derive(Error)]`, `#[error = "..."]` instead of `#[error("...")]`, using `display("...")` instead of `#[error("...")]`
- Async trait object-safety errors — declaring `async fn` directly on the trait then using `dyn Repository` (object-unsafe in stable Rust without boxing), missing `+ Send` on `Pin<Box<dyn Future<...>>` return, missing `#[async_trait]` macro, returning `impl Future` from trait method (object-unsafe)
- Wrong `Arc<dyn Repository>` syntax — `Arc<Repository>`, `Arc<dyn Repository + Sync>` without `Send`, `Arc<impl Repository>`, `Rc<dyn Repository>` (not Send for axum state)
- Invented trait methods — `Repository::find_by_id`, `Repository::insert`, `Repository::upsert` (when prompt only named `get_user`, `list_users`, `save_user`)
- Wrong `IntoResponse` impl — `fn into_response(self) -> Self::Response`, `impl IntoResponse for &AppError`, returning `String` instead of `Response`, calling `Response::new(body)` without status code, `Json(self).into_response()` without status override
- `Pin<Box<dyn Future>>` misuse — `Box::pin(async move {...})` missing `Box::pin`, wrong `Pin<Box<...>>` type annotation, missing `+ Send` bound, `Box<dyn Future<Output = T>>` without `Pin`
- Wrong `tower::Layer` impl — `impl<S> Layer for TimingLayer` instead of `impl<S> Layer<S> for TimingLayer`, missing associated `type Service`, inventing `Layer::wrap` or `Layer::apply`
- axum 0.8 `State` extractor — `axum::extract::State<T>` vs `axum::State<T>` path confusion, `State<Arc<dyn Repository>>` instead of `State<Arc<dyn Repository>>` with wrong generic arity

## Build

```
cargo check
```

## Project skeleton

`Cargo.toml` with:

```toml
[dependencies]
axum = "0.8"
tokio = { version = "1", features = ["full"] }
tower = { version = "0.5", features = ["full"] }
tower-http = { version = "0.6", features = ["trace"] }
sqlx = { version = "0.8", features = ["postgres", "runtime-tokio"] }
thiserror = "1"
async-trait = "0.1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tracing = "0.1"
tracing-subscriber = "0.3"
```
