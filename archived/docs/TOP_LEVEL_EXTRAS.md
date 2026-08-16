# Top-level compiler metadata

The common metadata API is exported directly by `asmpython`:

```python
from asmpython import Public, access, C, abi, const, owned

@access(Public)
def add(left: int, right: int) -> int:
    return left + right
```

The same decorators live in `asmpython.annotations` (`asmpython.annotations.access`,
`asmpython.annotations.abi`, `asmpython.annotations.threading`,
`asmpython.annotations.interrupts`). `asmpython.extras` is a deprecated alias
of `asmpython.annotations`, kept importable for compatibility.

## Native library exports

For native library builds, `@access(Public)` automatically publishes the
object using the ABI inferred from its annotations and compiler-known types.
An explicit `@abi(...)` declaration overrides the inferred ABI and also marks
the object for export.

```python
from asmpython import Public, access, C, abi

@access(Public)
def inferred(value: float) -> float:
    return value * 2.0

@abi(C)
def explicit(value: int) -> int:
    return value + 1
```

Public classes publish their class-ID object, compiled method symbols, and
materialized class-variable symbols. Public methods on non-public classes are
published individually.
