# ARM64 List Runtime Checkpoint

Checkpoint date: 2026-07-19

## Implemented freestanding ABI

`asmpython/_runtime/abi_lists_linux_arm64.S` now owns the first non-raising
AArch64 list runtime surface:

- `_abi_new_list(capacity)`
- `_abi_list_append(list, value)`
- `_abi_list_extend(destination, source)`
- `_abi_list_insert(list, index, value)`
- `_abi_list_pop(list)` for a non-empty list
- `_abi_list_repeat(list, count)`
- `_abi_list_reverse(list)`

The list representation exactly matches IR lowering and the x86-64 runtime:

- capacity at byte offset 0,
- active length at byte offset 8,
- element-buffer pointer at byte offset 16,
- one 8-byte cell per element.

Allocation uses a zero-initialized freestanding bump arena. Append doubles a full
capacity and clamps the new capacity to at least four. Extend reuses append and
snapshots source length and source buffer before mutation, so `xs.extend(xs)`
duplicates the original active cells exactly once. Insert uses Python index
clamping, grows through append, then shifts active cells right. Repeat checks
length and byte-size overflow, allocates its exact final capacity once, and
returns fresh empty lists for zero or negative counts. Reverse swaps active cells
in place. Pop decrements length and returns the prior final cell.

## Source-level probes

Committed probes cover:

- literal allocation, append growth, length, indexed assignment, and typed unpack,
- non-empty `pop()`,
- even-length `reverse()`,
- self-extension through `xs.extend(xs)`,
- middle insertion plus very-negative/front and oversized/end clamping,
- positive, zero, and negative list repetition.

Every probe asserts its exact undefined-symbol set before tool discovery and
rejects accidental `_abi_raise` dependencies. Each also has native AArch64 and
QEMU workflow jobs with exact stdout expectations.

## Verification status

Directly observed in the current environment:

- Clang's AArch64 integrated assembler accepted the combined list source.
- The result is an ELF64 little-endian `EM_AARCH64` relocatable object.
- Symbol inspection found all seven expected global functions.
- Standalone AArch64 syscall harnesses statically linked with no unresolved
  runtime symbols.
- Disassembly showed resolved calls through allocation, append, self-extend,
  insert, repeat, reverse, and pop.
- The insertion harness encodes checks for the final `[0, 1, 2, 3, 4]` cell order
  after middle, very-negative, and oversized insertions.
- The repetition harness encodes checks for `[7, 8] * 3` plus zero and negative
  count results.

Not directly observed in the current environment:

- execution under `qemu-aarch64` (the binary is not installed here),
- native ARM64 execution,
- the full repository test suite (the execution container cannot resolve GitHub
  to clone the repository).

Do not describe the committed QEMU/native jobs as passed until a workflow or
independent ARM64 environment records their results.

## Deliberate exclusions

- Empty `pop()` remains gated on catchable `IndexError` support.
- Plain list subscript reads that require the bounds-check/raise path remain
  outside runtime execution claims; probes use valid assignment and typed unpack
  to exercise the same cells without faking exceptions.
- List formatting/repr is not implemented.
- Indexed pop, remove, slicing, sorting, and deletion remain separate work items.
- Allocation exhaustion and repetition overflow currently return a null result;
  converting those into catchable `MemoryError`/`OverflowError` remains part of
  the exception-runtime track.
- Dict and set runtime coverage remains untouched.

## Next exact step

Implement valid-index list deletion through `_abi_list_del` as a separate slice:

1. normalize negative indices relative to active length,
2. shift later cells left by one,
3. decrement length while preserving header and capacity,
4. verify first, middle, final, and valid negative indices,
5. keep out-of-range `IndexError` behavior gated until ARM64 exceptions exist.

The pyinbin/PortaPy requirement remains unchanged: both interpreter products are
fully Python-authored and compiled by asmpython; this ARM64 assembly is runtime
support for compiled programs, not an interpreter implementation.
