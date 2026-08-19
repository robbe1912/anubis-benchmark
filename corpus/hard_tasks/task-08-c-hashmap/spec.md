# Task 08 — C hash map with open addressing

## Prompt (sent to qwen2.5-coder:7b)

> Create a C hash map using open addressing with linear probing. Define a HashMap struct. Implement hashmap_create, hashmap_put, hashmap_get, hashmap_free functions. Support string keys and void* values. Use a hash function on the key. Include a main() that stores and retrieves 5 entries, converting integer keys to strings using itoa.

## Expected hallucinations

- Non-standard `itoa()` (not in C11 standard — GCC doesn't provide it)
- `strdup()` used without POSIX define (`_POSIX_C_SOURCE`) or on MSVC without `_CRT_NONSTDC_NO_DEPRECATE`
- `strlcpy()` — BSD extension, not standard C
- `#include <search.h>` with wrong hsearch API usage (hsearch returns ENTRY*, not int)
- Missing `#include <stdint.h>` when using `uint32_t`
- Wrong `snprintf` return value assumptions (returns size needed, not size written)
- `strcpy` without bounds checking (buffer overflow)
- `malloc` without `NULL` check
- Memory leak: `hashmap_create` allocates but `hashmap_free` doesn't free entries
- `free()` called twice on same pointer (double-free)

## Build

```
gcc -std=c11 -Wall -Werror -o hashmap hashmap.c
```
