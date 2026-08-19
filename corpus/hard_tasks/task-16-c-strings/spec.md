# Task 16 — C string processing utility

## Prompt (sent to glm-5-turbo)

> Build a C string-processing utility (single-file `main.c`, C11). Read a text file line by line from `argv[1]`. For each line, tokenize with `strtok_r` on whitespace and punctuation. Count word frequencies using a hash table you implement yourself. Implement a `str_replace(const char *haystack, const char *needle, const char *replacement)` function that returns a freshly `malloc`'d string with the first occurrence replaced. Use `snprintf` to format the final output line `"%-20s %u\n"` for each word. Use `regex.h` (`regcomp`, `regexec`, `regfree`) to detect lines starting with a digit. Use `tolower` and `isspace` from `ctype.h` for normalization. After processing all lines, print the word frequencies sorted by count descending. Free all allocations.

## Expected hallucinations

- Wrong `strtok_r` signature (confusing with `strtok` or returning `int`).
- Invented `string.h` functions: `str_replace` treated as standard library call, `strcasestr`, `strdup` used without POSIX define, `strlcpy`/`strlcat` (BSD-only), `g_str_has_prefix` (GLib leak).
- Wrong `regex.h` API: `regex_compile` instead of `regcomp`, `regmatch` instead of `regexec`, missing `REG_EXTENDED` flag, returning `int` from `regexec` without checking `REG_NOMATCH`, forgetting `regfree`.
- `snprintf` return value misuse (treats return as bytes written instead of bytes that *would* be written; off-by-one buffer sizing).
- Wrong `strlen`/`strncpy` patterns: `strncpy` without manual NUL terminator, `strncat` size argument confusion, `strlen` used as allocation size without `+1`.
- Memory leaks: `regfree` skipped, hash-table buckets not freed, `str_replace` result never freed.
- `itoa` (non-standard) used for int-to-string conversion.
- `printf` format spec `%u` paired with `int`, or `%d` paired with `size_t`.
- `malloc` without `NULL` check, `free()` after early `return` skipping cleanup.
- File handle leak: `fclose` missing on error path.

## Build

```
gcc -std=c11 -O2 main.c -o main
```

## Project skeleton

None needed — single file `main.c`.
