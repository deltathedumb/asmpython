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

/* A double that is a whole number, back as an INT -- promoting to a big when
   it does not fit an int64, because `math.floor(1e30)` is an integer with a
   hundred bits and casting it is undefined rather than merely wrong. */
static apy_value apy_whole(double d) {
    if (d >= 9223372036854775808.0 || d < -9223372036854775808.0)
        return apy_big_from_double(d);
    return apy_from_int((int64_t)d);
}

APY_API apy_value apy_math_floor(apy_value v) {
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
    {
        double x = apy_math_arg(v, "floor");
        if (apy_error_occurred()) return 0;
        return apy_whole(floor(x));
    }
}

APY_API apy_value apy_math_ceil(apy_value v) {
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
    {
        double x = apy_math_arg(v, "ceil");
        if (apy_error_occurred()) return 0;
        return apy_whole(ceil(x));
    }
}

APY_API apy_value apy_math_trunc(apy_value v) {
    if (apy_is_int_like(v) || apy_is_big(v)) return v;
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
APY_API apy_value apy_math_log(apy_value v) {
    double x = apy_math_arg(v, "log");
    if (apy_error_occurred()) return 0;
    if (x <= 0) return apy_fail("ValueError", "math domain error");
    return apy_from_float(log(x));
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
    x = O(a)->v.i; y = O(b)->v.i;
    if (x < 0) x = -x;
    if (y < 0) y = -y;
    while (y) { int64_t t = x % y; x = y; y = t; }
    return apy_from_int(x);
}

APY_API apy_value apy_math_lcm(apy_value a, apy_value b) {
    apy_value g = apy_math_gcd(a, b);
    int64_t x, y, d;
    if (!g) return 0;
    d = O(g)->v.i;
    if (d == 0) return apy_from_int(0);
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

APY_API apy_value apy_math_atan2(apy_value a, apy_value b) {
    double x = apy_math_arg(a, "atan2"), y;
    if (apy_error_occurred()) return 0;
    y = apy_math_arg(b, "atan2");
    if (apy_error_occurred()) return 0;
    return apy_from_float(atan2(x, y));
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
