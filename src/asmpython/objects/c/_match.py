"""The object runtime, in C: `match`, and PEP 750 template strings.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * `match`
  * PEP 750 template strings
"""

C = r"""/* --- `match` ------------------------------------------------------------
   The predicates a `case` pattern needs that nothing else does. Class and
   value patterns reuse `apy_isinstance` and `apy_eq`; these three are the
   parts with rules of their own. */

/* Does a SEQUENCE pattern -- `case [a, b]` -- apply to this value?

   A str is NOT a sequence for matching, and neither is bytes. `case [x, y]`
   against "ab" must not bind 'a' and 'b': Python excludes them precisely
   because matching a string element-wise is almost never what was meant, and
   the loop that did it would silently succeed. */
APY_API int64_t apy_match_seq(apy_value v) {
    return O(v)->kind == APY_LIST_K || O(v)->kind == APY_TUPLE_K;
}

/* Does a MAPPING pattern -- `case {"k": v}` -- apply? */
APY_API int64_t apy_match_map(apy_value v) {
    return O(v)->kind == APY_DICT_K;
}

/* `cls.__match_args__`, or an empty tuple.

   POSITIONAL SUB-PATTERNS ARE ATTRIBUTE NAMES: `case Point(0, y)` means
   "attribute `x` equals 0, bind attribute `y`", and the class says which
   attributes those are. A class without the declaration accepts no positional
   patterns at all, which is a TypeError in CPython and an empty tuple here --
   the length check that follows reports it. */
APY_API apy_value apy_match_args(apy_value cls) {
    apy_value got;
    if (O(cls)->kind != APY_TYPE_K) return apy_tuple_new(1);
    got = apy_class_find(cls, apy_name("__match_args__"));
    if (got && (O(got)->kind == APY_TUPLE_K || O(got)->kind == APY_LIST_K))
        return got;
    return apy_tuple_new(1);
}

/* What `**rest` in a mapping pattern binds: the dict MINUS the keys the
   pattern named. A copy, because the subject must not change shape because
   something matched it. */
APY_API apy_value apy_match_rest(apy_value d, apy_value used) {
    apy_value out;
    int64_t i, k;
    if (O(d)->kind != APY_DICT_K) return apy_dict_new(1);
    out = apy_dict_new(O(d)->v.d.n ? O(d)->v.d.n : 1);
    for (i = 0; i < O(d)->v.d.n; i++) {
        int skip = 0;
        for (k = 0; k < O(used)->v.q.n; k++)
            if (apy_eq_raw(O(d)->v.d.keys[i], O(used)->v.q.items[k])) {
                skip = 1;
                break;
            }
        if (!skip && !apy_dict_set(out, O(d)->v.d.keys[i],
                                   O(d)->v.d.vals[i])) return 0;
    }
    return out;
}

/* `dir(x)` -- the names it answers to, SORTED.

   `__dir__` overrides the whole computation when a class defines one, and its
   answer is sorted but NOT deduplicated: CPython sorts what the method
   returned and hands it back, so a class returning ["b", "a", "a"] gets
   ["a", "a", "b"]. Deduplicating would be tidier and would disagree.

   Without the hook it is the instance's own attributes plus every class in
   the chain -- which IS deduplicated, because a subclass overriding a method
   must not make it appear twice. */
APY_API apy_value apy_dir(apy_value v) {
    apy_value out, hook;
    if (O(v)->kind == APY_INST_K
            && (hook = apy_class_find(O(v)->v.o.cls, apy_name("__dir__")))) {
        apy_value got = apy_call_n(apy_bind(hook, v), NULL, 0);
        if (!got) return 0;
        got = apy_iterable(got);
        if (!got) return 0;
        return apy_sorted(got);
    }
    out = apy_seq_new(APY_LIST_K, 8);
    if (O(v)->kind == APY_INST_K) {
        apy_value cls = O(v)->v.o.cls;
        apy_value d = O(v)->v.o.dict;
        int64_t i;
        for (i = 0; i < O(d)->v.d.n; i++)
            if (apy_set_find(out, O(d)->v.d.keys[i]) < 0)
                apy_seq_push(out, O(d)->v.d.keys[i]);
        while (cls && O(cls)->kind == APY_TYPE_K) {
            apy_value cd = O(cls)->v.t.dict;
            for (i = 0; i < O(cd)->v.d.n; i++)
                if (apy_set_find(out, O(cd)->v.d.keys[i]) < 0)
                    apy_seq_push(out, O(cd)->v.d.keys[i]);
            cls = O(cls)->v.t.base;
        }
    } else if (O(v)->kind == APY_TYPE_K) {
        apy_value cls = v;
        int64_t i;
        while (cls && O(cls)->kind == APY_TYPE_K) {
            apy_value cd = O(cls)->v.t.dict;
            for (i = 0; i < O(cd)->v.d.n; i++)
                if (apy_set_find(out, O(cd)->v.d.keys[i]) < 0)
                    apy_seq_push(out, O(cd)->v.d.keys[i]);
            cls = O(cls)->v.t.base;
        }
    }
    /* A built-in kind answers an empty list rather than a made-up one: the
       method table lives in the frontend, not in a place this can enumerate,
       and inventing a partial list would be worse than admitting to none. */
    return apy_sorted(out);
}

/* `ExceptionGroup(msg, [excs])`. */
APY_API apy_value apy_excgroup_new(apy_value msg, apy_value excs) {
    apy_value g;
    excs = apy_iterable(excs);
    if (!excs) return 0;
    if (!apy_is_seq(excs))
        return apy_fail("TypeError",
                        "second argument (exceptions) must be a sequence");
    if (O(excs)->v.q.n == 0)
        return apy_fail("ValueError",
                        "second argument (exceptions) must be a non-empty "
                        "sequence");
    g = apy_make_exc(apy_lit("ExceptionGroup"), msg);
    if (!g) return 0;
    O(g)->v.e.subs = excs;
    return g;
}

/* Every leaf of `g` that matches `want`, as a group of the same shape -- or 0
   when nothing in it does.

   THE NESTING IS PRESERVED: a match inside an inner group comes back inside
   an inner group, because `split` is defined to give you back something you
   could have raised, not a flat list. That is what makes the two halves add
   up to the original. */
APY_API apy_value apy_group_select_of(apy_value g, apy_value want,
                                     int64_t keep) {
    apy_value picked, out;
    int64_t i;
    if (O(g)->kind != APY_EXC_K || !O(g)->v.e.subs) return 0;
    picked = apy_seq_new(APY_LIST_K, 4);
    for (i = 0; i < O(O(g)->v.e.subs)->v.q.n; i++) {
        apy_value one = O(O(g)->v.e.subs)->v.q.items[i];
        if (O(one)->kind == APY_EXC_K && O(one)->v.e.subs) {
            apy_value inner = apy_group_select_of(one, want, keep);
            if (inner) apy_seq_push(picked, inner);
            continue;
        }
        {
            apy_value hit = apy_isinstance(one, want);
            if (!hit) return 0;
            if (apy_truth(hit) == (keep ? 1 : 0)) apy_seq_push(picked, one);
        }
    }
    if (O(picked)->v.q.n == 0) return 0;
    out = apy_make_exc(apy_lit("ExceptionGroup"),
                       O(g)->v.e.has_arg ? O(g)->v.e.arg : apy_none());
    if (!out) return 0;
    O(out)->v.e.subs = picked;
    return out;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_group_select(apy_value g, apy_value want,
                                  int keep) {
    return apy_group_select_of(g, want, (int64_t)keep);
}

/* `g.subgroup(T)` -- the part that matches, or None. */
APY_API apy_value apy_group_subgroup(apy_value g, apy_value want) {
    apy_value got;
    if (O(g)->kind != APY_EXC_K || !O(g)->v.e.subs)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'subgroup'%s",
                         apy_kind_name(g), "");
    got = apy_group_select(g, want, 1);
    if (apy_error_occurred()) return 0;
    return got ? got : apy_none();
}

/* `g.split(T)` -- `(matching, rest)`, either of which may be None. Together
   they hold every leaf the original did. */
APY_API apy_value apy_group_split(apy_value g, apy_value want);

/* `x.split(y)` where `x` may be a str OR an ExceptionGroup.

   ONE METHOD NAME, TWO RECEIVERS, and which is meant is not known until run
   time -- the same shape `count` and `index` have. Written as a dispatcher
   rather than two table entries because the method table is keyed by name and
   argument count, and both take exactly one. */
APY_API apy_value apy_split_of(apy_value x, apy_value arg) {
    if (O(x)->kind == APY_EXC_K && O(x)->v.e.subs)
        return apy_group_split(x, arg);
    return apy_str_split(x, arg);
}

APY_API apy_value apy_group_split(apy_value g, apy_value want) {
    apy_value hit, miss, out;
    if (O(g)->kind != APY_EXC_K || !O(g)->v.e.subs)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'split'%s",
                         apy_kind_name(g), "");
    hit = apy_group_select(g, want, 1);
    if (apy_error_occurred()) return 0;
    miss = apy_group_select(g, want, 0);
    if (apy_error_occurred()) return 0;
    out = apy_tuple_new(2);
    apy_seq_push(out, hit ? hit : apy_none());
    apy_seq_push(out, miss ? miss : apy_none());
    return out;
}

/* A class body's namespace, read by name.

   NOT `apy_dict_get`, whose miss is a KeyError: this stands for reading a
   NAME, and a name that is not bound is a NameError. Both spellings are
   passed because the KEY is mangled -- `__x` inside `class C` is stored as
   `_C__x` -- and the message has to say what the program wrote. */
APY_API apy_value apy_ns_get(apy_value ns, apy_value key, apy_value shown) {
    int64_t at = apy_dict_find(ns, key);
    char buf[200];
    if (at >= 0) return O(ns)->v.d.vals[at];
    snprintf(buf, sizeof buf, "name '%.*s' is not defined",
             (int)O(shown)->v.s.n, O(shown)->v.s.p);
    return apy_fail("NameError", buf);
}

/* Declared here because the object model they belong to is defined much
   further down, and this is the first use of each. */
APY_API apy_value apy_type_new(apy_value name, apy_value base);
APY_API apy_value apy_instance_new(apy_value cls);
APY_API apy_value apy_setattr(apy_value obj, apy_value name, apy_value value);
APY_API apy_value apy_type_set(apy_value cls, apy_value name, apy_value value);
static apy_value apy_native(int sel, int64_t arity, const char *name);
static int apy_eq_raw(apy_value a, apy_value b);
APY_API apy_value apy_dict_get_or(apy_value d, apy_value key,
                                  apy_value fallback);

/* The code object a frame names, built from what `apy_pos_add` recorded.

   ONE PER FUNCTION NAME and interned, so `tb.tb_frame.f_code is
   f.__code__`-style identity holds for two tracebacks out of one function. */
APY_API apy_value apy_code_of(apy_value name) {
    static apy_value cls, made;
    apy_value code, seen;
    if (!cls) {
        cls = apy_type_new(apy_lit("code"), 0);
        if (!cls) return 0;
        /* A METHOD, because that is how CPython spells it -- a program CALLS
           `co_positions()`, and `hasattr(code, "co_positions")` is the test
           the suite writes before it does. */
        apy_type_set(cls, apy_lit("co_positions"),
                     apy_native(APY_NAT_POSITIONS, 1, "co_positions"));
    }
    if (!made) made = apy_dict_new(8);
    seen = apy_dict_get_or(made, name, 0);
    if (seen) return seen;
    code = apy_instance_new(cls);
    if (!code) return 0;
    apy_setattr(code, apy_lit("co_name"), name);
    apy_setattr(code, apy_lit("co_qualname"), name);
    apy_setattr(code, apy_lit("co_filename"), apy_lit("<compiled>"));
    {
        /* THE POSITIONS OF THIS FUNCTION'S STATEMENTS, in the order they were
           recorded -- which is the order they were lowered, which is source
           order. A four-tuple apiece, spelled as CPython spells it:
           (lineno, end_lineno, col_offset, end_col_offset). */
        apy_value rows = apy_seq_new(APY_LIST_K, 8);
        int64_t i;
        int64_t first = 0;
        for (i = 0; i < apy_pos_n; i++) {
            apy_value row;
            if (!apy_eq_raw(apy_pos_tab[i].fn, name)) continue;
            row = apy_tuple_new(4);
            apy_seq_push(row, apy_from_int(apy_pos_tab[i].line));
            apy_seq_push(row, apy_from_int(apy_pos_tab[i].end_line));
            apy_seq_push(row, apy_from_int(apy_pos_tab[i].col));
            apy_seq_push(row, apy_from_int(apy_pos_tab[i].end_col));
            apy_seq_push(rows, row);
            if (!first) first = apy_pos_tab[i].line;
        }
        apy_setattr(code, apy_lit("_positions"), rows);
        apy_setattr(code, apy_lit("co_firstlineno"), apy_from_int(first));
    }
    if (apy_error_occurred()) return 0;
    if (!apy_dict_set(made, name, code)) return 0;
    return code;
}

/* The traceback an exception carries: where it came from, and the frame it
   came from. ONE FRAME DEEP -- there is no call stack here, so the chain a
   real traceback walks has a single link and `tb_next` is None. Saying that
   is better than inventing frames the runtime never had. */
APY_API apy_value apy_traceback_of(apy_value exc) {
    static apy_value tbcls, frcls;
    apy_value tb, frame, code, where;
    int64_t at = O(exc)->v.e.pos;
    if (!tbcls) {
        tbcls = apy_type_new(apy_lit("traceback"), 0);
        frcls = apy_type_new(apy_lit("frame"), 0);
        if (!tbcls || !frcls) return 0;
    }
    where = (at >= 0 && at < apy_pos_n) ? apy_pos_tab[at].fn
                                        : apy_lit("<module>");
    code = apy_code_of(where);
    if (!code) return 0;
    frame = apy_instance_new(frcls);
    if (!frame) return 0;
    apy_setattr(frame, apy_lit("f_code"), code);
    apy_setattr(frame, apy_lit("f_lineno"),
                apy_from_int(at >= 0 && at < apy_pos_n
                             ? apy_pos_tab[at].line : 0));
    apy_setattr(frame, apy_lit("f_globals"), apy_dict_new(1));
    apy_setattr(frame, apy_lit("f_locals"), apy_dict_new(1));
    tb = apy_instance_new(tbcls);
    if (!tb) return 0;
    apy_setattr(tb, apy_lit("tb_frame"), frame);
    apy_setattr(tb, apy_lit("tb_lineno"),
                apy_from_int(at >= 0 && at < apy_pos_n
                             ? apy_pos_tab[at].line : 0));
    apy_setattr(tb, apy_lit("tb_lasti"), apy_from_int(-1));
    apy_setattr(tb, apy_lit("tb_next"), apy_none());
    if (apy_error_occurred()) return 0;
    return tb;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

/* --- PEP 750 template strings ---------------------------------------------

   A t-string DOES NOT JOIN. That is the whole of it: an f-string decides at
   the point of writing that the answer is text and throws away everything it
   used to get there, and a template keeps the pieces apart so that whatever
   consumes it decides instead -- which is what makes one safe to hand a SQL
   or HTML builder and the other not. */

/* Declared here because the three are defined with the rest of the object
   model, further down: this is the first use of them. */
APY_API apy_value apy_type_new(apy_value name, apy_value base);
APY_API apy_value apy_instance_new(apy_value cls);
APY_API apy_value apy_setattr(apy_value obj, apy_value name, apy_value value);

/* One replacement field: what was WRITTEN and what it CAME TO, side by side.

   The expression source is kept because a consumer that reports an error, or
   builds a query with named parameters, needs the text -- and an f-string has
   already discarded it by the time anyone could ask. */
APY_API apy_value apy_interpolation_new(apy_value value, apy_value expression,
                                        apy_value conversion,
                                        apy_value spec) {
    static apy_value cls = 0;
    apy_value one;
    if (!cls) cls = apy_type_new(apy_lit("Interpolation"), 0);
    if (!cls) return 0;
    one = apy_instance_new(cls);
    if (!one) return 0;
    apy_setattr(one, apy_lit("value"), value);
    apy_setattr(one, apy_lit("expression"), expression);
    apy_setattr(one, apy_lit("conversion"), conversion);
    apy_setattr(one, apy_lit("format_spec"), spec);
    if (apy_error_occurred()) return 0;
    return one;
}

/* The template itself. `strings` is ALWAYS one longer than `interpolations`
   -- an empty piece stands between two adjacent fields, and one stands at
   each end -- so a consumer walks them in lockstep without having to ask
   which of the two came first. */
APY_API apy_value apy_template_new(apy_value strings, apy_value interps,
                                   apy_value values) {
    static apy_value cls = 0;
    apy_value t;
    if (!cls) cls = apy_type_new(apy_lit("Template"), 0);
    if (!cls) return 0;
    t = apy_instance_new(cls);
    if (!t) return 0;
    apy_setattr(t, apy_lit("strings"), strings);
    apy_setattr(t, apy_lit("interpolations"), interps);
    apy_setattr(t, apy_lit("values"), values);
    if (apy_error_occurred()) return 0;
    return t;
}

/* PEP 654's `except*` dispatch, whole, in one call.

   EVERY CLAUSE RUNS -- not the first that matches. A group carrying a
   ValueError and a TypeError enters both handlers, each holding its own half,
   and that is the entire difference from `except`. Splitting here rather than
   as a chain of calls in the lowering keeps the LEFTOVER in one place, and the
   leftover is what has to be re-raised once the clauses between them have not
   accounted for everything.

   Answers one entry per clause -- what it catches, or None -- and last what
   nothing caught, or None. */
APY_API apy_value apy_group_dispatch(apy_value raised, apy_value types) {
    apy_value rest, out;
    int64_t i, n = O(types)->v.q.n;
    int wrapped = 0, any_hit = 0;
    if (O(raised)->kind == APY_EXC_K && O(raised)->v.e.subs) {
        rest = raised;
    } else {
        /* A BARE EXCEPTION IS A GROUP OF ONE to `except*`, which is why a
           handler binds a group even where the program raised a plain
           ValueError. */
        apy_value one = apy_seq_new(APY_LIST_K, 1);
        if (!one) return 0;
        apy_seq_push(one, raised);
        rest = apy_make_exc(apy_lit("ExceptionGroup"), apy_lit(""));
        if (!rest) return 0;
        O(rest)->v.e.subs = one;
        wrapped = 1;
    }
    out = apy_tuple_new(n + 1);
    if (!out) return 0;
    for (i = 0; i < n; i++) {
        apy_value hit = 0;
        if (rest) {
            hit = apy_group_select(rest, O(types)->v.q.items[i], 1);
            if (apy_error_occurred()) return 0;
        }
        if (hit) {
            any_hit = 1;
            rest = apy_group_select(rest, O(types)->v.q.items[i], 0);
            if (apy_error_occurred()) return 0;
        }
        apy_seq_push(out, hit ? hit : apy_none());
    }
    if (!any_hit) {
        /* NOTHING MATCHED, so the ORIGINAL propagates -- not the wrapper this
           made to split with. A program catching the plain ValueError outside
           has to see the ValueError. */
        rest = raised;
    } else if (rest && wrapped && O(O(rest)->v.e.subs)->v.q.n == 1) {
        rest = O(O(rest)->v.e.subs)->v.q.items[0];
    }
    apy_seq_push(out, rest ? rest : apy_none());
    return out;
}

/* `ascii(x)` -- `repr(x)` with every non-ASCII character escaped.

   IT IS NOT AN ALIAS FOR `repr`, which is what it was here: `repr('aé')` is
   `'aé'` and `ascii('aé')` is `'a\\xe9'`. The whole point of the function is
   that its answer survives a channel that cannot carry the character, so
   handing back the character defeats it entirely.

   The three widths are Python's: `\\xNN` below 0x100, `\\uNNNN` below 0x10000,
   `\\UNNNNNNNN` above. */
APY_API apy_value apy_ascii(apy_value v) {
    apy_value shown = apy_repr(v);
    const unsigned char *p;
    int64_t n, i = 0, at = 0, room;
    char *out;
    if (!shown) return 0;
    p = (const unsigned char *)O(shown)->v.s.p;
    n = O(shown)->v.s.n;
    /* Ten bytes is the widest escape, `\\UNNNNNNNN`, and one input byte can
       never produce more than one escape. */
    room = n * 10 + 1;
    out = (char *)malloc((size_t)room);
    while (i < n) {
        if (p[i] < 0x80) {
            out[at++] = (char)p[i++];
            continue;
        }
        {
            int64_t used, code = apy_utf8_at(p, n, i, &used);
            if (code < 0x100) at += snprintf(out + at, 11, "\\x%02llx",
                                             (unsigned long long)code);
            else if (code < 0x10000) at += snprintf(out + at, 11, "\\u%04llx",
                                                    (unsigned long long)code);
            else at += snprintf(out + at, 11, "\\U%08llx",
                                (unsigned long long)code);
            i += used;
        }
    }
    out[at] = 0;
    return apy_str_take(out, at);
}

/* `x.hex()` where `x` may be BYTES or a FLOAT.

   ONE METHOD NAME, TWO RECEIVERS, and which is meant is not known until run
   time -- the same shape `count`, `index`, `pop` and `split` have. The two
   answer entirely different things: bytes give their contents in hex digits,
   a float gives the exact binary value it holds. */
APY_API apy_value apy_hex_of(apy_value x, apy_value sep) {
    if (O(x)->kind == APY_FLOAT_K) {
        /* `%a` IS the C library's hexadecimal float, and it is exact -- which
           is the whole reason `float.hex` exists: a decimal repr rounds and
           this one does not, so a value can be written down and read back
           unchanged. */
        /* THIRTEEN HEX DIGITS ALWAYS -- 52 mantissa bits, four to a digit --
           because that is what `float.hex` writes and what makes two values
           comparable as text. `%a` alone trims trailing zeros and gave
           `0x1.4p+1` where CPython says `0x1.4000000000000p+1`.

           ZERO IS THE EXCEPTION: CPython writes `0x0.0p+0`, not thirteen
           zeros, and the sign of a negative zero survives. */
        char buf[64];
        double f = O(x)->v.f;
        if (f == 0.0) {
            int neg = signbit(f);
            snprintf(buf, sizeof buf, "%s0x0.0p+0", neg ? "-" : "");
        } else {
            snprintf(buf, sizeof buf, "%.13a", f);
        }
        return apy_str_copy(buf, (int64_t)strlen(buf));
    }
    /* The separator is bytes' alone -- the no-argument form supplies a
       default for it, which is why this takes two even though a float uses
       neither. */
    return apy_bytes_hex(x, sep);
}

/* `float.fromhex('0x1.4p+1')` -- the inverse, and exact for the same reason. */
APY_API apy_value apy_float_fromhex(apy_value text) {
    double got;
    char *end = 0;
    if (O(text)->kind != APY_STR_K)
        return apy_fail2("TypeError",
                         "fromhex() argument must be str, not %s%s",
                         apy_kind_name(text), "");
    got = strtod(APY_CSTR(text), &end);
    if (!end || end == APY_CSTR(text))
        return apy_fail("ValueError",
                        "invalid hexadecimal floating-point string");
    return apy_from_float(got);
}

APY_API apy_value apy_isinstance(apy_value v, apy_value type_name) {
    const char *want;
    const char *have;
    /* THE METACLASS DECIDES, if it says so -- `__instancecheck__` is asked
       before anything structural, which is what makes `isinstance(42, Duck)`
       able to answer True. */
    if (type_name && O(type_name)->kind == APY_TYPE_K
        && O(type_name)->v.t.meta) {
        apy_value hook = apy_class_find(O(type_name)->v.t.meta,
                                        apy_name("__instancecheck__"));
        if (hook) {
            apy_value args[2];
            apy_value got;
            args[0] = type_name;
            args[1] = v;
            got = apy_call_n(hook, args, 2);
            /* A BOOL, whatever the hook returned. `isinstance` answers True
               or False in CPython however truthy the hook was -- returning
               the hook's own value printed `quacks`. */
            return got ? apy_from_bool(apy_truth(got) != 0) : got;
        }
    }
    /* A TUPLE OF TYPES means ANY OF THESE, and there is no ambiguity with
       asking about the tuple type itself: `isinstance(x, tuple)` arrives as
       the STRING "tuple", because a builtin kind has no value form. So a
       tuple HERE is always the multi-type form -- including one built at run
       time and held in a variable, which is what makes
       `isinstance(node, self.KINDS)` work. */
    /* PEP 604: `isinstance(x, int | str)` asks each ARM, which is the same
       question a tuple of types asks and is answered the same way. */
    if (O(type_name)->kind == APY_ALIAS_K
            && O(O(type_name)->v.ga.origin)->kind == APY_INST_K)
        return apy_isinstance(v, O(type_name)->v.ga.args);
    if (O(type_name)->kind == APY_TUPLE_K) {
        int64_t i;
        for (i = 0; i < O(type_name)->v.q.n; i++) {
            apy_value got = apy_isinstance(v, O(type_name)->v.q.items[i]);
            if (!got) return 0;
            if (apy_truth(got)) return apy_from_bool(1);
        }
        return apy_from_bool(0);
    }
    if (O(type_name)->kind == APY_FUNC_K && O(type_name)->v.fn.is_type)
        /* `t = int; isinstance(x, t)` -- the same question as the literal
           form, which the frontend rewrites to a name at the call site. */
        return apy_isinstance(v, O(type_name)->v.fn.name);
    if (O(type_name)->kind == APY_TYPE_K) {
        /* AN EXCEPTION TYPE REACHED AS A VALUE. `isinstance(e, ValueError)`
           is rewritten to the NAME at the call site, but `t = ValueError;
           isinstance(e, t)` -- and `g.split(ValueError)` -- hand the type
           object over instead, and an exception is not an `APY_INST_K`. It
           answered False for every such test, which is a wrong answer rather
           than a refusal. Compare by name through the chain, as the string
           form does. */
        if (O(v)->kind == APY_EXC_K)
            return apy_isinstance(v, O(type_name)->v.t.name);
        if (O(v)->kind == APY_INST_K)
            return apy_from_bool(apy_type_is_sub(O(v)->v.o.cls, type_name));
        /* A TYPE OBJECT FOR A BUILTIN KIND -- which is what `type(2)` answers
           now that it is a value rather than a name. Asking by NAME reuses
           the whole builtin rule below, including bool being an int and
           everything being an object. */
        return apy_isinstance(v, O(type_name)->v.t.name);
    }
    if (O(type_name)->kind != APY_STR_K)
        return apy_fail("TypeError", "isinstance() arg 2 must be a type, "
                                     "a tuple of types, or a union");
    want = O(type_name)->v.s.p;
    have = apy_kind_name(v);
    /* An INSTANCE never matches a built-in name. Its `apy_kind_name` is its
       class's name, so without this a class called `int` -- or, far more
       likely, `object` two lines down -- would answer True. */
    if (O(v)->kind == APY_INST_K) {
        /* EXCEPT WHEN ITS CLASS EXTENDS ONE. `class D(dict)` makes every D an
           instance of `dict`, which is what the base says and what a program
           testing `isinstance(d, dict)` is asking. */
        apy_value held = apy_inst_held(v);
        if (held && strcmp(apy_kind_name(held), want) == 0)
            return apy_from_bool(1);
        /* A tuple subclass is a tuple; `bool` under `int` is the same rule
           one level down, and asking the held value by NAME reuses it. */
        if (held && strcmp(want, "object") != 0)
            return apy_isinstance(held, type_name);
        return apy_from_bool(strcmp(want, "object") == 0);
    }
    if (strcmp(have, want) == 0) return apy_from_bool(1);
    if (O(v)->kind == APY_BOOL_K && strcmp(want, "int") == 0)
        return apy_from_bool(1);
    if (strcmp(want, "object") == 0) return apy_from_bool(1);
    if (O(v)->kind == APY_EXC_K) {
        /* An exception instance is an instance of every base in its chain. */
        const char *chain = O(v)->v.e.name;
        while (chain) {
            if (strcmp(chain, want) == 0) return apy_from_bool(1);
            chain = apy_exc_parent(chain);
        }
    }
    return apy_from_bool(0);
}

/* A slice. `stop` past the end clamps, a negative index counts from the end,
   and an empty range is an empty result rather than an error -- none of which
   indexing does, which is why this cannot share `apy_getitem`.
   `has_start`/`has_stop` distinguish `xs[:2]` from `xs[0:2]`, which differ for
   a negative step. */
/* `s.indices(n)` -- the (start, stop, step) a walk over a sequence of length
   `n` would really use, with the omitted bounds filled in and the negative
   ones resolved. A program uses it to implement `__getitem__` over its own
   storage without reimplementing the clamping rules. */
APY_API apy_value apy_slice_indices(apy_value sl, apy_value len_v) {
    int64_t n, start, stop, step = 1;
    apy_value out;
    if (O(sl)->kind != APY_SLICE_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'indices'%s",
                         apy_kind_name(sl), "");
    if (!apy_index_arg(len_v, &n, APY_IDX_SIZE)) return 0;
    if (O(sl)->v.sl.step && O(sl)->v.sl.step != apy_none()
            && O(O(sl)->v.sl.step)->kind != APY_NONE_K
            && !apy_index_arg(O(sl)->v.sl.step, &step, APY_IDX_SIZE))
        return 0;
    if (step == 0) return apy_fail("ValueError", "slice step cannot be zero");
    start = step > 0 ? 0 : n - 1;
    stop = step > 0 ? n : -1;
    if (O(sl)->v.sl.start && O(O(sl)->v.sl.start)->kind != APY_NONE_K) {
        if (!apy_index_arg(O(sl)->v.sl.start, &start, APY_IDX_SIZE)) return 0;
        if (start < 0) start += n;
        if (start < 0) start = step > 0 ? 0 : -1;
        if (start > n) start = step > 0 ? n : n - 1;
    }
    if (O(sl)->v.sl.stop && O(O(sl)->v.sl.stop)->kind != APY_NONE_K) {
        if (!apy_index_arg(O(sl)->v.sl.stop, &stop, APY_IDX_SIZE)) return 0;
        if (stop < 0) stop += n;
        if (stop < 0) stop = step > 0 ? 0 : -1;
        if (stop > n) stop = step > 0 ? n : n - 1;
    }
    out = apy_tuple_new(3);
    apy_seq_push(out, apy_from_int(start));
    apy_seq_push(out, apy_from_int(stop));
    apy_seq_push(out, apy_from_int(step));
    return out;
}

/* The buffer a view is over, as a char pointer. One place that knows a
   memoryview's source is always the bytes kind, so the cast is written once
   rather than at each of the six readers. */
static const char *apy_mview_buf(apy_value v) {
    return O(O(v)->v.mv.src)->v.s.p;
}

/* The offset in the underlying buffer of the view's `i`th byte. THE STRIDE IS
   APPLIED HERE and nowhere else, so a reversed view indexes, assigns and
   converts through the same arithmetic. */
static int64_t apy_mview_at(apy_value v, int64_t i) {
    return O(v)->v.mv.off + i * O(v)->v.mv.step;
}

APY_API apy_value apy_memoryview(apy_value src) {
    apy_obj *o;
    /* A NEW VIEW OVER THE SAME BUFFER, not the argument itself: returning
       `src` made `memoryview(m) is m` True where Python says False. The
       window is copied so that a slice handed here keeps its bounds. */
    if (O(src)->kind == APY_MVIEW_K) {
        apy_obj *w = apy_alloc(APY_MVIEW_K);
        w->v.mv = O(src)->v.mv;
        return V(w);
    }
    if (O(src)->kind != APY_BYTES_K)
        return apy_fail2("TypeError",
                         "memoryview: a bytes-like object is required, not "
                         "'%s'%s", apy_kind_name(src), "");
    o = apy_alloc(APY_MVIEW_K);
    o->v.mv.src = src;
    o->v.mv.off = 0;
    o->v.mv.n = O(src)->v.s.n;
    o->v.mv.step = 1;
    return V(o);
}

/* A view over the same buffer, `n` bytes from `off` at `step`. Slicing a
   memoryview answers one of these, so `mv[1:3][0]` reaches the right byte
   without either slice having copied anything. */
static apy_value apy_mview_slice(apy_value v, int64_t off, int64_t n,
                                 int64_t step) {
    apy_obj *o = apy_alloc(APY_MVIEW_K);
    o->v.mv.src = O(v)->v.mv.src;
    o->v.mv.off = off;
    o->v.mv.n = n;
    o->v.mv.step = step;
    return V(o);
}

/* What the view shows right now, as bytes. `bytes(mv)` and every consumer
   wanting a sequence goes through here -- the same shape as
   `apy_view_items`, and live for the same reason. */
APY_API apy_value apy_mview_bytes(apy_value v) {
    int64_t i, n = O(v)->v.mv.n;
    const char *buf = apy_mview_buf(v);
    char *out = (char *)malloc((size_t)(n ? n : 1) + 1);
    if (!out) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) out[i] = buf[apy_mview_at(v, i)];
    out[n] = 0;
    { apy_value r = apy_str_take(out, n);
      O(r)->kind = APY_BYTES_K;
      return r; }
}

APY_API apy_value apy_dict_view(apy_value d, int64_t part) {
    apy_obj *o;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'keys'%s",
                         apy_kind_name(d), "");
    o = apy_alloc(APY_VIEW_K);
    o->v.vw.dict = d;
    o->v.vw.part = (int)part;
    return V(o);
}

/* WHAT THE VIEW SHOWS RIGHT NOW, as a list. Every consumer that wants a
   sequence -- iteration, `sorted`, `list()` -- goes through this, so the
   contents are read at the moment they are asked for rather than when the
   view was made. That IS the liveness. */
APY_API apy_value apy_view_items(apy_value v) {
    apy_value d, out;
    int64_t i, n;
    if (O(v)->kind != APY_VIEW_K) return v;
    d = O(v)->v.vw.dict;
    n = O(d)->v.d.n;
    out = apy_seq_new(APY_LIST_K, n ? n : 1);
    for (i = 0; i < n; i++) {
        if (O(v)->v.vw.part == APY_PART_KEYS) {
            apy_seq_push(out, O(d)->v.d.keys[i]);
        } else if (O(v)->v.vw.part == APY_PART_VALUES) {
            apy_seq_push(out, O(d)->v.d.vals[i]);
        } else {
            apy_value pair = apy_tuple_new(2);
            apy_seq_push(pair, O(d)->v.d.keys[i]);
            apy_seq_push(pair, O(d)->v.d.vals[i]);
            apy_seq_push(out, pair);
        }
    }
    return out;
}

/* `list[int]` as a value. `args` is always a tuple, even for one argument,
   because that is what `get_args` answers and what the repr walks. */
APY_API apy_value apy_alias_new(apy_value origin, apy_value args) {
    apy_obj *o = apy_alloc(APY_ALIAS_K);
    o->v.ga.origin = origin;
    o->v.ga.args = args;
    return V(o);
}

/* `slice(a, b, c)` as an object. Also the builtin of the same name. */
APY_API apy_value apy_slice_new(apy_value start, apy_value stop,
                                apy_value step) {
    apy_obj *o = apy_alloc(APY_SLICE_K);
    o->v.sl.start = start;
    o->v.sl.stop = stop;
    o->v.sl.step = step;
    return V(o);
}

APY_API apy_value apy_slice(apy_value seq, int64_t start, int64_t stop,
                            int64_t step, int64_t has_start, int64_t has_stop) {
    int64_t n, i;
    apy_value out;
    /* A USER OBJECT GETS THE SLICE AS AN OBJECT. `c[1:2]` on an instance is
       `c.__getitem__(slice(1, 2, None))` -- the class decides what a slice of
       it means, and it can only do that if it is handed one. Sequences skip
       this entirely and are sliced below without allocating. */
    if (O(seq)->kind == APY_INST_K) {
        apy_value key = apy_slice_new(
            has_start ? apy_from_int(start) : apy_none(),
            has_stop ? apy_from_int(stop) : apy_none(),
            step == 1 ? apy_none() : apy_from_int(step));
        return apy_getitem(seq, key);
    }
    /* A SLICE OF A RANGE IS A RANGE, not a list: `range(0, 10, 2)[1:3]` is
       `range(2, 6, 2)` in CPython, and materialising it would undo the whole
       reason a range is three numbers. */
    if (O(seq)->kind == APY_RANGE_K) {
        int64_t len = apy_range_len(seq), lo, hi;
        if (step == 0)
            return apy_fail("ValueError", "slice step cannot be zero");
        lo = has_start ? start : (step < 0 ? len - 1 : 0);
        hi = has_stop ? stop : (step < 0 ? -1 : len);
        if (has_start && lo < 0) { lo += len; if (lo < 0) lo = step < 0 ? -1 : 0; }
        if (lo > len) lo = len;
        if (has_stop && hi < 0) { hi += len; if (hi < 0) hi = step < 0 ? -1 : 0; }
        if (hi > len) hi = len;
        return apy_range(apy_range_at(seq, lo),
                         O(seq)->v.rg.start + hi * O(seq)->v.rg.step,
                         O(seq)->v.rg.step * step);
    }
    /* A MEMORYVIEW GOES THE SAME WAY AS A USER OBJECT, because the answer
       is another view rather than a fresh sequence -- the copying path below
       would lose the write-through that is the whole point of one. */
    if (O(seq)->kind == APY_MVIEW_K) {
        apy_value key = apy_slice_new(
            has_start ? apy_from_int(start) : apy_none(),
            has_stop ? apy_from_int(stop) : apy_none(),
            step == 1 ? apy_none() : apy_from_int(step));
        return apy_getitem(seq, key);
    }
    if (step == 0) return apy_fail("ValueError", "slice step cannot be zero");
    /* A str is measured in CHARACTERS, bytes in bytes -- which is the whole
       reason the two cannot share the copy loop below. */
    if (O(seq)->kind == APY_STR_K) n = apy_str_chars(seq);
    else if (O(seq)->kind == APY_BYTES_K) n = O(seq)->v.s.n;
    else if (apy_is_seq(seq)) n = O(seq)->v.q.n;
    else return apy_fail2("TypeError", "'%s' object is not subscriptable%s",
                          apy_kind_name(seq), "");

    if (!has_start) start = step > 0 ? 0 : n - 1;
    else {
        if (start < 0) start += n;
        if (start < 0) start = step > 0 ? 0 : -1;
        if (start > n - 1) start = step > 0 ? n : n - 1;
    }
    if (!has_stop) stop = step > 0 ? n : -1;
    else {
        if (stop < 0) stop += n;
        if (stop < 0) stop = step > 0 ? 0 : -1;
        if (stop > n) stop = n;
    }

    if (O(seq)->kind == APY_STR_K) {
        /* THE BYTE OFFSET OF EACH CHARACTER, walked once. A slice with a step
           reaches characters in any order, so the offsets are needed as a
           table rather than as a running position -- and every character can
           be up to four bytes, which is what sizes the output. */
        const unsigned char *p = (const unsigned char *)O(seq)->v.s.p;
        int64_t bytes = O(seq)->v.s.n, at = 0, k = 0, out_n = 0, used;
        int64_t *offset = (int64_t *)malloc((size_t)(n + 1) * sizeof(int64_t));
        char *buf = (char *)malloc((size_t)(n > 0 ? n : 1) * 4 + 1);
        if (!offset || !buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
        while (k < n && at < bytes) {
            offset[k++] = at;
            apy_utf8_at(p, bytes, at, &used);
            at += used;
        }
        offset[n] = bytes;
        for (i = start; step > 0 ? i < stop : i > stop; i += step) {
            int64_t from = offset[i], to = (i + 1 <= n) ? offset[i + 1] : bytes;
            int64_t j;
            for (j = from; j < to; j++) buf[out_n++] = O(seq)->v.s.p[j];
        }
        buf[out_n] = 0;
        free(offset);
        return apy_str_take(buf, out_n);
    }
    if (O(seq)->kind == APY_BYTES_K) {
        char *buf = (char *)malloc((size_t)(n > 0 ? n : 1) + 1);
        int64_t out_n = 0;
        for (i = start; step > 0 ? i < stop : i > stop; i += step)
            buf[out_n++] = O(seq)->v.s.p[i];
        buf[out_n] = 0;
        { apy_value r = apy_str_take(buf, out_n);
          /* A slice of bytes is bytes. Indexing gives an int and slicing does
             not, which is the one asymmetry a reader will not expect. */
          O(r)->kind = O(seq)->kind;
          /* And a slice of a bytearray is a bytearray -- a fresh one, whose
             buffer this just malloc'd, so it is writable as it must be. */
          O(r)->v.s.mut = O(seq)->v.s.mut;
          return r; }
    }
    out = apy_seq_new(O(seq)->kind, n + 1);
    for (i = start; step > 0 ? i < stop : i > stop; i += step)
        apy_seq_push(out, O(seq)->v.q.items[i]);
    return out;
}

"""
