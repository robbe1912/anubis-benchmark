# Task 06 — C# ASP.NET Core REST API with Entity Framework Core

## Prompt (sent to qwen2.5-coder:7b)

> Create a C# ASP.NET Core Web API for managing a book library. Define a Book model with EF Core Code First. Create a DbContext called LibraryDbContext. Implement a BooksController with GET, POST, PUT, DELETE endpoints. Use async EF Core methods. Include Program.cs with DI registration.

## Expected hallucinations

- Wrong EF Core namespace (`System.Data.Entity` vs `Microsoft.EntityFrameworkCore`)
- Sync method on async context (`FirstOrDefault` without `await FirstOrDefaultAsync`)
- `SaveChanges()` instead of `SaveChangesAsync()`
- Missing `await` keyword on async calls
- Wrong DI registration (`AddDbContext` without `UseSqlServer`/`UseInMemoryDatabase`)
- Fabricated extension methods (`context.Books.AddAsync` — `Add` is standard for tracked entities)
- Missing `using` statements for common namespaces
- Wrong return type patterns (`IActionResult` vs `ActionResult<Book>`)
- `[HttpPost]` with `[FromBody]` on simple types
- Invented `ModelState.IsValid` patterns without proper validation setup

## Build

```
dotnet build
```

## Project skeleton

`.csproj` with `Microsoft.EntityFrameworkCore.SqlServer` or `Microsoft.EntityFrameworkCore.InMemory`.
