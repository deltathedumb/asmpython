"""The object runtime, in C: the numeric tower, comparison and conversions.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * the numeric tower
  * comparison
  * conversions
"""

C = r"""/* --- the numeric tower ------------------------------------------------- */
/* `bool` is an `int` for arithmetic and a distinct type for everything else,
   which is exactly CPython: `True + 1` is 2, and `type(True)` is not `int`. */
/* THE EXPORTED HALF, which `runtime/list_cell.py` replaces. The `static`
   below keeps the name its callers use and the cast they do not have to
   write; this body is what the runtime uses when nothing is ported. */
/* Declared here because the delegate is defined below its first use. */
static int apy_is_num(apy_value v);
APY_API int64_t apy_is_num_of(apy_value v) {
    return O(v)->kind == APY_BOOL_K || O(v)->kind == APY_INT_K
        || O(v)->kind == APY_FLOAT_K || O(v)->kind == APY_BIG_K;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above is what stands in when nothing is
   ported. The cast to a machine word happens here, once. */
static int apy_is_num(apy_value v) {
    return (int)apy_is_num_of(v);
}
/* THE EXPORTED HALF, which `runtime/list_cell.py` replaces. The `static`
   below keeps the name its callers use and the cast they do not have to
   write; this body is what the runtime uses when nothing is ported. */
APY_API int64_t apy_is_int_like_of(apy_value v) {
    return O(v)->kind == APY_BOOL_K || O(v)->kind == APY_INT_K
        || O(v)->kind == APY_BIG_K;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above is what stands in when nothing is
   ported. The cast to a machine word happens here, once. */
static int apy_is_int_like(apy_value v) {
    return (int)apy_is_int_like_of(v);
}
/* A big is either operand of a MIXED int/big pair, which the int64 fast paths
   cannot take. Every arithmetic operation asks this before reading `v.i`,
   because `v.i` on a big is a pointer read as an integer. */
static int apy_either_big(apy_value a, apy_value b) {
    return O(a)->kind == APY_BIG_K || O(b)->kind == APY_BIG_K;
}
APY_API double apy_num_f_of(apy_value v) {
    if (O(v)->kind == APY_FLOAT_K) return O(v)->v.f;
    if (O(v)->kind == APY_BIG_K) return apy_big_double(O(v));
    return (double)O(v)->v.i;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static double apy_num_f(apy_value v) {
    return apy_num_f_of(v);
}

/* The int64 operations, each answering whether the result fit. Written out
   rather than reached for as a compiler builtin: `__builtin_add_overflow` is
   not portable and this source is compiled by whatever toolchain the target
   uses, which is the same argument `apy_bits` makes about `__builtin_clzll`.
   Signed overflow is UNDEFINED in C, so the arithmetic is done unsigned and
   the check reads the signs of the result. */
static int apy_add_i64(int64_t a, int64_t b, int64_t *out) {
    uint64_t r = (uint64_t)a + (uint64_t)b;
    /* Overflow exactly when both operands disagree in sign with the result. */
    if ((((uint64_t)a ^ r) & ((uint64_t)b ^ r)) >> 63) return 0;
    *out = (int64_t)r;
    return 1;
}

static int apy_sub_i64(int64_t a, int64_t b, int64_t *out) {
    uint64_t r = (uint64_t)a - (uint64_t)b;
    if ((((uint64_t)a ^ (uint64_t)b) & ((uint64_t)a ^ r)) >> 63) return 0;
    *out = (int64_t)r;
    return 1;
}

static int apy_mul_i64(int64_t a, int64_t b, int64_t *out) {
    uint64_t ua = apy_abs64(a), ub = apy_abs64(b), p;
    int neg = (a < 0) != (b < 0);
    if (ua == 0 || ub == 0) { *out = 0; return 1; }
    p = ua * ub;
    /* The magnitude overflowed if dividing it back does not give the other
       operand. Cheaper than a 128-bit product and needs no wider type. */
    if (p / ua != ub) return 0;
    if (neg) {
        if (p > (uint64_t)9223372036854775808ULL) return 0;
        *out = (int64_t)(0u - p);
    } else {
        if (p > (uint64_t)9223372036854775807ULL) return 0;
        *out = (int64_t)p;
    }
    return 1;
}

APY_API apy_value apy_binop_error_of(apy_value op, apy_value a,
                                     apy_value b) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "unsupported operand type(s) for %s: '%s' and '%s'",
             (const char *)op, apy_kind_name(a), apy_kind_name(b));
    return apy_fail("TypeError", buf);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_binop_error(const char *op, apy_value a, apy_value b) {
    return apy_binop_error_of((apy_value)(uintptr_t)op, a, b);
}

/* Operator symbol -> the pair of methods a class may define for it. One table
   instead of a hook inside each of the twelve arithmetic entry points: every
   one of them ALREADY funnels an operand pair it cannot handle into the error
   above, and an instance is such a pair for all of them. So the dispatch goes
   where they already meet, and each operator gains exactly one changed word
   at its exit. */
static const char *const APY_OP_DUNDERS[][3] = {
    { "+",  "__add__",      "__radd__"      },
    { "-",  "__sub__",      "__rsub__"      },
    { "*",  "__mul__",      "__rmul__"      },
    { "/",  "__truediv__",  "__rtruediv__"  },
    { "//", "__floordiv__", "__rfloordiv__" },
    { "%",  "__mod__",      "__rmod__"      },
    { "** or pow()", "__pow__", "__rpow__"  },
    { "&",  "__and__",      "__rand__"      },
    { "|",  "__or__",       "__ror__"       },
    { "^",  "__xor__",      "__rxor__"      },
    { "<<", "__lshift__",   "__rlshift__"   },
    { ">>", "__rshift__",   "__rrshift__"   },
    { "@",  "__matmul__",   "__rmatmul__"   },
    { NULL, NULL, NULL },
};

/* Where the arithmetic operators end when neither operand is a kind they know.
   Ask the user's class first; report the operand pair only if nothing
   answered. `apy_binop_error` itself is kept for the two call sites in
   `sorted`/`min`, which want the REPORT and not another dispatch -- they have
   already decided the comparison failed and are naming why. */
static apy_value apy_op_apply(const char *op, apy_value a, apy_value b);

static apy_value apy_binop_fallback(const char *op, apy_value a, apy_value b) {
    int i;
    if (apy_either_inst(a, b))
        for (i = 0; APY_OP_DUNDERS[i][0]; i++)
            if (strcmp(APY_OP_DUNDERS[i][0], op) == 0) {
                apy_value r = apy_binary_dunder(a, b, APY_OP_DUNDERS[i][1],
                                                APY_OP_DUNDERS[i][2]);
                if (r || apy_error_occurred()) return r;
                /* NEITHER SIDE WROTE THE DUNDER, so a builtin-extending
                   instance means the builtin: `t + (4,)` on a `class
                   T(tuple)` is tuple concatenation, and reporting an
                   unsupported operand pair between `'T'` and `'tuple'` names
                   an operation tuples plainly support. Retried once, with
                   whichever side was an instance replaced -- the retry
                   cannot loop, because what goes back in is a builtin. */
                {
                    apy_value ua = apy_as_builtin(a, APY_OP_DUNDERS[i][1]);
                    apy_value ub = apy_as_builtin(b, APY_OP_DUNDERS[i][2]);
                    if (ua != a || ub != b) {
                        apy_value r2 = apy_op_apply(op, ua, ub);
                        if (r2 || apy_error_occurred()) return r2;
                    }
                }
                break;
            }
    return apy_binop_error(op, a, b);
}

/* ── complex ─────────────────────────────────────────────────────────────
   Complex joins the numeric tower for ARITHMETIC and equality and stays out
   of it for ORDERING: `1j < 2j` is a TypeError in Python, which is the whole
   reason it cannot be handled as a third float. Every operator below tests
   for it before the real-valued paths, because an int or a float on the other
   side has to widen rather than the complex narrowing. */
static int apy_is_complex(apy_value v) { return O(v)->kind == APY_COMPLEX_K; }

static int apy_either_complex(apy_value a, apy_value b) {
    return apy_is_complex(a) || apy_is_complex(b);
}

/* A numeric value as a complex. Returns 0 when the value is not a number at
   all, which is what makes `1j + 'a'` a TypeError rather than a silent zero. */
static int apy_as_complex(apy_value v, double *re, double *im) {
    if (apy_is_complex(v)) { *re = O(v)->v.z.re; *im = O(v)->v.z.im; return 1; }
    if (!apy_is_num(v)) return 0;
    *re = apy_num_f(v);
    *im = 0.0;
    return 1;
}

/* `complex(re, im)` from two runtime values.

   NOT `re + im * 1j`, which is what the frontend built first and which loses
   a signed zero: `complex(-0.0, 2)` came out `2j` because `0.0 + -0.0` is
   `+0.0`. The sign of a zero is observable in the repr, so the parts are
   converted and stored rather than computed. */
APY_API apy_value apy_complex_of(apy_value re, apy_value im) {
    /* `complex(x)` WITH ONE ARGUMENT ASKS THE CLASS. `__complex__` is the
       hook, and it only applies to the one-argument form -- `complex(a, b)`
       is building from parts and has nothing to ask. */
    if (O(re)->kind == APY_INST_K && O(im)->kind == APY_NONE_K) {
        apy_value got = apy_unary_dunder(re, "__complex__");
        if (apy_error_occurred()) return 0;
        if (got) return got;
    }
    /* `complex("1+2j")` -- THE STRING FORM, which is a parse rather than an
       arithmetic conversion, and only exists for the one-argument shape. The
       message above already promised a string was acceptable. */
    if (O(re)->kind == APY_STR_K && O(im)->kind == APY_NONE_K) {
        const char *p = APY_CSTR(re);
        char *end = 0;
        double a = 0.0, b = 0.0;
        while (*p == ' ' || *p == '	') p++;
        if (*p == '(') p++;
        a = strtod(p, &end);
        if (end == p)
            return apy_fail("ValueError",
                            "complex() arg is a malformed string");
        if (*end == 'j' || *end == 'J') {
            /* A bare imaginary: `complex("2j")`. */
            b = a; a = 0.0; end++;
        } else if (*end == '+' || *end == '-') {
            const char *at = end;
            double sign = *end == '-' ? -1.0 : 1.0;
            b = strtod(end, &end);
            if (end == at + 1) { b = sign; end = at + 1; }
            if (*end != 'j' && *end != 'J')
                return apy_fail("ValueError",
                                "complex() arg is a malformed string");
            end++;
        }
        if (*end == ')') end++;
        while (*end == ' ' || *end == '	') end++;
        if (*end)
            return apy_fail("ValueError",
                            "complex() arg is a malformed string");
        return apy_from_complex(a, b);
    }
    /* An omitted imaginary part is zero from here on: only the question of
       whether it was WRITTEN needed the distinction, and that is settled. */
    if (O(im)->kind == APY_NONE_K) im = apy_from_int(0);
    double rr, ri, ir, ii;
    if (!apy_as_complex(re, &rr, &ri))
        return apy_fail2("TypeError",
                         "complex() argument must be a string or a number, "
                         "not '%s'%s", apy_kind_name(re), "");
    if (!apy_as_complex(im, &ir, &ii))
        return apy_fail2("TypeError",
                         "complex() argument must be a string or a number, "
                         "not '%s'%s", apy_kind_name(im), "");
    /* The ordinary case -- two REAL arguments -- stores them untouched, so a
       signed zero survives: `complex(0, -0.0)` is `-0j`, and computing it as
       `0.0 + (-0.0)` gives `+0.0` and prints `0j`. */
    if (ri == 0.0 && ii == 0.0 && !signbit(ri) && !signbit(ii))
        return apy_from_complex(rr, ir);
    /* `complex(1+2j, 3+4j)` is `(1+2j) + (3+4j)*1j` = `(-3+5j)`. Rare, and
       CPython does exactly this. */
    return apy_from_complex(rr - ii, ri + ir);
}

static apy_value apy_complex_binop(const char *sym, apy_value a, apy_value b) {
    double ar, ai, br, bi;
    if (!apy_as_complex(a, &ar, &ai) || !apy_as_complex(b, &br, &bi))
        return apy_binop_error(sym, a, b);
    if (strcmp(sym, "+") == 0) return apy_from_complex(ar + br, ai + bi);
    if (strcmp(sym, "-") == 0) return apy_from_complex(ar - br, ai - bi);
    if (strcmp(sym, "*") == 0)
        return apy_from_complex(ar * br - ai * bi, ar * bi + ai * br);
    if (strcmp(sym, "/") == 0) {
        /* (a/b) = a * conj(b) / |b|^2. The textbook form, not Smith's
           scaling: CPython uses this one, and matching its ROUNDING matters
           more here than avoiding an overflow at 1e300 that no test reaches.
           Using a different formula gave a different last digit. */
        double d = br * br + bi * bi;
        if (d == 0.0)
            return apy_fail("ZeroDivisionError", "complex division by zero");
        return apy_from_complex((ar * br + ai * bi) / d,
                                (ai * br - ar * bi) / d);
    }
    /* `//`, `%` and `divmod` were removed from complex in Python 3. The
       message names the operator, as CPython's does. */
    return apy_fail2("TypeError",
                     "can't take floor or mod of complex number%s%s", "", "");
}

/* One operator, chosen by the symbol `apy_binop_fallback` was given. Only
   the arithmetic ones, because only those reach that fallback. */
static apy_value apy_op_apply(const char *op, apy_value a, apy_value b) {
    if (strcmp(op, "+") == 0)  return apy_add(a, b);
    if (strcmp(op, "-") == 0)  return apy_sub(a, b);
    if (strcmp(op, "*") == 0)  return apy_mul(a, b);
    if (strcmp(op, "/") == 0)  return apy_truediv(a, b);
    if (strcmp(op, "//") == 0) return apy_floordiv(a, b);
    if (strcmp(op, "%") == 0)  return apy_mod(a, b);
    if (strcmp(op, "&") == 0)  return apy_bitand(a, b);
    if (strcmp(op, "|") == 0)  return apy_bitor(a, b);
    if (strcmp(op, "^") == 0)  return apy_bitxor(a, b);
    if (strcmp(op, "<<") == 0) return apy_lshift(a, b);
    if (strcmp(op, ">>") == 0) return apy_rshift(a, b);
    return 0;
}

APY_API apy_value apy_add(apy_value a, apy_value b) {
    if (apy_either_complex(a, b)) return apy_complex_binop("+", a, b);
    if (O(a)->kind == APY_BYTES_K && O(b)->kind == APY_BYTES_K) {
        int64_t n = O(a)->v.s.n + O(b)->v.s.n;
        char *buf = (char *)malloc((size_t)n + 1);
        if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
        memcpy(buf, O(a)->v.s.p, (size_t)O(a)->v.s.n);
        memcpy(buf + O(a)->v.s.n, O(b)->v.s.p, (size_t)O(b)->v.s.n);
        buf[n] = 0;
        { apy_value r = apy_str_take(buf, n);
          O(r)->kind = APY_BYTES_K;
          O(r)->v.s.mut = O(a)->v.s.mut;
          return r; }
    }
    /* MIXING BYTES AND str IS A TypeError, which is the whole point of PEP
       3112 -- they are different types and `+` will not bridge them. An
       equality body ended up here during the bytes work and returned a C int
       as an `apy_value`, so `b"a" + "a"` segfaulted rather than raising. */
    if (O(a)->kind == APY_BYTES_K || O(b)->kind == APY_BYTES_K)
        return apy_fail2("TypeError", "can't concat %s to %s",
                         apy_kind_name(b), apy_kind_name(a));
    if (O(a)->kind == APY_STR_K && O(b)->kind == APY_STR_K) {
        int64_t n = O(a)->v.s.n + O(b)->v.s.n;
        char *buf = (char *)malloc((size_t)n + 1);
        memcpy(buf, O(a)->v.s.p, (size_t)O(a)->v.s.n);
        memcpy(buf + O(a)->v.s.n, O(b)->v.s.p, (size_t)O(b)->v.s.n);
        buf[n] = '\0';
        return apy_str_take(buf, n);
    }
    /* A str on the LEFT with anything else on the right is a concatenation
       that failed, and CPython says so in those words rather than in the
       generic operand form: `'ab' + 7` is `can only concatenate str (not
       "int") to str`. A str on the RIGHT of a non-str gets the generic
       message, because there the left operand's `__add__` is what refused. */
    /* UNLESS THE RIGHT OPERAND WRITES `__radd__`. `"the " + obj` is
       `str.__add__` answering NotImplemented and CPython then asking
       `type(obj).__radd__`, which is how a `StrEnum` member concatenates.
       Refusing here ran before that could happen and reported
       `can only concatenate str (not "Colours") to str` about a class that
       defines exactly the method for it. */
    if (O(a)->kind == APY_STR_K && O(b)->kind == APY_INST_K) {
        apy_value r = apy_binary_dunder(a, b, "__add__", "__radd__");
        if (r || apy_error_occurred()) return r;
    }
    if (O(a)->kind == APY_STR_K)
        return apy_fail2("TypeError",
                         "can only concatenate str (not \"%s\") to str%s",
                         apy_kind_name(b), "");
    if (apy_is_seq(a) && apy_is_seq(b) && O(a)->kind == O(b)->kind) {
        apy_value out = apy_seq_new(O(a)->kind, O(a)->v.q.n + O(b)->v.q.n + 1);
        int64_t i;
        for (i = 0; i < O(a)->v.q.n; i++) apy_seq_push(out, O(a)->v.q.items[i]);
        for (i = 0; i < O(b)->v.q.n; i++) apy_seq_push(out, O(b)->v.q.items[i]);
        return out;
    }
    /* A list or tuple on the LEFT gets the concatenation wording, exactly as a
       str does two branches up -- `[1] + (2,)` is `can only concatenate list
       (not "tuple") to list`. A list on the RIGHT of a non-sequence gets the
       generic form, because there the left operand refused. */
    if (apy_is_seq(a)) {
        char buf[256];
        snprintf(buf, sizeof buf, "can only concatenate %s (not \"%s\") to %s",
                 apy_kind_name(a), apy_kind_name(b), apy_kind_name(a));
        return apy_fail("TypeError", buf);
    }
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("+", a, b);
    if (O(a)->kind == APY_FLOAT_K || O(b)->kind == APY_FLOAT_K)
        return apy_from_float(apy_num_f(a) + apy_num_f(b));
    /* THE INT64 PATH IS TRIED FIRST AND ONLY PROMOTES ON OVERFLOW, which is
       what keeps ordinary arithmetic at one machine instruction and no
       allocation. Promotion is not so much a fallback for a rare case as the
       reason the common one may stay narrow: it can be exactly as wide as the
       hardware, because being WRONG is no longer one of its options. */
    if (!apy_either_big(a, b)) {
        int64_t r;
        if (apy_add_i64(O(a)->v.i, O(b)->v.i, &r)) return apy_from_int(r);
    }
    {
        apy_obj *x = apy_as_big(a), *y = apy_as_big(b);
        return apy_big_addsub(x, y, x->v.big.neg, y->v.big.neg);
    }
}

APY_API apy_value apy_sub(apy_value a, apy_value b) {
    a = apy_view_as_set(a); b = apy_view_as_set(b);
    if (apy_either_complex(a, b)) return apy_complex_binop("-", a, b);
    /* `-` between two sets is difference. Checked before the numeric test
       because a set is not a number and would otherwise report an unsupported
       operand pair for an operation Python defines. */
    if ((apy_is_set(a) || apy_is_set(b)) && !apy_either_inst(a, b))
        return apy_set_algebra("-", a, b, APY_DIFF, 1);
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("-", a, b);
    if (O(a)->kind == APY_FLOAT_K || O(b)->kind == APY_FLOAT_K)
        return apy_from_float(apy_num_f(a) - apy_num_f(b));
    if (!apy_either_big(a, b)) {
        int64_t r;
        if (apy_sub_i64(O(a)->v.i, O(b)->v.i, &r)) return apy_from_int(r);
    }
    {
        apy_obj *x = apy_as_big(a), *y = apy_as_big(b);
        /* Subtraction is addition with the right operand's sign flipped --
           flipped in the ARGUMENT and not in `y`, which some other name may
           still be holding. */
        return apy_big_addsub(x, y, x->v.big.neg, !y->v.big.neg);
    }
}

static apy_value apy_str_repeat(apy_value s, int64_t k) {
    int64_t n, i;
    char *buf;
    if (k < 0) k = 0;
    n = O(s)->v.s.n * k;
    buf = (char *)malloc((size_t)n + 1);
    for (i = 0; i < k; i++) memcpy(buf + i * O(s)->v.s.n, O(s)->v.s.p, (size_t)O(s)->v.s.n);
    buf[n] = '\0';
    return apy_str_take(buf, n);
}

static apy_value apy_seq_repeat(apy_value seq, int64_t k) {
    apy_value out = apy_seq_new(O(seq)->kind, O(seq)->v.q.n * (k > 0 ? k : 1) + 1);
    int64_t r, i;
    for (r = 0; r < k; r++)
        for (i = 0; i < O(seq)->v.q.n; i++) apy_seq_push(out, O(seq)->v.q.items[i]);
    return out;
}

/* `a @ b`. NO BUILT-IN KIND IMPLEMENTS IT -- there are no matrices here --
   so this is the dunder dispatch and nothing else. PEP 465 added the operator
   for exactly that: a spelling for libraries, with no meaning of its own. */
APY_API apy_value apy_matmul(apy_value a, apy_value b) {
    return apy_binop_fallback("@", a, b);
}

APY_API apy_value apy_mul(apy_value a, apy_value b) {
    if (apy_either_complex(a, b)) return apy_complex_binop("*", a, b);
    /* A repeat COUNT has to fit a machine word -- `[1] * (2 ** 100)` is a
       list longer than memory, and CPython says so rather than trying. */
    {
        int64_t k;
        if (apy_is_seq(a) && apy_is_int_like(b))
            return apy_index_arg(b, &k, APY_IDX_REPEAT) ? apy_seq_repeat(a, k) : 0;
        if (apy_is_seq(b) && apy_is_int_like(a))
            return apy_index_arg(a, &k, APY_IDX_REPEAT) ? apy_seq_repeat(b, k) : 0;
        if (O(a)->kind == APY_BYTES_K && apy_is_int_like(b))
            return apy_bytes_repeat(a, b);
        if (O(b)->kind == APY_BYTES_K && apy_is_int_like(a))
            return apy_bytes_repeat(b, a);
        if (O(a)->kind == APY_STR_K && apy_is_int_like(b))
            return apy_index_arg(b, &k, APY_IDX_REPEAT) ? apy_str_repeat(a, k) : 0;
        if (O(b)->kind == APY_STR_K && apy_is_int_like(a))
            return apy_index_arg(a, &k, APY_IDX_REPEAT) ? apy_str_repeat(b, k) : 0;
    }
    /* A SEQUENCE with a non-int on the other side gets its OWN message,
       because CPython's is about sequences rather than about operands --
       `'ab' * 1.5` and `[1] * [2]` both say "can't multiply sequence by
       non-int of type '...'". The generic binop text would be a different
       wrong answer, not a smaller one. A set is NOT a sequence and does get
       the generic one: `{1} * 2` is an unsupported operand pair. */
    if (O(a)->kind == APY_STR_K || O(b)->kind == APY_STR_K
        || apy_is_seq(a) || apy_is_seq(b)) {
        int a_is_seq = O(a)->kind == APY_STR_K || apy_is_seq(a);
        apy_value other = a_is_seq ? b : a;
        return apy_fail2("TypeError",
                         "can't multiply sequence by non-int of type '%s'%s",
                         apy_kind_name(other), "");
    }
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("*", a, b);
    if (O(a)->kind == APY_FLOAT_K || O(b)->kind == APY_FLOAT_K)
        return apy_from_float(apy_num_f(a) * apy_num_f(b));
    if (!apy_either_big(a, b)) {
        int64_t r;
        if (apy_mul_i64(O(a)->v.i, O(b)->v.i, &r)) return apy_from_int(r);
    }
    return apy_big_mul(apy_as_big(a), apy_as_big(b));
}

/* Number of significant bits in a non-zero u64. A loop rather than
   `__builtin_clzll`, because this file is compiled by whatever toolchain the
   target uses and a builtin is not portable; it runs at most 64 times and
   only on the slow `/` path. */
static int apy_bits(uint64_t x) {
    int n = 0;
    while (x) { n++; x >>= 1; }
    return n;
}

APY_API int64_t apy_abs64_of(int64_t v) {
    return (int64_t)(v < 0 ? -(uint64_t)v : (uint64_t)v);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static uint64_t apy_abs64(int64_t v) {
    return (uint64_t)apy_abs64_of(v);
}

/* `int / int`, correctly rounded -- the quotient of the two exact integers,
   rounded once to nearest-even, as CPython's `long_true_divide` does.

   The obvious `(double)a / (double)b` rounds THREE times: once converting
   each operand and once dividing. For operands under 2**53 the conversions
   are exact and it agrees; past that it does not, and the disagreement is a
   last-digit difference in a printed float, which is exactly the kind of
   defect this compiler is measured on.

   Long division in plain 64-bit arithmetic, no 128-bit type and no `long
   double`: both exist on the current toolchain and neither is portable, and
   `long double` would round twice anyway (64-bit significand, then 53). The
   loop grows the quotient to 54+ bits one bit at a time -- `rem` stays below
   `ub` so `rem << 1` cannot overflow -- then rounds the surplus off with a
   sticky bit carrying whether anything nonzero was dropped. */
static double apy_int_quot(int64_t ai, int64_t bi) {
    uint64_t ua = apy_abs64(ai), ub = apy_abs64(bi), q, rem;
    int neg = (ai < 0) != (bi < 0), e = 0, sticky = 0, drop;
    if (ua == 0) return neg ? -0.0 : 0.0;
    q = ua / ub;
    rem = ua % ub;
    while (q < ((uint64_t)1 << 54) && rem != 0) {
        q <<= 1;
        rem <<= 1;
        if (rem >= ub) { rem -= ub; q |= 1; }
        e--;
    }
    sticky = rem != 0;
    drop = apy_bits(q) - 53;
    if (drop > 0) {
        uint64_t mask = ((uint64_t)1 << drop) - 1;
        uint64_t low = q & mask, half = (uint64_t)1 << (drop - 1);
        q >>= drop;
        e += drop;
        /* Nearest, ties to even -- and a tie is only a tie when nothing was
           dropped below it, which is what `sticky` records. */
        if (low > half || (low == half && (sticky || (q & 1)))) q++;
    }
    return neg ? -ldexp((double)q, e) : ldexp((double)q, e);
}

APY_API apy_value apy_truediv(apy_value a, apy_value b) {
    if (apy_either_complex(a, b)) return apy_complex_binop("/", a, b);
    double x, y;
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("/", a, b);
    /* `O(b)->v.i` on a big is a pointer read as an integer, so the zero test
       is guarded -- and it needs no big case, because a big is never zero:
       `apy_big_done` demotes that value to the int 0. Every `v.i` read in the
       arithmetic below is gated the same way. */
    if (apy_is_int_like(b) && !apy_is_big(b) && O(b)->v.i == 0)
        return apy_fail("ZeroDivisionError", "division by zero");
    if (apy_is_int_like(a) && apy_is_int_like(b)) {
        if (!apy_either_big(a, b))
            return apy_from_float(apy_int_quot(O(a)->v.i, O(b)->v.i));
        {
            apy_obj *x = apy_as_big(a), *y = apy_as_big(b);
            double d = apy_big_quot(x, y);
            return apy_from_float(x->v.big.neg != y->v.big.neg ? -d : d);
        }
    }
    x = apy_num_f(a); y = apy_num_f(b);
    if (y == 0.0) return apy_fail("ZeroDivisionError", "division by zero");
    return apy_from_float(x / y);
}

/* `//` and `%` FLOOR toward negative infinity and take the divisor's sign.
   C truncates toward zero and takes the dividend's, so `-7 // 2` is -4 in
   Python and -3 in C, and `-7 % 3` is 2 and -1. Both are corrected, for ints
   and for floats, because Python applies the same rule to both.

   EVERY division by zero says "division by zero", for ints and floats and for
   `/`, `//` and `%` alike. This is not the message CPython used to give --
   3.11 said "integer division or modulo by zero" and "float modulo" -- and
   the older text is what a search of the internet still finds. 3.14 unified
   them, and 3.14 is the oracle the suite is generated from, so the older
   wording would be a wrong answer measured against it. */
static const char *APY_DIV0 = "division by zero";

/* The float `//` and `%` below are CPython's `float_divmod`, transcribed,
   NOT the obvious `floor(x / y)` and `fmod(x, y)`. They differ in two ways
   that a spot check does not reach:

     * `floor(x / y)` divides FIRST, so it rounds the quotient and then
       floors the rounded value. CPython subtracts the remainder before
       dividing, which makes the division exact, and only then floors. The
       two disagree when x/y lands just under an integer. `inf // 1.0` is
       the visible case: `floor(inf/1.0)` is `inf`, and CPython says `nan`.

     * for an exact multiple, `fmod` gives a zero with the sign of the
       DIVIDEND and CPython gives one with the sign of the DIVISOR. So
       `7.0 % -7.0` is `-0.0` in Python and `0.0` from plain fmod -- and
       repr shows the difference. */
APY_API apy_value apy_floordiv(apy_value a, apy_value b) {
    if (apy_either_complex(a, b))
        return apy_fail2("TypeError",
                         "can't take floor or mod of complex number%s%s",
                         "", "");
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("//", a, b);
    if (O(a)->kind == APY_FLOAT_K || O(b)->kind == APY_FLOAT_K) {
        double x = apy_num_f(a), y = apy_num_f(b), mod, div, fl;
        if (y == 0.0) return apy_fail("ZeroDivisionError", APY_DIV0);
        mod = fmod(x, y);
        div = (x - mod) / y;
        if (mod != 0.0) {
            if ((y < 0) != (mod < 0)) div -= 1.0;
        }
        if (div != 0.0) {
            fl = floor(div);
            /* `div` is an exact integer in the common case; the half-ulp
               nudge is CPython's own guard for the case where the subtract
               above still left a fraction. */
            if (div - fl > 0.5) fl += 1.0;
        } else {
            fl = copysign(0.0, x / y);
        }
        return apy_from_float(fl);
    }
    if (!apy_is_big(b) && O(b)->v.i == 0)
        return apy_fail("ZeroDivisionError", APY_DIV0);
    if (apy_either_big(a, b)) {
        apy_value q, r;
        apy_big_floordivmod(apy_as_big(a), apy_as_big(b), &q, &r);
        return q;
    }
    {
        int64_t q, r;
        /* INT64_MIN / -1 is the one signed division C leaves undefined, and
           it is the one case where the quotient does not fit. It is a big.

           CHECKED BEFORE DIVIDING, and it was not. `q` and `r` were computed
           on the line above this comment, so the division that the check
           exists to avoid had already happened -- and on x86 it does not
           produce a wrong number, it RAISES: `idiv` faults when the quotient
           will not fit the destination, so `-9223372036854775808 // -1` took
           the process down instead of answering 2**63. The guard was written,
           correct, and one line too late. */
        if (O(a)->v.i == (-9223372036854775807LL - 1) && O(b)->v.i == -1) {
            apy_value qq, rr;
            apy_big_floordivmod(apy_as_big(a), apy_as_big(b), &qq, &rr);
            return qq;
        }
        q = O(a)->v.i / O(b)->v.i;
        r = O(a)->v.i % O(b)->v.i;
        if (r != 0 && ((r < 0) != (O(b)->v.i < 0))) q--;
        return apy_from_int(q);
    }
}

APY_API apy_value apy_mod(apy_value a, apy_value b) {
    if (apy_either_complex(a, b))
        return apy_fail2("TypeError",
                         "can't take floor or mod of complex number%s%s",
                         "", "");
    /* `%` on a str or on bytes is PRINTF-STYLE FORMATTING in Python, not
       arithmetic. `b"%d" % 3` answers bytes and `"%d" % 3` answers a str,
       which `apy_str_percent` decides from the left operand's kind. */
    if (O(a)->kind == APY_STR_K || O(a)->kind == APY_BYTES_K)
        return apy_str_percent(a, b);
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("%", a, b);
    if (O(a)->kind == APY_FLOAT_K || O(b)->kind == APY_FLOAT_K) {
        double x = apy_num_f(a), y = apy_num_f(b), r;
        if (y == 0.0) return apy_fail("ZeroDivisionError", APY_DIV0);
        r = fmod(x, y);
        if (r != 0.0) {
            if ((y < 0) != (r < 0)) r += y;
        } else {
            /* The sign of the DIVISOR, not fmod's sign of the dividend. */
            r = copysign(0.0, y);
        }
        return apy_from_float(r);
    }
    if (!apy_is_big(b) && O(b)->v.i == 0)
        return apy_fail("ZeroDivisionError", APY_DIV0);
    if (apy_either_big(a, b)) {
        apy_value q, r;
        apy_big_floordivmod(apy_as_big(a), apy_as_big(b), &q, &r);
        return r;
    }
    {
        int64_t r;
        /* `INT64_MIN % -1` is 0, and computing it with C's `%` is undefined
           for the same reason the division is. */
        if (O(b)->v.i == -1) return apy_from_int(0);
        r = O(a)->v.i % O(b)->v.i;
        if (r != 0 && ((r < 0) != (O(b)->v.i < 0))) r += O(b)->v.i;
        return apy_from_int(r);
    }
}

APY_API apy_value apy_pow(apy_value a, apy_value b) {
    if (!apy_is_num(a) || !apy_is_num(b)) return apy_binop_fallback("** or pow()", a, b);
    if (apy_is_int_like(a) && apy_is_int_like(b) && !apy_is_big(b)
        && O(b)->v.i >= 0) {
        /* Square-and-multiply THROUGH `apy_mul`, which promotes on overflow.
           The loop used to multiply int64s directly and wrap, which is what
           made `2 ** 64` come out as 0. Reusing the operator rather than
           writing a second exact multiply here means there is one place that
           can be wrong about products, not two. */
        apy_value r = apy_from_int(1), base = a;
        int64_t n = O(b)->v.i;
        while (n) {
            if (n & 1) { r = apy_mul(r, base); if (!r) return 0; }
            n >>= 1;
            if (n) { base = apy_mul(base, base); if (!base) return 0; }
        }
        return r;
    }
    /* A BIG, NON-NEGATIVE EXPONENT cannot be answered. `2 ** (2 ** 64)` has
       more digits than the machine has bytes; CPython would grind until it
       ran out of memory. Reporting is the honest form of the same answer. */
    if (apy_is_int_like(a) && apy_is_big(b) && !O(b)->v.big.neg)
        return apy_big_too_large();
    {
        double x = apy_num_f(a), y = apy_num_f(b);
        /* `0 ** -1` is an ERROR, not an infinity: CPython raises
           ZeroDivisionError, and it says "zero to a negative power" whether
           the zero was an int or a float. C's `pow` would hand back inf. */
        if (x == 0.0 && y < 0.0)
            return apy_fail("ZeroDivisionError", "zero to a negative power");
        /* An INTEGRAL exponent goes through `py_pow_int` rather than libm's
           `pow`, which is a ulp off on this platform often enough to change
           the last printed digit -- see POW_INT_C in objects/support.py for the
           measurement.

           "Integral" means the VALUE, not the type: `x ** 2.0` computes the
           same number as `x ** 2` and CPython prints the same digits for
           both, so testing `apy_is_int_like(b)` alone left every float-typed
           whole exponent on the libm path. That was the single largest
           mismatch bucket against CPython -- over a thousand cases -- and it
           looked like a float-repr bug rather than a pow bug.

           The bound is 2**63: past it a double has no fractional part to
           lose, but the exponent no longer fits the loop counter, and libm's
           answer is inf or 0 either way. */
        if (y == floor(y) && y >= -9223372036854775808.0
                          && y < 9223372036854775808.0) {
            /* Negative exponents go to the same place: `py_pow_int` takes the
               reciprocal inside its double-double, which is one rounding
               instead of the two that `1.0 / py_pow_int(x, -n)` costs. That
               difference was 348 mismatched cases out of 4000. */
            return apy_from_float(py_pow_int(x, (long long)y));
        }
        /* A negative base with a fractional exponent is a COMPLEX number in
           Python -- `(-8) ** 0.5` is `(1.7e-16+2.83j)`. There is no complex
           kind here and inventing one is not v1, so this reports rather than
           returning the nan that libm would. A stated failure is recoverable;
           a nan that came from nowhere is not. */
        if (x < 0.0 && y != floor(y))
            return apy_fail("ValueError",
                            "negative number cannot be raised to a fractional "
                            "power (no complex support)");
        return apy_from_float(pow(x, y));
    }
}

APY_API apy_value apy_neg(apy_value a) {
    if (O(a)->kind == APY_INST_K) {
        apy_value r = apy_unary_dunder(a, "__neg__");
        if (r || apy_error_occurred()) return r;
    }
    /* Both parts, so that `-(1+2j)` is `(-1-2j)`. `apy_is_num` says no to a
       complex -- deliberately, since that predicate gates the ORDERED numeric
       paths -- so without this a negation reported "bad operand type". */
    if (apy_is_complex(a))
        return apy_from_complex(-O(a)->v.z.re, -O(a)->v.z.im);
    if (!apy_is_num(a))
        return apy_fail2("TypeError", "bad operand type for unary -: '%s'%s",
                         apy_kind_name(a), "");
    if (O(a)->kind == APY_FLOAT_K) return apy_from_float(-O(a)->v.f);
    if (apy_is_big(a)) {
        apy_obj *r = apy_big_alloc(O(a)->v.big.n);
        int64_t i;
        for (i = 0; i < O(a)->v.big.n; i++) r->v.big.limb[i] = O(a)->v.big.limb[i];
        r->v.big.neg = !O(a)->v.big.neg;
        return apy_big_done(r);
    }
    /* `-INT64_MIN` does not fit an int64 at all, so it PROMOTES; every other
       value negates in place. Negating through unsigned because a signed
       negation that overflows is undefined and gcc may assume it cannot. */
    if (O(a)->v.i == (-9223372036854775807LL - 1)) {
        apy_obj *r = apy_big_of_i64(O(a)->v.i);
        r->v.big.neg = 0;
        return apy_big_done(r);
    }
    return apy_from_int((int64_t)(-(uint64_t)O(a)->v.i));
}

APY_API apy_value apy_pos(apy_value a) {
    if (apy_is_complex(a)) return a;
    if (O(a)->kind == APY_INST_K) {
        apy_value r = apy_unary_dunder(a, "__pos__");
        if (r || apy_error_occurred()) return r;
    }
    if (!apy_is_num(a))
        return apy_fail2("TypeError", "bad operand type for unary +: '%s'%s",
                         apy_kind_name(a), "");
    if (O(a)->kind == APY_FLOAT_K) return apy_from_float(O(a)->v.f);
    if (apy_is_big(a)) return a;      /* immutable, so itself will do */
    return apy_from_int(O(a)->v.i);
}

APY_API apy_value apy_invert(apy_value a) {
    if (O(a)->kind == APY_INST_K) {
        apy_value r = apy_unary_dunder(a, "__invert__");
        if (r || apy_error_occurred()) return r;
    }
    if (!apy_is_int_like(a))
        return apy_fail2("TypeError", "bad operand type for unary ~: '%s'%s",
                         apy_kind_name(a), "");
    /* `~x` IS `-x - 1` -- that identity is what two's complement means, and
       expressing it that way rather than as a bit flip is what makes it work
       for a big, where there is no fixed width to flip within. */
    if (apy_is_big(a)) return apy_neg(apy_add(a, apy_from_int(1)));
    return apy_from_int(~O(a)->v.i);
}

/* `|`, `&` and `^` between two sets are union, intersection and symmetric
   difference. The shifts have no set meaning, so `which` past 2 never gets
   here. Both operands must be sets: `{1} | [2]` is a TypeError, which is what
   `strict` in `apy_set_algebra` is for. */
static const int APY_SET_OF_BITOP[3] = { APY_INTER, APY_UNION, APY_SYMDIFF };

static apy_value apy_intop(const char *name, apy_value a, apy_value b, int which) {
    if (which < 3 && (apy_is_set(a) || apy_is_set(b)) && !apy_either_inst(a, b))
        return apy_set_algebra(name, a, b, APY_SET_OF_BITOP[which], 1);
    if (!apy_is_int_like(a) || !apy_is_int_like(b))
        return apy_binop_fallback(name, a, b);
    /* `&`, `|` and `^` of two BOOLS give a BOOL, not an int: `True & True` is
       `True` and prints as such. The shifts do not -- `True << 1` is the int
       2 -- because only the three logical operators have a bool overload in
       CPython. Getting this wrong prints 1 where a program printed True, and
       nothing about the arithmetic would look wrong. */
    {
        int both_bool = O(a)->kind == APY_BOOL_K && O(b)->kind == APY_BOOL_K;
        switch (which) {
        case 0: if (both_bool) return apy_from_bool(O(a)->v.i & O(b)->v.i); break;
        case 1: if (both_bool) return apy_from_bool(O(a)->v.i | O(b)->v.i); break;
        case 2: if (both_bool) return apy_from_bool(O(a)->v.i ^ O(b)->v.i); break;
        default: break;
        }
    }
    /* THE BIG PATHS, and they come before anything reads `v.i` -- on a big
       that field is a pointer read as an integer. `&`, `|` and `^` convert to
       infinite two's complement and back, because that is the only form in
       which Python's answer for a negative operand is even expressible. The
       shifts stay in sign-magnitude: `<<` is an exact multiply by a power of
       two and `>>` an exact floor-divide by one. */
    if (which < 3) {
        if (apy_either_big(a, b))
            return apy_big_bitop(apy_as_big(a), apy_as_big(b), which);
    } else if (apy_is_big(b)) {
        /* A shift COUNT too large for an int64. The sign is still checked
           first, so `x << -(2**70)` is a ValueError like any other negative
           count; a positive one asks for a number with more bits than the
           machine has addresses, EXCEPT where the answer saturates -- `0 <<
           huge` is 0, and `x >> huge` is 0 or -1 by the sign alone. */
        if (O(b)->v.big.neg)
            return apy_fail("ValueError", "negative shift count");
        if (which == 3)
            return !apy_is_big(a) && O(a)->v.i == 0
                 ? apy_from_int(0) : apy_big_too_large();
        if (apy_is_big(a)) return apy_from_int(O(a)->v.big.neg ? -1 : 0);
        return apy_from_int(O(a)->v.i < 0 ? -1 : 0);
    } else if (apy_is_big(a) || which == 3) {
        int64_t count = O(b)->v.i;
        if (count < 0) return apy_fail("ValueError", "negative shift count");
        if (apy_is_big(a)) return apy_big_shift(O(a), count, which == 3);
        /* `1 << 64` is a 65-bit integer. This used to answer 0, and the
           comment here called that "at least the wrongness the 64-bit int
           limit already implies". The limit is gone, so the answer is now the
           number. A shift that stays inside int64 is verified by shifting
           back -- cheaper than counting bits and exact. */
        {
            int64_t r;
            if (count >= 63) return apy_big_shift(apy_as_big(a), count, 1);
            r = (int64_t)((uint64_t)O(a)->v.i << count);
            if ((r >> count) == O(a)->v.i) return apy_from_int(r);
            return apy_big_shift(apy_as_big(a), count, 1);
        }
    }

    switch (which) {
    case 0: return apy_from_int(O(a)->v.i & O(b)->v.i);
    case 1: return apy_from_int(O(a)->v.i | O(b)->v.i);
    case 2: return apy_from_int(O(a)->v.i ^ O(b)->v.i);
    default:
        if (O(b)->v.i < 0) return apy_fail("ValueError", "negative shift count");
        /* An arithmetic right shift saturates at the sign bit, which IS
           Python's answer for an over-long shift: `-1 >> 999` is -1 and
           `5 >> 999` is 0. A shift of 64 or more is UNDEFINED in C rather
           than zero -- x86 shifts by `count & 63` -- so it never reaches the
           shift itself. */
        if (O(b)->v.i >= 64) return apy_from_int(O(a)->v.i < 0 ? -1 : 0);
        return apy_from_int(O(a)->v.i >> O(b)->v.i);
    }
}

APY_API apy_value apy_bitand(apy_value a, apy_value b) {
    a = apy_view_as_set(a); b = apy_view_as_set(b); return apy_intop("&", a, b, 0); }
/* A VIEW BEHAVES AS A SET under `&`, `|`, `-` and `^`. `d.keys() & {'a'}` is
   ordinary Python, and the view is not itself a set -- it is a window on a
   dict -- so the operators convert it at the boundary rather than every
   consumer having to know. Values are NOT set-like in CPython (they may
   repeat), but converting one here only affects programs that already asked
   for something CPython refuses. */
static apy_value apy_view_as_set(apy_value v) {
    return O(v)->kind == APY_VIEW_K ? apy_to_set(apy_view_items(v)) : v;
}

/* Is this something `|` should read as a TYPE? A builtin type used as a
   value, a user class, or a union already built from either. */
static int apy_is_type_like(apy_value v) {
    if (O(v)->kind == APY_FUNC_K && O(v)->v.fn.is_type) return 1;
    if (O(v)->kind == APY_TYPE_K) return 1;
    if (O(v)->kind == APY_NONE_K) return 1;      /* `int | None` */
    return O(v)->kind == APY_ALIAS_K
        && O(O(v)->v.ga.origin)->kind == APY_INST_K;
}

/* Append `v`'s arms to `into`. A union contributes its own arms rather than
   itself, so unions flatten instead of nesting. */
static void apy_union_arms(apy_value into, apy_value v) {
    if (O(v)->kind == APY_ALIAS_K
            && O(O(v)->v.ga.origin)->kind == APY_INST_K) {
        int64_t i;
        for (i = 0; i < O(O(v)->v.ga.args)->v.q.n; i++)
            apy_seq_push(into, O(O(v)->v.ga.args)->v.q.items[i]);
        return;
    }
    /* `None` IN A UNION IS `NoneType`. `int | None` is written with the
       VALUE and holds the TYPE -- `get_args` answers `<class 'NoneType'>` in
       CPython, and pushing the singleton answered `None`, which is a
       different object with a different repr. */
    if (O(v)->kind == APY_NONE_K) { apy_seq_push(into, apy_kind_class(v)); return; }
    apy_seq_push(into, v);
}

APY_API apy_value apy_bitor(apy_value a, apy_value b) {
    a = apy_view_as_set(a); b = apy_view_as_set(b);
    /* PEP 604: `int | str` IS A TYPE, not an arithmetic operation. Either
       side may already be a union, and the arms flatten -- `int | str | None`
       is one three-armed union, which is what makes `isinstance` over it a
       single walk. */
    if (apy_is_type_like(a) && apy_is_type_like(b)) {
        apy_value args = apy_tuple_new(4);
        apy_union_arms(args, a);
        apy_union_arms(args, b);
        return apy_alias_new(apy_typing_form(apy_lit("Union")), args);
    }
    /* `d1 | d2` MERGES, with the right-hand side winning -- PEP 584. */
    if (O(a)->kind == APY_DICT_K && O(b)->kind == APY_DICT_K) {
        apy_value out = apy_copy(a);
        if (!out || !apy_update(out, b)) return 0;
        return out;
    }
    return apy_intop("|", a, b, 1);
}
APY_API apy_value apy_bitxor(apy_value a, apy_value b) {
    a = apy_view_as_set(a); b = apy_view_as_set(b); return apy_intop("^", a, b, 2); }
APY_API apy_value apy_lshift(apy_value a, apy_value b) { return apy_intop("<<", a, b, 3); }
APY_API apy_value apy_rshift(apy_value a, apy_value b) { return apy_intop(">>", a, b, 4); }

/* --- comparison -------------------------------------------------------- */
/* Equality is TOTAL: every pair of objects can be compared, and a pair with
   nothing in common is simply unequal. Ordering is not -- `7 < 'ab'` is a
   TypeError, and answering False there would be a wrong answer rather than a
   missing feature. */
APY_API int64_t apy_str_cmp_of(apy_value a, apy_value b) {
    int64_t n = O(a)->v.s.n < O(b)->v.s.n ? O(a)->v.s.n : O(b)->v.s.n;
    int c = n ? memcmp(O(a)->v.s.p, O(b)->v.s.p, (size_t)n) : 0;
    if (c) return c < 0 ? -1 : 1;
    if (O(a)->v.s.n == O(b)->v.s.n) return 0;
    return O(a)->v.s.n < O(b)->v.s.n ? -1 : 1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static int apy_str_cmp(apy_value a, apy_value b) {
    return (int)apy_str_cmp_of(a, b);
}

/* An int against a float, EXACTLY -- neither converted to the other's type.
   Returns -1/0/1, or APY_UNORD when the float is a nan.

   `(double)i == f` is the obvious version and it is wrong past 2**53, where
   the conversion rounds: `2**53 + 1 == float(2**53)` is False in Python and
   True through a double conversion. Comparing `i` against `floor(f)` as an
   integer keeps both sides exact, because a finite double whose magnitude is
   below 2**63 has an integral part that fits an int64 with no rounding at
   all. Outside that range the double is larger than any int64 and the answer
   follows from the sign alone. */
#define APY_UNORD 3
APY_API int64_t apy_cmp_int_double_of(int64_t i, double f) {
    double fl;
    int64_t t;
    if (isnan(f)) return APY_UNORD;
    /* 2**63 exactly: no int64 reaches it, and -2**63 is INT64_MIN itself, so
       the bound is inclusive on one side and not the other. */
    if (f >= 9223372036854775808.0) return -1;
    if (f < -9223372036854775808.0) return 1;
    fl = floor(f);
    t = (int64_t)fl;
    if (i != t) return i < t ? -1 : 1;
    /* Same integral part: whatever fraction the float has left decides. */
    return f > fl ? -1 : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_cmp_int_double(int64_t i, double f) {
    return (int)apy_cmp_int_double_of(i, f);
}

/* An arbitrary-precision integer against a double, EXACTLY. The float is not
   converted unless it has to be: a big is by construction outside int64
   range, so any float of smaller magnitude loses on magnitude alone and only
   the big's sign matters. Past 2**63 a finite double IS an integer -- every
   double that large has no fractional part left -- so converting it there is
   lossless, where `(double)big` would round and answer wrongly. */
static int apy_cmp_big_double(apy_obj *g, double f) {
    if (isnan(f)) return APY_UNORD;
    if (isinf(f)) return f > 0 ? -1 : 1;
    if (f > -9223372036854775808.0 && f < 9223372036854775808.0)
        return g->v.big.neg ? -1 : 1;
    {
        /* `apy_big_from_double` would demote a value that fits an int64, and
           |f| >= 2**63 cannot, so this is always a big. */
        apy_value other = apy_big_from_double(f);
        return apy_big_cmp(g, O(other));
    }
}

APY_API int64_t apy_num_order_of(apy_value a, apy_value b) {
    int fa = O(a)->kind == APY_FLOAT_K, fb = O(b)->kind == APY_FLOAT_K;
    if (apy_is_big(a) || apy_is_big(b)) {
        if (apy_is_big(a) && apy_is_big(b)) return apy_big_cmp(O(a), O(b));
        if (fa) { int c = apy_cmp_big_double(O(b), O(a)->v.f);
                  return c == APY_UNORD ? APY_UNORD : -c; }
        if (fb) return apy_cmp_big_double(O(a), O(b)->v.f);
        /* One big and one int64. The big is outside int64 range by
           construction, so its SIGN settles it and no digits are compared --
           which is also why the pair is never equal, and why `apy_eq_raw`
           needs no case for it at all. */
        if (apy_is_big(a)) return O(a)->v.big.neg ? -1 : 1;
        return O(b)->v.big.neg ? 1 : -1;
    }
    if (fa && fb) {
        double x = O(a)->v.f, y = O(b)->v.f;
        if (isnan(x) || isnan(y)) return APY_UNORD;
        if (x < y) return -1;
        return x > y ? 1 : 0;
    }
    if (fa) {
        int c = apy_cmp_int_double(O(b)->v.i, O(a)->v.f);
        return c == APY_UNORD ? APY_UNORD : -c;
    }
    if (fb) return apy_cmp_int_double(O(a)->v.i, O(b)->v.f);
    if (O(a)->v.i < O(b)->v.i) return -1;
    return O(a)->v.i > O(b)->v.i ? 1 : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_num_order(apy_value a, apy_value b) {
    return (int)apy_num_order_of(a, b);
}

static int apy_eq_raw(apy_value a, apy_value b);

/* Element-by-element, and only between the SAME kind: a list never equals a
   tuple in Python even when their contents match. */
/* IDENTITY FIRST, then equality -- which is what every CONTAINER does to
   its elements, and why `[nan] == [nan]` is True while `nan == nan` is False.
   A container asks "is this the same object?" before it asks the object, so
   a value that is not equal to itself still compares equal to itself inside
   one. `x in [x]` and `d == d` rest on the same rule. */
static int apy_eq_element(apy_value a, apy_value b) {
    return a == b || apy_eq_raw(a, b);
}

static int apy_seq_eq(apy_value a, apy_value b) {
    int64_t i;
    if (O(a)->kind != O(b)->kind) return 0;
    if (O(a)->v.q.n != O(b)->v.q.n) return 0;
    for (i = 0; i < O(a)->v.q.n; i++)
        if (!apy_eq_element(O(a)->v.q.items[i], O(b)->v.q.items[i])) return 0;
    return 1;
}

static int apy_dict_eq(apy_value a, apy_value b) {
    int64_t i, at;
    if (O(a)->v.d.n != O(b)->v.d.n) return 0;
    /* Order-free: `{1: 2, 3: 4} == {3: 4, 1: 2}` is True even though
       iteration order differs. Equality is about the pairs. */
    for (i = 0; i < O(a)->v.d.n; i++) {
        at = apy_dict_find(b, O(a)->v.d.keys[i]);
        if (at < 0) return 0;
        if (!apy_eq_element(O(a)->v.d.vals[i], O(b)->v.d.vals[at])) return 0;
    }
    return 1;
}

/* THE EXPORTED HALF, which `runtime/cursor.py` SPLITS: the IR answers two
   integers, two strings and the other same-kind pairs a program spends its
   time in, and everything below -- every mixed pair and every container --
   comes back here. */
APY_API int64_t apy_eq_raw_of(apy_value a, apy_value b) {
    return apy_eq_raw(a, b);
}
static int apy_eq_raw(apy_value a, apy_value b) {
    /* TWO RANGES ARE EQUAL WHEN THEY YIELD THE SAME ELEMENTS, which is not
       the same as holding the same three numbers: `range(0, 3, 1)` and
       `range(3)` are equal, and every empty range equals every other. */
    if (O(a)->kind == APY_RANGE_K && O(b)->kind == APY_RANGE_K) {
        int64_t na = apy_range_len(a), nb = apy_range_len(b);
        if (na != nb) return 0;
        if (na == 0) return 1;
        if (O(a)->v.rg.start != O(b)->v.rg.start) return 0;
        return na == 1 || O(a)->v.rg.step == O(b)->v.rg.step;
    }
    /* An instance dispatches to `__eq__`, and it is hooked HERE rather than in
       `apy_eq` so that a container holding instances compares element by
       element through the user's method: `[P(1)] == [P(1)]` has to ask P.
       The reflected name is `__eq__` itself -- Python tries `b.__eq__(a)`
       when the left operand has none, not a separate `__req__`.

       Falling through to IDENTITY when no class in either chain defines one
       is CPython's default and it is the whole reason this branch cannot just
       continue into the numeric path below: two instances there would compare
       whatever `v.i` happens to alias. */
    if (apy_either_inst(a, b)) {
        apy_value r = apy_binary_dunder(a, b, "__eq__", "__eq__");
        if (r) return apy_truth(r) != 0;
        if (apy_error_occurred()) return 0;
        /* A CLASS EXTENDING A BUILTIN COMPARES AS THE BUILTIN. Identity is
           the right default only for an object with no content; here it made
           two equal namedtuples distinct, and a set holding both kept both
           even though they hash alike. Hooked HERE rather than in `apy_eq`
           for the same reason the dunder is: a CONTAINER of them has to
           compare element by element the same way. */
        {
            apy_value ua = apy_as_builtin(a, "__eq__");
            apy_value ub = apy_as_builtin(b, "__eq__");
            if (ua != a || ub != b) return apy_eq_raw(ua, ub);
        }
        return a == b;
    }
    /* A MEMORYVIEW COMPARES BY CONTENT, and against bytes as well as against
       another view -- `memoryview(b"ab") == b"ab"` is True. */
    if (O(a)->kind == APY_MVIEW_K || O(b)->kind == APY_MVIEW_K) {
        apy_value x = O(a)->kind == APY_MVIEW_K ? apy_mview_bytes(a) : a;
        apy_value y = O(b)->kind == APY_MVIEW_K ? apy_mview_bytes(b) : b;
        if (O(x)->kind != APY_BYTES_K || O(y)->kind != APY_BYTES_K) return 0;
        return apy_str_cmp(x, y) == 0;
    }
    /* `d.keys()` and `d.items()` are SET-LIKE and compare as sets, against
       each other and against a real set. `d.values()` is not -- it defines no
       equality at all, so two of them are equal only when they are the same
       object, which is what the identity fallthrough below gives it. */
    if ((O(a)->kind == APY_VIEW_K && O(a)->v.vw.part != APY_PART_VALUES)
        || (O(b)->kind == APY_VIEW_K && O(b)->v.vw.part != APY_PART_VALUES)) {
        apy_value x = apy_view_as_set(a), y = apy_view_as_set(b);
        if (!apy_is_set(x) || !apy_is_set(y)) return 0;
        return O(x)->v.q.n == O(y)->v.q.n && apy_subset(x, y);
    }
    /* A set equals a FROZENSET with the same elements -- the two kinds are one
       equality class, unlike list and tuple, which are never equal to each
       other. Equal size plus one-way containment is enough because a set's own
       elements are already pairwise distinct. */
    if (apy_is_set(a) || apy_is_set(b))
        return apy_is_set(a) && apy_is_set(b)
            && O(a)->v.q.n == O(b)->v.q.n && apy_subset(a, b);
    if (O(a)->kind == APY_DICT_K || O(b)->kind == APY_DICT_K)
        return O(a)->kind == O(b)->kind && apy_dict_eq(a, b);
    if (apy_is_seq(a) || apy_is_seq(b))
        return apy_is_seq(a) && apy_is_seq(b) && apy_seq_eq(a, b);
    if (O(a)->kind == APY_COMPLEX_K || O(b)->kind == APY_COMPLEX_K) {
        double ar, ai, br, bi;
        /* A non-number is simply not equal -- `1j == 'a'` is False, never an
           error, because equality is total in Python. */
        if (!apy_as_complex(a, &ar, &ai) || !apy_as_complex(b, &br, &bi))
            return 0;
        return ar == br && ai == bi;
    }
    /* BYTES COMPARE BY CONTENT, and this branch is what makes them: without
       it a bytes value fell through to the numeric path, where `v.i` aliases
       the BUFFER POINTER -- so `b"ab" == b"ab"` was True only because the
       backend emits one static buffer for two identical literals, and
       `b"ab" == b"a" + b"b"` was False. The ordering path had the case from
       the start; equality did not, and the accident hid it.

       One branch covers bytearray too, and that is right rather than
       convenient: `b"a" == bytearray(b"a")` is True in CPython. */
    if (O(a)->kind == APY_BYTES_K || O(b)->kind == APY_BYTES_K)
        return O(a)->kind == O(b)->kind && apy_str_cmp(a, b) == 0;
    if (O(a)->kind == APY_STR_K || O(b)->kind == APY_STR_K)
        return O(a)->kind == O(b)->kind && apy_str_cmp(a, b) == 0;
    if (O(a)->kind == APY_NONE_K || O(b)->kind == APY_NONE_K)
        return O(a)->kind == O(b)->kind;
    /* `slice(1, 2) == slice(1, 3)` is False: a slice compares by its three
       BOUNDS, which is the only thing it holds. */
    if (O(a)->kind == APY_SLICE_K || O(b)->kind == APY_SLICE_K)
        return O(a)->kind == O(b)->kind
            && apy_eq_raw(O(a)->v.sl.start, O(b)->v.sl.start)
            && apy_eq_raw(O(a)->v.sl.stop, O(b)->v.sl.stop)
            && apy_eq_raw(O(a)->v.sl.step, O(b)->v.sl.step);
    /* EVERYTHING LEFT THAT IS NOT A NUMBER IS COMPARED BY IDENTITY, and this
       guard is the general form of a bug that reached four kinds. The numeric
       path below reads `v.i`, which for a non-number aliases whatever the
       union's first member is -- a POINTER for most kinds. Two slices sharing
       a `start`, two views onto one dict, two memoryviews over one buffer:
       each compared equal because the pointers matched, and none of them
       failed loudly. A kind added later gets identity here rather than an
       accident, and has to opt in to content equality above. */
    /* A BOUND METHOD IS A FRESH OBJECT PER ACCESS -- `c.m is c.m` is False --
       and two of them are EQUAL when they wrap the same function and the same
       receiver, which is what CPython compares. Only bound ones: two closures
       over the same `def` are distinct objects with distinct cells, and
       CPython calls those unequal.

       This was answered correctly by accident before the guard below existed:
       a function fell through to the numeric path, which read the union's
       first member -- the code pointer -- so two methods of one class matched
       whatever their receivers were. `c.m == d.m` was True. */
    if (O(a)->kind == APY_FUNC_K && O(b)->kind == APY_FUNC_K)
        return a == b || (O(a)->v.fn.bound && O(b)->v.fn.bound
                          && O(a)->v.fn.code == O(b)->v.fn.code
                          && O(a)->v.fn.bound == O(b)->v.fn.bound);
    if (!apy_is_num(a) || !apy_is_num(b)) return a == b;
    /* `nan == nan` is False, and so is `nan == 1.0`: APY_UNORD is not 0. */
    return apy_num_order(a, b) == 0;
}

/* A USER CLASS ANSWERS WITH WHATEVER ITS DUNDER RETURNS, not with a bool:
   `__eq__` returning a string is legal and its result IS the value of `==`.
   `apy_eq_raw` answers a C int, which every container and dict lookup wants,
   so the raw form is asked for first only here -- where the answer is a value
   the program will see rather than a decision this file is making. */
APY_API apy_value apy_eq(apy_value a, apy_value b) {
    if (apy_either_inst(a, b)) {
        apy_value r = apy_binary_dunder(a, b, "__eq__", "__eq__");
        if (r || apy_error_occurred()) return r;
        /* NEITHER SIDE WROTE `__eq__`, so a builtin-extending instance
           compares as the builtin it carries: `D({'a': 1}) == {'a': 1}` is
           True in CPython, and `apy_eq_raw` below compares an INSTANCE with
           a dict by identity and answers False. A wrong False is worse than
           an error here -- nothing marks it. */
        a = apy_as_builtin(a, "__eq__");
        b = apy_as_builtin(b, "__eq__");
    }
    return apy_from_bool(apy_eq_raw(a, b));
}

APY_API apy_value apy_ne(apy_value a, apy_value b) {
    if (apy_either_inst(a, b)) {
        apy_value r = apy_binary_dunder(a, b, "__ne__", "__ne__");
        if (r || apy_error_occurred()) return r;
        /* No `__ne__`: Python DERIVES it from `__eq__` by negating, and the
           negation is of the truth of what `__eq__` said -- so a class
           returning a string from `__eq__` has a `!=` of False. */
        r = apy_binary_dunder(a, b, "__eq__", "__eq__");
        if (apy_error_occurred()) return 0;
        if (r) return apy_from_bool(!apy_truth(r));
    }
    return apy_from_bool(!apy_eq_raw(a, b));
}

APY_API apy_value apy_is(apy_value a, apy_value b) { return apy_from_bool(a == b); }

/* `needle in haystack`. Defined by `==` over the elements, so a needle that
   cannot equal anything in there simply answers False -- `[7] in [7, 2]` is
   legal Python and it is False, not an error. A str haystack is a SUBSTRING
   test and demands a str needle, which is the one place `in` raises. */
APY_API apy_value apy_contains(apy_value needle, apy_value hay) {
    /* MEMBERSHIP IN A CLASS IS THE METACLASS'S BUSINESS, as iterating and
       measuring one are: `Colour.RED in Colour` is
       `type(Colour).__contains__(Colour, RED)`. The third of the three to need
       saying so -- see `apy_iter` and `apy_len`. */
    if (O(hay)->kind == APY_TYPE_K && O(hay)->v.t.meta) {
        apy_value hook = apy_class_find(O(hay)->v.t.meta,
                                        apy_name("__contains__"));
        if (hook) {
            apy_value argv[1];
            argv[0] = needle;
            return apy_call_n(apy_bind(hook, hay), argv, 1);
        }
    }
    /* ARITHMETIC, not a search: `10**11 in range(10**12)` is a division. */
    if (O(hay)->kind == APY_RANGE_K) {
        int64_t want;
        if (!apy_is_int_like(needle)) return apy_from_bool(0);
        if (!apy_index_arg(needle, &want, APY_IDX_SIZE)) return 0;
        return apy_from_bool(apy_range_find(hay, want) >= 0);
    }
    /* `k in d.keys()` -- read through, so a key added after the view was made
       is found. */
    if (O(hay)->kind == APY_VIEW_K)
        return apy_contains(needle, apy_view_items(hay));
    /* A CLASS EXTENDING A BUILTIN answers membership as the builtin, unless
       its body says otherwise -- `"a" in d` on a `class D(dict)` asks the
       dict's keys, the same question `for k in d` asks. */
    {
        apy_value as = apy_as_builtin(hay, "__contains__");
        if (as != hay) return apy_contains(needle, as);
    }
    /* `x in gen` CONSUMES the generator up to the match, and leaves the rest.
       That is what makes `2 in squares` then `list(squares)` answer `False`
       and `[]` -- a generator is consumed once. Stepping rather than draining
       is the whole of the difference, and draining reported the generator as
       not iterable at all. */
    if (O(hay)->kind == APY_GEN_K) {
        for (;;) {
            int done;
            apy_value item = apy_gen_step(hay, apy_none(), &done);
            if (!item) return 0;
            if (done) return apy_from_bool(0);
            if (apy_eq_element(needle, item)) return apy_from_bool(1);
        }
    }
    int64_t i;
    if (O(hay)->kind == APY_INST_K) {
        /* `__contains__` first, then `__getitem__` walked from 0 until it
           raises -- CPython's own fallback, and the reason a class with only
           `__getitem__` supports `in`. The walk is NOT implemented here: it
           needs an IndexError to stop on, and the sticky error flag makes
           "ran off the end" and "the program failed" the same state. A class
           with only `__getitem__` therefore reports below rather than
           quietly answering False. */
        apy_value r = apy_method1(hay, "__contains__", needle);
        if (r || apy_error_occurred()) return r ? apy_from_bool(apy_truth(r)) : r;
        /* No `__contains__`. `in` then falls back to ITERATION, which is
           CPython's rule and the reason a class with only `__getitem__`
           supports it. `apy_iterable` is the walk, and it knows how to stop:
           on the IndexError the class raises, or on StopIteration. */
        r = apy_iterable(hay);
        if (!r) return 0;
        if (r != hay) return apy_contains(needle, r);
        /* `apy_iterable` left it alone, which means `__len__` plus
           `__getitem__`: the index walk IS its protocol, so walk it. */
        {
            int64_t n = apy_raw_len(hay);
            if (apy_error_occurred()) return 0;
            for (i = 0; i < n; i++) {
                apy_value item = apy_key_at(hay, i);
                if (!item) return 0;
                if (apy_eq_element(needle, item)) return apy_from_bool(1);
            }
            return apy_from_bool(0);
        }
    }
    if (O(hay)->kind == APY_DICT_K) {
        /* `x in d` HASHES x, so an unhashable needle is a TypeError and not
           simply absent -- `[1] in {1: 2}` raises in CPython. This scan does
           not need a hash and so would happily answer False, which is a wrong
           answer rather than a missing feature. */
        const char *bad = apy_unhashable(needle);
        if (bad) return apy_unhashable_key(needle, bad);
        /* Membership and iteration both walk the KEYS -- `in` on a dict
           asks about keys, not values, and so does `for k in d`. */
        for (i = 0; i < O(hay)->v.d.n; i++)
            if (apy_eq_element(needle, O(hay)->v.d.keys[i]))
                return apy_from_bool(1);
        return apy_from_bool(0);
    }
    if (apy_is_set(hay)) {
        /* Like a dict and unlike a list: `x in s` hashes x, so `[1] in {1}`
           raises in CPython rather than answering False.
           A SET NEEDLE IS THE EXCEPTION. `{1} in {1, 2}` is False and
           `{1} in {frozenset([1])}` is True -- CPython retries an unhashable
           set as the frozenset it would be, because that is the only kind for
           which the retry has an answer. Equality here already treats a set
           and a frozenset as one thing, so the retry is just not asking. */
        if (!apy_is_set(needle)) {
            const char *bad = apy_unhashable(needle);
            if (bad) return apy_unhashable_elem(needle, bad);
        }
        return apy_from_bool(apy_set_find(hay, needle) >= 0);
    }
    if (apy_is_seq(hay)) {
        for (i = 0; i < O(hay)->v.q.n; i++)
            if (apy_eq_element(needle, O(hay)->v.q.items[i]))
                return apy_from_bool(1);
        return apy_from_bool(0);
    }
    if (O(hay)->kind == APY_BYTES_K) {
        int64_t i, hn = O(hay)->v.s.n;
        const unsigned char *hp = (const unsigned char *)O(hay)->v.s.p;
        if (apy_is_int_like(needle)) {
            int64_t want;
            if (!apy_index_arg(needle, &want, APY_IDX_SUB)) return 0;
            if (want < 0 || want > 255)
                return apy_fail("ValueError",
                                "byte must be in range(0, 256)");
            for (i = 0; i < hn; i++) if (hp[i] == want) return apy_from_bool(1);
            return apy_from_bool(0);
        }
        if (O(needle)->kind != APY_BYTES_K)
            return apy_fail2("TypeError",
                             "a bytes-like object is required, not '%s'%s",
                             apy_kind_name(needle), "");
        { int64_t nn = O(needle)->v.s.n;
          const unsigned char *np = (const unsigned char *)O(needle)->v.s.p;
          if (nn > hn) return apy_from_bool(0);
          for (i = 0; i + nn <= hn; i++)
              if (memcmp(hp + i, np, (size_t)nn) == 0) return apy_from_bool(1);
          return apy_from_bool(0); }
    }
    if (O(hay)->kind == APY_STR_K) {
        int64_t n, m;
        if (O(needle)->kind != APY_STR_K)
            return apy_fail2("TypeError",
                             "'in <string>' requires string as left operand, "
                             "not %s%s", apy_kind_name(needle), "");
        n = O(hay)->v.s.n; m = O(needle)->v.s.n;
        if (m == 0) return apy_from_bool(1);
        for (i = 0; i + m <= n; i++)
            if (memcmp(O(hay)->v.s.p + i, O(needle)->v.s.p, (size_t)m) == 0)
                return apy_from_bool(1);
        return apy_from_bool(0);
    }
    return apy_fail2("TypeError", "argument of type '%s' is not iterable%s",
                     apy_kind_name(hay), "");
}

/* -1 / 0 / 1, APY_UNORD for a nan (False for every ordering, no error), or
   2 for "these kinds cannot be ordered at all" (a TypeError). */
static int apy_order(apy_value a, apy_value b);

/* `apy_order` WITH THE USER'S `__lt__` BEHIND IT.

   `apy_order` answers 2 for "these are not orderable to me", which is the
   right answer for an int against a str and the WRONG one for two instances
   of a class that writes `__lt__`. The `<` operator already knew that --
   `apy_cmp` falls back to the dunder at exactly that point -- and `sorted`,
   `min` and `max` called `apy_order` directly and reported `unsupported
   operand type(s) for <` instead. So `Num.THREE < Num.ONE` worked and
   `sorted([Num.THREE, Num.ONE])` did not, for the same two objects.

   THE MIRRORED OPERATOR IS THE REFLECTED NAME, as in `apy_cmp`: `a < b` falls
   back to `b.__gt__(a)`, because what b is asked is the comparison from its
   side. */
APY_API int64_t apy_order_rich_of(apy_value a, apy_value b) {
    int c = apy_order(a, b);
    apy_value r;
    if (c != 2 || !apy_either_inst(a, b)) return c;
    r = apy_binary_dunder(a, b, "__lt__", "__gt__");
    if (!r) return 2;
    if (apy_truth(r)) return -1;
    r = apy_binary_dunder(b, a, "__lt__", "__gt__");
    if (!r) return 2;
    return apy_truth(r) ? 1 : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_order_rich(apy_value a, apy_value b) {
    return (int)apy_order_rich_of(a, b);
}

APY_API int64_t apy_order_of(apy_value a, apy_value b) {
    /* SET ORDERING IS CONTAINMENT, AND IT IS PARTIAL. `{1, 2} < {1, 3}` is
       False and so is `>`, and neither is an error -- the two sets simply
       stand in no order. That is the same outcome a nan produces, so it
       reuses APY_UNORD: every one of the four comparisons answers False and
       none of them raises. A set against a NON-set is a TypeError, which is
       the ordinary un-orderable-pair path below. */
    if (apy_is_set(a) && apy_is_set(b)) {
        int sub = apy_subset(a, b), sup = apy_subset(b, a);
        if (sub && sup) return 0;
        if (sub) return -1;
        if (sup) return 1;
        return APY_UNORD;
    }
    /* NO ORDERING. This is the rule that keeps complex from being a third
       float: `1j < 2j` is a TypeError, and so is comparing one to a real
       number. Falling through to the numeric path would have compared the
       real parts and answered, which is a wrong answer rather than a missing
       feature.

       `2` is the un-orderable-PAIR answer, distinct from `APY_UNORD`, which
       means "a nan was involved" and makes all four comparisons False without
       raising. The caller turns this into the TypeError, naming the operator
       -- which is knowable there and not here. */
    if (O(a)->kind == APY_COMPLEX_K || O(b)->kind == APY_COMPLEX_K)
        return 2;
    if (O(a)->kind == APY_BYTES_K && O(b)->kind == APY_BYTES_K)
        return apy_str_cmp(a, b);   /* octet order, which is what str_cmp does */
    if (O(a)->kind == APY_STR_K && O(b)->kind == APY_STR_K) return apy_str_cmp(a, b);
    if (apy_is_seq(a) && apy_is_seq(b) && O(a)->kind == O(b)->kind) {
        /* Lexicographic: the first differing element decides, and if one runs
           out first it is the smaller. */
        int64_t i, n = O(a)->v.q.n < O(b)->v.q.n ? O(a)->v.q.n : O(b)->v.q.n;
        for (i = 0; i < n; i++) {
            int c = apy_order_rich(O(a)->v.q.items[i], O(b)->v.q.items[i]);
            if (c == 2) return 2;
            if (c) return c;
        }
        if (O(a)->v.q.n == O(b)->v.q.n) return 0;
        return O(a)->v.q.n < O(b)->v.q.n ? -1 : 1;
    }
    if (!apy_is_num(a) || !apy_is_num(b)) return 2;
    return apy_num_order(a, b);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_order(apy_value a, apy_value b) {
    return (int)apy_order_of(a, b);
}

static apy_value apy_cmp(const char *op, apy_value a, apy_value b, int lt, int eq, int gt) {
    int c = apy_order(a, b);
    /* A nan is not less than, equal to, or greater than anything -- including
       itself. All four orderings answer False, and none of them is an error,
       so this is NOT the same case as an un-orderable pair of kinds. */
    if (c == APY_UNORD) return apy_from_bool(0);
    if (c == 2) {
        char buf[256];
        /* Two instances are "un-orderable" to `apy_order`, which is the same
           answer it gives an int and a str -- so the user's class gets its
           say here, at the point the built-in ordering has given up.

           THE REFLECTED NAME IS THE MIRRORED OPERATOR, not an `__r`-prefixed
           one: `a < b` falls back to `b.__gt__(a)`, because what b is asked
           is the comparison as seen from its side. Comparisons are the one
           family where that is true, and using `__rlt__` -- which does not
           exist -- would simply never fire. */
        static const char *const REFLECT[][3] = {
            { "<",  "__lt__", "__gt__" }, { "<=", "__le__", "__ge__" },
            { ">",  "__gt__", "__lt__" }, { ">=", "__ge__", "__le__" },
            { NULL, NULL, NULL },
        };
        if (apy_either_inst(a, b)) {
            int i;
            for (i = 0; REFLECT[i][0]; i++)
                if (strcmp(REFLECT[i][0], op) == 0) {
                    apy_value r = apy_binary_dunder(a, b, REFLECT[i][1],
                                                    REFLECT[i][2]);
                    if (r || apy_error_occurred()) return r;
                    break;
                }
        }
        snprintf(buf, sizeof buf,
                 "'%s' not supported between instances of '%s' and '%s'",
                 op, apy_kind_name(a), apy_kind_name(b));
        return apy_fail("TypeError", buf);
    }
    return apy_from_bool(c < 0 ? lt : (c == 0 ? eq : gt));
}

APY_API apy_value apy_lt(apy_value a, apy_value b) { return apy_cmp("<", a, b, 1, 0, 0); }
APY_API apy_value apy_le(apy_value a, apy_value b) { return apy_cmp("<=", a, b, 1, 1, 0); }
APY_API apy_value apy_gt(apy_value a, apy_value b) { return apy_cmp(">", a, b, 0, 0, 1); }
APY_API apy_value apy_ge(apy_value a, apy_value b) { return apy_cmp(">=", a, b, 0, 1, 1); }

/* --- conversions ------------------------------------------------------- */
/* `int('...')` and `float('...')` are NOT `strtoll` and `strtod` with the ends
   checked, and every difference below is a case the naive version got wrong:

   * Python allows UNDERSCORES between digits (`int('1_0')` is 10). C's
     converters stop at the first one. They are stripped into a scratch buffer
     rather than parsed around, because the rule -- one underscore, only
     between two digits -- is easier to enforce while copying.
   * C99's `strtod` accepts a HEX float, so `float('0x10')` came back as 16.0
     where CPython raises. The `0x` prefix is rejected before strtod sees it.
   * Leading AND trailing whitespace is stripped by Python for both. `strtoll`
     skips leading space itself; nothing skips the trailing.
   * The message quotes the string with `apy_repr`, not with `%s`. CPython
     does the same, and it is not cosmetic: `int('  -42\n')`'s message
     contains a real newline, and a raw `%s` puts that newline in the middle
     of a one-line error report.

   `float` still accepts `inf`/`nan` (as CPython does, case-insensitively via
   strtod) and rejects the `infinity`-with-junk forms the same way. */
static int apy_strip_us(const char *p, int64_t n, char *out, size_t cap) {
    int64_t i;
    size_t o = 0;
    for (i = 0; i < n; i++) {
        if (o + 1 >= cap) return 0;
        if (p[i] == '_') {
            /* Only between two digits -- `_1`, `1_`, `1__0` are all errors. */
            if (i == 0 || i + 1 >= n) return 0;
            if (p[i - 1] < '0' || p[i - 1] > '9') return 0;
            if (p[i + 1] < '0' || p[i + 1] > '9') return 0;
            continue;
        }
        out[o++] = p[i];
    }
    out[o] = '\0';
    return 1;
}

static int apy_is_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r'
        || c == '\f' || c == '\v';
}

/* `<kind>: <the string, repr'd>` -- the shape both conversion errors use. */
static apy_value apy_conv_error(const char *prefix, apy_value s) {
    apy_value q = apy_repr(s);
    char buf[256];
    snprintf(buf, sizeof buf, "%s%.*s", prefix,
             (int)O(q)->v.s.n, O(q)->v.s.p);
    return apy_fail("ValueError", buf);
}

APY_API apy_value apy_to_int(apy_value v) {
    /* A CLASS SAYS WHAT ITS INTEGER IS. `__int__` first and `__index__` after
       it -- the two are not the same question, and a class may define only
       the second. Without this the conversion reported that the object was
       not a number, which is the class's answer to give and not this one's. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__int__");
        if (apy_error_occurred()) return 0;
        if (!got) {
            got = apy_unary_dunder(v, "__index__");
            if (apy_error_occurred()) return 0;
        }
        if (got) return got;
    }
    if (O(v)->kind == APY_FLOAT_K) {
        /* `int(nan)` and `int(inf)` are errors, not whatever a cast gives --
           the cast is undefined for both. */
        double f = O(v)->v.f;
        if (isnan(f))
            return apy_fail("ValueError", "cannot convert float NaN to integer");
        if (isinf(f))
            return apy_fail("OverflowError",
                            "cannot convert float infinity to integer");
        /* A cast to int64 is UNDEFINED once the value does not fit, so
           anything past the range goes the exact way instead. Every double
           that large is already a whole number, so truncating toward zero --
           which is what `int()` does -- has nothing left to remove. */
        if (f >= 9223372036854775808.0 || f < -9223372036854775808.0)
            return apy_big_from_double(f);
        return apy_from_int((int64_t)f);
    }
    if (apy_is_big(v)) return v;
    if (apy_is_int_like(v)) return apy_from_int(O(v)->v.i);
    if (O(v)->kind == APY_STR_K) {
        /* LENGTH IS NOT BOUNDED any more. This used to refuse a literal of
           128 characters or more, which was right when the answer had to fit
           an int64 and is a wrong answer now -- `int('1' + '0' * 200)` is an
           ordinary Python expression. The scratch buffer is sized to the
           input instead of to a guess. */
        int64_t n = O(v)->v.s.n, lo = 0, hi;
        char *clean = (char *)malloc((size_t)n + 1);
        apy_value r;
        int neg = 0;
        if (!clean) { fputs("asmpython: out of memory\n", stderr); exit(1); }
        if (!apy_strip_us(O(v)->v.s.p, n, clean, (size_t)n + 1)) {
            free(clean);
            return apy_conv_error("invalid literal for int() with base 10: ", v);
        }
        hi = (int64_t)strlen(clean);
        while (lo < hi && apy_is_space(clean[lo])) lo++;
        while (hi > lo && apy_is_space(clean[hi - 1])) hi--;
        if (lo < hi && (clean[lo] == '+' || clean[lo] == '-')) {
            neg = clean[lo] == '-';
            lo++;
        }
        /* `apy_big_from_digits` demotes anything that fits, so a short
           literal comes back as an ordinary int and there is one parser
           rather than two that could disagree at the boundary. It answers 0
           with no error set for a non-digit, which is what distinguishes a
           bad literal from an overflow. */
        r = apy_big_from_digits(clean + lo, hi - lo, neg);
        free(clean);
        if (!r && !apy_error_occurred())
            return apy_conv_error("invalid literal for int() with base 10: ", v);
        return r;
    }
    return apy_fail2("TypeError", "int() argument must be a string, a bytes-like object or a real number, not '%s'%s",
                     apy_kind_name(v), "");
}

APY_API apy_value apy_to_float(apy_value v) {
    /* `__float__`, and `__index__` after it: an object that can be an integer
       can be a float, which is the rule CPython follows too. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__float__");
        if (apy_error_occurred()) return 0;
        if (!got) {
            got = apy_unary_dunder(v, "__index__");
            if (apy_error_occurred()) return 0;
            if (got) return apy_to_float(got);
        }
        if (got) return got;
    }
    if (O(v)->kind == APY_FLOAT_K) return v;
    if (apy_is_big(v)) {
        double d = apy_big_double(O(v));
        /* Past about 1.8e308 there is no double to convert to, and CPython
           reports rather than handing back an infinity that would then
           propagate silently through everything downstream. */
        if (isinf(d))
            return apy_fail("OverflowError", "int too large to convert to float");
        return apy_from_float(d);
    }
    if (apy_is_int_like(v)) return apy_from_float((double)O(v)->v.i);
    if (O(v)->kind == APY_STR_K) {
        /* The scratch buffer is sized to the INPUT, not to a guess. A fixed
           128 bytes was right while `int()` could not answer past 19 digits
           either, and it made `float('1' * 300)` a ValueError -- an ordinary
           expression with an ordinary answer, 1.11e299. */
        int64_t n = O(v)->v.s.n;
        char *clean = (char *)malloc((size_t)n + 1), *end;
        const char *p, *q;
        double r;
        if (!clean) { fputs("asmpython: out of memory\n", stderr); exit(1); }
        if (!apy_strip_us(O(v)->v.s.p, n, clean, (size_t)n + 1)) {
            free(clean);
            return apy_conv_error("could not convert string to float: ", v);
        }
        p = clean;
        q = p;
        while (apy_is_space(*q)) q++;
        if (*q == '+' || *q == '-') q++;
        if (q[0] == '0' && (q[1] == 'x' || q[1] == 'X')) {
            free(clean);
            return apy_conv_error("could not convert string to float: ", v);
        }
        r = strtod(p, &end);
        while (apy_is_space(*end)) end++;
        if (end == p || *end) {
            free(clean);
            return apy_conv_error("could not convert string to float: ", v);
        }
        free(clean);
        return apy_from_float(r);
    }
    return apy_fail2("TypeError", "float() argument must be a string or a real number, not '%s'%s",
                     apy_kind_name(v), "");
}

APY_API apy_value apy_to_bool(apy_value v) { return apy_from_bool(apy_truth(v)); }

"""
