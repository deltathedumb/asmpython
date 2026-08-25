# The sequence cell -- list and tuple -- written in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the kind the whole of stage 5a was
# built for. That document names this file's prerequisite before it names any
# kind: "the allocator does not survive `list`". A string's bytes are immortal
# and a bump pointer serves them; a list's `v.q.items` DOUBLES on growth and is
# the one allocation this runtime genuinely releases. `blocks.py` is what
# happened about that, and this is the first caller it was written for.
#
# READ `str_cell.py` FIRST. Same idiom, same reasons: a constant is a function
# returning a literal because the static path has no module-level storage, and
# this file is not importable under CPython because `i64` is not a name Python
# has.
#
# ── the layout, which is C's ────────────────────────────────────────────────
#
# `struct { apy_value *items; int64_t n, cap; } q;` -- the list/tuple arm of
# the union in `objects/c/`. The numbers below are NOT read off by eye:
# `test_ported_int.py` compiles the real C and asserts each one with
# `offsetof`, and asserts the kind constants against the enum's own values.
#
# `n` IS NOT `cap`. Two lengths that a reader will conflate exactly once: `n`
# is how many items the list HAS and `cap` is how many the buffer can hold.
# Every bug this file could have is a confusion between them, and the one that
# does not announce itself is using `cap` as the length -- which reads
# uninitialised slots as `apy_value`s and dereferences whatever was there.
#
# ── what is here and what is not ────────────────────────────────────────────
#
# THE CELL, ITS TWO CONSTRUCTORS, AND APPEND. Not indexing, not slicing, not
# the methods: `apy_getitem` is polymorphic over a dozen kinds and one of them
# is `dict`, so it cannot move until `dict` does. What CAN move is what only
# ever touches `v.q`, which is the four below.
#
# APPEND ARRIVED TWICE. It was written, refused by the purity test over a
# single call to `apy_none`, withdrawn, and put back once `singletons.py`
# made that call IR. The section above it records why, because the refusal
# found a wall standing in front of every remaining kind rather than a detail
# of this one.


def apy_list_kind() -> i64:
    return 5


def apy_tuple_kind() -> i64:
    return 6


def apy_q_items_offset() -> i64:
    return 8


def apy_q_n_offset() -> i64:
    return 16


def apy_q_cap_offset() -> i64:
    return 24


#: One slot in the items buffer: a handle, so one machine word.
#:
#: A FUNCTION RATHER THAN `sizeof(ptr)` AT EACH SITE, because it is the
#: conversion between a COUNT of items and a SIZE in bytes, and that
#: conversion appears five times below. Written out each time, one of the five
#: would eventually be the count where the size was wanted -- which allocates
#: an eighth of the buffer and corrupts the seventh append.
#:
#: A ONE-LINE CONSTANT, like every other layout number here, because
#: `test_ported_int.py` reads it with a regex to compare against the C's own
#: `sizeof(apy_value)`. A docstring here puts it out of that probe's reach,
#: which is why the note is a comment.
def apy_value_size() -> i64:
    return 8


# ── construction ────────────────────────────────────────────────────────────
#
# THE BUFFER COMES FROM `blocks.py`, NOT THE ARENA. This is the distinction
# stage 5a exists to draw: `apy_obj_alloc` hands out an immortal cell from a
# bump pointer, and `apy_alloc_block` hands out a size-classed buffer that can
# be returned and reused. The cell is immortal and the items are not, so they
# come from different places and this is the only file where both appear.


def apy_seq_alloc(kind: i64, cap: i64) -> ptr:
    """A sequence cell of `kind` with room for `cap` items and none in it.

    NOT EXPORTED, and deliberately: `REPLACES` lists only the names that
    displace C, and a helper that displaced `apy_seq_new` would take the
    tuple and set constructors with it before either was ported. The C's own
    `apy_seq_new` is static for the same reason it is shared.

    A CAPACITY OF ZERO BECOMES ONE, as the C does. A zero-capacity buffer is
    legal to allocate and its first append doubles zero, which stays zero --
    so the list would grow forever without ever gaining a slot. The C writes
    `if (cap < 1) cap = 1` for exactly this, and the bug it prevents is an
    infinite loop rather than a wrong answer.
    """
    if cap < 1:
        cap = 1
    cell: ptr = apy_obj_alloc(kind)
    if not cell:
        return cell
    items: ptr = apy_alloc_block(cap * apy_value_size())
    if not items:
        return items
    store(u64, u64(items), offset(cell, apy_q_items_offset()))
    store(i64, 0, offset(cell, apy_q_n_offset()))
    store(i64, cap, offset(cell, apy_q_cap_offset()))
    return cell


def apy_list_new(cap: i64) -> ptr:
    """An empty list with room for `cap`."""
    return apy_seq_alloc(apy_list_kind(), cap)


def apy_tuple_new(cap: i64) -> ptr:
    """An empty tuple with room for `cap`.

    A TUPLE IS BUILT MUTABLY AND THEN NEVER WRITTEN AGAIN. Immutability here
    is a property of what the frontend emits, not of the cell: the same
    `apy_seq_push` fills both, and nothing after construction offers a way to
    reach a tuple's slots. That is C's arrangement and this keeps it, because
    a second append path for tuples would be the same code with a different
    kind check.
    """
    return apy_seq_alloc(apy_tuple_kind(), cap)


# ── append, and the wall that stage 5b took down ────────────────────────────
#
# THIS FUNCTION WAS WRITTEN, WITHDRAWN, AND PUT BACK, and the middle step is
# the one worth recording. It answers `None`, so its first version ended in
# `apy_none()` -- a C function, which is neither the platform floor nor a
# `_slow` half, and `test_the_allocator_asks_the_floor_and_nothing_else`
# refused it over that single call. The refusal was right: the moment the
# ported runtime reaches past those two sets, "a backend owes three functions"
# stops being true.
#
# So `singletons.py` happened first, and `apy_none` is IR now. The call below
# is a call within this runtime rather than out of it, and this file is where
# stage 5b's whole point becomes visible: everything that answers None was
# waiting on it.


def apy_seq_grow(cell: ptr) -> ptr:
    """Double the items buffer. Answers the new one, or 0.

    DOUBLING RATHER THAN ADDING, because `blocks.py` reuses a block only at
    its own size class and every class is a power of two -- so a list that
    doubles releases a block that is exactly what the next list of that size
    will ask for, and one that grew by a constant would release a block no
    class wants. The allocator's shape and this policy were chosen together.

    THE OLD SIZE IS PASSED BACK IN. `apy_realloc_block` has no header to read
    -- see `blocks.py` for why -- so the caller owns the length it asked for.
    Here that is `cap * apy_value_size()` computed BEFORE `cap` is updated,
    which is why the two stores below are ordered as they are.
    """
    cap: i64 = load(i64, offset(cell, apy_q_cap_offset()))
    was: i64 = cap * apy_value_size()
    items: ptr = ptr(load(u64, offset(cell, apy_q_items_offset())))
    grown: ptr = apy_realloc_block(items, was, was * 2)
    if not grown:
        return grown
    store(u64, u64(grown), offset(cell, apy_q_items_offset()))
    store(i64, cap * 2, offset(cell, apy_q_cap_offset()))
    return grown


def apy_seq_push(seq: ptr, item: ptr) -> ptr:
    """Append to a list or tuple. The IR half of a split.

    DECLINES EVERYTHING THAT IS NOT `v.q`, and the decline matters more than
    the append. `v.q.items` is at offset 8, which in a `str` is `v.s.p` and in
    a `func` is the code pointer -- so a fast path that trusted its argument
    would read a length out of a string's byte pointer and write an item
    through it. The kind check is what makes reading the arm legal at all,
    exactly as `str_len.py` describes for the three IT declines.

    THE C HALF KEEPS THE ERROR MESSAGE. Building the AttributeError needs
    `apy_fail2` and `apy_kind_name`, both `static` in the C and so unnameable
    from the subset -- so a wrong argument goes back to the C that can still
    spell the sentence, and every call a working program makes stays here.
    """
    kind: i64 = i64(load(i32, offset(seq, 0)))
    if kind != apy_list_kind():
        if kind != apy_tuple_kind():
            return apy_seq_push_slow(seq, item)
    n: i64 = load(i64, offset(seq, apy_q_n_offset()))
    cap: i64 = load(i64, offset(seq, apy_q_cap_offset()))
    if n == cap:
        if not apy_seq_grow(seq):
            return apy_seq_push_slow(seq, item)
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    store(u64, u64(item), offset(items, n * apy_value_size()))
    store(i64, n + 1, offset(seq, apy_q_n_offset()))
    return apy_none()


# ── indexing: the first split of a POLYMORPHIC operation ───────────────────
#
# `apy_getitem` is `x[k]` for everything -- list, tuple, str, bytes, dict,
# range, memoryview, slice objects, and a user `__getitem__` -- and it reaches
# `apy_fail2`, `apy_kind_name`, `apy_is_int_like` and `apy_index_arg`, all
# `static` in the C and none of them nameable from the subset. So it cannot be
# REPLACED, and that is the ordinary case rather than the exception: 301 of
# the 336 functions still in C are blocked the same way.
#
# WHICH MAKES THE SPLIT THE SHAPE OF THE REMAINING WORK, not a workaround.
# Nearly every one of those 301 is blocked by the ERROR path -- `apy_fail2`
# alone blocks 91 and `apy_kind_name` 86 -- and an error path is exactly the
# half a fast path does not take. The IR answers the case it is sure of and
# hands back everything else, so the C keeps the sentence it says when a
# program is wrong and the IR takes the traffic.
#
# THE CASE THIS TAKES is a list or tuple indexed by a small integer, in
# range. That is `xs[i]` in a loop, which is the single most common operation
# a Python program performs.


def apy_getitem(seq: ptr, index: ptr) -> ptr:
    """`seq[index]`. The IR half of a split.

    DECLINES ANYTHING IT IS NOT SURE OF, and the declines are the correctness
    argument rather than the indexing. Reading `v.q` on a str would take the
    byte pointer for `items`; reading it on a dict would take the keys array
    and index it by position rather than by hash. So the kind is checked
    before the arm is touched, exactly as `str_len.py` and `apy_seq_push`
    describe.

    A BIG INTEGER IS A DIFFERENT KIND, which is what makes reading the payload
    safe here: `APY_INT_K` never holds a value that does not fit an int64 --
    every big result demotes -- so there is no case where this reads half of
    one. `apy_index_arg` in the C exists for the kinds this declines.

    OUT OF RANGE GOES TO THE C, rather than being an IndexError built here:
    the message is `apy_fail`'s and the first-error-wins flag is the C's, and
    a fast path that raised its own would have to reproduce both.
    """
    kind: i64 = i64(load(i32, offset(seq, 0)))
    if kind != apy_list_kind():
        if kind != apy_tuple_kind():
            return apy_getitem_slow(seq, index)
    if i64(load(i32, offset(index, 0))) != apy_int_kind():
        return apy_getitem_slow(seq, index)
    i: i64 = load(i64, offset(index, apy_payload_offset()))
    n: i64 = load(i64, offset(seq, apy_q_n_offset()))
    if i < 0:
        i = i + n
    if i < 0:
        return apy_getitem_slow(seq, index)
    if i >= n:
        return apy_getitem_slow(seq, index)
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    return ptr(load(u64, offset(items, i * apy_value_size())))


def apy_setitem(seq: ptr, index: ptr, item: ptr) -> ptr:
    """`seq[index] = item`. The IR half of a split.

    LIST ONLY, NOT TUPLE, and that is the one asymmetry with `apy_getitem`
    next to it: a tuple is readable and not writable, so a tuple reaching here
    must go to the C for the TypeError rather than being served. Serving it
    would make tuples mutable, which nothing else in the runtime would notice
    until a program did.

    Everything else it declines is what `apy_getitem` declines and for the
    same reasons -- a dict assigns by hash, a slice assigns a whole range, an
    instance may define `__setitem__`, and out of range is the C's message.
    """
    if i64(load(i32, offset(seq, 0))) != apy_list_kind():
        return apy_setitem_slow(seq, index, item)
    if i64(load(i32, offset(index, 0))) != apy_int_kind():
        return apy_setitem_slow(seq, index, item)
    i: i64 = load(i64, offset(index, apy_payload_offset()))
    n: i64 = load(i64, offset(seq, apy_q_n_offset()))
    if i < 0:
        i = i + n
    if i < 0:
        return apy_setitem_slow(seq, index, item)
    if i >= n:
        return apy_setitem_slow(seq, index, item)
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    store(u64, u64(item), offset(items, i * apy_value_size()))
    return apy_none()


def apy_contains(needle: ptr, hay: ptr) -> ptr:
    """`needle in hay`. The IR half of a split.

    A SCAN, WHICH MAKES THIS THE FIRST SPLIT THAT CAN DECLINE PART-WAY. Every
    other one decides from the arguments alone; this one has to look at each
    element, and an element it is not sure of sends the whole call to the C --
    which then scans again from the start.

    THAT DOUBLE WORK IS SAFE AND IT IS THE POINT. A scan reads and compares
    and does nothing else: no allocation, no error flag, no `__eq__` on the
    accepted kinds. So abandoning one half-way costs time and cannot cost
    correctness -- and the alternative, carrying on with an element whose kind
    this does not handle, is how a fast path answers for something it was
    never told about.

    IDENTITY IS SUBSUMED, NOT SKIPPED. CPython's `in` tests `is` before `==`,
    which is why `x = float("nan"); x in [x]` is True. For two `APY_INT_K`
    cells the payload comparison gives the same answer either way -- equal
    payloads for identical cells, and NaN is a float and declined -- so this
    needs no separate identity test. Widen the kinds and it would.
    """
    kind: i64 = i64(load(i32, offset(hay, 0)))
    if kind != apy_list_kind():
        if kind != apy_tuple_kind():
            return apy_contains_slow(needle, hay)
    if i64(load(i32, offset(needle, 0))) != apy_int_kind():
        return apy_contains_slow(needle, hay)
    want: i64 = load(i64, offset(needle, apy_payload_offset()))
    items: ptr = ptr(load(u64, offset(hay, apy_q_items_offset())))
    n: i64 = load(i64, offset(hay, apy_q_n_offset()))
    at: i64 = 0
    while at < n:
        element: ptr = ptr(load(u64, offset(items, at * apy_value_size())))
        if i64(load(i32, offset(element, 0))) != apy_int_kind():
            return apy_contains_slow(needle, hay)
        if load(i64, offset(element, apy_payload_offset())) == want:
            return apy_from_bool(1)
        at = at + 1
    return apy_from_bool(0)


# ── three predicates the rest of the runtime is waiting on ─────────────────
#
# NONE OF THESE IS INTERESTING AND ALL THREE ARE WALLS. `asmpython port`
# counts twenty-one functions whose only remaining blocker is one of them --
# not because they are hard, but because they are `static` in the C and the
# subset cannot name a static. Each keeps its old name there as a delegate.


def apy_is_int_like_of(v: ptr) -> i64:
    """Is `v` an integer, in the sense Python means?

    A BOOL IS AN INTEGER and that is the whole reason this exists rather than
    a kind comparison: `True + 1` is 2, `xs[True]` is `xs[1]`, and every
    place that accepts an index has to accept one.

    A BIG IS TOO, which the callers care about even more: an integer that
    outgrew a machine word is still an integer, and a check that missed it
    would refuse exactly the values a program went to the trouble of
    computing.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_int_kind():
        return 1
    if k == apy_bool_kind():
        return 1
    if k == apy_big_kind():
        return 1
    return 0


def apy_is_num_of(v: ptr) -> i64:
    """Is `v` a real number? The same three, plus float.

    NO COMPLEX HERE, deliberately: the callers are the ones that go on to ask
    for a `double`, and a complex has two.
    """
    if apy_is_int_like_of(v):
        return 1
    if i64(load(i32, offset(v, 0))) == apy_float_kind():
        return 1
    return 0


def apy_str_self_of(name: ptr, v: ptr) -> i64:
    """Is `v` a str or bytes receiver? Raises naming `name` if not.

    BYTES TOO. `b.strip()` is the same operation on the same layout -- a
    pointer and a length -- and the only difference is that the RESULT comes
    back tagged bytes, which `apy_str_like` does at the call site.

    ANSWERS A NUMBER AND RAISES AS A SIDE EFFECT, which is not a shape this
    port would choose; it is the C's, and forty-odd string methods open with
    `if (!apy_str_self(...)) return 0;`. Changing it would mean changing all
    of them, and that is a separate piece of work from making it callable.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_str_kind():
        return 1
    if k == apy_bytes_kind():
        return 1
    apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(v), name)
    return 0


def apy_seq_new_of(kind: i64, cap: i64) -> ptr:
    """A sequence cell of any kind. What `apy_list_new` and the rest wrap.

    ALREADY WRITTEN, as `apy_seq_alloc` in this file -- which said in its own
    docstring that it was not exported because doing so would take the tuple
    and set constructors with it before either was ported. Both went months
    ago; this is the same body under the name the C uses.
    """
    return apy_seq_alloc(kind, cap)


def apy_is_seq_of(v: ptr) -> i64:
    """Is `v` a list or a tuple?

    NOT "DOES IT HAVE `v.q`", which a set and a frozenset also do. The four
    share the arm, and what separates the two pairs is ordering: a sequence
    has positions and a set does not, so `xs[0]` means something for these
    two and nothing for the other two.

    A str IS NOT ONE HERE either, though Python calls it a sequence. The
    callers of this ask so they can read `v.q.items`, and a string keeps its
    characters somewhere else entirely.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_list_kind():
        return 1
    if k == apy_tuple_kind():
        return 1
    return 0
