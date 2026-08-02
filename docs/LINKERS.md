# Adding a toolchain

A backend produces **artifacts** — assembly, C, object bytes. A **toolchain**
turns those into a program you can run.

That is not the backend's job. The same x86-64 assembly is assembled by gas on
Linux, by gas-for-COFF on Windows, and linked by ld, lld, or a C driver
standing in for either; making the backend own it means every backend growing
a copy of the same toolchain search.

## The whole thing

```python
from asmpython.link import Toolchain, LinkRequest, LinkError, find_tool, run, register

class BareMetal(Toolchain):
    name = "bare-metal"
    description = "link with a linker script and no libc"

    def supports(self, target):
        return target.os == "none"

    def link(self, request: LinkRequest) -> Path:
        ld = find_tool(("ld.lld", "ld"), what="linker")
        objs = []
        for name, data in request.artifacts.items():
            path = request.workdir / name
            path.write_bytes(data)
            objs.append(str(path))
        run(request, [ld, "-T", "link.ld", *objs, "-o", str(request.output)],
            what="linking")
        return request.output

register(BareMetal())
```

Then:

```bash
asmpython build prog.py --toolchain bare-metal
asmpython toolchains                    # lists it
```

## What ships

| name   | what it does |
|--------|--------------|
| `cc`   | hands everything to `gcc`/`clang`. Assembles `.s`, compiles `.c`, links, and finds the system libraries. The default. |
| `none` | writes the artifacts and stops. What `--emit` means. |

`cc` uses a C compiler driver rather than calling `as` and `ld` directly
because the driver knows where `crt1.o`, libc and the dynamic loader live on
*this* machine. Reproducing that search is both the hardest part of linking and
the part with no portable answer — hand-written `ld` invocations work on the
machine they were written on.

## The request

```python
request.artifacts        # {filename: bytes} from the backend
request.target           # object_format, object_suffix, executable_suffix
request.output           # where the program goes
request.workdir          # for intermediates
request.extra_inputs     # --link-input: objects, archives, -l names
request.runtime_sources  # runtime the frontend needs (see below)
request.commands         # append what you run; --verbose prints it
```

Use `run(request, argv, what=...)` rather than `subprocess` directly. It
records the command line whether it succeeds or fails — when a build breaks,
the exact invocation is the first thing anyone wants, and a command
*described* in an error message drifts from the one that ran.

## Failures are diagnostics

Raise `LinkError`, not a bare exception:

```python
raise LinkError("no assembler found",
                detail="looked for: as, gas, clang",
                help="install binutils, or pass --toolchain none")
```

A missing assembler is an ordinary state for a machine to be in, not an
internal error. `find_tool` already does this, and names everything it tried —
"gcc not found" is unhelpful when `cc` or `clang` would have worked equally.

## The runtime

The IR has no I/O opcodes: `print` is a call to a named function, and
something has to define it. `asmpython.link.runtime` supplies a small C file for the
Python frontend, and the driver adds it when the backend is not
`self_contained`.

```python
class MyBackend(Backend):
    self_contained = False   # my artifacts need the runtime linked in
```

The C backend sets `True` — it emits its own `main` and its own `print_int`.
A machine backend sets `False`. Getting it wrong produces a duplicate-symbol
error or an undefined one, both at link time and both clear.

The IR's `main` is emitted under `ENTRY_SYMBOL` (`asmpython_main`), because it is
not C's `main` — it returns i64 where C requires int, and would collide with
the runtime's entry point. That constant lives in `asmpython.backend.base` so the
backend writing the symbol and the runtime calling it cannot disagree.

## Checklist

- [ ] `name` and `description` set; `register()` called
- [ ] `supports(target)` returns False for platforms you cannot produce
- [ ] every external command goes through `run(request, ...)`
- [ ] every failure a user can act on is a `LinkError` with `help`
- [ ] the produced program runs, and its output matches `asmpython run` on the same
      source
