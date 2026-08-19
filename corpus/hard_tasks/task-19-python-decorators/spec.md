# Task 19 — Python plugin system with importlib.metadata + decorators + Pydantic v2 + match

## Prompt (sent to glm-5-turbo)

> Build a Python plugin system. Use `importlib.metadata.entry_points` to discover plugins in group `myapp.plugins`. Define an abstract base class `PluginBase(ABC)` with `@abstractmethod` methods `validate(self) -> bool` and `load(self) -> None`, plus a `name: str` attribute. Write a class decorator `@register_plugin(name: str)` that registers the decorated class into a module-level `_REGISTRY: dict[str, type[PluginBase]]` dict (use `functools.wraps` correctly — note: classes use `functools.wraps` differently than functions, you need `update_wrapper` or copy `__wrapped__`). Define a Pydantic v2 `PluginConfig(ConfigBase)` model with `model_validator(mode="after")` that cross-validates `enabled` and `priority` fields; use `model_config = ConfigDict(extra="forbid")` not the legacy `class Config:` inner class. Provide a `discover()` function that loops over `entry_points(group="myapp.plugins")` (Python 3.12+ signature), imports each via `importlib.import_module`, and returns the loaded plugin classes from `_REGISTRY`. Add a `dispatch(event_name: str, payload: dict)` function that uses **structural pattern matching** (`match`/`case` with guards) on `event_name` to route `"create"`, `"update"`, `"delete"` events to the matching plugin method, with a default `case _:` fallthrough. Emit fenced code blocks each prefixed with `# File: path/to/file.py`.

## Expected hallucinations

- Wrong `importlib.metadata.entry_points` API — using `entry_points()["myapp.plugins"]` (3.9 dict form) when 3.12+ returns a set-like object with `.select(group=...)`; calling `entry_points().get(group=...)` (dict method does not exist on EntryPoints); inventing `entry_points.iter_entry_points()` (that's setuptools, not stdlib); forgetting to handle both 3.9 and 3.12+ signatures
- Pydantic v2 `model_validator` syntax — `@model_validator` without `mode=`, using `@model_validator(pre=True)` (Pydantic v1 style), `@validator("field")` (v1 deprecated), wrong return type (`-> Self` missing or returning `dict` instead of model instance in `mode="after"`), `model_validator(mode="post")` (does not exist, only `before|after|wrap`)
- ABC `@abstractmethod` misuse — `@abstractmethod def validate():` without `self`, missing `ABC` base class (so abstractmethod has no effect), `@abstract` instead of `@abstractmethod`, instantiating ABC directly without subclassing
- Wrong decorator return — class decorator returning a function instead of a class, missing `return cls`/`return wrapper`, `@functools.wraps(cls)` (wraps is for functions, not classes — for classes you need `functools.update_wrapper(wrapper, cls, updated=())` or similar), decorator applied to instance methods when designed for classes
- Invented `entry_points` methods — `entry_points.load()`, `entry_points.resolve()`, `ep.distribute()`, hallucinated `EntryPoint.dist_info`, treating `EntryPoint` as a class instead of namedtuple (real attrs: `name`, `group`, `value`)
- Wrong `@dataclass`/field syntax — `@dataclass` with `field(default_factory=list)` written as `field(default=[])` (mutable default), `field(repr=False)` vs `field(repr="False")`, missing `from dataclasses import dataclass, field`
- Structural `match` guard syntax — `case "create" if condition:` written as `case "create" when condition:` (some languages use `when`), `case ["create", payload]:` for an event name string (type mismatch), `case _ :` with action vs `default:` (Python uses `case _:`), `match event_name:` written as `switch event_name:`, missing colons, `case (x, y):` destructuring when input is `str`
- `importlib.import_module` confusion — `importlib.import(ep.value)` (correct), `importlib.import_module(ep)` (passing EntryPoint instead of str), forgetting to traverse `ep.value` with `mod:attr = "..."` split
- Type annotation errors — `dict[str, type[PluginBase]]` written as `Dict[str, Type[PluginBase]]` without importing `Dict`/`Type` from typing, `list[PluginBase]()` (call on subscription), `Callable[..., T]` without `from typing import Callable`
- Pydantic v2 `model_config` confusion — `class Config:` (v1) instead of `model_config = ConfigDict(...)`, `Config` as inner class alongside `model_config`, missing `from pydantic import ConfigDict`

## Build

```
python -c "import ast; ast.parse(open('plugin_system.py').read())"
```

## Project skeleton

`requirements.txt` with:

```
pydantic>=2
```

`plugin_system.py` is the single deliverable.
