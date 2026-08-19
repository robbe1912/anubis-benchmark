# Task 24 — C# async LINQ pipeline + Channel<T> + System.Text.Json polymorphic

## Purpose

Third+ C# benchmark task focusing on **BCL concurrent + serialization APIs** that
differ from task-06 (EF Core / ASP.NET), task-09 (MediatR/Serilog/Polly), and
task-10 (AutoMapper/NodaTime/CsvHelper). Targets:

- `IAsyncEnumerable<T>` + `await foreach` async streaming
- LINQ `GroupBy` + `Join` over async-collected data
- `System.Threading.Channels.Channel<T>` bounded producer/consumer
- `System.Text.Json` polymorphic serialization (`[JsonPolymorphic]`,
  `[JsonDerivedType]`, `JsonSerializerOptions` with `TypeInfoResolver`)

None of these patterns appear in earlier tasks. If FPs cluster on BCL types
(`Channel`, `ChannelWriter`, `JsonPolymorphicAttribute`) the scanner's BCL
coverage is the gap; if FPs cluster on LINQ method shapes, scope extraction is
the gap.

## Prompt (sent to glm-5-turbo)

> Build a .NET 8 C# async data pipeline. Define a `DataPipeline` class that consumes `IAsyncEnumerable<RawRecord>` from a source, groups records by `Category` using LINQ `GroupBy`, joins each group with an `ILookup<string, EnrichmentInfo>` (built from a `Join` over an enrichments list), pushes grouped+enriched results through a `System.Threading.Channels.Channel.CreateBounded<GroupedResult>(capacity)` to a consumer task, and serializes final results to JSON using `System.Text.Json` polymorphism with `[JsonPolymorphic]` + `[JsonDerivedType]` on a base `ResultBase` class (with derived `SuccessResult` / `ErrorResult`). Implement a producer method `ProduceAsync(IAsyncEnumerable<RawRecord>, ChannelWriter<GroupedResult>, CancellationToken)`, a consumer method `ConsumeAsync(ChannelReader<GroupedResult>, CancellationToken)`, wire them with `Task.WhenAll`, and propagate the `CancellationToken` everywhere. Use `await foreach (var item in source.WithCancellation(token).ConfigureAwait(false))`. Demonstrate polymorphic serialization via `JsonSerializer.Serialize<BaseResult>(results, options)` where `options = new JsonSerializerOptions { TypeInfoResolver = new JsonPolymorphismOptions() }` (or via attribute-declared polymorphism on `ResultBase`). Show `Channel.CreateBounded<GroupedResult>(100)` and proper `writer.TryComplete()` / `writer.WaitToWriteAsync(token)` usage. Include `RawRecord`, `EnrichmentInfo`, `GroupedResult`, `ResultBase`, `SuccessResult`, `ErrorResult` records/classes. Provide a `Program.Main` that wires it all together with `CancellationTokenSource`.

## Expected hallucinations

- Missing `IAsyncEnumerable` async-stream return type (`IEnumerable<Task<T>>` confusion)
- `await foreach` without `.WithCancellation(token)` or missing `await` keyword
- Invented LINQ extension: `source.GroupByAsync(...)`, `await source.Join(...)`
- Wrong `Channel.CreateBounded` signature: `new Channel<T>(capacity)` (no public ctor), `Channel.CreateBounded<T>(capacity, mode)` mode parameter misuse, `Channel.CreateBounded(capacity)` without `<T>`
- `ChannelWriter<T>.WriteAsync` (does not exist — it's `WriteAsync` on `Channel`, only `TryWrite` / `WaitToWriteAsync` on writer); inventing `writer.SendAsync(item)`
- `[JsonPolymorphic]` attribute wrong namespace (`System.Runtime.Serialization` instead of `System.Text.Json.Serialization`)
- Missing `[JsonDerivedType(typeof(SuccessResult))]` on base — relying on a non-existent `JsonPolymorphismOptions` resolver class
- `JsonSerializer.SerializeAsync` without `Stream` argument (overload confusion)
- `ConfigureAwait(true)` in library code (anti-pattern), or missing `ConfigureAwait(false)` entirely
- `CancellationToken` not threaded into `WaitToReadAsync` / `WaitToWriteAsync`
- `Channel.CreateBounded` returning `Channel<T>` but treated as `Channel<T>?` or `BoundedChannel<T>` (concrete impl is private)
- `Task.WhenAll` over `IAsyncEnumerable` directly (must materialize to `List<Task>` first)
- Inventing `writer.TryComplete(exception)` overload that doesn't exist on the unconfigured writer

## Build

```bash
dotnet build
```

## Project skeleton

`.csproj` net8.0 with no external packages required (all APIs are BCL):
`System.Threading.Channels` (in-box for net8.0), `System.Text.Json` (in-box).

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
```
