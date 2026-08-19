# Task 11 — Rust async REST API with Axum 0.7

## Prompt (sent to glm-5-turbo)

> Create a Rust async REST API with Axum 0.7. Implement CRUD endpoints for a Task entity (id, title, done). Use extractors (State, Path, Json) on handlers. Store shared state with Arc<RwLock<Vec<Task>>>. Implement custom AppError type that implements IntoResponse with proper status code mapping. Add CORS middleware using tower-http. Define a Router with `/api/tasks` routes (GET list, POST create, GET by id, PUT update, DELETE remove). Wire it into a Tokio runtime bound to 0.0.0.0:3000.

## Expected hallucinations

- Wrong extractor syntax (e.g. `axum::extract::State` vs `axum::extract::State<>` generic arity)
- Invented `axum::extract` types (e.g. `axum::extract::Body`, `axum::extract::QueryStream`)
- Wrong `IntoResponse` impl shape (missing `into_response`, wrong `Response` return type, calling `Response::new` with wrong builder)
- Missing Tower middleware traits (`tower::Service`, `tower_http::cors::CorsLayer::new()` confused with `Cors::new()`)
- Wrong `Router::route` chaining — `.route()` after `.nest()`, mixing `Router::new()` with `app.router()` style
- Invented `axum::Server::bind` (0.6 API) vs `axum::serve` (0.7 API) — version confusion
- Hallucinated `JsonExtractor` instead of `Json`, `PathExtractor` instead of `Path`

## Build

```
cargo build
```

## Project skeleton

`Cargo.toml` with:

```toml
axum = "0.7"
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tower-http = { version = "0.5", features = ["cors"] }
```
