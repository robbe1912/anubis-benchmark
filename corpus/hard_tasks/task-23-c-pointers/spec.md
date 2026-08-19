# Task 23 — C hash map with function pointers and dynamic resize

## Prompt (sent to glm-5-turbo)

> Build a C11 generic hash map in a single file `hashmap.c` plus its header `hashmap.h`. Define a `HashMap` struct with `size_t size`, `size_t capacity`, `HashEntry *buckets`, plus two function-pointer fields: `size_t (*hash_func)(const void *key)` and `int (*compare_func)(const void *a, const void *b)`. Define the typedefs `typedef size_t (*HashFunc)(const void *key);` and `typedef int (*CompareFunc)(const void *a, const void *b);`. Implement `HashMap *hashmap_create(HashFunc hash, CompareFunc cmp, size_t initial_capacity)` using `calloc` for the buckets array. Implement `void hashmap_free(HashMap *hm)` that frees each entry's key and the buckets array, then the struct itself (no double-free). Implement `void *hashmap_get(const HashMap *hm, const void *key)` returning `NULL` when missing. Implement `int hashmap_set(HashMap *hm, void *key, void *value)` that copies the key via a caller-supplied `key_copy` callback is NOT allowed — instead, the map takes ownership of `void *key`. When `size * 100 / capacity >= 75` (the `LOAD_FACTOR` macro, defined as `75`), call `hashmap_resize` to double the capacity and rehash all entries (open addressing, linear probing: on collision, probe `(hash + i) % capacity` for `i = 1..capacity-1`). Mark deleted entries with a tombstone flag so `hashmap_get` continues probing past them but `hashmap_set` may reuse them. Provide `main()` that registers a string-keyed map (`djb2` hash + `strcmp` wrapper) storing `int *` values, inserts 1000 entries, looks up 5, prints counts via `printf("size=%zu capacity=%zu\n", hm->size, hm->capacity);`, then frees everything.

## Expected hallucinations

- `malloc(sizeof(HashMap))` without `NULL` check, or `calloc(n, sizeof(X))` invoked as `calloc(sizeof(X), n)` (swapped order — undefined behavior on overflow).
- Function-pointer typedef syntax errors: `typedef size_t (*)HashFunc(const void*);` (incorrect token order) instead of `typedef size_t (*HashFunc)(const void*);`.
- Function-pointer call syntax: `hm->hash_func(key)` written as `hm->hash_func`(parens missing) or `*hm->hash_func(key)` (wrong deref) or `hm.hash_func(key)` (using `.` on a pointer to struct).
- `void*` dereference errors: `*value = 42;` where `value` is `void*`, or arithmetic `ptr + i` on a `void*` (GCC extension but UB per standard).
- `size_t` format specifier mistakes: `printf("%d", hm->size)` (should be `%zu`), `printf("%lu", ...)` on a platform where `size_t` is `unsigned long long`.
- Invented `realloc` pattern: `arr = realloc(arr, new_size * 2);` (leaks old array on failure; correct is `tmp = realloc(arr, ...); if (tmp) arr = tmp;`).
- Tombstone handling: marking `entry->key = NULL` for both empty and deleted without a separate `is_deleted` flag, so `hashmap_get` stops probing at deleted entries (silently drops keys past tombstone).
- Rehash bug: iterating `buckets` after `realloc` invalidated the pointer (`HashMap *new_hm = realloc(hm, ...); /* then iterate hm->buckets */`).
- Double-free: `hashmap_free` calling `free(entry->key)` then iterating over buckets that still point at freed keys after resize.
- Modulo on negative hash: `idx = (hash + i) % capacity` without `+ capacity` when hash is signed `long` (signed modulo can yield negative).
- `LOAD_FACTOR` macro: `#define LOAD_FACTOR 0.75` compared via `size * LOAD_FACTOR > capacity` (float arithmetic on integers — fine but commonly written as `size > capacity * LOAD_FACTOR` which loses precision), or macro defined as `75` but used in a `0..1` comparison.
- `strcmp` wrapper returning `int` declared as `CompareFunc` but body returns `bool` (signature mismatch — function pointer incompatible).
- `hashmap_set` not handling the case where the key already exists (creating a duplicate entry instead of replacing the value).
- `entry->key = key;` aliasing caller's pointer — caller then frees the same memory, leading to use-after-free in `hashmap_free`.
- Missing `#include <stddef.h>` for `size_t`, or `#include <string.h>` for `strcmp`.
- `calloc` return cast: `HashMap *hm = (HashMap*)calloc(...)` is correct C, but `HashMap hm = *calloc(...)` (deref of allocated pointer) is a hallucinated pattern.

## Build

```
gcc -std=c11 -Wall -Wextra -c hashmap.c
gcc -std=c11 -Wall -Wextra -o hashmap hashmap.c main.c   # if main is split
```

## Project skeleton

None needed — two files: `hashmap.h` (struct + function prototypes + typedefs) and `hashmap.c` (implementation + `main()`).
