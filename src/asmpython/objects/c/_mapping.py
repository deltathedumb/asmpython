"""The object runtime, in C: dict, set and frozenset.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * dict
  * set and frozenset
"""

C = r"""/* --- dict -------------------------------------------------------------- */
/* An association LIST, not a hash table. Lookup is linear.

   That is a deliberate v1: a hash table needs a hash function per kind and a
   resize policy, and both are places to be subtly wrong, while the property
   the conformance suite actually measures is that `{1: 'a'}[1]` is 'a' and
   that iteration order is insertion order. Linear scan gets both right in
   forty lines. It is O(n) per lookup and that is written here so the next
   person replaces it on purpose rather than discovering it.

   Insertion order is preserved because entries are appended and a re-assigned
   key keeps its original slot -- which is not an implementation accident in
   Python but a guarantee since 3.7.

   Keys compare with `==`, so `d[1]` and `d[True]` are the same entry, exactly
   as in CPython. */
static apy_value apy_dict_new_cap(int64_t cap) {
    apy_obj *o = apy_alloc(APY_DICT_K);
    if (cap < 1) cap = 1;
    o->v.d.keys = (apy_value *)(uintptr_t)apy_alloc_block(
        cap * (int64_t)sizeof(apy_value));
    o->v.d.vals = (apy_value *)(uintptr_t)apy_alloc_block(
        cap * (int64_t)sizeof(apy_value));
    o->v.d.n = 0;
    o->v.d.cap = cap;
    return V(o);
}

APY_API apy_value apy_dict_new(int64_t cap) { return apy_dict_new_cap(cap); }

APY_API int64_t apy_dict_find_of(apy_value d, apy_value key) {
    int64_t i;
    if (!d) return -1;
    for (i = 0; i < O(d)->v.d.n; i++)
        if (apy_eq_raw(O(d)->v.d.keys[i], key)) return i;
    return -1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int64_t apy_dict_find(apy_value d, apy_value key) {
    return apy_dict_find_of(d, key);
}

/* An unhashable key is a TypeError in CPython even though nothing here needs
   to hash it -- accepting one would let `{[1]: 2}` run and then disagree.

   RECURSIVE, because hashability is: a tuple is hashable only if everything
   in it is, so `{(1, [2]): 3}` is a TypeError in CPython and a shallow
   "tuples are fine" test let it through. Returns the kind name of the
   INNERMOST offender, or NULL when the value is hashable -- the name is what
   the message needs, and finding it is the same walk as deciding. */
APY_API apy_value apy_unhashable_of(apy_value v) {
    int64_t i;
    if (O(v)->kind == APY_LIST_K || O(v)->kind == APY_DICT_K
        || O(v)->kind == APY_SET_K)
        return (apy_value)(uintptr_t)apy_kind_name(v);
    /* A bytearray is unhashable for the reason every mutable container is:
       the hash would stop describing the contents the moment it is written
       to, and the dict that filed it could never find it again. */
    if (O(v)->kind == APY_BYTES_K && O(v)->v.s.mut) return (apy_value)(uintptr_t)apy_kind_name(v);
    /* A FROZENSET is hashable and is not walked, unlike a tuple: every element
       it holds was checked on the way in, so there is nothing a walk could
       find. A tuple has no such gate -- `(1, [2])` is a perfectly ordinary
       tuple -- which is why that one is recursive. */
    if (O(v)->kind == APY_TUPLE_K) {
        for (i = 0; i < O(v)->v.q.n; i++) {
            apy_value bad = apy_unhashable_of(O(v)->v.q.items[i]);
            if (bad) return bad;
        }
    }
    /* A USER OBJECT WHOSE CLASS DEFINES `__eq__` AND NOT `__hash__` is
       unhashable, and this is where a CONTAINER finds that out. `hash(x)`
       already refused it, but `{x: 1}` and `{x}` went through here, found
       nothing to complain about, and built a mapping whose key could never be
       looked up again -- two objects that compare equal hashed by address and
       landed in different places. A silent wrong answer where CPython raises. */
    if (O(v)->kind == APY_INST_K
            && !apy_class_find(O(v)->v.o.cls, apy_name("__hash__"))
            && apy_class_find(O(v)->v.o.cls, apy_name("__eq__")))
        return (apy_value)(uintptr_t)APY_CSTR(O(O(v)->v.o.cls)->v.t.name);
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static const char *apy_unhashable(apy_value v) {
    return (const char *)(uintptr_t)apy_unhashable_of(v);
}

/* CPython 3.14 wraps the plain "unhashable type" text when the value is used
   AS A DICT KEY: `cannot use 'tuple' as a dict key (unhashable type:
   'list')`, naming the key's own kind and then the innermost offender. 3.13
   and earlier said only the inner half, which is still what a search finds
   and what this file used to report. The suite is generated from 3.14. */
APY_API apy_value apy_unhashable_key_of(apy_value key,
                                        apy_value inner) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "cannot use '%s' as a dict key (unhashable type: '%s')",
             apy_kind_name(key), (const char *)inner);
    return apy_fail("TypeError", buf);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_unhashable_key(apy_value key, const char *inner) {
    return apy_unhashable_key_of(key,
        (apy_value)(uintptr_t)inner);
}

APY_API apy_value apy_dict_set(apy_value d, apy_value key, apy_value val) {
    int64_t at;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("TypeError",
                         "'%s' object does not support item assignment%s",
                         apy_kind_name(d), "");
    {
        const char *bad = apy_unhashable(key);
        if (bad) return apy_unhashable_key(key, bad);
    }
    at = apy_dict_find(d, key);
    if (at >= 0) {
        /* A re-assigned key keeps its ORIGINAL position. Python's insertion
           order is about first insertion, not last write. */
        O(d)->v.d.vals[at] = val;
        return apy_none();
    }
    if (O(d)->v.d.n == O(d)->v.d.cap) {
        int64_t was = O(d)->v.d.cap * (int64_t)sizeof(apy_value);
        O(d)->v.d.cap *= 2;
        O(d)->v.d.keys = (apy_value *)(uintptr_t)apy_realloc_block(
            (apy_value)(uintptr_t)O(d)->v.d.keys, was,
            O(d)->v.d.cap * (int64_t)sizeof(apy_value));
        O(d)->v.d.vals = (apy_value *)(uintptr_t)apy_realloc_block(
            (apy_value)(uintptr_t)O(d)->v.d.vals, was,
            O(d)->v.d.cap * (int64_t)sizeof(apy_value));
    }
    O(d)->v.d.keys[O(d)->v.d.n] = key;
    O(d)->v.d.vals[O(d)->v.d.n] = val;
    O(d)->v.d.n++;
    return apy_none();
}

static apy_value apy_dict_get(apy_value d, apy_value key) {
    int64_t at;
    {
        const char *bad = apy_unhashable(key);
        if (bad) return apy_unhashable_key(key, bad);
    }
    at = apy_dict_find(d, key);
    if (at < 0) {
        apy_value shown = apy_repr(key);
        char buf[200];
        snprintf(buf, sizeof buf, "%.*s",
                 (int)O(shown)->v.s.n, O(shown)->v.s.p);
        return apy_fail("KeyError", buf);
    }
    return O(d)->v.d.vals[at];
}

/* `{'a': 1}` -- key and value both with repr, separated by ": ". An empty
   dict is `{}`, which is why the length is checked before anything is built. */
APY_API apy_value apy_dict_text_of(apy_value v) {
    int64_t n = O(v)->v.d.n, i, len = 3, out = 0;
    apy_value *parts;
    char *buf;
    if (n == 0) return apy_lit("{}");
    /* ALREADY BEING RENDERED -- `d['self'] = d` is as ordinary as the list
       version, and recursing on it runs the stack out. See
       `apy_repr_entered`. */
    if (apy_repr_entered(v)) return apy_lit("{...}");
    parts = (apy_value *)malloc((size_t)n * 2 * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        parts[i * 2] = apy_text(O(v)->v.d.keys[i], 1);
        parts[i * 2 + 1] = apy_text(O(v)->v.d.vals[i], 1);
        len += O(parts[i * 2])->v.s.n + O(parts[i * 2 + 1])->v.s.n + 4;
    }
    apy_repr_left(v);
    buf = (char *)malloc((size_t)len + 1);
    buf[out++] = '{';
    for (i = 0; i < n * 2; i++) {
        if (i) {
            buf[out++] = (i & 1) ? ':' : ',';
            buf[out++] = ' ';
        }
        memcpy(buf + out, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
        out += O(parts[i])->v.s.n;
    }
    buf[out++] = '}';
    buf[out] = 0;
    free(parts);
    return apy_str_take(buf, out);
}
/* THE NAME ITS CALLERS USE, kept as a delegate. */
static apy_value apy_dict_text(apy_value v) { return apy_dict_text_of(v); }

/* The i'th key, for `for k in d`. Index-based like the sequence loop,
   and for the same reason: there is no iterator protocol yet.
   A set goes through here rather than through `apy_getitem`, which refuses it:
   a set is iterable and not subscriptable, and this is the function that means
   "iterate". */
APY_API apy_value apy_key_at(apy_value v, int64_t i) {
    /* PAIRED WITH `apy_raw_len`, AND THE PAIR HAS TO MOVE TOGETHER. That
       function answers a builtin-extending instance with the builtin's
       length; if this one did not also unwrap, the walk would read the right
       COUNT of elements out of the wrong object -- an instance's union arm
       where a sequence's `items` pointer belongs. It did, and `sorted(s)` on
       a `class St(set)` died dereferencing it, with the program's buffered
       output lost so that nothing was printed at all. */
    if (O(v)->kind == APY_INST_K && O(v)->v.o.held
            && !apy_class_find(O(v)->v.o.cls, apy_name("__getitem__")))
        return apy_key_at(O(v)->v.o.held, i);
    if (O(v)->kind == APY_VIEW_K) return apy_key_at(apy_view_items(v), i);
    if (O(v)->kind == APY_GEN_K)
        /* From what the length query drained -- see `apy_raw_len`. */
        return O(v)->v.g.cache ? apy_key_at(O(v)->v.g.cache, i) : apy_none();
    if (O(v)->kind == APY_DICT_K) return O(v)->v.d.keys[i];
    if (apy_is_set(v)) return O(v)->v.q.items[i];
    /* AN ITERATOR IGNORES `i` AND ADVANCES.

       Every consumer here walks by index: read the length once, then ask for
       0, 1, 2... An iterator has a position of its own, and a consumer that
       indexed it from zero would replay elements it had already yielded and
       would leave it unconsumed afterwards -- `for v in it` twice would run
       twice.

       So the cursor is what answers, and `i` is ignored. That is exact for a
       sequential walk, which is the only kind anything does, and wrong for a
       random-access one, which nothing does. If a consumer ever indexes out of
       order this is where it will go wrong, and it will go wrong silently --
       hence saying so here rather than in a commit message. */
    if (O(v)->kind == APY_ITER_K) {
        apy_value src = O(v)->v.it.src;
        int64_t n = apy_raw_len(src);
        if (apy_error_occurred() || O(v)->v.it.i >= n) return apy_none();
        return apy_key_at(src, O(v)->v.it.i++);
    }
    return apy_getitem(v, apy_from_int(i));
}

/* --- set and frozenset -------------------------------------------------- */
/* The list layout again (`v.q`), with uniqueness kept by a LINEAR SCAN on
   insert -- the same association-list decision the dict above documents, made
   for the same reason and with the same cost. Insertion is O(n), so building
   an n-element set is O(n**2). Stated so the next person replaces it on
   purpose.

   set and frozenset are two kinds rather than one kind with a flag, so that
   `apy_kind_name` -- which feeds `type(x).__name__`, `isinstance` and every
   error message -- stays a switch on the kind and cannot forget to look at a
   flag. Everything else about them is identical, INCLUDING equality:
   `{1, 2} == frozenset([1, 2])` is True in Python, where `[1] == (1,)` is
   False. Sets compare by membership; sequences compare by type and order.

   ORDER IS INSERTION ORDER, AND CPYTHON'S IS NOT. CPython iterates the slots
   of a hash table, so `set([3, 1, 2])` prints `{1, 2, 3}` and this prints
   `{3, 1, 2}`. That is a real divergence and it is visible to any program that
   prints a set it did not build in sorted order. It is not reproducible
   without reproducing CPython's table size, growth policy, probe sequence and
   per-kind hash -- and for str the hash is salted per process, so there is no
   fixed answer to reproduce. The conformance suite sorts every set it prints
   for exactly that reason (see cases/sets/operations-are-sorted-for-
   determinism), and the only set reprs it expects verbatim are `{1, 2}` and
   `frozenset({1, 2})`, where the two orders coincide.

   ELEMENTS ARE COMPARED WITH `==`, so `{1, 1.0, True}` holds ONE element and
   it is the FIRST one inserted -- `len` is 1 and the element prints as `1`.
   A linear scan that stops at the first equal element gets that for free. */
APY_API apy_value apy_unhashable_elem_of(apy_value item,
                                         apy_value inner) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "cannot use '%s' as a set element (unhashable type: '%s')",
             apy_kind_name(item), (const char *)inner);
    return apy_fail("TypeError", buf);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_unhashable_elem(apy_value item, const char *inner) {
    return apy_unhashable_elem_of(item, (apy_value)(uintptr_t)inner);
}

APY_API int64_t apy_set_find_of(apy_value s, apy_value item) {
    int64_t i;
    for (i = 0; i < O(s)->v.q.n; i++)
        if (apy_eq_raw(O(s)->v.q.items[i], item)) return i;
    return -1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int64_t apy_set_find(apy_value s, apy_value item) {
    return apy_set_find_of(s, item);
}

/* 1 when the element was added, 0 when an equal one was already there, -1 when
   it is unhashable and the error flag has been set. Three outcomes because
   `.add` and `.discard` need to tell "already present" from "refused", and a
   caller that only cares about failure can test for a negative. */
/* Declared here because set INSERTION needs an element's hash to place it,
   several thousand lines before hashing is defined. */
static int64_t apy_hash_raw(apy_value v);

/* THE TABLE SIZE CPYTHON WOULD BE USING for a set of `n` elements: the
   smallest power of two at least 8 whose load stays under 60%, which is
   CPython's growth rule. Returns the mask, one less than that size. */
APY_API int64_t apy_set_mask_of(int64_t n) {
    int64_t size = 8;
    while (n * 5 >= size * 3) size *= 2;
    return size - 1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int64_t apy_set_mask(int64_t n) {
    return apy_set_mask_of(n);
}

/* Order the elements the way CPython's table would hold them.
   Called when the mask changes, which is O(log n) times over a set's life. */
APY_API void apy_set_reorder_of(apy_value s, int64_t mask) {
    apy_value *items = O(s)->v.q.items;
    int64_t n = O(s)->v.q.n, i, j;
    for (i = 1; i < n; i++) {
        apy_value held = items[i];
        int64_t want = apy_hash_raw(held) & mask;
        for (j = i; j > 0 && (apy_hash_raw(items[j - 1]) & mask) > want; j--)
            items[j] = items[j - 1];
        items[j] = held;
    }
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static void apy_set_reorder(apy_value s, int64_t mask) {
    apy_set_reorder_of(s, mask);
}

/* 1 when the element was added, 0 when an equal one was already there, -1 when
   it is unhashable and the error flag has been set.

   THE POSITION IS THE SLOT'S, NOT THE END. A set is an open-addressed hash
   table in CPython, so `{3, 1, 2}` iterates as 1, 2, 3 -- the three land in
   slots 3, 1 and 2 of an eight-slot table whatever order they were written
   in, and iteration walks the slots. Appending gave insertion order, which is
   the one thing CPython's order is never about, and seven conformance cases
   read it back.

   WHAT THIS DOES NOT CLAIM: two elements landing in ONE slot are ordered here
   by arrival, where CPython places them by a perturbed probe
   (`i = i*5 + 1 + perturb`). For sets whose elements do not collide -- every
   one in the suite, and most small int and str sets -- the order is exactly
   CPython's; with collisions it can differ. A real open-addressed table with
   that probe is the eventual shape, and buys O(1) membership as well; this
   keeps the array and the linear scan and changes only where things go. */
/* Declared here because the exported half above calls it. */
static int apy_set_insert(apy_value s, apy_value item);
APY_API int64_t apy_set_insert_of(apy_value s, apy_value item) {
    return apy_set_insert(s, item);
}
static int apy_set_insert(apy_value s, apy_value item) {
    int64_t n, mask, was, i, want;
    const char *bad = apy_unhashable(item);
    if (bad) { apy_unhashable_elem(item, bad); return -1; }
    if (apy_set_find(s, item) >= 0) return 0;
    n = O(s)->v.q.n;
    was = apy_set_mask(n);
    mask = apy_set_mask(n + 1);
    apy_q_append(s, item);
    if (mask != was) {
        /* The table grew, so every slot moved. */
        apy_set_reorder(s, mask);
        return 1;
    }
    /* Walk back over anything that belongs after this one. */
    want = apy_hash_raw(item) & mask;
    for (i = n; i > 0
             && (apy_hash_raw(O(s)->v.q.items[i - 1]) & mask) > want; i--)
        O(s)->v.q.items[i] = O(s)->v.q.items[i - 1];
    O(s)->v.q.items[i] = item;
    return 1;
}

APY_API apy_value apy_set_new(int64_t cap) { return apy_seq_new(APY_SET_K, cap); }
APY_API apy_value apy_frozenset_new(int64_t cap) { return apy_seq_new(APY_FROZEN_K, cap); }

/* Building a DISPLAY -- `{a, b}` -- and nothing else. It does not refuse a
   frozenset receiver, because `frozenset([...])` is built by filling a fresh
   frozen cell here before any program can see it. `.add` is `apy_set_add`,
   which does refuse. */
APY_API apy_value apy_set_push(apy_value s, apy_value item) {
    if (apy_set_insert(s, item) < 0) return 0;
    return apy_none();
}

/* `set(x)` / `frozenset(x)` over anything iterable. Goes through
   `apy_raw_len`/`apy_key_at` rather than reading `v.q` directly, so a dict
   contributes its KEYS and a str its characters, which is what Python's
   constructors do. */
/* Declared here because the exported half above calls it. */
static apy_value apy_set_from(int kind, apy_value src);
APY_API apy_value apy_set_from_of(int64_t kind, apy_value src) {
    return apy_set_from((int)kind, src);
}
static apy_value apy_set_from(int kind, apy_value src) {
    int64_t n = apy_raw_len(src), i;
    apy_value out;
    if (apy_error_occurred()) return 0;
    out = apy_seq_new(kind, n + 1);
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(src, i);
        if (!item) return 0;
        if (apy_set_insert(out, item) < 0) return 0;
    }
    return out;
}

APY_API apy_value apy_to_set(apy_value v) { return apy_set_from(APY_SET_K, v); }
APY_API apy_value apy_to_frozenset(apy_value v) {
    /* `frozenset(f)` is allowed to hand back the same object, and CPython
        does; there is nothing to copy because nothing can change it. */
    if (O(v)->kind == APY_FROZEN_K) return v;
    return apy_set_from(APY_FROZEN_K, v);
}

/* `{1, 2}`, `frozenset({1, 2})`, and `set()` / `frozenset()` when empty. The
   empty set is NOT `{}` -- that is the empty dict, and printing it as `{}`
   would print a set that reads back as a dict. */
APY_API apy_value apy_set_text_of(apy_value v) {
    int frozen = O(v)->kind == APY_FROZEN_K;
    int64_t n = O(v)->v.q.n, i, len = frozen ? 13 : 3, out = 0;
    apy_value *parts;
    char *buf;
    if (n == 0) return apy_lit(frozen ? "frozenset()" : "set()");
    parts = (apy_value *)malloc((size_t)n * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        parts[i] = apy_text(O(v)->v.q.items[i], 1);
        len += O(parts[i])->v.s.n + 2;
    }
    buf = (char *)malloc((size_t)len + 1);
    if (frozen) { memcpy(buf, "frozenset(", 10); out = 10; }
    buf[out++] = '{';
    for (i = 0; i < n; i++) {
        if (i) { buf[out++] = ','; buf[out++] = ' '; }
        memcpy(buf + out, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
        out += O(parts[i])->v.s.n;
    }
    buf[out++] = '}';
    if (frozen) buf[out++] = ')';
    buf[out] = 0;
    free(parts);
    return apy_str_take(buf, out);
}
/* THE NAME ITS CALLERS USE, kept as a delegate. */
static apy_value apy_set_text(apy_value v) { return apy_set_text_of(v); }

/* Every element of `a` is in `b`. The whole of set ordering rests on this:
   `<=` is subset, `<` is proper subset, and two sets neither of which contains
   the other are INCOMPARABLE rather than an error. */
APY_API int64_t apy_subset_of(apy_value a, apy_value b) {
    int64_t i;
    for (i = 0; i < O(a)->v.q.n; i++)
        if (apy_set_find(b, O(a)->v.q.items[i]) < 0) return 0;
    return 1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_subset(apy_value a, apy_value b) {
    return (int)apy_subset_of(a, b);
}

enum { APY_UNION, APY_INTER, APY_DIFF, APY_SYMDIFF };

/* The four algebra operations, in one place because they differ by four
   lines. `strict` is the OPERATOR/METHOD distinction and it is not cosmetic:
   `{1} | [2]` is a TypeError and `{1}.union([2])` is `{1, 2}`. CPython's
   operators demand two sets so that `|` cannot silently mean two things; its
   methods take any iterable because they were named.

   THE RESULT'S KIND IS THE LEFT OPERAND'S: `frozenset([1]) | {2}` is a
   frozenset and `{2} | frozenset([1])` is a set. Both directions are asserted
   by cases/sets/disjoint-and-frozen-ops.

   The result is appended to RAW. Both inputs are already duplicate-free and
   every branch below adds each element at most once, so the O(n) scan
   `apy_set_insert` would do could not find anything -- it would only make an
   O(n**2) operation O(n**3). */
/* Declared here because the exported half calls it. */
static apy_value apy_set_algebra(const char *op, apy_value a, apy_value b,
                                 int which, int strict);
APY_API apy_value apy_set_algebra_of(apy_value op, apy_value a,
                                     apy_value b, int64_t which,
                                     int64_t strict) {
    return apy_set_algebra((const char *)op, a, b, (int)which, (int)strict);
}
static apy_value apy_set_algebra(const char *op, apy_value a, apy_value b,
                                 int which, int strict) {
    apy_value rhs, out;
    int64_t i;
    if (!apy_is_set(a) || (strict && !apy_is_set(b)))
        return apy_binop_error(op, a, b);
    rhs = apy_is_set(b) ? b : apy_set_from(APY_SET_K, b);
    if (!rhs) return 0;
    out = apy_seq_new(O(a)->kind, O(a)->v.q.n + O(rhs)->v.q.n + 1);
    if (which == APY_INTER) {
        /* WHICH OF THE TWO EQUAL ELEMENTS ENDS UP IN THE RESULT IS VISIBLE,
           because `1`, `1.0` and `True` are equal and print differently:
           `{1, 2} & {True}` is `{True}` in CPython and `{False, True} & {1}`
           is `{1}`. CPython iterates the SMALLER of the two sets and keeps
           the element it iterated, swapping only when the right side is
           strictly larger -- so on a tie the RIGHT operand's element wins.
           Keeping the left's unconditionally is the natural implementation
           and it disagrees with CPython on half of these.

           THE SWAP ONLY APPLIES WHEN THE RIGHT SIDE IS REALLY A SET. Given
           any other iterable CPython walks it whatever its length, so
           `{False, True}.intersection([1, 2, 3])` is `{1}` even though the
           list is longer. Applying the size rule to the coerced set answered
           `{True}` there. */
        int swap = apy_is_set(b) && O(rhs)->v.q.n > O(a)->v.q.n;
        apy_value from = swap ? a : rhs;
        apy_value test = swap ? rhs : a;
        for (i = 0; i < O(from)->v.q.n; i++)
            if (apy_set_find(test, O(from)->v.q.items[i]) >= 0)
                apy_q_append(out, O(from)->v.q.items[i]);
        return out;
    }
    /* Union and both differences keep the LEFT operand's element for anything
       the two have in common -- `{1, 2} | {True}` is `{1, 2}`, not
       `{True, 2}`. That falls out of walking `a` first. */
    for (i = 0; i < O(a)->v.q.n; i++) {
        apy_value item = O(a)->v.q.items[i];
        int there = apy_set_find(rhs, item) >= 0;
        if (!there || which == APY_UNION) apy_q_append(out, item);
    }
    if (which == APY_UNION || which == APY_SYMDIFF)
        for (i = 0; i < O(rhs)->v.q.n; i++)
            if (apy_set_find(a, O(rhs)->v.q.items[i]) < 0)
                apy_q_append(out, O(rhs)->v.q.items[i]);
    return out;
}

/* The METHOD forms. Any iterable on the right; a receiver that is not a set
   is an AttributeError, because `[1].union(...)` is a missing attribute in
   CPython and not a bad operand. */
/* Declared here because the exported half calls it. */
static apy_value apy_set_method(const char *name, apy_value a, apy_value b,
                                int which);
APY_API apy_value apy_set_method_of(apy_value name, apy_value a,
                                    apy_value b, int64_t which) {
    return apy_set_method((const char *)name, a, b, (int)which);
}
static apy_value apy_set_method(const char *name, apy_value a, apy_value b,
                                int which) {
    if (!apy_is_set(a))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(a), name);
    return apy_set_algebra(name, a, b, which, 0);
}

APY_API apy_value apy_set_union(apy_value a, apy_value b) {
    return apy_set_method("union", a, b, APY_UNION);
}
APY_API apy_value apy_set_intersection(apy_value a, apy_value b) {
    return apy_set_method("intersection", a, b, APY_INTER);
}
APY_API apy_value apy_set_difference(apy_value a, apy_value b) {
    return apy_set_method("difference", a, b, APY_DIFF);
}
APY_API apy_value apy_set_symdiff(apy_value a, apy_value b) {
    return apy_set_method("symmetric_difference", a, b, APY_SYMDIFF);
}

/* `issubset` / `issuperset` / `isdisjoint`, which take any iterable where the
   operators `<=` / `>=` demand a set -- the same split as the algebra. */
/* Declared here because the exported half calls it. */
static apy_value apy_set_relate(const char *name, apy_value a, apy_value b,
                                int which);
APY_API apy_value apy_set_relate_of(apy_value name, apy_value a,
                                    apy_value b, int64_t which) {
    return apy_set_relate((const char *)name, a, b, (int)which);
}
static apy_value apy_set_relate(const char *name, apy_value a, apy_value b,
                                int which) {
    apy_value rhs;
    int64_t i;
    if (!apy_is_set(a))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(a), name);
    rhs = apy_is_set(b) ? b : apy_set_from(APY_SET_K, b);
    if (!rhs) return 0;
    if (which == 0) return apy_from_bool(apy_subset(a, rhs));
    if (which == 1) return apy_from_bool(apy_subset(rhs, a));
    for (i = 0; i < O(a)->v.q.n; i++)
        if (apy_set_find(rhs, O(a)->v.q.items[i]) >= 0) return apy_from_bool(0);
    return apy_from_bool(1);
}

APY_API apy_value apy_set_issubset(apy_value a, apy_value b) {
    return apy_set_relate("issubset", a, b, 0);
}
APY_API apy_value apy_set_issuperset(apy_value a, apy_value b) {
    return apy_set_relate("issuperset", a, b, 1);
}
APY_API apy_value apy_set_isdisjoint(apy_value a, apy_value b) {
    return apy_set_relate("isdisjoint", a, b, 2);
}

/* The mutators. Every one of them refuses a frozenset by NAME -- a frozenset
   has no `add` attribute at all in CPython, so the report is an AttributeError
   about a missing attribute and not a TypeError about immutability. */
APY_API int64_t apy_mutable_set_of(apy_value name, apy_value s) {
    if (O(s)->kind == APY_SET_K) return 1;
    apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
              apy_kind_name(s), (const char *)name);
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_mutable_set(const char *name, apy_value s) {
    return (int)apy_mutable_set_of((apy_value)(uintptr_t)name, s);
}

APY_API apy_value apy_set_add(apy_value s, apy_value item) {
    if (!apy_mutable_set("add", s)) return 0;
    if (apy_set_insert(s, item) < 0) return 0;
    return apy_none();
}

/* `discard` on an absent element is a no-op; `remove` on one is a KeyError.
   That is the only difference between them, and it is why `remove` cannot
   simply call this. */
APY_API apy_value apy_set_discard(apy_value s, apy_value item) {
    int64_t at, k;
    if (!apy_mutable_set("discard", s)) return 0;
    {
        const char *bad = apy_unhashable(item);
        if (bad) return apy_unhashable_elem(item, bad);
    }
    at = apy_set_find(s, item);
    if (at >= 0) {
        for (k = at; k + 1 < O(s)->v.q.n; k++)
            O(s)->v.q.items[k] = O(s)->v.q.items[k + 1];
        O(s)->v.q.n--;
    }
    return apy_none();
}

/* `s.remove(x)` -- reached through `apy_list_remove`, which dispatches on the
   receiver's kind. The KeyError's message is the element's REPR, exactly as
   for a missing dict key: `{1}.remove(9)` says `KeyError: 9`. */
static apy_value apy_set_remove(apy_value s, apy_value item) {
    int64_t at;
    {
        const char *bad = apy_unhashable(item);
        if (bad) return apy_unhashable_elem(item, bad);
    }
    at = apy_set_find(s, item);
    if (at < 0) {
        apy_value shown = apy_repr(item);
        char buf[200];
        snprintf(buf, sizeof buf, "%.*s",
                 (int)O(shown)->v.s.n, O(shown)->v.s.p);
        return apy_fail("KeyError", buf);
    }
    return apy_set_discard(s, item);
}

/* `s.pop()` -- reached through `apy_list_pop`. Python's takes an ARBITRARY
   element and specifies nothing about which; this takes the first, which is
   the oldest inserted. cases/sets/mutation-methods checks only that what came
   out was in the set, which is the guarantee the language actually gives. */
static apy_value apy_set_pop(apy_value s) {
    apy_value taken;
    if (!apy_mutable_set("pop", s)) return 0;
    if (O(s)->v.q.n == 0)
        /* The quotes are IN the message on purpose. A KeyError's message is
           the repr of its argument -- that is why a missing str dict key
           reports `KeyError: 'k'` -- and CPython's argument here is the
           string "pop from an empty set", so the repr of it carries quotes.
           Writing it bare would print a traceback line CPython never does. */
        return apy_fail("KeyError", "'pop from an empty set'");
    taken = O(s)->v.q.items[0];
    apy_set_discard(s, taken);
    return taken;
}

"""
