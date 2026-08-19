# Task 07 — C++ thread-safe task queue with STL

## Prompt (sent to qwen2.5-coder:7b)

> Create a C++ thread-safe task queue using std::mutex and std::condition_variable. Define a Task struct with id (int), description (string), priority (int). Implement a TaskQueue class with push, pop (blocking), try_pop, and size methods. Use std::thread to spawn 2 worker threads. Include a main() that demonstrates pushing and popping tasks.

## Expected hallucinations

- Wrong STL headers (`<queue>` included but `<thread>` missing)
- Double-lock deadlock (`std::lock_guard` then `mtx.lock()` inside same scope)
- Wrong container methods (`push_back` on `std::queue` — should be `push`)
- `std::move` on non-movable types or unnecessary move on pass-by-value
- Missing `#include <condition_variable>`
- Wrong `condition_variable::wait` signature (missing predicate lambda)
- `std::optional<Task>::get()` — should be `.value()` or `*`
- C-style cast `(int)task.priority` instead of `static_cast<int>`
- `std::this_thread::sleep(ms)` — wrong API, should be `sleep_for(duration)`

## Build

```
g++ -std=c++17 -Wall -Werror -pthread -o taskqueue main.cpp
```
