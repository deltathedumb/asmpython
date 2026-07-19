# PortaPy bootstrap project

PortaPy is the embeddable DLL/shared-library fork of pyinbin's reusable
interpreter core. The interpreter is fully Python-built: its parser, bytecode
compiler, VM, object model, imports, builtins, and standard library remain Python
source compiled by asmpython.

This directory is the in-tree bootstrap used to stabilize the public API before
the separately versioned PortaPy repository is cut.

## Current contents

- `reference_api.py` — Python-authored opaque-handle API model over the current
  pyinbin core. It tests ownership, statuses, execution, calls, conversions, and
  error handling without introducing a native-language VM.
- `include/portapy.h` — provisional public C ABI for the eventual generated DLL/
  shared-library exports.

The reference adapter temporarily imports `asmpython.pyinbin` because the fork
has not been physically separated yet. The final PortaPy source tree will contain
its own copy of the reusable Python interpreter core and must not depend on the
asmpython compiler package at runtime.

## Non-negotiable implementation rule

The C header is a host boundary only. Implementing parser/VM/object/import
semantics in C, C++, Rust, or assembly would violate the project requirement.
Native glue may export symbols and adapt platform calling conventions, but the
interpreter itself must be generated from Python source.

See `docs/PORTAPY-DESIGN.md` and `docs/PYINBIN-DESIGN.md`.
