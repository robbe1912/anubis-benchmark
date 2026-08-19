# Task 09 — C# MediatR + Serilog + FluentValidation Pipeline

## Purpose

Second C# benchmark task to assess overfitting of `CSHARP_KEYWORDS` expansion
(commit 6e9615d). Uses DIFFERENT libraries from task-06 (EF Core + ASP.NET):

- **MediatR** — IRequest, INotificationHandler, Mediator pipeline
- **Serilog** — structured logging, LogContext, ILogger integration
- **FluentValidation** — AbstractValidator, IRuleBuilderOptions
- **Polly** — resilience, CircuitBreakerPolicy, AsyncRetryPolicy

None of these types appear in `CSHARP_KEYWORDS`. If the benchmark shows the
SAME categories of FP as task-06 (BCL type not cached, framework type flagged),
the skip-list generalizes poorly and should be replaced with live fetchers.
If FPs are CONFINED to MediatR/Serilog/Polly/FluentValidation types (correctly
flagged because they're not in the cache), the architecture is sound and the
fix is fetcher coverage, not keyword list expansion.

## Prompt (sent to glm-5-turbo)

> Create a C# .NET 8 console application that processes orders through a MediatR pipeline. Define a CreateOrderCommand implementing IRequest<OrderResult>. Write a CreateOrderHandler using Serilog for structured logging (LogContext, ILogger) and FluentValidation for input validation (AbstractValidator). Add a Polly retry policy wrapping the handler (3 retries with exponential backoff). Register MediatR + Serilog + FluentValidation in Program.cs via Microsoft.Extensions.DependencyInjection.

## Expected hallucinations

- Wrong MediatR namespace (`MediatR.Requests` vs `MediatR`)
- `IRequestHandler<T>` without `IRequest<T>` pairing
- Sync `Handle` method instead of `async Task<T> Handle`
- Fabricated `AbstractValidator.Validate` return type (`bool` vs `ValidationResult`)
- Serilog `Log.Logger` property misuse (it's `Log.Information`, not `Log.Write`)
- FluentValidation `RuleFor` lambda returning wrong type
- Polly `Policy.Handle<T>` with wrong overload signature
- Wrong DI registration (`services.AddMediatR()` without assembly param)
- Missing `MediatR.IRequest` interface implementation
- Invented `IMediator.SendAsync` (it's `Send`, returns Task<T>)
- `[FromServices]` in console app context (ASP.NET Core only)

## Build

```
dotnet build
```

## Project skeleton

`.csproj` with: `MediatR`, `Serilog.Extensions.Hosting`, `Serilog.Sinks.Console`,
`FluentValidation`, `Polly`, `Microsoft.Extensions.DependencyInjection`,
`Microsoft.Extensions.Hosting`.
