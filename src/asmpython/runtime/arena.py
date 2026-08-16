# The allocator, in the machine subset, over the platform floor's arena.
#
# STAGE 4 OF docs/INERT-RUNTIME.md. Every object in a compiled program is now
# handed out from here: `apy_alloc` in the C is a four-line wrapper around
# `apy_obj_alloc`, which this file defines, so the 15,000 lines of C runtime and
# the ported code allocate from one place.
#
# WHY A BUMP ALLOCATOR AND NOT A REAL ONE. Because nothing here frees objects.
# That is not an assumption -- it was checked: of the 51 `free()` calls in the
# C runtime, every one releases a BUFFER (a list's item array, a formatting
# scratch, a split's parts) and not one releases an `apy_obj`. Cells are
# immortal, and an allocator for immortal objects is a pointer and a limit.
#
# So this replaces `malloc(152)` per object with a pointer increment, and asks
# the platform for memory a megabyte at a time instead of 152 bytes at a time.
# The floor gets hit once per chunk rather than once per integer, which is what
# stage 2's three-function claim needs to survive contact with real programs.
#
# WHAT IT COSTS. The tail of a chunk is abandoned when the next request does
# not fit -- at most 151 bytes per megabyte, which is under two hundredths of
# a percent, and the alternative is a free list for objects nothing frees.


def apy_arena_chunk() -> i64:
    """How much to ask the platform for at a time.

    A megabyte is about 6,900 cells. Small enough that a program allocating
    one object does not reserve a lot, large enough that `plat_heap` is called
    thousands of times less often than an object is made.
    """
    return 1048576


def apy_arena() -> ptr:
    """The allocator's whole state: a next pointer and a byte count.

    `reserve` gives it static storage, ZEROED -- so the first request finds
    zero bytes left and takes the slow path, which is exactly the right
    behaviour with no initialisation step to run and nothing to call at
    startup. A runtime with an init hook is a runtime every backend has to
    remember to call.
    """
    return reserve("apy_arena_ir", 16)


def apy_alloc_bytes(n: i64) -> ptr:
    """`n` bytes, eight-aligned, never freed. Null if the platform says no."""
    want: i64 = (n + 7) // 8 * 8
    state: ptr = apy_arena()
    left: i64 = load(i64, offset(state, 8))
    if left < want:
        # A REQUEST LARGER THAN A CHUNK GETS ITS OWN CHUNK, rather than
        # failing or looping. Nothing here asks for one today -- a cell is 152
        # bytes -- but an allocator whose behaviour depends on the caller
        # staying small is one that breaks the first time something does not.
        size: i64 = apy_arena_chunk()
        if want > size:
            size = want
        fresh: ptr = plat_heap(size)
        if not fresh:
            return fresh
        store(u64, u64(fresh), state)
        store(i64, size, offset(state, 8))
        left = size
    at: ptr = ptr(load(u64, state))
    store(u64, u64(at) + u64(want), state)
    store(i64, left - want, offset(state, 8))
    return at


def apy_obj_alloc(kind: i64) -> ptr:
    """One object cell, tagged and zeroed. What every `apy_alloc` reaches.

    ZEROED BEYOND THE TAG, because the C's allocator was: an exception carries
    `context`, `cause` and `notes` that most of them never set, and four
    separate places build one. Three of them remembering is how the fourth
    reads uninitialised memory as a value.

    The zeroing is by i64 rather than by byte because the payload starts at 8
    and the cell is 152, so it divides exactly -- eighteen stores, no tail, and
    no byte loop for a backend to make slow.
    """
    cell: ptr = apy_alloc_bytes(apy_obj_size())
    if not cell:
        return cell
    store(i32, i32(kind), offset(cell, apy_kind_offset()))
    at: i64 = apy_payload_offset()
    while at < apy_obj_size():
        store(i64, 0, offset(cell, at))
        at = at + 8
    return cell
