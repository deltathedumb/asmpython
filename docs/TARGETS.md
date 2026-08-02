# Adding a target

A **target** is a platform: an architecture, an operating system, a calling
convention, an object format. A **backend** is a code generator. They are
separate registries because they vary independently — one code generator
serves several platforms, and one platform can be reached by several code
generators.

Nothing in the compiler holds a list of platforms. `driver` asks the registry
for a target by name, so adding one is a registration.

## The whole thing

```python
from asmpython.target import Target, register

register(Target(
    "riscv64-linux",
    arch="riscv64",
    os="linux",
    abi="lp64d",
    object_format="elf",
    pointer_size=8,
    stack_alignment=16,
    object_suffix=".o",
    executable_suffix="",
), aliases=("rv64",))
```

Then:

```bash
asmpython build prog.py --target riscv64-linux --backend my-backend
asmpython targets                       # lists it, with its aliases
```

Built-in targets go through this same call. There is no privileged path — if
`asmpython/targets/__init__.py` were deleted, asmpython would still compile and simply
have no platforms until something registered one.

## Every field is explicit, and that matters

```python
Target(name, arch, os, abi, object_format,
       pointer_size, little_endian, stack_alignment,
       object_suffix, executable_suffix)
```

**Never parse `name` to decide behaviour.** Read the field that says what you
mean. The x86-64 backend used to choose its calling convention with
`"windows" in target.name`. That worked for the two targets that shipped and
silently gave System V to everything else — a target named `uefi-x64` would
have compiled, linked, and passed its arguments in the wrong registers, which
shows up as corrupted data inside a callee and nowhere near the call.

`abi` is the field a machine backend must not get wrong. It is a string the
backend interprets; a backend that does not implement the ABI a target
declares should refuse:

```python
try:
    return _ABIS[target.abi]
except KeyError:
    raise UnsupportedOperation(
        f"target {target.name!r} declares ABI {target.abi!r}, which this "
        f"backend does not implement")
```

Refusing is the point. A backend that falls back to a default produces a
program that runs and is wrong.

## `host`

`host` is not a registered target — it resolves to whichever platform asmpython is
running on:

```bash
asmpython build prog.py --target host      # or just omit --target
```

A machine backend should declare `default_target = "host"` rather than naming
a platform. The x86-64 backend defaulted to `x86_64-linux`, so `asmpython build
--backend x86-64` on Windows emitted ELF directives and handed them to a COFF
assembler — a cross-compile nobody asked for, reported as an assembler syntax
error.

## Overriding a built-in

Registering an existing name replaces it:

```python
register(Target("x86_64-linux", arch="x86_64", os="linux",
                abi="sysv", stack_alignment=32))
```

This is deliberate, and it differs from the backend registry, which refuses
duplicate names. Two backends with one name is always a mistake; "the same
platform, configured differently" is a thing people legitimately want.

## What a target does NOT decide

- **How to link.** That is a toolchain — see [LINKERS.md](LINKERS.md).
- **Which backend to use.** Any backend may be asked for any target; it
  refuses if it cannot.
- **Instruction selection.** That is the backend's, entirely.

## Checklist

- [ ] `arch`, `os`, `abi` and `object_format` all set — none inferred from the
      name
- [ ] `object_suffix` and `executable_suffix` match what the platform expects
      (`.obj`/`.exe` on Windows, `.o`/`` elsewhere)
- [ ] a backend exists that implements your `abi`, or yours refuses clearly
- [ ] `asmpython targets` shows it
- [ ] a program compiled for it runs, and agrees with `asmpython run` on the same
      program
