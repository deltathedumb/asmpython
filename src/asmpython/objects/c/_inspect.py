"""The object runtime, in C: extraction, inspection, repr and str.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * extraction
  * inspection
  * repr and str
"""

C = r"""/* --- extraction -------------------------------------------------------- */
/* The frontend calls these only where it has proved the kind, so they do not
   check. A wrong call here is a compiler bug, not a user error, and a check
   would hide it behind a plausible zero. */
APY_API int64_t apy_as_int(apy_value v) { return O(v)->v.i; }

/* A VALUE AS AN INDEX, checked. `apy_as_int` is a raw extraction the frontend
   calls where it has proved the kind; this is for the places where the value
   came from the program -- a slice bound, a `range` argument -- and may be
   anything, including a user object with `__index__`.

   A wrong kind reports rather than reading whatever the union happens to hold
   at that offset, which for an instance is its class pointer. */
/* Defined just below; the bound converter calls it. */
APY_API int64_t apy_index(apy_value v);

/* A SLICE BOUND, WHICH IS NOT AN INDEX. `xs[2 ** 100]` is a request this
   runtime cannot serve and CPython refuses it too; `xs[:2 ** 100]` is the
   whole list, and refusing THAT would be wrong. So a big clamps to a value
   past any real length rather than raising, keeping its sign so that
   `xs[-(2 ** 100):]` is the whole list as well.

   `apy_index` cannot make this distinction because it does not know which it
   was asked for -- which is why the frontend picks the converter rather than
   the converter guessing. */
APY_API int64_t apy_slice_bound(apy_value v) {
    if (apy_is_big(v))
        return O(v)->v.big.neg ? -((int64_t)1 << 62) : ((int64_t)1 << 62);
    return apy_index(v);
}

APY_API int64_t apy_index(apy_value v) {
    /* THE BIG TEST COMES FIRST, and it has to: `apy_is_int_like` is TRUE for
       a big -- that is the whole point of it, since a big is an integer --
       so testing it first sent every big down the fast path and returned
       `v.i`, which on a big is the LIMB POINTER read as an integer.

       Nothing crashed. A slice bound became a large positive address, so
       `xs[-(2 ** 100):]` was empty and `xs[:-(2 ** 100)]` was the whole
       list -- exactly inverted, and silent. The refusal below was
       unreachable. */
    if (apy_is_big(v)) {
        apy_fail("OverflowError",
                 "cannot fit 'int' into an index-sized integer");
        return 0;
    }
    if (apy_is_int_like(v)) return O(v)->v.i;
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__index__");
        if (apy_error_occurred()) return 0;
        /* AND THE SAME TEST ON WHAT `__index__` ANSWERED, which may be a big
           just as easily as the argument was. */
        if (got && apy_is_big(got)) {
            apy_fail("OverflowError",
                     "cannot fit 'int' into an index-sized integer");
            return 0;
        }
        if (got && apy_is_int_like(got)) return O(got)->v.i;
    }
    apy_fail2("TypeError",
              "'%s' object cannot be interpreted as an integer%s",
              apy_kind_name(v), "");
    return 0;
}
/* KIND-AWARE, unlike its int and bool neighbours. Those are raw extractions
   the frontend only emits where it has proved the kind; this one is reached
   with an int whenever a program passes one to a `float` parameter, which
   Python allows and people write. Reading `v.f` there reinterpreted the
   integer bits as a double and `f(42)` answered 4.15e-322. */
APY_API double apy_as_float(apy_value v) {
    if (O(v)->kind == APY_FLOAT_K) return O(v)->v.f;
    if (apy_is_big(v)) return apy_big_double(O(v));
    if (apy_is_int_like(v)) return (double)O(v)->v.i;
    return O(v)->v.f;
}
APY_API int64_t apy_as_bool(apy_value v) { return O(v)->v.i != 0; }

/* --- inspection -------------------------------------------------------- */
/* `b'ab'`, with CPython's escaping rules.

   Which are NOT the same as str's, and the differences are the whole function:
   every byte outside printable ASCII becomes `\\xNN` (never `\\uNNNN`, since
   there is no character here to have a code point), `\\t`, `\\n` and `\\r` keep
   their short forms, and the quote is single unless the value contains one and
   no double. */
APY_API apy_value apy_bytes_repr(apy_value v) {
    const unsigned char *p = (const unsigned char *)O(v)->v.s.p;
    if (O(v)->v.s.mut) {
        /* `bytearray(b'abc')` -- the repr of the bytes it holds, wrapped.
           Built by clearing the flag round the recursive call rather than by
           a second escaping loop, so the two spellings cannot drift. */
        apy_value inner;
        char *wrapped;
        int64_t m;
        O(v)->v.s.mut = 0;
        inner = apy_bytes_repr(v);
        O(v)->v.s.mut = 1;
        if (!inner) return 0;
        m = O(inner)->v.s.n;
        wrapped = (char *)malloc((size_t)m + 12);
        if (!wrapped) { fputs("asmpython: out of memory\n", stderr); exit(1); }
        memcpy(wrapped, "bytearray(", 10);
        memcpy(wrapped + 10, O(inner)->v.s.p, (size_t)m);
        wrapped[10 + m] = ')';
        wrapped[11 + m] = 0;
        return apy_str_take(wrapped, m + 11);
    }
    int64_t n = O(v)->v.s.n, i;
    int has_single = 0, has_double = 0;
    for (i = 0; i < n; i++) {
        if (p[i] == '\'') has_single = 1;
        if (p[i] == '"') has_double = 1;
    }
    char quote = (has_single && !has_double) ? '"' : '\'';

    /* Four characters is the widest any one byte becomes (`\\xNN`), plus the
       quotes and the `b`. */
    int64_t cap = n * 4 + 4;
    char *out = (char *)malloc((size_t)cap + 1);
    if (!out) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    int64_t k = 0;
    out[k++] = 'b';
    out[k++] = quote;
    for (i = 0; i < n; i++) {
        unsigned char c = p[i];
        if (c == (unsigned char)quote || c == '\\') {
            out[k++] = '\\'; out[k++] = (char)c;
        } else if (c == '\t') { out[k++] = '\\'; out[k++] = 't';
        } else if (c == '\n') { out[k++] = '\\'; out[k++] = 'n';
        } else if (c == '\r') { out[k++] = '\\'; out[k++] = 'r';
        } else if (c >= 32 && c < 127) {
            out[k++] = (char)c;
        } else {
            static const char *hex = "0123456789abcdef";
            out[k++] = '\\'; out[k++] = 'x';
            out[k++] = hex[c >> 4]; out[k++] = hex[c & 15];
        }
    }
    out[k++] = quote;
    out[k] = 0;
    return apy_str_take(out, k);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

/* THE EXPORTED HALF, which `runtime/kindname.py` splits: the IR answers
   every kind but an exception, and an exception comes back here because its
   DISPLAYED name is a class lookup rather than a literal. */
APY_API apy_value apy_kind_name_of(apy_value v) {
    return (apy_value)(uintptr_t)apy_kind_name(v);
}
static const char *apy_kind_name(apy_value v) {
    switch (O(v)->kind) {
    case APY_NONE_K:  return "NoneType";
    case APY_BOOL_K:  return "bool";
    case APY_INT_K:   return "int";
    case APY_FLOAT_K: return "float";
    case APY_DICT_K:  return "dict";
    case APY_EXC_K:   return apy_exc_shown(O(v)->v.e.name);
    case APY_LIST_K:  return "list";
    case APY_TUPLE_K: return "tuple";
    case APY_SET_K:   return "set";
    case APY_FROZEN_K: return "frozenset";
    /* A big is an `int`. There is one integer type in Python and the width is
       an implementation detail this file is deliberately hiding -- a program
       that can tell `2 ** 100` from `5` by its type name is seeing a seam
       that should not exist. */
    case APY_BIG_K:   return "int";
    /* An instance answers with its CLASS's name, which is what makes
       `type(p).__name__` say `Point` and every TypeError about a user object
       name the user's type rather than a word from this file. */
    case APY_INST_K:  return APY_CSTR(O(O(v)->v.o.cls)->v.t.name);
    case APY_TYPE_K:  return "type";
    case APY_FUNC_K:
        if (O(v)->v.fn.is_type) return "type";
        return O(v)->v.fn.builtin ? "builtin_function_or_method" : "function";
    case APY_CELL_K:  return "cell";
    case APY_SUPER_K: return "super";
    case APY_BYTES_K: return O(v)->v.s.mut ? "bytearray" : "bytes";
    case APY_COMPLEX_K: return "complex";
    /* A CURSOR names what MADE it: `map(str, xs)` is a `map`, which is what
       `type(...).__name__` answers and what tells a reader why it is lazy.
       A plain `iter(x)` is an `iterator` -- CPython names those after what
       they walk (`list_iterator`), which is the one distinction not kept. */
    case APY_ITER_K:
        switch (O(v)->v.it.mode) {
        case APY_IT_MAP:       return "map";
        case APY_IT_FILTER:    return "filter";
        case APY_IT_ENUMERATE: return "enumerate";
        case APY_IT_ZIP:       return "zip";
        default:               return "iterator";
        }
    case APY_ELLIPSIS_K: return "ellipsis";
    case APY_NOTIMPL_K: return "NotImplementedType";
    /* All three share every field; only the name differs, and a program reads
       it to tell them apart -- `async def` with `yield` is an async
       generator, which is neither of the other two. */
    case APY_SLICE_K: return "slice";
    case APY_ALIAS_K:
        /* A UNION IS NOT A GENERIC ALIAS to a program that asks. `int | str`
           is built on the `Union` form, and `type(...).__name__` is how a
           program tells the two apart. */
        return O(O(v)->v.ga.origin)->kind == APY_INST_K
            ? "Union" : "types.GenericAlias";
    case APY_MVIEW_K: return "memoryview";
    case APY_RANGE_K: return "range";
    case APY_VIEW_K:
        switch (O(v)->v.vw.part) {
        case APY_PART_KEYS:   return "dict_keys";
        case APY_PART_VALUES: return "dict_values";
        default:              return "dict_items";
        }
    case APY_PROP_K:
        switch (O(v)->v.p.kind) {
        case APY_PROP_CLASSMETHOD:  return "classmethod";
        case APY_PROP_STATICMETHOD: return "staticmethod";
        default:                    return "property";
        }
    case APY_GEN_K:
        if (O(v)->v.g.agen) return "async_generator";
        return O(v)->v.g.coro ? "coroutine" : "generator";
    default:          return "str";
    }
}

APY_API apy_value apy_type_name(apy_value v) {
    /* The class's own name value, not a fresh copy: `type(a).__name__ is
       type(b).__name__` for two instances of one class, as in CPython. */
    if (O(v)->kind == APY_INST_K) return O(O(v)->v.o.cls)->v.t.name;
    /* `type(C).__name__` IS THE METACLASS'S NAME when one made the class.
       An ordinary class has no metaclass recorded and is a `type`. */
    if (O(v)->kind == APY_TYPE_K)
        return O(v)->v.t.meta ? O(O(v)->v.t.meta)->v.t.name : apy_lit("type");
    return apy_lit(apy_kind_name(v));
}

APY_API int64_t apy_truth(apy_value v) {
    switch (O(v)->kind) {
    case APY_NONE_K:  return 0;
    case APY_BOOL_K:
    case APY_INT_K:   return O(v)->v.i != 0;
    case APY_FLOAT_K: return O(v)->v.f != 0.0;
    case APY_COMPLEX_K: return O(v)->v.z.re != 0.0 || O(v)->v.z.im != 0.0;
    case APY_DICT_K:  return O(v)->v.d.n != 0;
    case APY_EXC_K:   return 1;
    /* Never zero: a zero-valued big demotes to the int 0 on construction. */
    case APY_BIG_K:   return 1;
    case APY_LIST_K:
    case APY_TUPLE_K:
    case APY_SET_K:
    case APY_FROZEN_K: return O(v)->v.q.n != 0;
    case APY_INST_K: {
        /* `__bool__` first, then `__len__`, then true -- CPython's order, and
           the fallback matters: an object with neither is ALWAYS truthy, so a
           bare `if obj:` on a plain instance takes the then-branch. Answering
           0 there would silently invert every such test. */
        apy_value r = apy_unary_dunder(v, "__bool__");
        if (r) return apy_truth(r);
        if (apy_error_occurred()) return 0;
        r = apy_unary_dunder(v, "__len__");
        if (r) return apy_truth(r);
        return 1;
    }
    /* Emptiness is truth only for things that HAVE a length. */
    case APY_STR_K:
    case APY_BYTES_K: return O(v)->v.s.n != 0;
    /* Everything else -- a function, a type, an iterator, a cell -- is an
       object with no emptiness to speak of, and Python calls those true.
       Reading `v.s.n` for them read whatever field the union happened to
       overlap, which for a type is its base pointer: `if et:` on a caught
       exception's type answered FALSE for every class with no base, so
       `et.__name__ if et else None` in a `__exit__` reported None. */
    default:          return 1;
    }
}

/* A str is stored as UTF-8 BYTES, but Python's `len` counts CHARACTERS:
   `len('e')` is 1 and `len('é')` is also 1, while the byte counts are 1
   and 2. Counting bytes is right for pure ASCII and silently wrong for
   everything else, which is the worst shape a bug can have -- so count the
   bytes that are not UTF-8 continuation bytes (`10xxxxxx`), which is the
   codepoint count for any well-formed UTF-8 and degrades to the byte count
   for ASCII.

   This is the only place the byte/character distinction is resolved today.
   Indexing and slicing will need the same treatment when they arrive; they
   are not in v1, and pretending otherwise by leaving `len` in bytes would
   only hide the problem. */
static int64_t apy_str_chars(apy_value v) {
    const unsigned char *p = (const unsigned char *)O(v)->v.s.p;
    int64_t i, n = O(v)->v.s.n, chars = 0;
    for (i = 0; i < n; i++)
        if ((p[i] & 0xC0) != 0x80) chars++;
    return chars;
}

APY_API apy_value apy_len(apy_value v) {
    /* THE LENGTH OF A CLASS IS THE METACLASS'S BUSINESS, exactly as iterating
       one is: `len(Colour)` is `type(Colour).__len__(Colour)`, which is how an
       enum says how many members it has. Iteration grew this case and length
       did not, so `for c in Colour` worked and `len(Colour)` reported
       `object of type 'EnumMeta' has no len()` -- about a class whose
       metaclass plainly defines one. */
    if (O(v)->kind == APY_TYPE_K && O(v)->v.t.meta) {
        apy_value hook = apy_class_find(O(v)->v.t.meta, apy_name("__len__"));
        if (hook) return apy_call_n(apy_bind(hook, v), NULL, 0);
    }
    /* A CLASS THAT EXTENDS A BUILTIN has one for everything it did not write.
       Asked before the dunder walk below, which would report "has no len()"
       for a `class D(dict)` whose body says nothing about length. */
    if (O(v)->kind == APY_INST_K && apy_inst_held(v)
            && !apy_class_find(O(v)->v.o.cls, apy_name("__len__")))
        return apy_len(apy_inst_held(v));
    /* THROUGH THE VIEW to the dict: a view has no length of its own, and
       taking one when it was made is what a snapshot does. */
    if (O(v)->kind == APY_VIEW_K)
        return apy_from_int(O(O(v)->v.vw.dict)->v.d.n);
    if (O(v)->kind == APY_MVIEW_K) return apy_from_int(O(v)->v.mv.n);
    if (O(v)->kind == APY_RANGE_K) return apy_from_int(apy_range_len(v));
    if (O(v)->kind == APY_DICT_K) return apy_from_int(O(v)->v.d.n);
    if (apy_is_seq(v) || apy_is_set(v)) return apy_from_int(O(v)->v.q.n);
    if (O(v)->kind == APY_INST_K) {
        apy_value r = apy_unary_dunder(v, "__len__");
        if (r || apy_error_occurred()) return r;
        /* No `__len__` falls through to the same "has no len()" the runtime
           reports for an int, naming the user's class -- which is exactly
           what CPython says for an instance without one. */
    }
    /* bytes counts OCTETS and str counts characters, so this cannot fall
       through to the str arm below -- which measures characters. */
    if (O(v)->kind == APY_BYTES_K) return apy_from_int(O(v)->v.s.n);
    if (O(v)->kind != APY_STR_K)
        return apy_fail2("TypeError", "object of type '%s' has no len()%s",
                         apy_kind_name(v), "");
    return apy_from_int(apy_str_chars(v));
}

/* --- repr and str ------------------------------------------------------ */
/* `repr` quotes a string and `str` does not; everything else is the same for
   the kinds here. Python prints with str() and shows with repr(), and getting
   that backwards prints `'abc'` where CPython prints `abc`. */
/* DEFINED IN THE STRING PART, which is joined after this one into the same
   translation unit. `repr` walks by code point and asks whether a character
   is printable; both answers live where the Unicode table does. */
static int apy_cp_printable(uint32_t cp);
static int64_t apy_utf8_step(const unsigned char *p, int64_t n, int64_t i,
                             uint32_t *out);

static apy_value apy_text(apy_value v, int quoted);

/* What `__str__` or `__repr__` gave back, which MUST be a str.

   Converting it instead -- calling `apy_text` on the result -- looks more
   forgiving and is a trap: `def __str__(self): return self` would then
   recurse until the C stack ran out, and a stack overflow is not a diagnosis.
   CPython raises here, so this does, with CPython's wording. */
APY_API apy_value apy_text_result_of(apy_value r, apy_value whichv) {
    const char *which = (const char *)whichv;
    char buf[128];
    if (O(r)->kind == APY_STR_K) return r;
    snprintf(buf, sizeof buf, "%s returned non-string (type %s)",
             which, apy_kind_name(r));
    return apy_fail("TypeError", buf);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_text_result(apy_value r, const char *which) {
    return apy_text_result_of(r, (apy_value)(uintptr_t)which);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

/* A container always shows its ELEMENTS with repr, whichever of str/repr was
   asked of the container: `print(['a'])` is `['a']`, not `[a]`. A one-element
   tuple keeps its trailing comma, because `(1)` is not a tuple. */
/* CONTAINERS CURRENTLY BEING RENDERED. A list that holds itself is an
   ordinary thing to build -- `xs.append(xs)` -- and rendering it naively
   recurses until the stack runs out. Python prints `[...]` for the repeat,
   which is what makes the output finite and readable.

   A small array rather than a set: the depth of a repr is a handful of frames
   in every real case, and a linear scan of it is cheaper than a hash. */
static apy_value apy_repr_active[64];
static int apy_repr_depth;

APY_API int64_t apy_repr_entered(apy_value v) {
    int i;
    for (i = 0; i < apy_repr_depth; i++)
        if (apy_repr_active[i] == v) return 1;
    if (apy_repr_depth < 64) apy_repr_active[apy_repr_depth++] = v;
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

APY_API void apy_repr_left(apy_value v) {
    if (apy_repr_depth > 0 && apy_repr_active[apy_repr_depth - 1] == v)
        apy_repr_depth--;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

APY_API apy_value apy_seq_text_of(apy_value v) {
    int tup = O(v)->kind == APY_TUPLE_K;
    int64_t n, i, len = 2, out = 0;
    apy_value *parts;
    char *buf;
    /* ALREADY BEING RENDERED: this is the cycle, and Python writes `[...]`
       for it. A tuple that contains itself cannot be built directly but can
       be reached through a list, so both spellings are needed. */
    if (apy_repr_entered(v)) return apy_lit(tup ? "(...)" : "[...]");
    n = O(v)->v.q.n;
    parts = (apy_value *)malloc((size_t)(n ? n : 1) * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        parts[i] = apy_text(O(v)->v.q.items[i], 1);
        len += O(parts[i])->v.s.n + 2;
    }
    apy_repr_left(v);
    if (tup && n == 1) len += 1;
    buf = (char *)malloc((size_t)len + 1);
    buf[out++] = tup ? '(' : '[';
    for (i = 0; i < n; i++) {
        if (i) { buf[out++] = ','; buf[out++] = ' '; }
        memcpy(buf + out, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
        out += O(parts[i])->v.s.n;
    }
    if (tup && n == 1) buf[out++] = ',';
    buf[out++] = tup ? ')' : ']';
    buf[out] = '\0';
    free(parts);
    return apy_str_take(buf, out);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static apy_value apy_seq_text(apy_value v) { return apy_seq_text_of(v); }

APY_API apy_value apy_text_of(apy_value v, int64_t quoted) {
    char buf[64];
    switch (O(v)->kind) {
    /* IGNORES `quoted`. `str(b'ab')` is "b'ab'" in Python 3 -- bytes has no
       separate str, which is the wart CPython emits a BytesWarning about
       under -b. Reproducing it means `print(b'ab')` shows the repr, and a
       `str()` that stripped the prefix would disagree with CPython on every
       line that printed one. */
    case APY_BYTES_K: return apy_bytes_repr(v);
    case APY_NONE_K: return apy_lit("None");
    case APY_BOOL_K: return apy_lit(O(v)->v.i ? "True" : "False");
    case APY_INT_K:
        snprintf(buf, sizeof buf, "%lld", (long long)O(v)->v.i);
        return apy_str_copy(buf, (int64_t)strlen(buf));
    case APY_BIG_K:   return apy_big_text((apy_value)O(v));
    case APY_COMPLEX_K: {
        /* CPython's rules exactly, and they are fussier than they look:

             1+2j   -> "(1+2j)"     parenthesised, sign always shown
             2j     -> "2j"         a ZERO real part is omitted, and so are
                                    the brackets
             -0+2j  -> "(-0+2j)"    but only a POSITIVELY signed zero is
                                    omitted; `-0.0` is a real part
             1-2j   -> "(1-2j)"
             0j     -> "0j"

           The sign test is on the BIT, not the value, because `-0.0 == 0.0`
           and the two print differently. Writing this as "if re is zero" made
           `complex(-0.0, 2)` print `2j`, which reads back as a different
           number. */
        char rbuf[64], ibuf[64];
        double re = O(v)->v.z.re, im = O(v)->v.z.im;
        int re_is_pos_zero = (re == 0.0) && !signbit(re);
        apy_complex_part(ibuf, sizeof ibuf, im);
        if (re_is_pos_zero) {
            snprintf(buf, sizeof buf, "%sj", ibuf);
            return apy_str_copy(buf, (int64_t)strlen(buf));
        }
        apy_complex_part(rbuf, sizeof rbuf, re);
        /* The imaginary part carries its own sign when negative, so the `+`
           is only written when it does not. `nan` has no sign to read, and
           CPython writes `+nanj`; `signbit` on a nan is unreliable, so the
           leading character of the rendered text is what decides. */
        if (ibuf[0] == '-')
            snprintf(buf, sizeof buf, "(%s%sj)", rbuf, ibuf);
        else
            snprintf(buf, sizeof buf, "(%s+%sj)", rbuf, ibuf);
        return apy_str_copy(buf, (int64_t)strlen(buf));
    }
    case APY_FLOAT_K:
        py_repr_double(buf, sizeof buf, O(v)->v.f);
        return apy_str_copy(buf, (int64_t)strlen(buf));
    case APY_DICT_K:  return apy_dict_text(v);
    case APY_RANGE_K: {
        /* `range(0, 10, 2)` -- and `range(0, 3)` when the step is 1, which
           is how CPython prints one. */
        char rbuf[96];
        int wrote;
        if (O(v)->v.rg.step == 1)
            wrote = snprintf(rbuf, sizeof rbuf, "range(%lld, %lld)",
                             (long long)O(v)->v.rg.start,
                             (long long)O(v)->v.rg.stop);
        else
            wrote = snprintf(rbuf, sizeof rbuf, "range(%lld, %lld, %lld)",
                             (long long)O(v)->v.rg.start,
                             (long long)O(v)->v.rg.stop,
                             (long long)O(v)->v.rg.step);
        return apy_str_copy(rbuf, wrote);
    }
    case APY_ELLIPSIS_K: return apy_lit("Ellipsis");
    case APY_NOTIMPL_K: return apy_lit("NotImplemented");
    case APY_ALIAS_K: {
        /* `list[int]`, not `list[<class 'int'>]`. A TYPE ARGUMENT RENDERS AS
           ITS NAME here even though `str(int)` is `<class 'int'>` -- CPython's
           alias repr uses the qualname, and the difference is visible in
           every annotation a program prints. */
        apy_value origin = O(v)->v.ga.origin;
        /* THE UNION IS THE ONLY FORM THAT PRINTS WITH BARS. PEP 604 made `int
           | str` the spelling for that one; every other form keeps the
           subscript it was written with, and testing "the origin is an
           instance" made `Annotated[int, 'x']` print as a union. */
        apy_value form_nm = O(origin)->kind == APY_INST_K
            ? apy_dict_get_or(O(origin)->v.o.dict, apy_lit("_name"), 0) : 0;
        int is_union = form_nm && strcmp(APY_CSTR(form_nm), "Union") == 0;
        apy_value head = (O(origin)->kind == APY_FUNC_K
                          && O(origin)->v.fn.is_type) ? O(origin)->v.fn.name
                         : O(origin)->kind == APY_TYPE_K
                           ? O(origin)->v.t.name : apy_text(origin, 0);
        apy_value args = O(v)->v.ga.args;
        int64_t i, n = apy_is_seq(args) ? O(args)->v.q.n : 0;
        int64_t room = O(head)->v.s.n + 8;
        char *out;
        int64_t at;
        apy_value *parts = (apy_value *)malloc(
            (size_t)(n ? n : 1) * sizeof(apy_value));
        for (i = 0; i < n; i++) {
            apy_value one = O(args)->v.q.items[i];
            /* `NoneType` IS SPELLED `None` INSIDE A UNION, which is how
               CPython prints `int | None` -- the class is what the union
               HOLDS and `None` is what it is written as. */
            parts[i] = (O(one)->kind == APY_TYPE_K
                        && strcmp(APY_CSTR(O(one)->v.t.name), "NoneType") == 0)
                ? apy_lit("None")
                : O(one)->kind == APY_TYPE_K ? O(one)->v.t.name
                : (O(one)->kind == APY_FUNC_K && O(one)->v.fn.is_type)
                    ? O(one)->v.fn.name
                    : apy_text(one, 1);
            room += O(parts[i])->v.s.n + 2;
        }
        out = (char *)malloc((size_t)room + 1);
        /* A UNION PRINTS WITH BARS, not as `Union[...]`: PEP 604 made `int |
           str` the spelling, and that is what CPython's repr answers. It is
           an alias like any other underneath. */
        if (is_union) {
            at = 0;
            for (i = 0; i < n; i++) {
                if (i) { out[at++] = ' '; out[at++] = '|'; out[at++] = ' '; }
                memcpy(out + at, O(parts[i])->v.s.p,
                       (size_t)O(parts[i])->v.s.n);
                at += O(parts[i])->v.s.n;
            }
            out[at] = 0;
            free(parts);
            return apy_str_take(out, at);
        }
        memcpy(out, O(head)->v.s.p, (size_t)O(head)->v.s.n);
        at = O(head)->v.s.n;
        out[at++] = '[';
        for (i = 0; i < n; i++) {
            if (i) { out[at++] = ','; out[at++] = ' '; }
            memcpy(out + at, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
            at += O(parts[i])->v.s.n;
        }
        out[at++] = ']';
        out[at] = 0;
        free(parts);
        return apy_str_take(out, at);
    }
    case APY_SLICE_K: {
        /* `slice(1, 2, None)` -- always all three, and always the repr of
           each, which is how CPython prints one whether or not the bound was
           written. */
        apy_value a = apy_text(O(v)->v.sl.start, 1);
        apy_value b = apy_text(O(v)->v.sl.stop, 1);
        apy_value c = apy_text(O(v)->v.sl.step, 1);
        int64_t n = O(a)->v.s.n + O(b)->v.s.n + O(c)->v.s.n + 12;
        char *out = (char *)malloc((size_t)n + 1);
        int wrote = snprintf(out, (size_t)n + 1, "slice(%.*s, %.*s, %.*s)",
                             (int)O(a)->v.s.n, O(a)->v.s.p,
                             (int)O(b)->v.s.n, O(b)->v.s.p,
                             (int)O(c)->v.s.n, O(c)->v.s.p);
        return apy_str_take(out, wrote);
    }
    case APY_EXC_K:   return apy_exc_text(v, quoted);
    case APY_LIST_K:
    case APY_TUPLE_K: return apy_seq_text(v);
    case APY_SET_K:
    case APY_FROZEN_K: return apy_set_text(v);
    case APY_VIEW_K: {
        /* `dict_keys(['a'])` -- the KIND NAME around the list of what the
           view is looking at, which is how CPython prints one. Falling
           through to the default answered the empty string, so a program that
           printed `d.keys()` printed nothing at all. */
        apy_value items = apy_seq_text(apy_view_items(v));
        const char *head = apy_kind_name(v);
        int64_t room = (int64_t)strlen(head) + O(items)->v.s.n + 3;
        char *out = (char *)malloc((size_t)room + 1);
        int wrote = snprintf(out, (size_t)room + 1, "%s(%.*s)", head,
                             (int)O(items)->v.s.n, O(items)->v.s.p);
        return apy_str_take(out, wrote);
    }
    case APY_INST_K: {
        /* A TYPING FORM PRINTS AS `typing.Name`. It is an instance with no
           `__repr__`, so the default `<_SpecialForm object at 0x...>` came
           out -- an address where CPython prints the name a program wrote. */
        if (apy_is_special_form(v)) {
            apy_value nm = apy_dict_get_or(O(v)->v.o.dict, apy_lit("_name"), 0);
            if (nm) {
                char *outf = (char *)malloc((size_t)O(nm)->v.s.n + 8);
                int wrotef = snprintf(outf, (size_t)O(nm)->v.s.n + 8,
                                      "typing.%.*s", (int)O(nm)->v.s.n,
                                      O(nm)->v.s.p);
                return apy_str_take(outf, wrotef);
            }
        }
        /* `str(x)` asks `__str__` and FALLS BACK to `__repr__`; `repr(x)`
           asks only `__repr__`. That asymmetry is Python's and it is load
           bearing: a class defining only `__repr__` prints with it, and one
           defining only `__str__` still shows its default repr in a list. */
        apy_value r = quoted ? 0 : apy_unary_dunder(v, "__str__");
        if (r || apy_error_occurred())
            return r ? apy_text_result(r, "__str__") : r;
        r = apy_unary_dunder(v, "__repr__");
        if (r || apy_error_occurred())
            return r ? apy_text_result(r, "__repr__") : r;
        /* A CLASS EXTENDING A BUILTIN SHOWS THE BUILTIN. `class D(dict)`
           with no `__repr__` prints `{'a': 1}` in CPython, and the default
           below would hide the entire contents -- which for a Counter or a
           defaultdict is the whole value. The class name is not added,
           because CPython does not add it either; a subclass that wants its
           name in the repr writes one, as `Counter` and `deque` do. */
        if (O(v)->v.o.held) return apy_repr(O(v)->v.o.held);
        /* The default. CPython prints the ADDRESS, which no two runs agree on
           and which no conformance case can therefore assert -- every case
           that prints a bare instance defines `__repr__`. The address is
           printed anyway rather than omitted, because a program that prints
           one is telling the reader it did not define one. */
        snprintf(buf, sizeof buf, "<%s object at 0x%llx>",
                 apy_kind_name(v), (unsigned long long)v);
        return apy_str_copy(buf, (int64_t)strlen(buf));
    }
    case APY_TYPE_K:
        /* PRINTING A CLASS IS THE METACLASS'S BUSINESS when it says so.
           `repr(Colour)` is `type(Colour).__repr__(Colour)`, which is how an
           enum prints as `<enum 'Colour'>` -- the fourth of the metaclass
           dunders to need saying so, beside `__iter__`, `__len__` and
           `__contains__`. */
        if (O(v)->v.t.meta) {
            apy_value hook = apy_class_find(O(v)->v.t.meta,
                                            apy_name("__repr__"));
            if (hook)
                return apy_call_n(apy_bind(hook, v), NULL, 0);
        }
        snprintf(buf, sizeof buf, "<class '%s'>",
                 APY_CSTR(O(v)->v.t.name));
        return apy_str_copy(buf, (int64_t)strlen(buf));
    case APY_FUNC_K:
        /* A BUILTIN TYPE NAME PRINTS AS A CLASS. `print(int)` says
           `<class 'int'>` in Python, and it reaches here as a callable thunk
           -- so the flag, not the kind, decides what it is called. */
        if (O(v)->v.fn.is_type) {
            snprintf(buf, sizeof buf, "<class '%s'>",
                     APY_CSTR(O(v)->v.fn.name));
            return apy_str_copy(buf, (int64_t)strlen(buf));
        }
        snprintf(buf, sizeof buf, "<%s %s at 0x%llx>",
                 O(v)->v.fn.bound ? "bound method" : "function",
                 APY_CSTR(O(v)->v.fn.name), (unsigned long long)v);
        return apy_str_copy(buf, (int64_t)strlen(buf));
    default: break;
    }
    if (!quoted) return v;
    {
        /* Python prefers single quotes and switches to double only when the
           text contains a single quote and no double. */
        const char *p = O(v)->v.s.p;
        int64_t n = O(v)->v.s.n, i, out = 0;
        int has_sq = 0, has_dq = 0;
        char q, *buf2;
        for (i = 0; i < n; i++) {
            if (p[i] == '\'') has_sq = 1;
            if (p[i] == '"') has_dq = 1;
        }
        q = (has_sq && !has_dq) ? '"' : '\'';
        buf2 = (char *)malloc((size_t)n * 4 + 3);
        buf2[out++] = q;
        /* BY CODE POINT ABOVE 0x7F, because whether a character is
           printable is a question about the CHARACTER: U+00A0 and U+2003
           are spaces Python escapes, and asked a byte at a time neither one
           is anything at all. Below 0x80 a byte IS a character and the walk
           this replaces still stands.

           THE THREE WIDTHS ARE PYTHON'S: `\\xNN` under 0x100, `\\uNNNN`
           under 0x10000, `\\UNNNNNNNN` above. An unprintable character
           written out in its own bytes would come back from `eval`
           unchanged and LOOK like the space it is not. */
        for (i = 0; i < n; ) {
            unsigned char c = (unsigned char)p[i];
            if (c < 0x80) {
                if (c == (unsigned char)q || c == '\\') {
                    buf2[out++] = '\\'; buf2[out++] = (char)c;
                } else if (c == '\n') { buf2[out++] = '\\'; buf2[out++] = 'n'; }
                else if (c == '\r') { buf2[out++] = '\\'; buf2[out++] = 'r'; }
                else if (c == '\t') { buf2[out++] = '\\'; buf2[out++] = 't'; }
                else if (c < 0x20 || c == 0x7f) {
                    out += (int64_t)sprintf(buf2 + out, "\\x%02x", c);
                } else buf2[out++] = (char)c;
                i++;
                continue;
            }
            {
                uint32_t cp;
                int64_t used = apy_utf8_step((const unsigned char *)p, n,
                                             i, &cp);
                /* A BYTE THAT IS NOT VALID UTF-8 is escaped as itself rather
                   than replaced: `repr` is what a person reads to find out
                   what is ACTUALLY in a string, so a lie about its bytes is
                   the one thing it must not tell. */
                if (!used) {
                    out += (int64_t)sprintf(buf2 + out, "\\x%02x", c);
                    i++;
                } else if (apy_cp_printable(cp)) {
                    int64_t k;
                    for (k = 0; k < used; k++) buf2[out++] = p[i + k];
                    i += used;
                } else if (cp < 0x100) {
                    out += (int64_t)sprintf(buf2 + out, "\\x%02x",
                                            (unsigned)cp);
                    i += used;
                } else if (cp < 0x10000) {
                    out += (int64_t)sprintf(buf2 + out, "\\u%04x",
                                            (unsigned)cp);
                    i += used;
                } else {
                    out += (int64_t)sprintf(buf2 + out, "\\U%08x",
                                            (unsigned)cp);
                    i += used;
                }
            }
        }
        buf2[out++] = q;
        buf2[out] = '\0';
        return apy_str_take(buf2, out);
    }
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_text(apy_value v, int quoted) {
    return apy_text_of(v, (int64_t)quoted);
}

APY_API apy_value apy_repr(apy_value v) { return apy_text(v, 1); }

/* `del d[k]` and `del xs[i]`.

   Two containers, two failure modes, and CPython's own messages for each: a
   missing dict key is a KeyError naming the key's repr, and an out-of-range
   list index is "list assignment index out of range" -- the ASSIGNMENT text,
   because deleting is a store-shaped operation and CPython says so.

   A tuple is refused: immutability is the whole distinction from a list, and
   letting a `del` through would erase it. */
APY_API apy_value apy_delitem(apy_value seq, apy_value key) {
    int64_t i;
    if (O(seq)->kind == APY_INST_K) {
        /* `del obj[k]` IS `obj.__delitem__(k)`. Never dispatched before, so
           a class that wrote one had it ignored and the delete was reported
           as unsupported -- a wrong answer about the class's own method. */
        apy_value r = apy_method1(seq, "__delitem__", key);
        if (r || apy_error_occurred()) return r;
        /* A CLASS THAT EXTENDS A BUILTIN deletes from the one it carries. */
        if (apy_inst_held(seq)) return apy_delitem(apy_inst_held(seq), key);
    }
    if (O(seq)->kind == APY_DICT_K) {
        const char *bad = apy_unhashable(key);
        if (bad) return apy_fail2("TypeError", "unhashable type: '%s'%s",
                                  bad, "");
        i = apy_dict_find(seq, key);
        if (i < 0) {
            apy_value shown = apy_repr(key);
            return apy_fail2("KeyError", "%s%s", APY_CSTR(shown), "");
        }
        /* Shift the survivors down, preserving INSERTION ORDER -- which is
           part of the language since 3.7, so swapping the last entry into the
           hole would be a wrong answer rather than a faster one. */
        for (; i + 1 < O(seq)->v.d.n; i++) {
            O(seq)->v.d.keys[i] = O(seq)->v.d.keys[i + 1];
            O(seq)->v.d.vals[i] = O(seq)->v.d.vals[i + 1];
        }
        O(seq)->v.d.n--;
        return apy_none();
    }
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("TypeError", "'%s' object doesn't support item deletion%s",
                         apy_kind_name(seq), "");
    /* `del xs[1:3]` REMOVES A SPAN. Falling through to the index path asked
       `apy_index_arg` for an integer, got the slice, and reported an
       IndexError about a subscript the program never wrote. */
    if (key && O(key)->kind == APY_SLICE_K) {
        apy_value bounds = apy_slice_indices(key,
                                             apy_from_int(O(seq)->v.q.n));
        int64_t start, stop, step, from, to;
        if (!bounds) return 0;
        start = O(O(bounds)->v.q.items[0])->v.i;
        stop = O(O(bounds)->v.q.items[1])->v.i;
        step = O(O(bounds)->v.q.items[2])->v.i;
        if (step != 1)
            return apy_fail("ValueError",
                            "only step 1 slice deletion is supported");
        if (start < 0) start = 0;
        if (stop > O(seq)->v.q.n) stop = O(seq)->v.q.n;
        if (stop < start) stop = start;
        for (from = stop, to = start; from < O(seq)->v.q.n; from++, to++)
            O(seq)->v.q.items[to] = O(seq)->v.q.items[from];
        O(seq)->v.q.n -= stop - start;
        return apy_none();
    }
    if (!apy_index_arg(key, &i, APY_IDX_SUB)) return 0;
    if (i < 0) i += O(seq)->v.q.n;
    if (i < 0 || i >= O(seq)->v.q.n)
        return apy_fail("IndexError", "list assignment index out of range");
    for (; i + 1 < O(seq)->v.q.n; i++)
        O(seq)->v.q.items[i] = O(seq)->v.q.items[i + 1];
    O(seq)->v.q.n--;
    return apy_none();
}


"""
