# The closure cell, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the smallest cell in the runtime: one
# slot, holding one value. A `cell` is what a closure captures -- two
# functions that both read `n` from an enclosing scope share ONE of these, so
# that assigning through either is visible to the other. Python calls the same
# thing a cell in `__closure__`, for the same reason.
#
# WHY IT IS WORTH PORTING NOW rather than with the rest of `func`. It needs
# nothing that was not already available: no buffer, no growth, no error
# message, and no dispatch. `apy_obj_alloc` is IR, `apy_none` is IR, and the
# whole of this file is one allocation and two memory accesses -- so it is the
# largest amount of the C that can be displaced for the least new machinery.
#
# ── the layout, which is C's ────────────────────────────────────────────────
#
# `struct { apy_value slot; } cell;` -- one arm of the union in `objects/c/`,
# and the only one with a single member. The offset below is NOT read off by
# eye: `test_ported_int.py` compiles the real C and asserts it with
# `offsetof`, and asserts the kind against the enum's own value.


def apy_cell_kind() -> i64:
    return 12


def apy_cell_slot_offset() -> i64:
    return 8


def apy_cell_new(initial: ptr) -> ptr:
    """A fresh cell holding `initial`.

    NO NULL CHECK ON `initial`, deliberately: a cell may legitimately hold a
    null, because that is what a closure over a name bound LATER starts as --
    `apy_env_cell` builds one before the value exists. Refusing it here would
    make a forward reference impossible rather than safe.
    """
    cell: ptr = apy_obj_alloc(apy_cell_kind())
    if not cell:
        return cell
    store(u64, u64(initial), offset(cell, apy_cell_slot_offset()))
    return cell


def apy_cell_get(c: ptr) -> ptr:
    """What the cell holds."""
    return ptr(load(u64, offset(c, apy_cell_slot_offset())))


def apy_cell_set(c: ptr, v: ptr) -> ptr:
    """Put `v` in the cell. Answers None, as assignment does.

    THE POINT OF THE WHOLE KIND is that this is visible through every other
    reference to the same cell. Nothing here enforces that -- it follows from
    the cell being one object that several closures hold a pointer to -- which
    is why `apy_func_cell` handing out the SAME cell rather than a copy is the
    part that matters, and it is still in the C.
    """
    store(u64, u64(v), offset(c, apy_cell_slot_offset()))
    return apy_none()
