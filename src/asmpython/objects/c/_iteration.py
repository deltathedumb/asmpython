"""The object runtime, in C: the iteration protocol.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * the iteration protocol
"""

C = r"""/* --- the iteration protocol --------------------------------------------- */
/* `for v in x` -- ADVANCE UNTIL DONE, not walk by index.

   The index walk this replaces read the length once and then asked for 0, 1,
   2... It was simple and it was wrong in two ways that no amount of care at
   the call site could fix: a generator has no length until it has been run,
   so iterating one had to drain it first and laziness was impossible; and a
   body that appends to the list it is walking saw the length from before,
   where CPython sees the new elements.

   Two entry points. `apy_getiter` turns a value into something with a
   position -- which for a generator is the generator itself, for a user
   object whatever `__iter__` said, and for a container a cursor over it.
   `apy_step` advances one place and answers `apy_stop()` at the end.

   THE SENTINEL IS A CELL, not a null: null already means "an error is set",
   and exhaustion is not an error. One static cell, so the test is a pointer
   compare. */
static apy_obj apy_stop_cell = { APY_NONE_K, { 0 } };

APY_API apy_value apy_stop(void) { return V(&apy_stop_cell); }

/* Is this the exhaustion sentinel? A pointer comparison the IR cannot spell,
   so it is a call. */
APY_API int64_t apy_is_stop(apy_value v) { return v == apy_stop(); }

/* ONE STEP OF A `yield from`, with the value the outer generator was SENT.

   Delegation has to STEP the inner generator rather than drain it: `got =
   yield ...` inside the inner one reads what the OUTER was sent, and a drained
   generator has already run past every such point with nothing. A source that
   is not a generator has nowhere to put the sent value and is simply
   advanced, which is what `yield from [1, 2]` means. */
APY_API apy_value apy_delegate_step(apy_value src, apy_value sent) {
    if (O(src)->kind == APY_GEN_K) {
        int done = 0;
        apy_value v = apy_gen_step(src, sent, &done);
        if (!v) return 0;
        return done ? apy_stop() : v;
    }
    return apy_step(src);
}

/* Every cursor is built here, so no site forgets a field. */
APY_API apy_value apy_cursor_of(apy_value src, apy_value fn,
                                int64_t mode, int64_t start) {
    apy_obj *o = apy_alloc(APY_ITER_K);
    o->v.it.src = src;
    o->v.it.fn = fn;
    o->v.it.mode = (int)mode;
    o->v.it.i = start;
    o->v.it.n0 = (src && O(src)->kind == APY_DICT_K) ? O(src)->v.d.n : -1;
    return V(o);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static apy_value apy_cursor(apy_value src, apy_value fn, int mode,
                            int64_t start) {
    return apy_cursor_of(src, fn, (int64_t)mode, start);
}

APY_API apy_value apy_getiter(apy_value v) {
    /* A VIEW IS READ WHEN THE WALK STARTS, which is what `for k in d.keys()`
       expects: the keys are the ones the dict has now, not the ones it had
       when the view was made. */
    if (O(v)->kind == APY_VIEW_K) return apy_getiter(apy_view_items(v));
    /* A generator IS its own cursor: stepping it resumes it, and that is the
       whole of the lazy path. */
    if (O(v)->kind == APY_GEN_K) return v;
    if (O(v)->kind == APY_ITER_K) return v;
    if (O(v)->kind == APY_TYPE_K && O(v)->v.t.meta) {
        /* ITERATING A CLASS IS THE METACLASS'S BUSINESS: `for c in Color` is
           `type(Color).__iter__(Color)`, which is how an enum lists its
           members. A class with no metaclass cannot be iterated, and the
           refusal below is still the right answer for it. */
        apy_value hook = apy_class_find(O(v)->v.t.meta, apy_name("__iter__"));
        if (hook) {
            apy_value got = apy_call_n(apy_bind(hook, v), NULL, 0);
            if (!got) return 0;
            if (O(got)->kind == APY_GEN_K || O(got)->kind == APY_ITER_K)
                return got;
            if (O(got)->kind == APY_INST_K
                && apy_class_find(O(got)->v.o.cls, apy_name("__next__")))
                return got;
            return apy_fail2("TypeError",
                             "iter() returned non-iterator of type '%s'%s",
                             apy_kind_name(got), "");
        }
    }
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__iter__");
        if (apy_error_occurred()) return 0;
        if (got) {
            /* WHAT `__iter__` RETURNS MUST BE AN ITERATOR. A generator or a
               cursor is one, and so is a user object with `__next__`; a str
               is not, however walkable it looks. Walking it anyway turned a
               broken class into a working one that iterated something else
               entirely -- CPython refuses, and naming the type is what tells
               the author which method to fix. */
            if (O(got)->kind == APY_GEN_K || O(got)->kind == APY_ITER_K)
                return got;
            if (O(got)->kind == APY_INST_K
                && apy_class_find(O(got)->v.o.cls, apy_name("__next__")))
                return got;
            return apy_fail2("TypeError",
                             "iter() returned non-iterator of type '%s'%s",
                             apy_kind_name(got), "");
        }
        /* THE BUILTIN AGAIN, and here as well as in `apy_iter` because this
           is the entry a GENERATOR's `for` uses -- it steps a cursor rather
           than walking by index, since an index walk cannot survive a
           suspension. Fixing only `apy_iter` left `[k for k in d]` working
           and `(k for k in d)` not, which is the same loop written twice.

           `apy_getiter` AND NOT `apy_iterable`: this function answers a
           CURSOR, and `apy_iterable` answers the thing to walk -- returning
           the latter handed a bare dict to the stepper, which reported
           `'dict' object is not an iterator`. */
        if (O(v)->v.o.held) return apy_getiter(O(v)->v.o.held);
        /* No `__iter__`: `__len__` plus `__getitem__`, or `__getitem__`
           walked until it reports IndexError. A cursor over the object does
           both, since `apy_step` reads through `apy_getitem`. */
        if (!apy_class_find(O(v)->v.o.cls, apy_name("__getitem__")))
            return apy_fail2("TypeError", "'%s' object is not iterable%s",
                             apy_kind_name(v), "");
    } else if (!apy_is_seq(v) && !apy_is_set(v) && O(v)->kind != APY_STR_K
               && O(v)->kind != APY_BYTES_K && O(v)->kind != APY_DICT_K
               && O(v)->kind != APY_RANGE_K) {
        return apy_fail2("TypeError", "'%s' object is not iterable%s",
                         apy_kind_name(v), "");
    }
    return apy_cursor(v, 0, APY_IT_PLAIN, 0);
}

/* Walk a cursor to the end and BECOME a plain one over what it produced.

   Asking a lazy thing for its length is asking it to run, and the honest
   answer is to run it once and keep the result -- so a length query followed
   by an index walk sees the same elements, and a second length query is
   cheap. What is consumed stays consumed: the cursor is at the start of the
   list it produced, not of the source it came from. */
static apy_value apy_drain_cursor(apy_value it) {
    apy_value out = apy_seq_new(APY_LIST_K, 8);
    int64_t guard;
    for (guard = 0; guard < 100000000; guard++) {
        apy_value v = apy_step(it);
        if (!v) return 0;
        if (v == apy_stop()) break;
        apy_seq_push(out, v);
    }
    O(it)->v.it.src = out;
    O(it)->v.it.fn = 0;
    O(it)->v.it.mode = APY_IT_PLAIN;
    O(it)->v.it.i = 0;
    return out;
}

APY_API apy_value apy_step(apy_value it) {
    if (O(it)->kind == APY_GEN_K) {
        int done;
        apy_value v = apy_gen_step(it, apy_none(), &done);
        if (!v) return 0;
        return done ? apy_stop() : v;
    }
    if (O(it)->kind == APY_INST_K) {
        /* A user iterator: `__next__` until StopIteration, which is the
           protocol rather than a sentinel here. */
        apy_value v = apy_unary_dunder(it, "__next__");
        if (v) return v;
        if (apy_error_matches(apy_lit("StopIteration"))) {
            apy_error_clear();
            return apy_stop();
        }
        if (apy_error_occurred()) return 0;
        return apy_fail2("TypeError", "'%s' object is not an iterator%s",
                         apy_kind_name(it), "");
    }
    if (O(it)->kind != APY_ITER_K)
        return apy_fail2("TypeError", "'%s' object is not an iterator%s",
                         apy_kind_name(it), "");
    switch (O(it)->v.it.mode) {
    case APY_IT_MAP: {
        apy_value v = apy_step(O(it)->v.it.src);
        if (!v || v == apy_stop()) return v;
        return apy_call_n(O(it)->v.it.fn, &v, 1);
    }
    case APY_IT_FILTER:
        for (;;) {
            apy_value v = apy_step(O(it)->v.it.src), keep;
            if (!v || v == apy_stop()) return v;
            /* `filter(None, xs)` keeps the truthy elements -- a real form, and
               why the callable is TESTED rather than simply called. */
            keep = O(O(it)->v.it.fn)->kind == APY_NONE_K
                ? v : apy_call_n(O(it)->v.it.fn, &v, 1);
            if (!keep) return 0;
            if (apy_truth(keep)) return v;
        }
    case APY_IT_ENUMERATE: {
        apy_value v = apy_step(O(it)->v.it.src), pair;
        if (!v || v == apy_stop()) return v;
        pair = apy_seq_new(APY_TUPLE_K, 2);
        apy_seq_push(pair, apy_from_int(O(it)->v.it.i++));
        apy_seq_push(pair, v);
        return pair;
    }
    case APY_IT_ZIP: {
        apy_value row, cursors = O(it)->v.it.src;
        int64_t k, n = O(cursors)->v.q.n;
        /* `zip()` with no arguments is EMPTY, not endless -- the loop below
           would otherwise find nothing to stop it. */
        if (n == 0) return apy_stop();
        row = apy_seq_new(APY_TUPLE_K, n + 1);
        for (k = 0; k < n; k++) {
            apy_value v = apy_step(O(cursors)->v.q.items[k]);
            if (!v) return 0;
            /* STOPS AT THE SHORTEST, which is what makes zip lossy and what
               every caller relies on. `strict` reports instead. */
            if (v == apy_stop()) {
                if (O(it)->v.it.fn && apy_truth(O(it)->v.it.fn) && k > 0)
                    return apy_fail("ValueError",
                                    "zip() argument 2 is shorter than "
                                    "argument 1");
                return apy_stop();
            }
            apy_seq_push(row, v);
        }
        return row;
    }
    default: break;
    }
    {
        apy_value src = O(it)->v.it.src;
        int64_t at = O(it)->v.it.i;
        if (O(src)->kind == APY_INST_K) {
            /* Walked through `__getitem__`, ending on the IndexError the
               class raises -- CPython's rule for the older protocol. */
            apy_value got = apy_getitem(src, apy_from_int(at));
            if (!got) {
                if (apy_error_matches(apy_lit("IndexError"))) {
                    apy_error_clear();
                    return apy_stop();
                }
                return 0;
            }
            O(it)->v.it.i = at + 1;
            return got;
        }
        /* THE LENGTH IS READ EVERY STEP, which is the point: a body that
           appends to the list it is walking sees the new elements, and one
           that shortens it stops early -- both as CPython does. */
        {
            int64_t n = apy_raw_len(src);
            if (apy_error_occurred()) return 0;
            /* A DICT THAT CHANGED SIZE UNDER THE WALK. The table is rehashed
               by the write, so continuing would skip or repeat entries; the
               refusal is what makes the loss impossible rather than
               occasional. */
            if (O(it)->v.it.n0 >= 0 && O(src)->kind == APY_DICT_K
                    && n != O(it)->v.it.n0)
                return apy_fail("RuntimeError",
                                "dictionary changed size during iteration");
            if (at >= n) return apy_stop();
            O(it)->v.it.i = at + 1;
            return apy_key_at(src, at);
        }
    }
}

/* `hash(x)`. THE VALUES ARE NOT CPYTHON'S and are not meant to be: CPython
   salts str and bytes hashes per process, so there is no fixed number to
   agree with, and the conformance suite's own README lists `hash()` among the
   implementation accidents it deliberately does not pin (tier `impl`).

   What IS observable and IS reproduced: WHICH values have a hash at all.
   `hash({1, 2})` is a TypeError and `hash(frozenset([1, 2]))` is a number,
   and cases/sets/frozenset-is-hashable checks exactly that.

   Equal values must hash equally or a dict built on these would lose keys, so
   the numeric kinds all reduce to the integer they equal where they can, and
   a frozenset combines its elements with XOR -- commutative, because a set has
   no order for the hash to depend on. */
/* THE EXPORTED HALF, which `runtime/containers.py` SPLITS: the IR hashes
   None, integers, strings, bytes, tuples and frozensets, and a float, a big
   or an instance comes back here. */
APY_API int64_t apy_hash_raw_of(apy_value v) {
    return apy_hash_raw(v);
}
static int64_t apy_hash_raw(apy_value v) {
    int64_t i, h;
    switch (O(v)->kind) {
    case APY_NONE_K:  return 0x5eed10d;
    case APY_BOOL_K:
    case APY_INT_K:   return O(v)->v.i;
    case APY_FLOAT_K:
        /* An integral float hashes as the integer it equals, so `{1: 'a'}`
           and `d[1.0]` find each other. Anything else is its bit pattern. */
        if (O(v)->v.f == floor(O(v)->v.f)
            && O(v)->v.f >= -9223372036854775808.0
            && O(v)->v.f < 9223372036854775808.0)
            return (int64_t)O(v)->v.f;
        { double f = O(v)->v.f; int64_t bits; memcpy(&bits, &f, sizeof bits);
          return bits; }
    case APY_BIG_K:
        /* No big equals an int64 -- see the normalisation invariant -- so
           this cannot collide with the integer case in a way that matters,
           and it needs no agreement with it either. */
        h = (int64_t)0x9e3779b9ULL;
        for (i = 0; i < O(v)->v.big.n; i++)
            h = (int64_t)((uint64_t)h * 1000003ULL) ^ O(v)->v.big.limb[i];
        return O(v)->v.big.neg ? -h : h;
    case APY_STR_K:
        h = (int64_t)0xcbf29ce484222325ULL;      /* FNV-1a */
        for (i = 0; i < O(v)->v.s.n; i++) {
            h ^= (unsigned char)O(v)->v.s.p[i];
            h = (int64_t)((uint64_t)h * 0x100000001b3ULL);
        }
        return h;
    case APY_TUPLE_K:
        h = 0x345678;
        for (i = 0; i < O(v)->v.q.n; i++)
            h = (int64_t)((uint64_t)h * 1000003ULL) ^ apy_hash_raw(O(v)->v.q.items[i]);
        return h ^ O(v)->v.q.n;
    case APY_FROZEN_K:
        h = 0;
        for (i = 0; i < O(v)->v.q.n; i++)
            h ^= apy_hash_raw(O(v)->v.q.items[i]) * (int64_t)0x9e3779b97f4a7c15ULL;
        return h ^ O(v)->v.q.n;
    case APY_INST_K: {
        /* The class decides. `__hash__` returns an int, and returning
           anything else is the class's error, not something to paper over. */
        apy_value h = apy_unary_dunder(v, "__hash__");
        if (h) {
            if (O(h)->kind == APY_INT_K || O(h)->kind == APY_BOOL_K)
                return O(h)->v.i;
            apy_fail2("TypeError", "__hash__ method should return an "
                                   "integer, not '%s'%s", apy_kind_name(h), "");
            return 0;
        }
        if (apy_error_occurred()) return 0;
        /* A CLASS EXTENDING A BUILTIN HASHES AS THE BUILTIN, so two equal
           namedtuples land in the same bucket. The address would not: they
           compare equal through the held tuple and would still both sit in a
           set, which is a wrong ANSWER rather than an error. */
        if (O(v)->v.o.held
                && !apy_class_find(O(v)->v.o.cls, apy_name("__eq__")))
            return apy_hash_raw(O(v)->v.o.held);
        /* DEFINING `__eq__` AND NOT `__hash__` MAKES A CLASS UNHASHABLE.
           Saying so is what turns a dict key of one into an error rather
           than a lookup that silently finds nothing -- two equal objects
           hashed by address land in different buckets. The interpreter host
           has always enforced this; without it here the two paths printed
           different things for the same program. */
        if (apy_class_find(O(v)->v.o.cls, apy_name("__eq__"))) {
            char buf[128];
            snprintf(buf, sizeof buf, "unhashable type: '%s'",
                     APY_CSTR(O(O(v)->v.o.cls)->v.t.name));
            apy_fail("TypeError", buf);
            return 0;
        }
        return (int64_t)v;
    }
    default:
        return (int64_t)v;      /* by identity, as CPython does for objects */
    }
}

APY_API apy_value apy_hash(apy_value v) {
    const char *bad = apy_unhashable(v);
    if (bad) {
        char buf[128];
        /* The BARE form. `hash([1])` says only `unhashable type: 'list'`;
           the longer "cannot use ... as a dict key" wrapper belongs to the
           container that refused it, and there is no container here. */
        snprintf(buf, sizeof buf, "unhashable type: '%s'", bad);
        return apy_fail("TypeError", buf);
    }
    return apy_from_int(apy_hash_raw(v));
}

"""
