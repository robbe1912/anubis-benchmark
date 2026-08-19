# Task 12 — Python FastAPI service with Pydantic v2 + httpx

## Prompt (sent to glm-5-turbo)

> Create a Python FastAPI service using Pydantic v2 models. Define `UserCreate` and `UserResponse` Pydantic models with field-level validators (validate email format with `field_validator`, normalize username to lowercase). Implement async endpoints using `async def` and `httpx.AsyncClient` for upstream calls to a placeholder user service at `https://api.example.com/users`. Use FastAPI dependency injection for a fake database session. Wire a `BackgroundTasks` job that writes a log entry after user creation. Include endpoints: POST /users (create + background task + upstream enrichment), GET /users/{user_id} (calls upstream service).

## Expected hallucinations

- Wrong Pydantic v2 validator syntax — using deprecated `@validator` instead of `@field_validator`, missing `@field_validator("field")` decorator argument, returning `cls.v` instead of `v`
- Invented FastAPI dependencies — `Depends(Dependency)` with wrong provider signature, `fastapi.Depends` confused with `inject`, hallucinated `fastapi.dependency_provider`
- Wrong `httpx.AsyncClient` patterns — `httpx.get` (sync) used inside `async def`, `client.get()` without `await`, missing `async with httpx.AsyncClient()` context manager
- `BackgroundTasks` wrong API — `BackgroundTasks().add_task` vs `background_tasks.add_task`, invented `BackgroundTask` (singular) class with different API
- Pydantic v1 vs v2 confusion — `class Config:` instead of `model_config`, `.dict()` instead of `.model_dump()`, `.parse_obj()` instead of `.model_validate()`
- Invented `pydantic.EmailStr` import path, wrong `Annotated` usage for constraints

## Build

```
python -m py_compile main.py
```

## Project skeleton

`requirements.txt` with:

```
fastapi
pydantic>=2
httpx
uvicorn[standard]
```
