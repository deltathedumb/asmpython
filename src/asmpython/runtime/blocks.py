# The BUFFER allocator, in the machine subset: size classes and free lists.
#
# STAGE 5's PREREQUISITE, and docs/INERT-RUNTIME.md names it before it names a
# kind: "the allocator does not survive `list`". Stage 4's arena is a bump
# pointer and it is correct for CELLS because nothing frees one -- checked,
# not assumed, across all 51 `free()` calls in the C. A list's `v.q.items` is
# the other case: it DOUBLES on growth and it is the one allocation this
# runtime genuinely releases. A bump pointer cannot resize and cannot reclaim,
# so `list` and `dict` need this file before they need anything else.
#
# WHY NOT ONE ALLOCATOR FOR BOTH. Rounding every allocation up to a size class
# costs a cell 40% -- 152 bytes into a 256-byte class -- to serve the one kind
# of allocation that is ever freed. So `apy_alloc_bytes` keeps its exact-fit
# bump for immortal things and this file layers classes on top of it for the
# things that come back. Both take their memory from the same arena, so the
# platform floor is still three functions and still gets hit once a megabyte.
#
# THE SIZE TRAVELS WITH THE POINTER. `free(p)` needs no size because malloc
# writes a header before every block; a size-classed allocator over a bump
# arena has no header to read, and adding one would cost eight bytes on every
# buffer to store what every caller already knows. So the caller passes the
# length it asked for. That is a contract a caller can break, which is the
# honest cost -- and a broken one puts a block on the wrong list, where the
# next allocation of that class hands out memory that is too small. The corpus
# finds that immediately; nothing about it is subtle in the way a header would
# have avoided.
#
# WHAT THIS IS NOT. It is not a general-purpose allocator and does not try to
# be. There is no coalescing, no splitting and no return to the platform: a
# block is reused at its own class or not at all. That is enough because the
# only thing that frees here GROWS BY DOUBLING, so every block it releases is
# exactly the size the next one up will ask for.


def apy_block_min_shift() -> i64:
    """The smallest class, as a power of two.

    EIGHT BYTES, because a free block stores the next pointer INSIDE itself
    and that pointer is eight bytes wide. A smaller class could not hold its
    own link, which is the one thing every free block has to do.
    """
    return 3


def apy_block_classes() -> i64:
    """How many classes there are: 2**3 through 2**34, which is 16GB.

    Past the last class a block is allocated and never reused -- see
    `apy_free_block`. That is not a limit anyone reaches with a list of
    pointers, and choosing a number here rather than growing the table is what
    keeps the free-list heads a fixed-size reservation.
    """
    return 32


def apy_block_heads() -> ptr:
    """One free-list head per class, zeroed by `reserve`.

    ZERO IS AN EMPTY LIST, which is why nothing has to initialise this. The
    same property `arena.py` relies on, for the same reason: a runtime with a
    startup hook is a runtime every backend has to remember to call.
    """
    return reserve("apy_block_heads_ir", 256)


def apy_block_class(n: i64) -> i64:
    """The smallest class whose blocks are at least `n` bytes.

    A LOOP RATHER THAN A COUNT-LEADING-ZEROS, because the subset has no such
    instruction and a backend that has one is free to recognise this. At most
    thirty-two iterations, and the answer is used once per allocation rather
    than once per element.
    """
    shift: i64 = apy_block_min_shift()
    size: i64 = 1
    size = size << shift
    while size < n:
        shift = shift + 1
        size = size << 1
    return shift


def apy_block_size(shift: i64) -> i64:
    """The byte size of a class."""
    size: i64 = 1
    return size << shift


def apy_alloc_block(n: i64) -> ptr:
    """`n` bytes that may later be given back. Null if the platform says no.

    THE FREE LIST FIRST, which is the whole point: a list that doubled from 64
    to 128 released a 64-byte block, and the next list to reach 64 takes that
    one instead of touching the arena. Without the reuse this would be a bump
    allocator with extra rounding -- strictly worse than what it replaces.
    """
    if n < 1:
        n = 1
    shift: i64 = apy_block_class(n)
    if shift - apy_block_min_shift() < apy_block_classes():
        head: ptr = offset(apy_block_heads(),
                           (shift - apy_block_min_shift()) * 8)
        first: ptr = ptr(load(u64, head))
        if first:
            # POP: the block's first eight bytes are the link, and they stop
            # being a link the moment the block is handed out.
            store(u64, load(u64, first), head)
            return first
    return apy_alloc_bytes(apy_block_size(shift))


def apy_free_block(p: ptr, was: i64) -> i64:
    """Give `was` bytes at `p` back, for the next request of that class.

    A NULL POINTER IS A NO-OP, matching `free(NULL)` -- the C's slice
    assignment reaches here with whatever the list held, and a list built with
    a failed allocation holds null.

    PAST THE LAST CLASS THE BLOCK IS DROPPED rather than tracked. A block that
    large came straight from the arena at its own size and there is no head to
    hang it on; leaking it is what this file does instead of growing a data
    structure for a case nothing reaches.
    """
    if not p:
        return 0
    if was < 1:
        was = 1
    shift: i64 = apy_block_class(was)
    if shift - apy_block_min_shift() >= apy_block_classes():
        return 0
    head: ptr = offset(apy_block_heads(), (shift - apy_block_min_shift()) * 8)
    # PUSH: the freed block becomes the list's first node, and its first eight
    # bytes -- which the caller has finished with -- become the link.
    store(u64, load(u64, head), p)
    store(u64, u64(p), head)
    return 0


def apy_realloc_block(p: ptr, was: i64, want: i64) -> ptr:
    """`want` bytes holding the first `was` of what `p` held.

    SAME CLASS IS THE SAME BLOCK, and that is not just an optimisation: it is
    what makes a caller that grows by one element at a time -- which nothing
    here does, but something might -- cost nothing until it crosses a power of
    two.

    A NULL SOURCE IS AN ALLOCATION, matching `realloc(NULL, n)`, because
    `apy_q_append` reaches here with whatever `apy_seq_new` produced.

    THE COPY IS BY EIGHT BYTES AT A TIME. Every class is a multiple of eight
    and every buffer this serves holds `apy_value`s, so there is no tail and
    no byte loop for a backend to make slow.
    """
    if want < 1:
        want = 1
    if not p:
        return apy_alloc_block(want)
    if was > 0:
        if apy_block_class(was) == apy_block_class(want):
            return p
    fresh: ptr = apy_alloc_block(want)
    if not fresh:
        return fresh
    keep: i64 = was
    if keep > want:
        keep = want
    at: i64 = 0
    while at + 8 <= keep:
        store(u64, load(u64, offset(p, at)), offset(fresh, at))
        at = at + 8
    while at < keep:
        store(u8, load(u8, offset(p, at)), offset(fresh, at))
        at = at + 1
    apy_free_block(p, was)
    return fresh
