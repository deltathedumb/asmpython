"""The object runtime, in C: the format mini-language.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * format specs
"""

C = r"""/* --- format specs ------------------------------------------------------- */
/* `format(v, spec)`, `f"{v:spec}"` and `"{:spec}".format(v)` are ONE function
   because they are one language: the mini-language of PEP 3101, spelled

       [[fill]align][sign][#][0][width][grouping][.precision][type]

   Written out here rather than handed to `printf` because three parts of it
   have no printf equivalent -- `^` centring, `,` grouping, and the `=` align
   that puts padding between a sign and its digits -- and because a spec is
   USER INPUT, so translating it into a printf format string would be a way
   for a program to hand `%n` to the C library. */

typedef struct {
    char fill, align, sign, type, group;
    /* PEP 682's `z`: a negative zero formats as a POSITIVE one. It sits
       between the sign and the `#`, and it is about the VALUE rather than
       about the padding, which is why it is a flag of its own. */
    int alt, zero, width, precision, has_precision, coerce_zero;
} apy_spec;

static int apy_spec_parse(const char *p, int64_t n, apy_spec *out) {
    int64_t i = 0;
    out->fill = ' '; out->align = 0; out->sign = 0; out->type = 0;
    out->group = 0; out->alt = 0; out->zero = 0; out->width = 0;
    out->precision = 0; out->has_precision = 0; out->coerce_zero = 0;
    /* FILL is only a fill when an align follows it, which is why position 1 is
       examined before position 0: in `{:<5}` the `<` is the align and in
       `{:*<5}` the `*` is the fill. */
    if (n >= 2 && (p[1] == '<' || p[1] == '>' || p[1] == '^' || p[1] == '=')) {
        out->fill = p[0]; out->align = p[1]; i = 2;
    } else if (n >= 1 && (p[0] == '<' || p[0] == '>' || p[0] == '^'
                          || p[0] == '=')) {
        out->align = p[0]; i = 1;
    }
    if (i < n && (p[i] == '+' || p[i] == '-' || p[i] == ' ')) out->sign = p[i++];
    /* PEP 682, between the sign and the `#`. */
    if (i < n && p[i] == 'z') { out->coerce_zero = 1; i++; }
    if (i < n && p[i] == '#') { out->alt = 1; i++; }
    if (i < n && p[i] == '0') {
        /* A leading zero means `0=` -- padding between the sign and the
           digits -- unless an explicit align already said otherwise. */
        out->zero = 1;
        if (!out->align) { out->align = '='; out->fill = '0'; }
        i++;
    }
    while (i < n && p[i] >= '0' && p[i] <= '9')
        out->width = out->width * 10 + (p[i++] - '0');
    if (i < n && (p[i] == ',' || p[i] == '_')) out->group = p[i++];
    if (i < n && p[i] == '.') {
        i++;
        out->has_precision = 1;
        while (i < n && p[i] >= '0' && p[i] <= '9')
            out->precision = out->precision * 10 + (p[i++] - '0');
    }
    if (i < n) out->type = p[i++];
    return i == n;
}

/* Insert `group` every three digits of `body`, from the right, in place. The
   caller owns a buffer with room; a spec wide enough to overflow it is
   refused rather than truncated. */
static int64_t apy_group_digits(char *body, int64_t n, char group) {
    char tmp[160];
    int64_t out = 0, i;
    if (n > 120) return n;
    for (i = 0; i < n; i++) {
        if (i && (n - i) % 3 == 0) tmp[out++] = group;
        tmp[out++] = body[i];
    }
    memcpy(body, tmp, (size_t)out);
    return out;
}

/* Pad `body` to the spec's width under its align, and hand back a str.

   `=` splits: the sign stays at the front and the fill goes between it and the
   digits, which is what makes `{:08.2f}` of -1.5 come out `-0001.50` and not
   `000-1.50`. */
static apy_value apy_spec_pad(const char *body, int64_t n, const apy_spec *sp,
                         int numeric) {
    int64_t width = sp->width, pad, left, i, out = 0, signlen = 0;
    char align = sp->align;
    char *buf;
    if (!align) align = numeric ? '>' : '<';
    if (width <= n) return apy_str_copy(body, n);
    pad = width - n;
    buf = (char *)malloc((size_t)width + 1);
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    if (align == '=') {
        if (n && (body[0] == '-' || body[0] == '+' || body[0] == ' '))
            signlen = 1;
        memcpy(buf, body, (size_t)signlen);
        out = signlen;
        for (i = 0; i < pad; i++) buf[out++] = sp->fill;
        memcpy(buf + out, body + signlen, (size_t)(n - signlen));
        out += n - signlen;
    } else if (align == '>') {
        for (i = 0; i < pad; i++) buf[out++] = sp->fill;
        memcpy(buf + out, body, (size_t)n);
        out += n;
    } else if (align == '^') {
        left = pad / 2;
        for (i = 0; i < left; i++) buf[out++] = sp->fill;
        memcpy(buf + out, body, (size_t)n);
        out += n;
        for (i = 0; i < pad - left; i++) buf[out++] = sp->fill;
    } else {
        memcpy(buf, body, (size_t)n);
        out = n;
        for (i = 0; i < pad; i++) buf[out++] = sp->fill;
    }
    buf[out] = 0;
    return apy_str_take(buf, out);
}

/* An unsigned integer in `base`, most significant digit first. */
static int64_t apy_int_digits(char *buf, uint64_t mag, int base, int upper) {
    const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    char rev[80];
    int64_t n = 0, i;
    if (!mag) rev[n++] = '0';
    while (mag) {
        rev[n++] = digits[mag % (unsigned)base];
        mag /= (unsigned)base;
    }
    for (i = 0; i < n; i++) buf[i] = rev[n - 1 - i];
    return n;
}

static apy_value apy_bad_code(char code, apy_value v) {
    char c[2];
    c[0] = code ? code : 's';
    c[1] = 0;
    return apy_fail2("ValueError",
                     "Unknown format code '%s' for object of type '%s'",
                     c, apy_kind_name(v));
}

APY_API apy_value apy_format(apy_value v, apy_value spec) {
    apy_spec sp;
    const char *sptr = O(spec)->kind == APY_STR_K ? APY_CSTR(spec) : "";
    int64_t slen = O(spec)->kind == APY_STR_K ? O(spec)->v.s.n : 0;
    char body[600];
    int64_t n = 0;

    /* A user object formats ITSELF, given the spec, and is asked BEFORE the
       spec is parsed and before the empty-spec shortcut: `f"{obj}"` is
       `format(obj, "")`, which calls `__format__("")` -- not `str(obj)`, and
       a class defining both can tell the difference. */
    if (O(v)->kind == APY_INST_K) {
        apy_value r = apy_method1(v, "__format__", spec);
        if (r || apy_error_occurred()) return r;
    }
    /* An EMPTY spec is `str(v)` and nothing else. */
    if (!slen) return apy_str(v);
    if (!apy_spec_parse(sptr, slen, &sp))
        return apy_fail2("ValueError", "Invalid format specifier '%s'%s",
                         sptr, "");

    if (sp.type == 's' || (!sp.type && O(v)->kind == APY_STR_K)) {
        apy_value s = apy_str(v);
        int64_t len;
        if (!s) return 0;
        len = O(s)->v.s.n;
        if (sp.has_precision && sp.precision < len) len = sp.precision;
        return apy_spec_pad(APY_CSTR(s), len, &sp, 0);
    }
    if (sp.type == 'b' || sp.type == 'o' || sp.type == 'x' || sp.type == 'X'
        || sp.type == 'd' || sp.type == 'n' || sp.type == 'c') {
        int64_t iv;
        uint64_t mag;
        int base = 10, upper = 0;
        if (!apy_is_int_like(v) && !apy_is_big(v)) return apy_bad_code(sp.type, v);
        if (apy_is_big(v)) {
            /* A big integer has no int64 to divide down; its decimal text is
               what there is, so only the plain forms are offered. */
            apy_value s = apy_str(v);
            if (!s) return 0;
            return apy_spec_pad(APY_CSTR(s), O(s)->v.s.n, &sp, 1);
        }
        iv = O(v)->v.i;
        if (sp.type == 'c') {
            /* THE CHARACTER, encoded -- `format(255, 'c')` is `chr(255)`, and
               a str is stored as UTF-8, so that is two bytes and not one.
               Writing the low byte raw produced a string that compared
               unequal to `chr(255)` and was not valid UTF-8 either. */
            apy_value ch = apy_chr(v);
            if (!ch) return 0;
            return apy_spec_pad(APY_CSTR(ch), O(ch)->v.s.n, &sp, 0);
        }
        if (sp.type == 'b') base = 2;
        else if (sp.type == 'o') base = 8;
        else if (sp.type == 'x') base = 16;
        else if (sp.type == 'X') { base = 16; upper = 1; }
        mag = iv < 0 ? (uint64_t)(-(iv + 1)) + 1u : (uint64_t)iv;
        if (iv < 0) body[n++] = '-';
        else if (sp.sign == '+') body[n++] = '+';
        else if (sp.sign == ' ') body[n++] = ' ';
        if (sp.alt && base != 10) {
            body[n++] = '0';
            body[n++] = sp.type;
        }
        {
            int64_t d = apy_int_digits(body + n, mag, base, upper);
            if (sp.group) d = apy_group_digits(body + n, d, sp.group);
            n += d;
        }
        body[n] = 0;
        return apy_spec_pad(body, n, &sp, 1);
    }
    {
        /* The float types. `printf` is the right decimal conversion -- the
           same one `repr` uses -- so only the sign, grouping and padding are
           added around it. */
        double d;
        char tmp[400];
        int prec = sp.has_precision ? sp.precision : 6;
        char type = sp.type;
        const char *src;
        int64_t len;
        if (!apy_is_num(v)) return apy_bad_code(type, v);
        d = apy_as_float(v);
        if (type == '%') { d *= 100.0; type = 'f'; }
        /* PEP 682: `z` turns a negative zero into a positive one -- and it is
           about the ROUNDED value, so `format(-0.001, 'z.1f')` is `0.0` too.
           Applied after the scaling above and before the conversion below,
           which is the only point at which both are true. */
        if (sp.coerce_zero) {
            double scale = 1.0;
            int k;
            for (k = 0; k < prec && k < 17; k++) scale *= 10.0;
            /* `signbit`, not `d < 0.0`: NEGATIVE ZERO is not less than
               zero, and it is the value the flag exists for. */
            if (signbit(d) && fabs(d) * scale < 0.5) d = 0.0;
        }
        if (!type && sp.has_precision) {
            /* A PRECISION WITH NO TYPE is `g`: `format(3.14159, '.3')` is
               '3.14', three SIGNIFICANT digits, not three decimal places and
               not the unrounded number. Without this the precision was
               dropped and the whole value printed. */
            snprintf(tmp, sizeof tmp, "%.*g", prec ? prec : 1, d);
        } else if (!type) {
            /* No type and no precision: `str(v)`, the shortest round-tripping
               form, and NOT `%g` -- `f"{0.1:>8}"` must still say `0.1`. */
            apy_value s = apy_str(v);
            if (!s) return 0;
            if (O(s)->v.s.n >= (int64_t)sizeof tmp)
                return apy_spec_pad(APY_CSTR(s), O(s)->v.s.n, &sp, 1);
            memcpy(tmp, APY_CSTR(s), (size_t)O(s)->v.s.n);
            tmp[O(s)->v.s.n] = 0;
        } else if (type == 'f' || type == 'F') {
            snprintf(tmp, sizeof tmp, "%.*f", prec, d);
        } else if (type == 'e' || type == 'E') {
            snprintf(tmp, sizeof tmp, type == 'e' ? "%.*e" : "%.*E", prec, d);
        } else if (type == 'g' || type == 'G') {
            snprintf(tmp, sizeof tmp, type == 'g' ? "%.*g" : "%.*G",
                     prec ? prec : 1, d);
        } else {
            return apy_bad_code(type, v);
        }
        src = tmp;
        len = (int64_t)strlen(tmp);
        n = 0;
        if (src[0] == '-') { body[n++] = '-'; src++; len--; }
        else if (sp.sign == '+') body[n++] = '+';
        else if (sp.sign == ' ') body[n++] = ' ';
        memcpy(body + n, src, (size_t)len);
        if (sp.group) {
            /* Group the INTEGER part only: the separator belongs to the left
               of the point, and grouping the fraction would produce a number
               that does not read back. */
            int64_t head = 0, grouped;
            char tail[400];
            while (head < len && body[n + head] != '.' && body[n + head] != 'e'
                   && body[n + head] != 'E') head++;
            memcpy(tail, body + n + head, (size_t)(len - head));
            grouped = apy_group_digits(body + n, head, sp.group);
            memcpy(body + n + grouped, tail, (size_t)(len - head));
            len = grouped + (len - head);
        }
        n += len;
        if (sp.type == '%') body[n++] = '%';
        body[n] = 0;
        return apy_spec_pad(body, n, &sp, 1);
    }
}

/* `"{} {:>5} {name!r}".format(...)` -- the OTHER half of PEP 3101: the
   replacement-field syntax around the spec `apy_format` reads.

   Auto-numbering and explicit numbering cannot be mixed, and CPython says so
   rather than guessing; that check is what `auto` below is for. A nested spec
   -- `{:{width}}` -- is one level deep, which is all CPython allows too. */
/* The AUTO-NUMBERING COUNTER IS SHARED with any nested spec: in
   `"{:>{}}".format('q', 5)` the field takes `'q'` and the `{}` inside the spec
   takes `5`. A recursive call with its own counter took `'q'` twice and then
   reported it as a bad format code -- so the state travels by pointer. */
static apy_value apy_format_at(apy_value fmt, apy_value args, apy_value kw,
                               int64_t *auto_at, int *auto_used,
                               int *explicit_used) {
    const char *p;
    int64_t n, i = 0, out_cap, out_n = 0;
    char *out;
    if (O(fmt)->kind != APY_STR_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'format'%s",
                         apy_kind_name(fmt), "");
    p = APY_CSTR(fmt);
    n = O(fmt)->v.s.n;
    out_cap = n + 64;
    out = (char *)malloc((size_t)out_cap + 1);
    if (!out) { fputs("asmpython: out of memory\n", stderr); exit(1); }

    while (i < n) {
        if (p[i] == '{' && i + 1 < n && p[i + 1] == '{') {
            out[out_n++] = '{'; i += 2; continue;
        }
        if (p[i] == '}' && i + 1 < n && p[i + 1] == '}') {
            out[out_n++] = '}'; i += 2; continue;
        }
        if (p[i] != '{') {
            if (out_n + 1 >= out_cap) {
                out_cap *= 2;
                out = (char *)realloc(out, (size_t)out_cap + 1);
            }
            out[out_n++] = p[i++];
            continue;
        }
        {
            /* One replacement field: `{field!conv:spec}`. */
            int64_t start = ++i, colon = -1, bang = -1, depth = 0;
            char field[128], conv = 0;
            apy_value value, spec, shown;
            while (i < n && (p[i] != '}' || depth)) {
                if (p[i] == '{') depth++;
                else if (p[i] == '}') depth--;
                else if (p[i] == ':' && colon < 0 && !depth) colon = i;
                else if (p[i] == '!' && bang < 0 && colon < 0
                         && i + 1 < n && p[i + 1] != '=') bang = i;
                i++;
            }
            if (i >= n) {
                free(out);
                return apy_fail("ValueError",
                                "Single '{' encountered in format string");
            }
            {
                int64_t fend = colon >= 0 ? colon : (bang >= 0 ? bang : i);
                int64_t flen = fend - start;
                int64_t blen;
                if (flen >= (int64_t)sizeof field) flen = sizeof field - 1;
                memcpy(field, p + start, (size_t)flen);
                field[flen] = 0;
                /* `{x[0]}` and `{a.real}`: the NAME stops at the first `.` or
                   `[`, and what follows is a chain of accessors applied to
                   whatever the name resolved to. Treating the whole thing as
                   one keyword looked for an argument called `x[0]`. */
                for (blen = 0; blen < flen; blen++)
                    if (field[blen] == '.' || field[blen] == '[') break;
                if (!flen) {
                    if (*explicit_used) {
                        free(out);
                        return apy_fail("ValueError",
                                        "cannot switch from manual field "
                                        "specification to automatic field "
                                        "numbering");
                    }
                    *auto_used = 1;
                    value = *auto_at < O(args)->v.q.n
                        ? O(args)->v.q.items[(*auto_at)++] : 0;
                    if (!value) {
                        free(out);
                        return apy_fail("IndexError",
                                        "Replacement index out of range for "
                                        "positional args tuple");
                    }
                } else if (field[0] >= '0' && field[0] <= '9') {
                    int64_t at = 0, k;
                    if (*auto_used) {
                        free(out);
                        return apy_fail("ValueError",
                                        "cannot switch from automatic field "
                                        "numbering to manual field "
                                        "specification");
                    }
                    *explicit_used = 1;
                    for (k = 0; field[k] >= '0' && field[k] <= '9'; k++)
                        at = at * 10 + (field[k] - '0');
                    if (at >= O(args)->v.q.n) {
                        free(out);
                        return apy_fail("IndexError",
                                        "Replacement index out of range for "
                                        "positional args tuple");
                    }
                    value = O(args)->v.q.items[at];
                } else {
                    char base[64];
                    apy_value key;
                    int64_t at;
                    int64_t n2 = blen < (int64_t)sizeof base - 1
                        ? blen : (int64_t)sizeof base - 1;
                    memcpy(base, field, (size_t)n2);
                    base[n2] = 0;
                    key = apy_lit(base);
                    at = apy_dict_find(kw, key);
                    if (at < 0) {
                        free(out);
                        return apy_fail2("KeyError", "'%s'%s", base, "");
                    }
                    value = O(kw)->v.d.vals[at];
                }
                /* THE ACCESSORS, left to right. `{a.b[0].c}` is ordinary
                   Python written inside a format field, and each step is the
                   operation it looks like. */
                {
                    int64_t k = blen;
                    while (k < flen && value) {
                        char part[64];
                        int64_t j = 0;
                        if (field[k] == '.') {
                            k++;
                            while (k < flen && field[k] != '.'
                                   && field[k] != '['
                                   && j < (int64_t)sizeof part - 1)
                                part[j++] = field[k++];
                            part[j] = 0;
                            value = apy_getattr(value, apy_lit(part));
                        } else if (field[k] == '[') {
                            int all_digits = 1;
                            k++;
                            while (k < flen && field[k] != ']'
                                   && j < (int64_t)sizeof part - 1) {
                                if (field[k] < '0' || field[k] > '9')
                                    all_digits = 0;
                                part[j++] = field[k++];
                            }
                            part[j] = 0;
                            if (k < flen && field[k] == ']') k++;
                            /* AN ALL-DIGIT KEY IS AN INDEX, as CPython reads
                               it -- `{x[0]}` indexes a list, and a mapping
                               with a numeric string key needs the quotes a
                               format field cannot carry. */
                            value = apy_getitem(
                                value, j && all_digits
                                    ? apy_from_int(strtoll(part, 0, 10))
                                    : apy_lit(part));
                        } else {
                            break;
                        }
                    }
                    if (!value) { free(out); return 0; }
                }
            }
            if (bang >= 0) conv = p[bang + 1];
            if (colon >= 0) {
                /* A NESTED spec -- `{:{width}}` -- is itself formatted first,
                   with the same arguments. One level, which is CPython's
                   limit too. */
                apy_value inner = apy_str_copy(p + colon + 1,
                                               i - colon - 1);
                spec = memchr(p + colon + 1, '{', (size_t)(i - colon - 1))
                    ? apy_format_at(inner, args, kw, auto_at, auto_used,
                                    explicit_used)
                    : inner;
                if (!spec) { free(out); return 0; }
            } else {
                spec = apy_lit("");
            }
            /* `!a` WAS FOLDED INTO `!r` UNTIL THE ESCAPING EXISTED, and
               the two agree for every ASCII value -- so `"{!a}".format(x)`
               answered `'cafÃ©'`'s own bytes where CPython writes
               `'café'`, and nothing noticed until a case used a
               non-ASCII one. */
            if (conv == 'a') value = apy_ascii(value);
            else if (conv == 'r') value = apy_repr(value);
            else if (conv == 's') value = apy_str(value);
            if (!value) { free(out); return 0; }
            shown = apy_format(value, spec);
            if (!shown) { free(out); return 0; }
            while (out_n + O(shown)->v.s.n >= out_cap) {
                out_cap = out_cap * 2 + O(shown)->v.s.n;
                out = (char *)realloc(out, (size_t)out_cap + 1);
            }
            memcpy(out + out_n, APY_CSTR(shown), (size_t)O(shown)->v.s.n);
            out_n += O(shown)->v.s.n;
            i++;                        /* past the '}' */
        }
    }
    out[out_n] = 0;
    return apy_str_take(out, out_n);
}

/* `"%d %s" % (1, "a")` -- printf-style formatting.
   TRANSLATED INTO THE MINI-LANGUAGE, not reimplemented. `%05.2f` and
   `{:05.2f}` mean the same thing down to the zero padding and the rounding,
   so the padding, the precision and every presentation type are read from
   `apy_format` rather than written a second time here. What this function
   owns is the printf SPELLING: which flags mean what, where the arguments
   come from, and the two conversions (`%r`, `%s`) the mini-language has no
   type character for.

   WHAT IT DOES NOT DO: the mapping form, `"%(name)s" % {...}`. A dict on the
   right is currently one argument like any other, which is right for `%s` and
   wrong for the mapping form -- so that spelling is refused below rather than
   quietly formatting the dict. */
static apy_value apy_str_percent(apy_value fmt, apy_value right) {
    const char *p = APY_CSTR(fmt);
    int64_t n = O(fmt)->v.s.n, i = 0, out_cap = n + 64, out_n = 0, at = 0;
    int64_t supplied;
    char *out;
    /* A TUPLE ON THE RIGHT IS THE ARGUMENT LIST; anything else is one
       argument. That is the whole of the rule, and it is why `"%s" % (1, 2)`
       is an error while `"%s" % [1, 2]` prints the list. */
    int many = O(right)->kind == APY_TUPLE_K;
    /* A MAPPING ON THE RIGHT supplies NAMED fields only -- `"%(x)s" % {...}`
       -- and nothing is consumed positionally, so an unused entry is not an
       error. `"ab" % {"ab": 1}` is just `"ab"`. */
    int mapping = O(right)->kind == APY_DICT_K;
    supplied = many ? O(right)->v.q.n : 1;

    out = (char *)malloc((size_t)out_cap + 1);
    if (!out) { fputs("asmpython: out of memory\n", stderr); exit(1); }

    while (i < n) {
        char spec[64];
        int64_t sn = 0;
        apy_value value, shown, named = 0;
        char conv;
        int minus = 0, zero = 0;

        if (p[i] != '%') {
            if (out_n + 1 >= out_cap) {
                out_cap = out_cap * 2 + 8;
                out = (char *)realloc(out, (size_t)out_cap + 1);
            }
            out[out_n++] = p[i++];
            continue;
        }
        i++;
        if (i < n && p[i] == '%') {      /* `%%` is a literal percent */
            if (out_n + 1 >= out_cap) {
                out_cap = out_cap * 2 + 8;
                out = (char *)realloc(out, (size_t)out_cap + 1);
            }
            out[out_n++] = '%'; i++; continue;
        }
        if (i < n && p[i] == '(') {
            /* `%(name)s` -- the MAPPING FORM. The key runs to the matching
               `)`; what follows is an ordinary spec. */
            char key[64];
            int64_t j = 0;
            apy_value found;
            if (!mapping) {
                free(out);
                return apy_fail("TypeError", "format requires a mapping");
            }
            i++;
            while (i < n && p[i] != ')' && j < (int64_t)sizeof key - 1)
                key[j++] = p[i++];
            key[j] = 0;
            if (i < n && p[i] == ')') i++;
            found = apy_dict_get_or(right, apy_lit(key), 0);
            if (!found) {
                free(out);
                return apy_fail2("KeyError", "'%s'%s", key, "");
            }
            named = found;
        }
        /* THE FLAGS ARE COLLECTED, NOT EMITTED, because two of them depend
           on the conversion that has not been read yet -- and because the
           mini-language fixes an order (align, sign, `#`, `0`, width) that
           printf does not. Emitting each flag where it was read produced
           `+<` for `%-+d`, which is not a spec at all. */
        int plus = 0, space = 0, hash = 0, is_text;
        int64_t wid_at, wid_n = 0, prec_at, prec_n = 0;
        while (i < n && (p[i] == '-' || p[i] == '+' || p[i] == ' '
                         || p[i] == '0' || p[i] == '#')) {
            if (p[i] == '-') minus = 1;
            else if (p[i] == '0') zero = 1;
            else if (p[i] == '+') plus = 1;
            else if (p[i] == ' ') space = 1;
            else hash = 1;
            i++;
        }
        wid_at = i;
        while (i < n && p[i] >= '0' && p[i] <= '9') { i++; wid_n++; }
        prec_at = i;
        if (i < n && p[i] == '.') {
            i++; prec_n++;
            while (i < n && p[i] >= '0' && p[i] <= '9') { i++; prec_n++; }
        }
        if (i >= n) {
            free(out);
            return apy_fail("ValueError", "incomplete format");
        }
        conv = p[i++];
        is_text = conv == 's' || conv == 'r' || conv == 'a' || conv == 'c'
            || conv == 'b';
        /* PRINTF RIGHT-ALIGNS A STRING; the mini-language left-aligns one.
           The only difference between the two languages that is not a
           spelling, and `"%5s" % "ab"` is where it shows. */
        if (minus) spec[sn++] = '<';
        else if (is_text) spec[sn++] = '>';
        if (plus) spec[sn++] = '+';
        else if (space) spec[sn++] = ' ';
        if (hash) spec[sn++] = '#';
        /* A zero fill on TEXT is not a thing printf does either. */
        if (zero && !minus && !is_text) spec[sn++] = '0';
        { int64_t k;
          for (k = 0; k < wid_n; k++) spec[sn++] = p[wid_at + k];
          for (k = 0; k < prec_n; k++) spec[sn++] = p[prec_at + k]; }
        if (!named) {
            if (at >= supplied) {
                free(out);
                return apy_fail("TypeError",
                                "not enough arguments for format string");
            }
            value = many ? O(right)->v.q.items[at] : right;
            at++;
        } else {
            value = named;
        }

        /* `%s` and `%r` have no mini-language type character: the value
           becomes text FIRST and the spec then pads that text. */
        if (conv == 's' || conv == 'b') {
            if (O(fmt)->kind == APY_BYTES_K
                && O(value)->kind == APY_BYTES_K) {
                /* `b"%s" % b"ab"` inserts THE BYTES, not their repr -- and
                   `%b` is PEP 461's spelling of the same thing. Re-tagged as
                   a str so the padding below stays one implementation: the
                   two kinds share a layout, and the result is stamped back
                   to bytes at the end. */
                value = apy_from_bytes(
                    (apy_value)(uintptr_t)O(value)->v.s.p, O(value)->v.s.n);
            } else {
                value = apy_str(value);
            }
        }
        else if (conv == 'r') { value = apy_repr(value); }
        else if (conv == 'a') { value = apy_ascii(value); }
        else if (conv == 'c') {
            value = apy_is_int_like(value) ? apy_chr(value) : apy_str(value);
        } else if (conv == 'i' || conv == 'u') {
            /* Both are spelled `d` in the mini-language, and `%u` has meant
               `%d` since Python 2. */
            spec[sn++] = 'd';
        } else {
            spec[sn++] = conv;
        }
        if (!value) { free(out); return 0; }
        spec[sn] = 0;
        shown = apy_format(value, apy_str_copy(spec, sn));
        if (!shown) { free(out); return 0; }
        while (out_n + O(shown)->v.s.n >= out_cap) {
            out_cap = out_cap * 2 + O(shown)->v.s.n;
            out = (char *)realloc(out, (size_t)out_cap + 1);
        }
        memcpy(out + out_n, APY_CSTR(shown), (size_t)O(shown)->v.s.n);
        out_n += O(shown)->v.s.n;
    }
    /* A MAPPING has nothing to leave unconsumed: its entries are reached by
       name, and an unused one is ordinary. */
    if (!mapping && at < supplied) {
        free(out);
        return apy_fail("TypeError",
                        "not all arguments converted during string "
                        "formatting");
    }
    out[out_n] = 0;
    { apy_value r = apy_str_take(out, out_n);
      /* `b"%d" % 3` IS BYTES. The whole of the difference is the kind: the
         format string's own bytes are ASCII either way, and every conversion
         above produced text. */
      if (O(fmt)->kind == APY_BYTES_K) O(r)->kind = APY_BYTES_K;
      return r; }
}

/* Re-tag a str METHOD'S RESULT to match its receiver.
   `b"a b".split()` answers a list of BYTES, not of str, and `b.find(...)`
   answers an int that must be left alone -- so this converts str results and
   the str elements of a sequence result, and nothing else. One place, at the
   call site, rather than a change to each of the fifty-odd methods. */
APY_API apy_value apy_str_like(apy_value recv, apy_value out) {
    if (!out || O(recv)->kind != APY_BYTES_K) return out;
    if (O(out)->kind == APY_STR_K) {
        apy_value made = apy_str_copy(O(out)->v.s.p, O(out)->v.s.n);
        O(made)->kind = APY_BYTES_K;
        return made;
    }
    if (apy_is_seq(out)) {
        int64_t i;
        for (i = 0; i < O(out)->v.q.n; i++)
            O(out)->v.q.items[i] = apy_str_like(recv, O(out)->v.q.items[i]);
    }
    return out;
}

APY_API apy_value apy_str_format(apy_value fmt, apy_value args, apy_value kw) {
    int64_t auto_at = 0;
    int auto_used = 0, explicit_used = 0;
    return apy_format_at(fmt, args, kw, &auto_at, &auto_used, &explicit_used);
}

"""
