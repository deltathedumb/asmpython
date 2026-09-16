"""The object runtime, in C: math.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * math
"""

C = r"""/* --- math --------------------------------------------------------------- */
/* `import math`. Every one of these is a function of its arguments alone, so
   the module needs no state and each member is an ordinary runtime call --
   which is what lets `import math` be a handful of instructions at the
   statement rather than a compilation unit.

   THE INTEGER-PRESERVING ONES ARE THE POINT. `math.floor(-2.5)` is the INT
   -3, not the float -3.0, and `math.trunc` and `math.ceil` are the same; a
   float result there would print differently and compare differently. The
   ones that are genuinely real-valued (`sqrt`, `log`) answer floats. */

APY_API double apy_math_arg_of(apy_value v, apy_value fn) {
    if (apy_is_num(v)) return apy_num_f(v);
    apy_fail2("TypeError", "must be real number, not %s%s",
              apy_kind_name(v), "");
    return 0.0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static double apy_math_arg(apy_value v, const char *fn) {
    return apy_math_arg_of(v, (apy_value)(uintptr_t)fn);
}

APY_API apy_value apy_math_sqrt(apy_value v) {
    double x = apy_math_arg(v, "sqrt");
    if (apy_error_occurred()) return 0;
    if (x < 0) return apy_fail("ValueError", "math domain error");
    return apy_from_float(sqrt(x));
}

/* PEP 3141'S HOOKS, forward-declared because `_calling.py` defines this and
   comes after this part -- the same arrangement `_sequence.py` uses for it.
   `floor`, `ceil` and `trunc` are the three math functions with a dunder of
   their own, and asking for it is the ONLY way a real number that is not a
   float can be rounded: `Fraction(7, 2)` and `Decimal("3.5")` both define
   one and neither converts exactly to a double. */
static apy_value apy_unary_dunder(apy_value v, const char *name);
/* `apy_lt` LIVES IN `_numeric.py`, which is concatenated after this part --
   the same forward declaration `apy_unary_dunder` needs, for the same
   reason. `isqrt` of a big compares big values and has nothing else to
   compare them with. */
APY_API apy_value apy_lt(apy_value a, apy_value b);

/* A double that is a whole number, back as an INT -- promoting to a big when
   it does not fit an int64, because `math.floor(1e30)` is an integer with a
   hundred bits and casting it is undefined rather than merely wrong. */
static apy_value apy_whole(double d) {
    if (d >= 9223372036854775808.0 || d < -9223372036854775808.0)
        return apy_big_from_double(d);
    return apy_from_int((int64_t)d);
}

APY_API apy_value apy_math_floor(apy_value v) {
    /* A BOOL BECOMES AN INT: `math.floor(True)` is `1` in CPython and
       not `True`. These answer an integer, and a bool is one only by
       inheritance -- handing the argument straight back printed it. */
    if (O(v)->kind == APY_BOOL_K) return apy_from_int(O(v)->v.i);
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
    /* THE CLASS'S OWN ANSWER FIRST, before any float conversion -- see the
       forward declaration above. A class with no `__floor__` falls through to
       the conversion, which is where a plain object is refused. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__floor__");
        if (got) return got;
        if (apy_error_occurred()) return 0;
    }
    {
        double x = apy_math_arg(v, "floor");
        if (apy_error_occurred()) return 0;
        return apy_whole(floor(x));
    }
}

APY_API apy_value apy_math_ceil(apy_value v) {
    /* A BOOL BECOMES AN INT: `math.floor(True)` is `1` in CPython and
       not `True`. These answer an integer, and a bool is one only by
       inheritance -- handing the argument straight back printed it. */
    if (O(v)->kind == APY_BOOL_K) return apy_from_int(O(v)->v.i);
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
    /* THE CLASS'S OWN ANSWER FIRST, before any float conversion -- see the
       forward declaration above. A class with no `__ceil__` falls through to
       the conversion, which is where a plain object is refused. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__ceil__");
        if (got) return got;
        if (apy_error_occurred()) return 0;
    }
    {
        double x = apy_math_arg(v, "ceil");
        if (apy_error_occurred()) return 0;
        return apy_whole(ceil(x));
    }
}

APY_API apy_value apy_math_trunc(apy_value v) {
    /* A BOOL BECOMES AN INT: `math.floor(True)` is `1` in CPython and
       not `True`. These answer an integer, and a bool is one only by
       inheritance -- handing the argument straight back printed it. */
    if (O(v)->kind == APY_BOOL_K) return apy_from_int(O(v)->v.i);
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
    /* THE CLASS'S OWN ANSWER FIRST, before any float conversion -- see the
       forward declaration above. A class with no `__trunc__` falls through to
       the conversion, which is where a plain object is refused. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__trunc__");
        if (got) return got;
        if (apy_error_occurred()) return 0;
    }
    {
        double x = apy_math_arg(v, "trunc");
        if (apy_error_occurred()) return 0;
        return apy_whole(x < 0 ? ceil(x) : floor(x));
    }
}

APY_API apy_value apy_math_fabs(apy_value v) {
    double x = apy_math_arg(v, "fabs");
    if (apy_error_occurred()) return 0;
    return apy_from_float(fabs(x));
}

APY_API apy_value apy_math_isnan(apy_value v) {
    double x = apy_math_arg(v, "isnan");
    if (apy_error_occurred()) return 0;
    return apy_from_bool(x != x);
}

APY_API apy_value apy_math_isinf(apy_value v) {
    double x = apy_math_arg(v, "isinf");
    if (apy_error_occurred()) return 0;
    return apy_from_bool(x == x && x - x != 0.0);
}

APY_API apy_value apy_math_isfinite(apy_value v) {
    double x = apy_math_arg(v, "isfinite");
    if (apy_error_occurred()) return 0;
    return apy_from_bool(x == x && x - x == 0.0);
}

/* `isqrt(n)` is the FLOOR of the real square root, exactly -- so it is
   computed by integer Newton rather than by rounding `sqrt`, which is off by
   one for values near a perfect square once they exceed a double's 53 bits. */
APY_API apy_value apy_math_isqrt(apy_value v) {
    int64_t n, r;
    if (!apy_is_int_like(v))
        return apy_fail2("TypeError",
                         "'%s' object cannot be interpreted as an integer%s",
                         apy_kind_name(v), "");
    /* A BIG GETS NEWTON'S METHOD IN BIG ARITHMETIC. The machine-word path
       below reads `v.i`, which is a POINTER when the argument outgrew a
       word -- `isqrt(10 ** 40)` answered a number bearing no relation to
       10**20. The same bug `gcd`, `lcm`, `comb` and `perm` had, and the
       same shape of fix: detect the big and go the general way.

       CONVERGENCE FROM ABOVE. `x` starts at `n` and each step replaces it
       with `(x + n / x) / 2`, which is never below the true root and
       decreases while it is above -- so `y < x` is the whole termination
       test and the loop runs about `log2(bits)` times. */
    if (apy_is_big(v)) {
        apy_value zero = apy_from_int(0), two = apy_from_int(2), x, y;
        if (apy_truth(apy_lt(v, zero)))
            return apy_fail("ValueError",
                            "isqrt() argument must be nonnegative");
        if (!apy_truth(v)) return apy_from_int(0);
        x = v;
        y = apy_floordiv(apy_add(x, apy_from_int(1)), two);
        if (!y) return 0;
        while (apy_truth(apy_lt(y, x))) {
            apy_value q;
            x = y;
            q = apy_floordiv(v, x);
            if (!q) return 0;
            y = apy_floordiv(apy_add(x, q), two);
            if (!y) return 0;
        }
        return x;
    }
    n = O(v)->v.i;
    if (n < 0) return apy_fail("ValueError",
                               "isqrt() argument must be nonnegative");
    if (n == 0) return apy_from_int(0);
    r = (int64_t)sqrt((double)n);
    while (r > 0 && r > n / r) r--;
    while ((r + 1) <= n / (r + 1)) r++;
    return apy_from_int(r);
}

APY_API apy_value apy_math_factorial(apy_value v) {
    int64_t n, i;
    apy_value acc;
    if (!apy_is_int_like(v))
        return apy_fail("TypeError",
                        "'float' object cannot be interpreted as an integer");
    n = O(v)->v.i;
    if (n < 0) return apy_fail("ValueError",
                               "factorial() not defined for negative values");
    /* Through the ordinary multiply, so a result past int64 promotes to a big
       the way `2 ** 100` does -- `factorial(30)` has 108 bits. */
    acc = apy_from_int(1);
    for (i = 2; i <= n; i++) {
        acc = apy_mul(acc, apy_from_int(i));
        if (!acc) return 0;
    }
    return acc;
}

static apy_value apy_math_1(apy_value v, double (*fn)(double),
                            const char *name) {
    double x = apy_math_arg(v, name), r;
    if (apy_error_occurred()) return 0;
    errno = 0;
    r = fn(x);
    if (errno == EDOM) return apy_fail("ValueError", "math domain error");
    return apy_from_float(r);
}

APY_API apy_value apy_math_exp(apy_value v) { return apy_math_1(v, exp, "exp"); }
/* `log(x)` AND `log(x, base)`. The second form was missing entirely --
   `math.log(8, 2)` answered the natural log of 8 and ignored the base,
   which is a wrong number rather than an error. The default `base` is a
   sentinel rather than `e`, because `log(x, e)` computed as a QUOTIENT is
   not bit-identical to `log(x)` and a program comparing the two would
   see them differ in the last place. */
APY_API apy_value apy_math_log(apy_value v, apy_value base) {
    double x = apy_math_arg(v, "log"), b;
    if (apy_error_occurred()) return 0;
    if (x <= 0) return apy_fail("ValueError", "math domain error");
    if (O(base)->kind == APY_NONE_K) return apy_from_float(log(x));
    b = apy_math_arg(base, "log");
    if (apy_error_occurred()) return 0;
    if (b <= 0) return apy_fail("ValueError", "math domain error");
    if (b == 1.0) return apy_fail("ZeroDivisionError", "float division by zero");
    return apy_from_float(log(x) / log(b));
}
APY_API apy_value apy_math_log2(apy_value v) {
    double x = apy_math_arg(v, "log2");
    if (apy_error_occurred()) return 0;
    if (x <= 0) return apy_fail("ValueError", "math domain error");
    return apy_from_float(log2(x));
}
APY_API apy_value apy_math_log10(apy_value v) {
    double x = apy_math_arg(v, "log10");
    if (apy_error_occurred()) return 0;
    if (x <= 0) return apy_fail("ValueError", "math domain error");
    return apy_from_float(log10(x));
}
APY_API apy_value apy_math_sin(apy_value v) { return apy_math_1(v, sin, "sin"); }
APY_API apy_value apy_math_cos(apy_value v) { return apy_math_1(v, cos, "cos"); }
APY_API apy_value apy_math_tan(apy_value v) { return apy_math_1(v, tan, "tan"); }
APY_API apy_value apy_math_atan(apy_value v) { return apy_math_1(v, atan, "atan"); }

/* THE REST OF libm's ONE-ARGUMENT FAMILY. Each is `apy_math_1`
   over the C function of the same name -- `gamma` is the exception,
   because C spells the true gamma `tgamma` and reserves `gamma` for
   an older name that meant `lgamma` on some platforms. Every one of
   these was simply absent: `math.acos` was an AttributeError. */
APY_API apy_value apy_math_acos(apy_value v) { return apy_math_1(v, acos, "acos"); }
APY_API apy_value apy_math_asin(apy_value v) { return apy_math_1(v, asin, "asin"); }
APY_API apy_value apy_math_acosh(apy_value v) { return apy_math_1(v, acosh, "acosh"); }
APY_API apy_value apy_math_asinh(apy_value v) { return apy_math_1(v, asinh, "asinh"); }
APY_API apy_value apy_math_atanh(apy_value v) { return apy_math_1(v, atanh, "atanh"); }
APY_API apy_value apy_math_cosh(apy_value v) { return apy_math_1(v, cosh, "cosh"); }
APY_API apy_value apy_math_sinh(apy_value v) { return apy_math_1(v, sinh, "sinh"); }
APY_API apy_value apy_math_tanh(apy_value v) { return apy_math_1(v, tanh, "tanh"); }
APY_API apy_value apy_math_expm1(apy_value v) { return apy_math_1(v, expm1, "expm1"); }
APY_API apy_value apy_math_log1p(apy_value v) { return apy_math_1(v, log1p, "log1p"); }
APY_API apy_value apy_math_erf(apy_value v) { return apy_math_1(v, erf, "erf"); }
APY_API apy_value apy_math_erfc(apy_value v) { return apy_math_1(v, erfc, "erfc"); }
APY_API apy_value apy_math_gamma(apy_value v) { return apy_math_1(v, tgamma, "gamma"); }
APY_API apy_value apy_math_lgamma(apy_value v) { return apy_math_1(v, lgamma, "lgamma"); }
APY_API apy_value apy_math_cbrt(apy_value v) { return apy_math_1(v, cbrt, "cbrt"); }

/* `exp2(x)` IS `2 ** x`, and C99 has it -- but MSVC did not until 2013 and
   `pow(2.0, x)` is exact for the same inputs, so the fallback is written
   out rather than depended on. */
APY_API apy_value apy_math_exp2(apy_value v) {
    double x = apy_math_arg(v, "exp2");
    if (apy_error_occurred()) return 0;
    return apy_from_float(pow(2.0, x));
}

/* `ulp(x)` -- the gap to the next float. NOT a libm function anywhere:
   CPython computes it, and so does this. `nextafter` toward infinity minus
   the value itself is the definition. */
APY_API apy_value apy_math_ulp(apy_value v) {
    double x = apy_math_arg(v, "ulp"), n;
    if (apy_error_occurred()) return 0;
    if (x != x) return apy_from_float(x);
    x = x < 0 ? -x : x;
    if (x == 0.0) return apy_from_float(4.9406564584124654e-324);
    n = nextafter(x, 1.0e308 * 10.0);
    if (n != n || n == n * 2.0) return apy_from_float(x);
    return apy_from_float(n - x);
}

APY_API apy_value apy_math_degrees(apy_value v) {
    double x = apy_math_arg(v, "degrees");
    if (apy_error_occurred()) return 0;
    return apy_from_float(x * (180.0 / 3.141592653589793115997963468544185161590576171875));
}

APY_API apy_value apy_math_radians(apy_value v) {
    double x = apy_math_arg(v, "radians");
    if (apy_error_occurred()) return 0;
    return apy_from_float(x * (3.141592653589793115997963468544185161590576171875 / 180.0));
}

APY_API apy_value apy_math_gcd(apy_value a, apy_value b) {
    int64_t x, y;
    if (!apy_is_int_like(a) || !apy_is_int_like(b))
        return apy_fail("TypeError",
                        "'float' object cannot be interpreted as an integer");
    /* TWO LOOPS, AND THE SECOND ONE IS THE FIX. This read `v.i` off both
       arguments and ran Euclid in machine words -- which is a POINTER read
       as an integer when the argument is a big, so `gcd(2**100, 2**60)`
       answered 8. The fast path is kept because it is most calls and much
       faster (a machine-word remainder is one instruction where `apy_mod`
       allocates a value per step); it runs only when NEITHER argument is a
       big. `runtime/mathints.py` has the same pair, and the two have to
       agree. */
    if (apy_is_big(a) || apy_is_big(b)) {
        apy_value u = apy_abs(a), v;
        if (!u) return 0;
        v = apy_abs(b);
        if (!v) return 0;
        while (apy_truth(v)) {
            apy_value r = apy_mod(u, v);
            if (!r) return 0;
            u = v;
            v = r;
        }
        return u;
    }
    x = O(a)->v.i; y = O(b)->v.i;
    if (x < 0) x = -x;
    if (y < 0) y = -y;
    while (y) { int64_t t = x % y; x = y; y = t; }
    return apy_from_int(x);
}

APY_API apy_value apy_math_perm(apy_value n, apy_value k) {
    int64_t top, want, i;
    apy_value acc;
    if (!apy_is_int_like(n) || !apy_is_int_like(k))
        return apy_fail("TypeError",
                        "'float' object cannot be interpreted as an integer");
    /* A COUNT THAT DOES NOT FIT A MACHINE WORD is a loop that would never
       finish, so it is refused rather than started. CPython answers it,
       eventually; nothing that could reach here would wait. */
    if (apy_is_big(n) || apy_is_big(k))
        return apy_fail("OverflowError",
                        "comb()/perm() arguments must fit in a machine word here");
    top = O(n)->v.i; want = O(k)->v.i;
    if (top < 0 || want < 0)
        return apy_fail("ValueError",
                        "perm() arguments must be non-negative");
    if (want > top) return apy_from_int(0);
    /* THE FALLING FACTORIAL, computed directly rather than as
       `factorial(n) / factorial(n - k)`: `perm(1000, 2)` is 999000 and the
       quotient form would build a 2568-digit number to answer it. Through
       the ordinary multiply, so a result past int64 promotes to a big. */
    acc = apy_from_int(1);
    for (i = 0; i < want; i++) {
        acc = apy_mul(acc, apy_from_int(top - i));
        if (!acc) return 0;
    }
    return acc;
}

APY_API apy_value apy_math_comb(apy_value n, apy_value k) {
    int64_t top, want, i;
    apy_value acc;
    if (!apy_is_int_like(n) || !apy_is_int_like(k))
        return apy_fail("TypeError",
                        "'float' object cannot be interpreted as an integer");
    if (apy_is_big(n) || apy_is_big(k))
        return apy_fail("OverflowError",
                        "comb()/perm() arguments must fit in a machine word here");
    top = O(n)->v.i; want = O(k)->v.i;
    if (top < 0 || want < 0)
        return apy_fail("ValueError",
                        "comb() arguments must be non-negative");
    if (want > top) return apy_from_int(0);
    /* MIRRORED to the smaller half, since `comb(n, k) == comb(n, n - k)`:
       `comb(1000, 999)` is 1000 loops or one. */
    if (want > top - want) want = top - want;
    /* DIVIDING AS IT GOES, which is exact at every step because the product
       of any `i + 1` consecutive integers is divisible by `(i + 1)!`.
       Multiplying the whole numerator first would build a number far larger
       than the answer -- `comb(1000, 500)` would need the 2568-digit
       `1000!` to produce a 300-digit result. */
    acc = apy_from_int(1);
    for (i = 0; i < want; i++) {
        acc = apy_mul(acc, apy_from_int(top - i));
        if (!acc) return 0;
        acc = apy_floordiv(acc, apy_from_int(i + 1));
        if (!acc) return 0;
    }
    return acc;
}

APY_API apy_value apy_math_lcm(apy_value a, apy_value b) {
    apy_value g = apy_math_gcd(a, b);
    int64_t x, y, d;
    if (!g) return 0;
    if (!apy_truth(g)) return apy_from_int(0);
    /* A BIG TAKES THE SAME ROUTE `apy_math_gcd` does, and for the same
       reason: reading `v.i` off one reads a pointer. */
    if (apy_is_big(a) || apy_is_big(b) || apy_is_big(g)) {
        apy_value u = apy_abs(a), v, q;
        if (!u) return 0;
        v = apy_abs(b);
        if (!v) return 0;
        q = apy_floordiv(u, g);
        if (!q) return 0;
        return apy_mul(q, v);
    }
    d = O(g)->v.i;
    x = O(a)->v.i; y = O(b)->v.i;
    if (x < 0) x = -x;
    if (y < 0) y = -y;
    /* Divide BEFORE multiplying, so a product that would overflow an int64
       but whose lcm does not still answers. */
    return apy_mul(apy_from_int(x / d), apy_from_int(y));
}

APY_API apy_value apy_math_copysign(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "copysign"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "copysign");
    if (apy_error_occurred()) return 0;
    return apy_from_float(copysign(x, y));
}

APY_API apy_value apy_math_pow(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "pow"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "pow");
    if (apy_error_occurred()) return 0;
    /* ALWAYS a float, unlike `**`: `math.pow(2, 3)` is `8.0`. That is the
       whole difference between the two and the reason both exist. */
    return apy_from_float(pow(x, y));
}

/* TWO-ARGUMENT libm, the same shape as `apy_math_atan2` below. `fmod` keeps
   the sign of its first operand where `%` on floats in Python does not --
   which is exactly why `math.fmod` exists beside the operator. */
APY_API apy_value apy_math_fmod(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "fmod"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "fmod");
    if (apy_error_occurred()) return 0;
    errno = 0;
    { double r = fmod(x, y);
      if (errno == EDOM) return apy_fail("ValueError", "math domain error");
      return apy_from_float(r); }
}

APY_API apy_value apy_math_remainder(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "remainder"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "remainder");
    if (apy_error_occurred()) return 0;
    errno = 0;
    { double r = remainder(x, y);
      if (errno == EDOM) return apy_fail("ValueError", "math domain error");
      return apy_from_float(r); }
}

APY_API apy_value apy_math_nextafter(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "nextafter"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "nextafter");
    if (apy_error_occurred()) return 0;
    return apy_from_float(nextafter(x, y));
}

/* `ldexp(x, n)` -- `x * 2 ** n`, and the SECOND argument is an INTEGER
   rather than a float, which is the one place this family differs. */
APY_API apy_value apy_math_ldexp(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "ldexp");
    int64_t n;
    if (apy_error_occurred()) return 0;
    if (!apy_is_int_like(b))
        return apy_fail("TypeError",
                        "Expected an int as second argument to ldexp.");
    n = O(b)->v.i;
    if (n > 2147483647) n = 2147483647;
    if (n < -2147483648) n = -2147483648;
    return apy_from_float(ldexp(x, (int)n));
}

APY_API apy_value apy_math_atan2(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "atan2"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "atan2");
    if (apy_error_occurred()) return 0;
    return apy_from_float(atan2(x, y));
}

/* --- the sequence functions ---------------------------------------------
   `fsum`, `prod` and `dist` take an ITERABLE rather than a fixed number of
   arguments, which is why they arrive as one value: the frontend hands over
   the list. `apy_iterable` drains a generator into one, exactly as
   `apy_extend` does, so `math.fsum(x for x in xs)` works. */

APY_API apy_value apy_math_fsum(apy_value seq) {
    /* NEUMAIER SUMMATION, which is what makes this different from `sum`:
       `sum([0.1] * 10)` is 0.9999999999999999 and `fsum` is 1.0. The
       compensation `c` accumulates the low-order bits `s + x` rounded away,
       and adding it once at the end recovers them. CPython uses an exact
       partials list, which is more accurate still for adversarial input;
       Neumaier agrees with it on everything a program is likely to sum and
       is twenty lines shorter. */
    double s = 0.0, c = 0.0, x, t;
    int64_t i, n;
    seq = apy_iterable(seq);
    if (!seq) return 0;
    n = apy_raw_len(seq);
    for (i = 0; i < n; i++) {
        apy_value item = apy_getitem(seq, apy_from_int(i));
        if (!item) return 0;
        x = apy_math_arg(item, "fsum");
        if (apy_error_occurred()) return 0;
        t = s + x;
        if ((s < 0 ? -s : s) >= (x < 0 ? -x : x)) c += (s - t) + x;
        else c += (x - t) + s;
        s = t;
    }
    return apy_from_float(s + c);
}

APY_API apy_value apy_math_prod(apy_value seq, apy_value start) {
    /* THE PRODUCT STAYS EXACT FOR INTEGERS, because it goes through
       `apy_mul` rather than a double -- `math.prod(range(1, 21))` is
       2432902008176640000 and a float would have lost the low bits. The
       `start` is what decides the type of an empty product, as in
       CPython: `prod([])` is the int 1 and `prod([], start=1.0)` is 1.0. */
    apy_value acc = start;
    int64_t i, n;
    seq = apy_iterable(seq);
    if (!seq) return 0;
    n = apy_raw_len(seq);
    for (i = 0; i < n; i++) {
        apy_value item = apy_getitem(seq, apy_from_int(i));
        if (!item) return 0;
        acc = apy_mul(acc, item);
        if (!acc) return 0;
    }
    return acc;
}

APY_API apy_value apy_math_dist(apy_value p, apy_value q) {
    /* EUCLIDEAN DISTANCE between two points of the same length. The
       ValueError for a length mismatch is CPython's own wording. */
    double total = 0.0, d;
    int64_t i, n, m;
    p = apy_iterable(p);
    if (!p) return 0;
    q = apy_iterable(q);
    if (!q) return 0;
    n = apy_raw_len(p);
    m = apy_raw_len(q);
    if (n != m)
        return apy_fail("ValueError",
                        "both points must have the same number of dimensions");
    for (i = 0; i < n; i++) {
        apy_value a = apy_getitem(p, apy_from_int(i));
        apy_value b;
        double x, y;
        if (!a) return 0;
        b = apy_getitem(q, apy_from_int(i));
        if (!b) return 0;
        x = apy_math_arg(a, "dist");
        if (apy_error_occurred()) return 0;
        y = apy_math_arg(b, "dist");
        if (apy_error_occurred()) return 0;
        d = x - y;
        total += d * d;
    }
    return apy_from_float(sqrt(total));
}

/* `frexp` and `modf` ANSWER A PAIR, which is why they are here and not with
   the one-argument family: each builds a two-tuple. */
APY_API apy_value apy_math_frexp(apy_value v) {
    double x = apy_math_arg(v, "frexp"), m;
    int e = 0;
    apy_value out;
    if (apy_error_occurred()) return 0;
    m = frexp(x, &e);
    out = apy_tuple_new(2);
    apy_seq_push(out, apy_from_float(m));
    apy_seq_push(out, apy_from_int((int64_t)e));
    return out;
}

APY_API apy_value apy_math_modf(apy_value v) {
    /* FRACTIONAL PART FIRST, then the integer part -- CPython's order, and
       the opposite of what the name suggests to most readers. */
    double x = apy_math_arg(v, "modf"), ip = 0.0, fp;
    apy_value out;
    if (apy_error_occurred()) return 0;
    fp = modf(x, &ip);
    out = apy_tuple_new(2);
    apy_seq_push(out, apy_from_float(fp));
    apy_seq_push(out, apy_from_float(ip));
    return out;
}

/* `fma(x, y, z)` -- `x * y + z` with ONE rounding, C99's own function.
   The point is that `x * y` is not rounded before `z` is added, so the
   result is exact where a written-out `x * y + z` is not. */
APY_API apy_value apy_math_fma(apy_value a, apy_value b, apy_value c) {
    double x = apy_math_arg(a, "fma"), y, z;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "fma");
    if (apy_error_occurred()) return 0;
    z = apy_math_arg(c, "fma");
    if (apy_error_occurred()) return 0;
    return apy_from_float(fma(x, y, z));
}

/* `sumprod(p, q)` -- the dot product, and INTEGER-PRESERVING like `prod`:
   `sumprod([1,2,3],[4,5,6])` is the int 32, not 32.0, because it goes
   through `apy_mul` and `apy_add` rather than a double. */
APY_API apy_value apy_math_sumprod(apy_value p, apy_value q) {
    apy_value acc = apy_from_int(0);
    int64_t i, n, m;
    p = apy_iterable(p);
    if (!p) return 0;
    q = apy_iterable(q);
    if (!q) return 0;
    n = apy_raw_len(p);
    m = apy_raw_len(q);
    if (n != m)
        return apy_fail("ValueError", "Inputs are not the same length");
    for (i = 0; i < n; i++) {
        apy_value a = apy_getitem(p, apy_from_int(i)), b, t;
        if (!a) return 0;
        b = apy_getitem(q, apy_from_int(i));
        if (!b) return 0;
        t = apy_mul(a, b);
        if (!t) return 0;
        acc = apy_add(acc, t);
        if (!acc) return 0;
    }
    return acc;
}

APY_API apy_value apy_math_hypot(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "hypot"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "hypot");
    if (apy_error_occurred()) return 0;
    return apy_from_float(sqrt(x * x + y * y));
}

/* `isclose(a, b)` with PEP 485's default tolerances: relative 1e-9, absolute
   0. The relative one is taken against the LARGER magnitude, which is what
   makes the relation symmetric -- `isclose(a, b)` and `isclose(b, a)` agree,
   and a version dividing by one side does not. */
APY_API apy_value apy_math_isclose(apy_value a, apy_value b,
                                   apy_value rel, apy_value abs_tol) {
    double x = apy_math_arg(a, "isclose"), y, r, t, d, ax, ay;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "isclose");
    if (apy_error_occurred()) return 0;
    r = apy_math_arg(rel, "isclose");
    if (apy_error_occurred()) return 0;
    t = apy_math_arg(abs_tol, "isclose");
    if (apy_error_occurred()) return 0;
    if (r < 0 || t < 0)
        return apy_fail("ValueError", "tolerances must be non-negative");
    if (x == y) return apy_from_bool(1);
    if (x != x || y != y) return apy_from_bool(0);
    if (x - x != 0.0 || y - y != 0.0) return apy_from_bool(0);
    d = fabs(x - y);
    ax = fabs(x); ay = fabs(y);
    return apy_from_bool(d <= r * (ax > ay ? ax : ay) || d <= t);
}

"""
