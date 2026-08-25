"""The object runtime, in C: the list, dict and set methods.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * list, dict and set methods
"""

C = r"""/* --- list, dict and set methods ----------------------------------------- */
/* SEVERAL OF THESE SERVE MORE THAN ONE KIND, and that is forced rather than
   chosen: the frontend's method table is keyed by (method name, argument
   count) alone -- there is no receiver type to key on, because a dynamic value
   does not have one until run time. So `pop`, `remove`, `count` and `index`
   each get ONE symbol, and the dispatch on what the receiver actually is
   happens here. Splitting them into `apy_list_pop` and `apy_set_pop` at the
   ABI would mean the frontend deciding, which it cannot. */
/* `d.pop(k)` and `d.pop(k, default)`. A MISSING KEY WITH NO DEFAULT IS A
   KeyError, which is the whole difference from `d.get(k)` -- and the reason
   the two-argument form exists at all. */
static apy_value apy_dict_pop(apy_value d, apy_value key, apy_value fallback,
                              int64_t has_default) {
    int64_t at = apy_dict_find(d, key);
    if (at < 0) {
        apy_value shown;
        char buf[200];
        if (has_default) return fallback;
        /* The KEY'S REPR is the message, as every other missing-key report
           here does it -- `KeyError: 'a'` and not `KeyError: a`. */
        shown = apy_repr(key);
        snprintf(buf, sizeof buf, "%.*s",
                 (int)O(shown)->v.s.n, O(shown)->v.s.p);
        return apy_fail("KeyError", buf);
    }
    {
        apy_value taken = O(d)->v.d.vals[at];
        int64_t k, n = O(d)->v.d.n;
        for (k = at; k + 1 < n; k++) {
            O(d)->v.d.keys[k] = O(d)->v.d.keys[k + 1];
            O(d)->v.d.vals[k] = O(d)->v.d.vals[k + 1];
        }
        O(d)->v.d.n = n - 1;
        return taken;
    }
}

/* `d.pop(k, default)`. Its own entry point because the method table is keyed
   by ARGUMENT COUNT, and at two arguments `xs.pop(i)` cannot be meant. */
APY_API apy_value apy_pop_or(apy_value d, apy_value key, apy_value fallback) {
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("TypeError",
                         "pop() takes at most 1 argument for '%s'%s",
                         apy_kind_name(d), "");
    return apy_dict_pop(d, key, fallback, 1);
}

/* `d.popitem()` -- the LAST pair, and removed. Last rather than arbitrary:
   CPython has taken it from the end since dicts became ordered, and a program
   that pops a dict empty in a loop sees the reverse of insertion order. */
APY_API apy_value apy_dict_popitem(apy_value d) {
    apy_value out;
    int64_t n;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'popitem'%s",
                         apy_kind_name(d), "");
    n = O(d)->v.d.n;
    /* QUOTED, like `apy_set_pop`'s: `str(KeyError(x))` is the REPR of the
       argument, so a KeyError message carries its own quotes here rather than
       being re-quoted on the way out. */
    if (n == 0)
        return apy_fail("KeyError", "'popitem(): dictionary is empty'");
    out = apy_tuple_new(2);
    apy_seq_push(out, O(d)->v.d.keys[n - 1]);
    apy_seq_push(out, O(d)->v.d.vals[n - 1]);
    O(d)->v.d.n = n - 1;
    return out;
}

APY_API apy_value apy_list_pop(apy_value seq, apy_value index, int64_t given) {
    int64_t i, n, k;
    /* A DICT REACHES HERE TOO -- one method name, three receivers, and the
       frontend cannot know which it has until run time. `d.pop(k)` takes a
       KEY where `xs.pop(i)` takes an index, so the dict case has to be split
       off before anything treats the argument as a position. */
    if (O(seq)->kind == APY_DICT_K)
        return apy_dict_pop(seq, index, apy_none(), 0);
    if (apy_is_set(seq) && !given) return apy_set_pop(seq);
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("AttributeError", "'%s' object has no attribute 'pop'%s",
                         apy_kind_name(seq), "");
    n = O(seq)->v.q.n;
    if (n == 0) return apy_fail("IndexError", "pop from empty list");
    if (given) {
        if (!apy_index_arg(index, &i, APY_IDX_SIZE)) return 0;
    } else i = n - 1;
    if (i < 0) i += n;
    if (i < 0 || i >= n) return apy_fail("IndexError", "pop index out of range");
    {
        apy_value taken = O(seq)->v.q.items[i];
        for (k = i; k + 1 < n; k++)
            O(seq)->v.q.items[k] = O(seq)->v.q.items[k + 1];
        O(seq)->v.q.n = n - 1;
        return taken;
    }
}

/* `index` and `count` exist on str, list and tuple and on NOTHING else -- a
   dict and a set do not have them, and answering 0 for `{1}.count(x)` would be
   a wrong answer where CPython reports a missing attribute. Iterating anything
   iterable was the natural implementation and it is wrong in exactly that way,
   so the admissible kinds are named rather than inferred. */
static int apy_has_index(const char *name, apy_value v) {
    if (apy_is_seq(v) || O(v)->kind == APY_STR_K) return 1;
    apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
              apy_kind_name(v), name);
    return 0;
}

APY_API apy_value apy_index_of(apy_value seq, apy_value item) {
    /* ON A RANGE THIS IS ARITHMETIC, not a walk: the position of a value in
       `range(0, 10**12, 3)` is one division. */
    if (O(seq)->kind == APY_RANGE_K) {
        int64_t want, at;
        if (!apy_is_int_like(item))
            return apy_fail("ValueError", "value is not in range");
        if (!apy_index_arg(item, &want, APY_IDX_SIZE)) return 0;
        at = apy_range_find(seq, want);
        if (at < 0) return apy_fail("ValueError", "value is not in range");
        return apy_from_int(at);
    }
    int64_t i, n;
    /* A str receiver means SUBSTRING search, not element search. Falling
       through to the element loop below would answer correctly for a
       one-character needle and silently wrongly for every other one, because
       `apy_key_at` on a str yields single characters. */
    /* BYTES TOO: `b"abc".index(b"b")` is a SUBSTRING search, and the element
       loop below would answer for a one-byte needle and silently wrongly for
       any longer one. */
    if (O(seq)->kind == APY_STR_K || O(seq)->kind == APY_BYTES_K)
        return apy_str_index_of(seq, item);
    if (!apy_has_index("index", seq)) return 0;
    n = O(seq)->v.q.n;
    for (i = 0; i < n; i++)
        if (apy_eq_raw(O(seq)->v.q.items[i], item)) return apy_from_int(i);
    /* `list.index(x): x not in list`, naming the KIND and not the element.
       3.11 said `<repr> is not in list`, which is what a search of the
       internet still finds and what this used to report; 3.14 changed it and
       3.14 is what the suite is generated from. */
    return apy_fail2("ValueError", "%s.index(x): x not in %s",
                     apy_kind_name(seq), apy_kind_name(seq));
}

APY_API apy_value apy_count_of(apy_value seq, apy_value item) {
    /* A RANGE HOLDS EACH VALUE AT MOST ONCE, so the count is the membership
       test -- and arithmetic rather than a walk. */
    if (O(seq)->kind == APY_RANGE_K) {
        int64_t want;
        if (!apy_is_int_like(item)) return apy_from_int(0);
        if (!apy_index_arg(item, &want, APY_IDX_SIZE)) return 0;
        return apy_from_int(apy_range_find(seq, want) >= 0 ? 1 : 0);
    }
    int64_t i, n, hits = 0;
    /* Substring counting for a str -- and for BYTES, for the same reason
       `index` splits. */
    if (O(seq)->kind == APY_STR_K || O(seq)->kind == APY_BYTES_K)
        return apy_str_count_in(seq, item, 0, 0);
    if (!apy_has_index("count", seq)) return 0;
    n = O(seq)->v.q.n;
    for (i = 0; i < n; i++)
        if (apy_eq_raw(O(seq)->v.q.items[i], item)) hits++;
    return apy_from_int(hits);
}

APY_API apy_value apy_list_remove(apy_value seq, apy_value item) {
    apy_value at;
    if (apy_is_set(seq)) {
        /* A set's `remove` reports a KeyError naming the element, a list's a
           ValueError naming it differently. Same method name, two languages. */
        if (!apy_mutable_set("remove", seq)) return 0;
        return apy_set_remove(seq, item);
    }
    /* ONLY a list and a set have `remove`. Without this the miss below turns
       every other kind's missing attribute into `list.remove(x): x not in
       list`, which names a type the receiver is not. */
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'remove'%s",
                         apy_kind_name(seq), "");
    at = apy_index_of(seq, item);
    if (!at) {
        /* `remove` reports differently from `index` for the same miss. */
        apy_error_clear();
        return apy_fail("ValueError", "list.remove(x): x not in list");
    }
    return apy_list_pop(seq, at, 1);
}

/* `d.keys()` / `d.values()` / `d.items()` -- lists, not views. A view is live
   and these are snapshots, which differs if the dict is mutated while one is
   held; `list(d.keys())` is how the suite uses them and that is identical. */
APY_API apy_value apy_dict_parts(apy_value d, int64_t which) {
    /* A VIEW, not a snapshot -- `ks = d.keys()` then `d['b'] = 2` and
       `len(ks)` is 2. What it shows is read when it is asked, which is the
       whole of the liveness; see `apy_view_items`. */
    if (O(d)->kind == APY_DICT_K) return apy_dict_view(d, which);
    return apy_dict_parts_snapshot(d, which);
}

static apy_value apy_dict_parts_snapshot(apy_value d, int64_t which) {
    int64_t i;
    apy_value out;
    if (O(d)->kind != APY_DICT_K)
        /* NAMES THE ATTRIBUTE, which it did not: `(1).keys()` said
           "'int' object has no attribute" and stopped, because
           the format had a `%s` where the name goes and was handed
           "". `which` says which of the three was asked for. */
        return apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
                         apy_kind_name(d),
                         which == APY_PART_KEYS ? "keys"
                         : which == APY_PART_VALUES ? "values"
                         : "items");
    out = apy_seq_new(APY_LIST_K, O(d)->v.d.n + 1);
    for (i = 0; i < O(d)->v.d.n; i++) {
        if (which == 0) apy_seq_push(out, O(d)->v.d.keys[i]);
        else if (which == 1) apy_seq_push(out, O(d)->v.d.vals[i]);
        else {
            apy_value pair = apy_seq_new(APY_TUPLE_K, 2);
            apy_seq_push(pair, O(d)->v.d.keys[i]);
            apy_seq_push(pair, O(d)->v.d.vals[i]);
            apy_seq_push(out, pair);
        }
    }
    return out;
}

APY_API apy_value apy_dict_get_or(apy_value d, apy_value key, apy_value fallback) {
    int64_t at;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("AttributeError", "'%s' object has no attribute 'get'%s",
                         apy_kind_name(d), "");
    at = apy_dict_find(d, key);
    return at < 0 ? fallback : O(d)->v.d.vals[at];
}

/* `.update(x)` on a set (any iterable of elements) or on a dict (a dict of
   pairs). One symbol for the same reason `pop` is one symbol. */
APY_API apy_value apy_update(apy_value target, apy_value src) {
    int64_t n, i;
    if (O(target)->kind == APY_DICT_K) {
        if (O(src)->kind == APY_DICT_K) {
            for (i = 0; i < O(src)->v.d.n; i++)
                if (!apy_dict_set(target, O(src)->v.d.keys[i],
                                  O(src)->v.d.vals[i]))
                    return 0;
            return apy_none();
        }
        /* NOT ONLY A MAPPING. `d.update([(1, 2), (3, 4)])` is legal and so is
           `d.update(['ab'])` -- any iterable of two-element iterables, which
           is why a str of two-character strings works and a str of characters
           does not. The three failures are three different reports: a
           non-iterable argument names its kind, a non-iterable ELEMENT does
           not name anything, and an element of the wrong length is a
           ValueError giving its position and its length. */
        n = apy_raw_len(src);
        if (apy_error_occurred()) return 0;
        for (i = 0; i < n; i++) {
            apy_value pair = apy_key_at(src, i);
            int64_t len;
            if (!pair) return 0;
            /* ANY iterable, not just a pair-shaped one. `[{1: 2}]` gets as
               far as the length check and fails there, naming its length --
               only a genuinely non-iterable element gets the bare message. */
            if (O(pair)->kind != APY_STR_K && !apy_is_seq(pair)
                && !apy_is_set(pair) && O(pair)->kind != APY_DICT_K)
                return apy_fail("TypeError", "object is not iterable");
            len = apy_raw_len(pair);
            if (apy_error_occurred()) return 0;
            if (len != 2) {
                char buf[128];
                snprintf(buf, sizeof buf,
                         "dictionary update sequence element #%lld has "
                         "length %lld; 2 is required",
                         (long long)i, (long long)len);
                return apy_fail("ValueError", buf);
            }
            if (!apy_dict_set(target, apy_key_at(pair, 0), apy_key_at(pair, 1)))
                return 0;
        }
        return apy_none();
    }
    if (!apy_mutable_set("update", target)) return 0;
    n = apy_raw_len(src);
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(src, i);
        if (!item) return 0;
        if (apy_set_insert(target, item) < 0) return 0;
    }
    return apy_none();
}

/* `.clear()` -- empties in place and answers None. Setting the count to zero
   rather than freeing: nothing here frees, and the items array is reused. */
APY_API apy_value apy_clear(apy_value v) {
    if (O(v)->kind == APY_DICT_K) { O(v)->v.d.n = 0; return apy_none(); }
    if (O(v)->kind == APY_LIST_K || O(v)->kind == APY_SET_K) {
        O(v)->v.q.n = 0;
        return apy_none();
    }
    return apy_fail2("AttributeError", "'%s' object has no attribute 'clear'%s",
                     apy_kind_name(v), "");
}

/* `.copy()` -- SHALLOW, like Python's: the new container holds the same
   elements, not copies of them. A frozenset's copy is itself, which is what
   CPython returns and is safe for the same reason `frozenset(f)` is. */
APY_API apy_value apy_copy(apy_value v) {
    int64_t i;
    apy_value out;
    if (O(v)->kind == APY_FROZEN_K) return v;
    if (O(v)->kind == APY_DICT_K) {
        out = apy_dict_new_cap(O(v)->v.d.n + 1);
        for (i = 0; i < O(v)->v.d.n; i++)
            if (!apy_dict_set(out, O(v)->v.d.keys[i], O(v)->v.d.vals[i]))
                return 0;
        return out;
    }
    if (O(v)->kind == APY_LIST_K || O(v)->kind == APY_SET_K) {
        out = apy_seq_new(O(v)->kind, O(v)->v.q.n + 1);
        for (i = 0; i < O(v)->v.q.n; i++) apy_q_append(out, O(v)->v.q.items[i]);
        return out;
    }
    return apy_fail2("AttributeError", "'%s' object has no attribute 'copy'%s",
                     apy_kind_name(v), "");
}

/* `x += y` -- the IN-PLACE operators, which are not sugar for `x = x + y`.

   A list EXTENDS ITSELF and hands itself back, so every other name bound to it
   sees the new elements; a tuple has no in-place form and falls through to
   `+`, which builds a new one and leaves the caller's alone. That difference
   is the whole of `__iadd__`, and it is observable from another frame:

       def extend(xs): xs += [99]     # the caller's list grows
       def rebind(t):  t += (99,)     # the caller's tuple does not

   Rewriting `+=` to `+` got the second right and the first wrong, silently,
   for every list passed to a function that appends to it. */
APY_API apy_value apy_iadd(apy_value a, apy_value b) {
    if (O(a)->kind == APY_INST_K) {
        apy_value r = apy_method1(a, "__iadd__", b);
        if (r || apy_error_occurred()) return r;
    }
    if (O(a)->kind == APY_LIST_K) {
        if (!apy_extend(a, b)) return 0;
        return a;
    }
    return apy_add(a, b);
}

/* `s |= other`, `s &= other`, `s -= other`, `s ^= other` on a SET, and
   `d |= other` on a dict -- the same in-place rule as `+=` and for the same
   reason: the object other names hold must change. */
APY_API apy_value apy_iop(apy_value a, apy_value b, apy_value op) {
    const char *what = APY_CSTR(op);
    if (O(a)->kind == APY_INST_K) {
        char name[16];
        snprintf(name, sizeof name, "__i%s__",
                 what[0] == '|' ? "or" : what[0] == '&' ? "and"
                 : what[0] == '^' ? "xor" : what[0] == '-' ? "sub" : "mul");
        {
            apy_value r = apy_method1(a, name, b);
            if (r || apy_error_occurred()) return r;
        }
    }
    if (O(a)->kind == APY_DICT_K && what[0] == '|') {
        if (!apy_update(a, b)) return 0;
        return a;
    }
    if (O(a)->kind == APY_SET_K) {
        apy_value out = what[0] == '|' ? apy_bitor(a, b)
            : what[0] == '&' ? apy_bitand(a, b)
            : what[0] == '^' ? apy_bitxor(a, b)
            : apy_sub(a, b);
        int64_t i;
        if (!out) return 0;
        /* Computed then copied back, rather than mutated as it goes: the two
           operands may be the SAME set, and `s &= s` would otherwise read
           elements it had already removed. */
        O(a)->v.q.n = 0;
        for (i = 0; i < O(out)->v.q.n; i++)
            apy_q_append(a, O(out)->v.q.items[i]);
        return a;
    }
    if (what[0] == '|') return apy_bitor(a, b);
    if (what[0] == '&') return apy_bitand(a, b);
    if (what[0] == '^') return apy_bitxor(a, b);
    if (what[0] == '-') return apy_sub(a, b);
    return apy_mul(a, b);
}

/* `xs.insert(i, v)`. The index is CLAMPED, not checked -- `insert(99, v)` on a
   two-element list appends, and `insert(-99, v)` prepends. That is Python's
   rule and it is why `insert` never raises IndexError where `xs[i] = v` does. */
APY_API apy_value apy_list_insert(apy_value seq, apy_value where,
                                  apy_value item) {
    int64_t n, i, at;
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'insert'%s",
                         apy_kind_name(seq), "");
    if (!apy_is_int_like(where))
        return apy_fail2("TypeError",
                         "'%s' object cannot be interpreted as an integer%s",
                         apy_kind_name(where), "");
    n = O(seq)->v.q.n;
    at = O(where)->v.i;
    if (at < 0) at += n;
    if (at < 0) at = 0;
    if (at > n) at = n;
    apy_q_append(seq, item);                 /* grow by one, then shift up */
    for (i = O(seq)->v.q.n - 1; i > at; i--)
        O(seq)->v.q.items[i] = O(seq)->v.q.items[i - 1];
    O(seq)->v.q.items[at] = item;
    return apy_none();
}

/* `xs.sort()` -- IN PLACE and answering None, which is the whole difference
   from `sorted(xs)`. Sorting a copy and rebinding would leave every other
   reference to the list unsorted, and sharing a list is the reason a program
   sorts in place. */
APY_API apy_value apy_list_sort(apy_value seq, apy_value keyfn,
                                apy_value reverse) {
    apy_value out;
    int64_t i;
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'sort'%s",
                         apy_kind_name(seq), "");
    out = apy_sorted_by(seq, keyfn, reverse);
    if (!out) return 0;
    for (i = 0; i < O(out)->v.q.n; i++)
        O(seq)->v.q.items[i] = O(out)->v.q.items[i];
    return apy_none();
}

/* `xs.reverse()` -- in place, and None. `reversed(xs)` is the other one. */
APY_API apy_value apy_list_reverse(apy_value seq) {
    int64_t i, n;
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'reverse'%s",
                         apy_kind_name(seq), "");
    n = O(seq)->v.q.n;
    for (i = 0; i < n / 2; i++) {
        apy_value t = O(seq)->v.q.items[i];
        O(seq)->v.q.items[i] = O(seq)->v.q.items[n - 1 - i];
        O(seq)->v.q.items[n - 1 - i] = t;
    }
    return apy_none();
}

/* `d.setdefault(k, v)` -- read, and INSERT when missing. One lookup's worth of
   difference from `d.get(k, v)`, and the difference is the whole point: the
   dict is left holding the default. */
APY_API apy_value apy_setdefault(apy_value d, apy_value key,
                                 apy_value fallback) {
    int64_t at;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'setdefault'%s",
                         apy_kind_name(d), "");
    at = apy_dict_find(d, key);
    if (at >= 0) return O(d)->v.d.vals[at];
    if (!apy_dict_set(d, key, fallback)) return 0;
    return fallback;
}

/* `s.encode()` and `b.decode()`.

   The bytes ARE the str's bytes: this runtime stores text as UTF-8 already, so
   encoding is a change of KIND and not of content. That makes both exact for
   UTF-8 and wrong for every other codec, which is why neither takes an
   encoding argument -- offering one it would ignore is worse than not having
   it. */
"""
