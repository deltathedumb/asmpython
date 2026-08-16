# ARM64 List Runtime Checkpoint

Checkpoint date: 2026-07-20

## Implemented freestanding ABI

The ARM64 runtime now exposes the following non-raising list surface across the
core, deletion, and slicing assembly slices:

- `_abi_new_list(capacity)`
- `_abi_list_append(list, value)`
- `_abi_list_extend(destination, source)`
- `_abi_list_insert(list, index, value)`
- `_abi_list_pop(list)` for a non-empty list
- `_abi_list_repeat(list, count)`
- `_abi_list_reverse(list)`
- `_abi_list_del(list, index)` for a valid index
- `_abi_list_slice(list, start, stop)` for a plain positive-step slice

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
in place. Pop decrements length and returns the prior final cell. Valid-index
deletion normalizes negative indices, shifts later cells left, and preserves the
header and capacity. Plain slicing accepts the compiler's `INT64_MIN`/`INT64_MAX`
missing-bound sentinels, normalizes negative bounds, clamps both endpoints to
`[0, len]`, and returns a newly allocated shallow-copy list.

## Source-level probes

Committed probes cover:

- literal allocation, append growth, length, indexed assignment, and typed unpack,
- non-empty `pop()`,
- even-length `reverse()`,
- self-extension through `xs.extend(xs)`,
- middle insertion plus very-negative/front and oversized/end clamping,
- positive, zero, and negative list repetition,
- first, middle, final, and valid-negative deletion,
- explicit, missing, negative, clipped, reversed, and full-range plain slices.

Every probe asserts its exact undefined-symbol set before tool discovery and
rejects accidental `_abi_raise` dependencies. Opaque element values are checked
through executable return codes rather than depending on the generic element
formatter. Each feature has native AArch64 and QEMU jobs with exact stdout
expectations.

## Verification status

Directly observed in GitHub Actions on `beta/3.14.0`:

- all ARM64 structural and manifest tests pass,
- the comprehensive native and QEMU runtime workflows pass through every current
  string, integer, list, and floating-point stage,
- list core, extend, insert, pop, repeat, reverse, deletion, and plain slicing
  pass on both native AArch64 and `qemu-aarch64`,
- deletion produces the expected final active cells `[1, 4]`,
- slicing produces Python-compatible results for `xs[1:4]`, `xs[:3]`, `xs[2:]`,
  `xs[-4:-1]`, `xs[4:2]`, and `xs[-99:99]`.

## Deliberate exclusions

- Empty `pop()` remains gated on catchable `IndexError` support.
- Out-of-range deletion remains gated on catchable `IndexError` support.
- Plain list subscript reads that require the bounds-check/raise path remain
  outside runtime execution claims; probes use valid assignment and typed unpack
  to exercise the same cells without faking exceptions.
- List formatting/repr is not implemented.
- Indexed pop, remove, sorting, slice assignment, and slice deletion remain
  separate work items.
- Explicit-step slicing remains unsupported by the ARM64 runtime; the compiler
  lowers it to `_abi_list_slice_step`, which is intentionally still outside the
  manifest.
- A zero slice step must eventually raise `ValueError`; that path remains gated
  on the ARM64 exception runtime.
- Allocation exhaustion and repetition overflow currently return a null result;
  converting those into catchable `MemoryError`/`OverflowError` remains part of
  the exception-runtime track.
- Dict and set runtime coverage remains untouched.

## Next exact step

Implement explicit-step slicing through `_abi_list_slice_step` as a separate
ARM64 runtime slice:

1. normalize omitted and explicit bounds according to the sign of `step`,
2. support positive and negative nonzero steps,
3. compute an exact output length without overflowing,
4. allocate a fresh result and copy selected 8-byte cells,
5. verify forward stride, reverse stride, clipped bounds, empty results, and
   negative endpoints,
6. keep `step == 0` gated until catchable `ValueError` exists.

The pyinbin/PortaPy requirement remains unchanged: both interpreter products are
fully Python-authored and compiled by asmpython; this ARM64 assembly is runtime
support for compiled programs, not an interpreter implementation.
