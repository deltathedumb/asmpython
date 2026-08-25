# The function object's fields, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md. A `func` cell is the widest arm of the
# union -- eighteen members -- and none of what is here builds one. These are
# the SETTERS the frontend calls right after `apy_func_new`, one per property
# a `def` can have: its closure cells, its defaults, whether it takes `**kw`,
# how many parameters are positional-only, its `__qualname__`, its `__doc__`.
#
# WHY SETTERS AND NOT THE CONSTRUCTOR. `apy_func_new` decides a layout and
# allocates; these only write into one that exists, so they need the offsets
# and nothing else. The constructor moves when `func` does, and the frontend
# emits a run of these after every `def` -- so this is the larger half by call
# count and the smaller half by risk.
#
# EACH ONE ANSWERS THE FUNCTION, which is what lets the frontend chain them:
#
#     f = apy_func_qualname(apy_func_coro(apy_func_new(...)), name)
#
# ── the widths are not uniform, and that is the trap ───────────────────────
#
# The C declares `apy_value *cells` and `int64_t ncells` alongside plain `int`
# for `kwarg`, `kwonly`, `posonly`, `builtin` and `coro`. So some of these
# fields are EIGHT bytes and some are FOUR, and storing eight into a four-byte
# field overwrites the one after it -- `builtin` and `coro` are adjacent, so
# marking a function a builtin would also make it a coroutine. Every offset
# and every width below came from `offsetof`/`sizeof` asked of the C compiler,
# and `test_ported_int.py` asks it again on every run.


def apy_func_kind() -> i64:
    return 11


def apy_fn_cells_offset() -> i64:
    return 32


def apy_fn_ncells_offset() -> i64:
    return 40


def apy_fn_defaults_offset() -> i64:
    return 56


def apy_fn_ndefaults_offset() -> i64:
    return 64


def apy_fn_nkwdefault_offset() -> i64:
    return 76


def apy_fn_kwarg_offset() -> i64:
    return 80


def apy_fn_kwonly_offset() -> i64:
    return 84


def apy_fn_posonly_offset() -> i64:
    return 88


def apy_fn_doc_offset() -> i64:
    return 104


def apy_fn_builtin_offset() -> i64:
    return 120


def apy_fn_coro_offset() -> i64:
    return 124


def apy_fn_qualname_offset() -> i64:
    return 128


def apy_fn_annotate_offset() -> i64:
    return 136


# ── the two that index an array ────────────────────────────────────────────
#
# BOUNDS-CHECKED AND SILENT, exactly as the C is. An out-of-range index does
# nothing rather than failing, because the frontend emits these from a count
# it computed itself -- so a bad index is a compiler bug, and the runtime is
# not the place to discover it. What the check prevents is that bug becoming
# a write through a wild pointer.


def apy_func_cell(f: ptr, i: i64, cell: ptr) -> ptr:
    """Put `cell` in closure slot `i`. Answers `f`, so calls chain."""
    if i >= 0:
        if i < load(i64, offset(f, apy_fn_ncells_offset())):
            cells: ptr = ptr(load(u64, offset(f, apy_fn_cells_offset())))
            store(u64, u64(cell), offset(cells, i * 8))
    return f


def apy_func_default(f: ptr, i: i64, value: ptr) -> ptr:
    """Put `value` in default slot `i`. Answers `f`."""
    if i >= 0:
        if i < load(i64, offset(f, apy_fn_ndefaults_offset())):
            slots: ptr = ptr(load(u64, offset(f, apy_fn_defaults_offset())))
            store(u64, u64(value), offset(slots, i * 8))
    return f


# ── the counts and flags ───────────────────────────────────────────────────


def apy_func_kwdefaults(f: ptr, n: i64) -> ptr:
    """How many of the trailing parameters are keyword-only WITH defaults."""
    store(i32, i32(n), offset(f, apy_fn_nkwdefault_offset()))
    return f


def apy_func_kwarg(f: ptr, on: i64) -> ptr:
    """Whether this function takes `**kwargs`."""
    if on != 0:
        store(i32, 1, offset(f, apy_fn_kwarg_offset()))
        return f
    store(i32, 0, offset(f, apy_fn_kwarg_offset()))
    return f


def apy_func_kwonly(f: ptr, n: i64) -> ptr:
    """How many parameters follow a `*`, and so must be passed by name."""
    store(i32, i32(n), offset(f, apy_fn_kwonly_offset()))
    return f


def apy_func_posonly(f: ptr, n: i64) -> ptr:
    """How many parameters precede a `/`, and so cannot be passed by name."""
    store(i32, i32(n), offset(f, apy_fn_posonly_offset()))
    return f


# ── the ones that check the kind first ─────────────────────────────────────
#
# GUARDED, because the frontend applies these to whatever a decorator handed
# back. `@some_decorator` may answer a class or an instance, and writing a
# function's `coro` flag into a class's `v.t` arm would corrupt a field that
# arm uses for something else entirely. The C guards for the same reason;
# where it does not, neither does this.


def apy_func_qualname(f: ptr, name: ptr) -> ptr:
    """`__qualname__` -- the dotted path, so a method reports `C.m`."""
    if i64(load(i32, offset(f, 0))) == apy_func_kind():
        store(u64, u64(name), offset(f, apy_fn_qualname_offset()))
    return f


def apy_func_annotate(f: ptr, thunk: ptr) -> ptr:
    """PEP 649's lazy annotations: a function that BUILDS the dict.

    A thunk rather than the dict, so an annotation naming a class defined
    later does not have to be evaluated at `def` time -- which is the whole
    of what PEP 649 changed.
    """
    if i64(load(i32, offset(f, 0))) == apy_func_kind():
        store(u64, u64(thunk), offset(f, apy_fn_annotate_offset()))
    return f


def apy_func_builtin(f: ptr) -> ptr:
    """Mark this as a builtin, which changes how it reprs and what it accepts."""
    if i64(load(i32, offset(f, 0))) == apy_func_kind():
        store(i32, 1, offset(f, apy_fn_builtin_offset()))
    return f


def apy_func_coro(f: ptr) -> ptr:
    """Mark this `async def`, so calling it answers a coroutine rather than
    running it."""
    if i64(load(i32, offset(f, 0))) == apy_func_kind():
        store(i32, 1, offset(f, apy_fn_coro_offset()))
    return f


def apy_func_doc(f: ptr, text: ptr) -> ptr:
    """`__doc__`. Unguarded, as the C is -- the frontend only ever reaches
    here with the function it just built."""
    store(u64, u64(text), offset(f, apy_fn_doc_offset()))
    return f


def apy_fn_arity_offset() -> i64:
    return 16


def apy_fn_pnames_offset() -> i64:
    return 96


def apy_func_param(f: ptr, i: i64, name: ptr) -> ptr:
    """Record parameter `i`'s NAME, so a keyword argument can find its slot.

    ALLOCATED ON FIRST USE, as the C does: most functions are called
    positionally and never need the array, so building it in `apy_func_new`
    would cost every `def` in the program for the few that are called by
    keyword.

    ZEROED BY HAND, because the C used `calloc` and this allocator does not
    zero. That difference is the whole risk in this function: an unwritten
    slot holds whatever the size class last had in it, and a keyword lookup
    would compare against a stale handle -- matching some earlier function's
    parameter name, which is a wrong answer rather than a crash. The loop
    below is what `calloc`'s second argument used to buy.
    """
    arity: i64 = load(i64, offset(f, apy_fn_arity_offset()))
    if i < 0:
        return f
    if i >= arity:
        return f
    names: ptr = ptr(load(u64, offset(f, apy_fn_pnames_offset())))
    if not names:
        names = apy_alloc_block(arity * 8)
        if not names:
            return f
        at: i64 = 0
        while at < arity:
            store(u64, 0, offset(names, at * 8))
            at = at + 1
        store(u64, u64(names), offset(f, apy_fn_pnames_offset()))
    store(u64, u64(name), offset(names, i * 8))
    return f
