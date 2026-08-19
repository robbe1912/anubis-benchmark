# Task 15 — C++ data processing pipeline with STL algorithms

## Prompt (sent to glm-5-turbo)

> Write a C++ data processing pipeline using STL algorithms. Define a Sales struct with fields: product (std::string), quantity (int), price (double), date (std::string). Read a vector of Sales records (you may hard-code sample data). Then perform: (1) sort by revenue (quantity*price) descending using std::sort or std::stable_sort with a custom comparator lambda; (2) use std::transform to compute and append a revenue field (or produce a parallel vector<double>); (3) use std::accumulate (note init value type must match, i.e. 0.0 not 0) to compute the total revenue; (4) use std::partition or std::remove_if to filter out records with quantity below 10; (5) use std::sort followed by std::unique plus the erase-unique-idiom to deduplicate by product name; (6) use std::copy with std::ostream_iterator to print the final formatted results to std::cout. Use lambda expressions throughout. Compile as a single main.cpp file. Include main() and all necessary headers.

## Expected hallucinations

- Wrong `std::sort` signature (missing comparator slot, or comparator as binary function but unary passed)
- Invented STL methods (`std::sort_descending`, `vec.sort()`, `std::filter`, `std::map_into`)
- Wrong iterator categories (passing `std::back_inserter` where a random-access iterator is required)
- Lambda capture confusion (`[=]` vs `[&]`, capturing this in a free function, capturing variables that don't exist)
- Wrong `std::accumulate` initial value type (`0` instead of `0.0` for double totals, causing integer truncation)
- Missing `#include <numeric>` for `std::accumulate`
- Missing `#include <iterator>` for `std::ostream_iterator` / `std::back_inserter`
- Missing `#include <algorithm>` for `std::sort`, `std::unique`, `std::partition`, `std::remove_if`, `std::transform`, `std::copy`
- `std::remove_if` without the erase idiom (using the iterator returned, not actually erasing from container — wrong size)
- `std::unique` without erase (typical `v.erase(std::unique(v.begin(), v.end()), v.end())` idiom missed)
- `std::transform` returning value into iterator not writing through `std::back_inserter`
- `std::accumulate` with wrong BinaryOp signature (returns same type as init)
- Comparators that aren't strict weak ordering (causing UB at runtime)
- `std::partition` returning iterator used as `v.end()` mistakenly
- `std::copy(v.begin(), v.end(), std::cout)` — missing `std::ostream_iterator` wrapper

## Build

```
g++ -std=c++17 -O2 main.cpp -o main
```

## Project skeleton

None needed — single file with `#include` headers (`<iostream>`, `<vector>`, `<string>`, `<algorithm>`, `<numeric>`, `<functional>`, `<iterator>`).
