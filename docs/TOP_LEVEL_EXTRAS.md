# Top-level compiler metadata

The common metadata API is exported directly by `asmpython`:

```python
from asmpython import Public, access, C, abi, const, owned

@access(Public)
def add(left: int, right: int) -> int:
    return left + right
```

`asmpython.extras` remains available as a compatibility namespace, including
`asmpython.extras.access`, `asmpython.extras.abi`,
`asmpython.extras.threading`, and `asmpython.extras.interrupts`.

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
