# Task 21 — Go concurrent web scraper with worker pool

## Prompt (sent to glm-5-turbo)

> Build a Go concurrent web scraper (single package `main`, file `main.go`). Define a `Scraper` struct holding an `*http.Client`, a `sync.WaitGroup`, a buffered `chan string` for job URLs, and an `int32` atomic counter for completed fetches. Spawn a fixed-size worker pool (8 goroutines) reading URLs from the jobs channel; each worker calls `client.Do(req)`, checks `resp.StatusCode == 200`, defers `resp.Body.Close()`, fully reads the body via `io.ReadAll`, and writes the byte length into a `sync/atomic.Int64` total. Use `context.WithTimeout(context.Background(), 30*time.Second)` for the overall deadline; pass `req.WithContext(ctx)` to each request. Implement graceful shutdown: a `signal.NotifyContext` goroutine cancels the context on SIGINT, workers select on `ctx.Done()` to exit, and the main goroutine closes the jobs channel after all sends then calls `wg.Wait()`. Add a `sync/atomic.AddInt64`-based failure counter. After shutdown, print `success=N failure=M bytes=B` using `fmt.Printf` with `%d`. Include `main()` that seeds the jobs channel with 50 URLs of the form `https://httpbin.org/delay/1?id=%d`.

## Expected hallucinations

- `context.WithCancel` used where deadline-based `WithTimeout`/`WithDeadline` was requested, or wrong signature for `WithTimeout` (missing `time.Duration`).
- Invented `sync.WaitGroup` methods: `wg.AddChannel`, `wg.DoneAsync`, `wg.WaitTimeout`, `wg.Go(func)` (WaitGroup is not errgroup/`go pool` helper).
- Wrong channel direction syntax: declaring `chan<- string` then receiving from it, or `<-chan` then sending.
- `http.Client.Do` error handling skipped — direct `resp.Body` deref without nil check on err.
- Confusion between `atomic.Int64` (type, method `.Add(int64)`, `.Store`, `.Load`) and `atomic.AddInt64(*int64, int64)` (free function with pointer). Mixing the two: declaring `atomic.Int64` then calling `atomic.AddInt64(&counter)` on it.
- `req.WithContext` called on a `*http.Request` that was constructed without context (should be `http.NewRequestWithContext`), or attempting `req.Context = ctx` (field is not exported/settable).
- `signal.NotifyContext` used as if it returns `(<-chan os.Signal, context.CancelFunc)` (the old `signal.Notify` shape) instead of `(context.Context, context.CancelFunc)`.
- `defer resp.Body.Close()` placed before the error check, panicking on nil `resp`.
- `io.ReadAll` confused with `ioutil.ReadAll` (removed in Go 1.16+) or invented `io.ReadBytes`.
- `select { case <-ctx.Done(): case job := <-jobs: }` written as `select { case ctx.Done(): case jobs: }` (missing receive operators).
- `wg.Done()` called from main instead of worker, or `wg.Add` after worker spawn (race).
- `sync/atomic` package imported as `sync/atomic.Int64` without importing `sync/atomic` (typed atomic vs function API confusion).
- Invented `chan string.Buffer` parallel-channel pattern, or returning `[]byte` from a goroutine via shared mutable slice without synchronization.

## Build

```
go build ./...
```

## Project skeleton

None needed — single file `main.go` in package `main`.
