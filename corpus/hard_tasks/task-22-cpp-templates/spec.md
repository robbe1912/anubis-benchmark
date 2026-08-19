# Task 22 — C++17 type-safe event system with variadic templates

## Prompt (sent to glm-5-turbo)

> Build a C++17 type-safe event system in a single header `event_emitter.hpp`. Define a class template `EventEmitter<EventTypes...>` that stores handlers in a `std::variant<EventTypes...>` dispatch table. Implement `template <typename Event, typename F> void on(F&& handler)` that registers a handler keyed by `std::is_same_v<Event, EventTypes>` fold-expression over the pack. Implement `template <typename Event> void emit(Event&& evt)` that perfect-forwards via `std::forward<Event>(evt)` to all matching handlers. Use `std::visit` with an `overloaded` helper built via fold-expression: `template <typename... Fs> struct overloaded : Fs... { using Fs::operator()...; };`. Add a `std::get_if<std::vector<Handler<Event>>>(&storage_)` lookup guarded by `if constexpr (std::is_same_v<Event, EventTypes>)` over the pack. Provide a `clear<Event>()` that resets only the matching alternative. Use `static_assert(sizeof...(EventTypes) > 0)` to forbid empty event lists. Cover with a `main()` in a separate `main.cpp` that emits three event types (`PlayerMoved{int x,y}`, `ScoreChanged{int delta}`, `GameEnded{}`), registers 2 handlers per event via lambdas, fires 5 events, and prints a per-handler count.

## Expected hallucinations

- `std::visit` called without the visitor as the first argument, or with the variant/visitor order swapped: `std::visit(variant, visitor)` instead of `std::visit(visitor, variant...)`.
- Invented `std::variant` methods: `.get<T>()`, `.as<T>()`, `.cast<T>()`, `.is<T>()` (correct API is `std::get<T>(v)`, `std::get_if<T>(&v)`, `std::holds_alternative<T>(v)`).
- Wrong fold expression syntax: `(std::is_same_v<Event, EventTypes> || ...)` written without parens, or `(... || std::is_same_v<...>)` with reversed pack-position for unary left fold vs right fold.
- Missing `template` disambiguator: `Fs::operator()...` written as `using Fs::operator()...` without `template` keyword when `Fs` is a dependent pack — should be `using Fs::operator()...;` (correct) but commonly hallucinated as `Fs::template operator()...` or simply `Fs::operator()` without pack expansion.
- `std::forward<Event>(evt)` written as `std::forward(evt)` (missing type), `std::move(evt)` instead (incorrect for perfect forwarding), or applied to a non-deduced `auto&&` parameter without explicit template argument.
- Invented `std::get_if` overloads: `std::get_if<T>(v)` taking the variant by value (correct is by pointer `&v`); `std::get_if<T>(&v)` used in a context that returns `nullptr`-without-check.
- `if constexpr` written without `constexpr`, or `if constexpr (std::is_same<Event, EventTypes>::value || ...)` (pre-C++17 form inside constexpr).
- `EventEmitter<EventTypes...>::on<Event, F>` declared without `template <typename Event>` (relying on deducing `Event` from `F`, which fails for generic lambdas).
- Class template specialization abuse: hand-rolling `EventEmitter<int>`, `EventEmitter<std::string>` instead of using the pack.
- `std::variant<EventTypes...>` indexed via `v.index()` compared against `boost::mpl::index_of` or invented `std::variant_index_v<Event, EventTypes...>`.
- Missing `#include <variant>`, `#include <type_traits>`, `#include <utility>` (for `std::forward`), or wrong header `<boost::variant>` (mixing Boost.Variant).
- `static_assert(sizeof...(EventTypes) > 0, "msg")` written without the message argument (allowed in C++17 but commonly written as a single-arg form pre-C++17).
- `overloaded` helper written as a class without the `Fs...` pack expansion in base classes, e.g. `struct overloaded { template <typename F> overloaded(F&&); }`.

## Build

```
g++ -std=c++17 -Wall -Wextra -c event_emitter.hpp
g++ -std=c++17 -Wall -Wextra -pthread -o events main.cpp
```

## Project skeleton

None needed — two files: `event_emitter.hpp` (template definitions) and `main.cpp` (driver).
