"""The object runtime, in C: codecs.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * codecs
"""

C = r"""/* --- codecs ------------------------------------------------------------- */
/* Text is held as UTF-8, so `encode("utf-8")` is a re-tag and every other
   encoding is a real conversion. Both directions go through CODE POINTS: the
   internal form is decoded to them and the target built from them, which is
   one shared middle rather than a matrix of pairs. */

/* Which codec a name means. Canonicalised the way CPython does -- case and
   the `-`/`_` distinction do not matter -- so `UTF_8` and `utf-8` are one. */
enum { APY_ENC_UTF8 = 0, APY_ENC_ASCII, APY_ENC_LATIN1,
       APY_ENC_UTF16, APY_ENC_UTF16LE, APY_ENC_UTF16BE,
       APY_ENC_UTF32, APY_ENC_UTF32LE, APY_ENC_UTF32BE, APY_ENC_UNKNOWN };

static int apy_codec_of(apy_value name) {
    char buf[32];
    int64_t i, n;
    const char *p;
    if (!name || O(name)->kind != APY_STR_K) return APY_ENC_UTF8;
    p = O(name)->v.s.p;
    n = O(name)->v.s.n;
    if (n >= (int64_t)sizeof buf) return APY_ENC_UNKNOWN;
    for (i = 0; i < n; i++) {
        char c = p[i];
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
        buf[i] = (c == '_') ? '-' : c;
    }
    buf[n] = 0;
    if (!strcmp(buf, "utf-8") || !strcmp(buf, "utf8")
        || !strcmp(buf, "u8")) return APY_ENC_UTF8;
    if (!strcmp(buf, "ascii") || !strcmp(buf, "us-ascii")
        || !strcmp(buf, "646")) return APY_ENC_ASCII;
    if (!strcmp(buf, "latin-1") || !strcmp(buf, "latin1")
        || !strcmp(buf, "iso-8859-1") || !strcmp(buf, "l1")
        || !strcmp(buf, "8859")) return APY_ENC_LATIN1;
    if (!strcmp(buf, "utf-16") || !strcmp(buf, "utf16")) return APY_ENC_UTF16;
    if (!strcmp(buf, "utf-16-le") || !strcmp(buf, "utf-16le"))
        return APY_ENC_UTF16LE;
    if (!strcmp(buf, "utf-16-be") || !strcmp(buf, "utf-16be"))
        return APY_ENC_UTF16BE;
    if (!strcmp(buf, "utf-32") || !strcmp(buf, "utf32")) return APY_ENC_UTF32;
    if (!strcmp(buf, "utf-32-le") || !strcmp(buf, "utf-32le"))
        return APY_ENC_UTF32LE;
    if (!strcmp(buf, "utf-32-be") || !strcmp(buf, "utf-32be"))
        return APY_ENC_UTF32BE;
    return APY_ENC_UNKNOWN;
}

/* The error handler, as a small code. Only the three that can be honoured
   without a callback registry. */
enum { APY_ERR_STRICT = 0, APY_ERR_REPLACE, APY_ERR_IGNORE };

static int apy_errors_of(apy_value name) {
    if (!name || O(name)->kind != APY_STR_K) return APY_ERR_STRICT;
    if (!strcmp(APY_CSTR(name), "replace")) return APY_ERR_REPLACE;
    if (!strcmp(APY_CSTR(name), "ignore")) return APY_ERR_IGNORE;
    return APY_ERR_STRICT;
}

/* One code point out of UTF-8. Answers how many bytes it consumed, or 0 for
   a malformed sequence -- which is the whole of the validation the strict
   handler needs. */
APY_API int64_t apy_utf8_step_of(apy_value pv, int64_t n, int64_t i,
                             apy_value outv) {
    const unsigned char *p = (const unsigned char *)pv;
    uint32_t *out = (uint32_t *)outv;
    unsigned char c = p[i];
    if (c < 0x80) { *out = c; return 1; }
    if ((c & 0xE0) == 0xC0 && i + 1 < n && (p[i+1] & 0xC0) == 0x80) {
        *out = (uint32_t)((c & 0x1F) << 6) | (uint32_t)(p[i+1] & 0x3F);
        return *out >= 0x80 ? 2 : 0;      /* an overlong form is malformed */
    }
    if ((c & 0xF0) == 0xE0 && i + 2 < n && (p[i+1] & 0xC0) == 0x80
        && (p[i+2] & 0xC0) == 0x80) {
        *out = (uint32_t)((c & 0x0F) << 12)
             | (uint32_t)((p[i+1] & 0x3F) << 6) | (uint32_t)(p[i+2] & 0x3F);
        return *out >= 0x800 ? 3 : 0;
    }
    if ((c & 0xF8) == 0xF0 && i + 3 < n && (p[i+1] & 0xC0) == 0x80
        && (p[i+2] & 0xC0) == 0x80 && (p[i+3] & 0xC0) == 0x80) {
        *out = (uint32_t)((c & 0x07) << 18)
             | (uint32_t)((p[i+1] & 0x3F) << 12)
             | (uint32_t)((p[i+2] & 0x3F) << 6) | (uint32_t)(p[i+3] & 0x3F);
        return (*out >= 0x10000 && *out <= 0x10FFFF) ? 4 : 0;
    }
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int64_t apy_utf8_step(const unsigned char *p, int64_t n,
                             int64_t i, uint32_t *out) {
    return apy_utf8_step_of((apy_value)(uintptr_t)p, n, i,
                            (apy_value)(uintptr_t)out);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

/* One code point INTO UTF-8. Answers how many bytes it wrote. */
static int apy_utf8_put(char *out, uint32_t cp) {
    if (cp < 0x80) { out[0] = (char)cp; return 1; }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

APY_API apy_value apy_str_encode(apy_value s, apy_value encoding,
                                 apy_value errors) {
    int codec, handler;
    const unsigned char *p;
    int64_t n, i, at = 0;
    char *buf;
    apy_value out;
    if (O(s)->kind != APY_STR_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'encode'%s",
                         apy_kind_name(s), "");
    codec = apy_codec_of(encoding);
    handler = apy_errors_of(errors);
    if (codec == APY_ENC_UNKNOWN)
        return apy_fail2("LookupError", "unknown encoding: %s%s",
                         APY_CSTR(encoding), "");
    if (codec == APY_ENC_UTF8) {
        /* ALREADY THE INTERNAL FORM: a copy, re-tagged. */
        out = apy_str_copy(O(s)->v.s.p, O(s)->v.s.n);
        O(out)->kind = APY_BYTES_K;
        return out;
    }
    p = (const unsigned char *)O(s)->v.s.p;
    n = O(s)->v.s.n;
    /* Four bytes per code point covers every target, and a code point is at
       least one byte of the source -- so `4 * n` can never be short. */
    buf = (char *)malloc((size_t)(n * 4 + 8));
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    /* THE BOM IS PART OF THE ENCODING for the unsuffixed spellings, which is
       what makes `len("a".encode("utf-16"))` 4 rather than 2. */
    if (codec == APY_ENC_UTF16) {
        buf[at++] = (char)0xFF; buf[at++] = (char)0xFE;
    } else if (codec == APY_ENC_UTF32) {
        buf[at++] = (char)0xFF; buf[at++] = (char)0xFE;
        buf[at++] = 0; buf[at++] = 0;
    }
    for (i = 0; i < n; ) {
        uint32_t cp;
        int64_t used = apy_utf8_step(p, n, i, &cp);
        if (!used) { cp = 0xFFFD; used = 1; }
        i += used;
        if (codec == APY_ENC_ASCII || codec == APY_ENC_LATIN1) {
            uint32_t limit = codec == APY_ENC_ASCII ? 0x80u : 0x100u;
            if (cp >= limit) {
                if (handler == APY_ERR_IGNORE) continue;
                if (handler == APY_ERR_REPLACE) { buf[at++] = '?'; continue; }
                free(buf);
                return apy_fail2("UnicodeEncodeError",
                                 "'%s' codec can't encode character%s",
                                 codec == APY_ENC_ASCII ? "ascii" : "latin-1",
                                 "");
            }
            buf[at++] = (char)cp;
            continue;
        }
        if (codec == APY_ENC_UTF32 || codec == APY_ENC_UTF32LE
            || codec == APY_ENC_UTF32BE) {
            int be = codec == APY_ENC_UTF32BE;
            int k;
            for (k = 0; k < 4; k++) {
                int shift = be ? (24 - 8 * k) : (8 * k);
                buf[at++] = (char)((cp >> shift) & 0xFF);
            }
            continue;
        }
        {   /* UTF-16, with a surrogate pair above the BMP. */
            int be = codec == APY_ENC_UTF16BE;
            uint32_t units[2];
            int count = 1, k;
            if (cp >= 0x10000) {
                uint32_t v = cp - 0x10000;
                units[0] = 0xD800 + (v >> 10);
                units[1] = 0xDC00 + (v & 0x3FF);
                count = 2;
            } else units[0] = cp;
            for (k = 0; k < count; k++) {
                if (be) {
                    buf[at++] = (char)((units[k] >> 8) & 0xFF);
                    buf[at++] = (char)(units[k] & 0xFF);
                } else {
                    buf[at++] = (char)(units[k] & 0xFF);
                    buf[at++] = (char)((units[k] >> 8) & 0xFF);
                }
            }
        }
    }
    out = apy_bytes_copy(buf, at);
    free(buf);
    return out;
}

APY_API apy_value apy_bytes_decode(apy_value b, apy_value encoding,
                                   apy_value errors) {
    int codec, handler;
    const unsigned char *p;
    int64_t n, i, at = 0;
    char *buf;
    if (O(b)->kind == APY_MVIEW_K) b = apy_mview_bytes(b);
    if (O(b)->kind != APY_BYTES_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'decode'%s",
                         apy_kind_name(b), "");
    codec = apy_codec_of(encoding);
    handler = apy_errors_of(errors);
    if (codec == APY_ENC_UNKNOWN)
        return apy_fail2("LookupError", "unknown encoding: %s%s",
                         APY_CSTR(encoding), "");
    p = (const unsigned char *)O(b)->v.s.p;
    n = O(b)->v.s.n;
    /* Three bytes of UTF-8 per input byte is the worst case for every codec
       here -- one latin-1 byte becomes at most two, one UTF-16 unit at most
       three -- so this cannot be short. */
    buf = (char *)malloc((size_t)(n * 3 + 8));
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    if (codec == APY_ENC_UTF8) {
        for (i = 0; i < n; ) {
            uint32_t cp;
            int64_t used = apy_utf8_step(p, n, i, &cp);
            if (!used) {
                if (handler == APY_ERR_IGNORE) { i++; continue; }
                if (handler == APY_ERR_REPLACE) {
                    at += apy_utf8_put(buf + at, 0xFFFD);
                    i++;
                    continue;
                }
                free(buf);
                return apy_fail2("UnicodeDecodeError",
                                 "'utf-8' codec can't decode byte%s%s",
                                 "", "");
            }
            memcpy(buf + at, p + i, (size_t)used);
            at += used;
            i += used;
        }
        return apy_str_take(buf, at);
    }
    if (codec == APY_ENC_LATIN1 || codec == APY_ENC_ASCII) {
        for (i = 0; i < n; i++) {
            if (codec == APY_ENC_ASCII && p[i] >= 0x80) {
                if (handler == APY_ERR_IGNORE) continue;
                if (handler == APY_ERR_REPLACE) {
                    at += apy_utf8_put(buf + at, 0xFFFD);
                    continue;
                }
                free(buf);
                return apy_fail2("UnicodeDecodeError",
                                 "'ascii' codec can't decode byte%s%s",
                                 "", "");
            }
            /* EVERY BYTE IS A CODE POINT in latin-1, which is what makes it
               the round-trip encoding for arbitrary octets. */
            at += apy_utf8_put(buf + at, p[i]);
        }
        return apy_str_take(buf, at);
    }
    {   /* UTF-16 and UTF-32, with the BOM consumed where one is allowed. */
        int wide = (codec == APY_ENC_UTF32 || codec == APY_ENC_UTF32LE
                    || codec == APY_ENC_UTF32BE) ? 4 : 2;
        int be = (codec == APY_ENC_UTF16BE || codec == APY_ENC_UTF32BE);
        i = 0;
        if (codec == APY_ENC_UTF16 && n >= 2) {
            if (p[0] == 0xFF && p[1] == 0xFE) { be = 0; i = 2; }
            else if (p[0] == 0xFE && p[1] == 0xFF) { be = 1; i = 2; }
        } else if (codec == APY_ENC_UTF32 && n >= 4) {
            if (p[0] == 0xFF && p[1] == 0xFE && !p[2] && !p[3]) {
                be = 0; i = 4;
            } else if (!p[0] && !p[1] && p[2] == 0xFE && p[3] == 0xFF) {
                be = 1; i = 4;
            }
        }
        for (; i + wide <= n; i += wide) {
            uint32_t cp = 0;
            int k;
            for (k = 0; k < wide; k++) {
                int shift = be ? (8 * (wide - 1 - k)) : (8 * k);
                cp |= (uint32_t)p[i + k] << shift;
            }
            /* A SURROGATE PAIR IS ONE CHARACTER. Only UTF-16 has them, and a
               lone half is as malformed as a truncated UTF-8 sequence. */
            if (wide == 2 && cp >= 0xD800 && cp <= 0xDBFF && i + 4 <= n) {
                uint32_t low = 0;
                for (k = 0; k < 2; k++) {
                    int shift = be ? (8 * (1 - k)) : (8 * k);
                    low |= (uint32_t)p[i + 2 + k] << shift;
                }
                if (low >= 0xDC00 && low <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                    i += 2;
                }
            }
            at += apy_utf8_put(buf + at, cp);
        }
        return apy_str_take(buf, at);
    }
}

/* `b.hex()` and `b.hex(sep)` -- the octets as lowercase hex pairs. The
   separator form is `bytes.hex(':')`, which is what makes a fingerprint
   readable and is the only reason the argument exists. */
APY_API apy_value apy_bytes_hex(apy_value b, apy_value sep) {
    static const char *D = "0123456789abcdef";
    int64_t n, i, out = 0;
    char *buf;
    char s = 0;
    if (O(b)->kind != APY_BYTES_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'hex'%s",
                         apy_kind_name(b), "");
    if (O(sep)->kind == APY_STR_K && O(sep)->v.s.n == 1) s = APY_CSTR(sep)[0];
    n = O(b)->v.s.n;
    buf = (char *)malloc((size_t)(n * 3 + 2));
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)O(b)->v.s.p[i];
        if (s && i) buf[out++] = s;
        buf[out++] = D[c >> 4];
        buf[out++] = D[c & 15];
    }
    buf[out] = 0;
    return apy_str_take(buf, out);
}

/* `bytes.fromhex(text)` -- the inverse, ignoring ASCII spaces between pairs
   the way CPython does. */
APY_API apy_value apy_bytes_fromhex(apy_value self, apy_value text) {
    /* The RECEIVER is ignored and present only so the shape matches the
       method table's -- `b.fromhex(s)` and `bytes.fromhex(s)` are the same
       call, and one signature means one implementation. */
    (void)self;
    int64_t n, i, out = 0;
    char *buf;
    int hi = -1;
    if (O(text)->kind != APY_STR_K)
        return apy_fail("TypeError", "fromhex() argument must be str");
    n = O(text)->v.s.n;
    buf = (char *)malloc((size_t)(n / 2 + 2));
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) {
        char c = APY_CSTR(text)[i];
        int d;
        if (c == ' ' || c == '\t' || c == '\n') continue;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else {
            free(buf);
            return apy_fail("ValueError",
                            "non-hexadecimal number found in fromhex() arg");
        }
        if (hi < 0) hi = d;
        else { buf[out++] = (char)((hi << 4) | d); hi = -1; }
    }
    if (hi >= 0) {
        free(buf);
        return apy_fail("ValueError",
                        "non-hexadecimal number found in fromhex() arg");
    }
    {
        apy_value v = apy_str_take(buf, out);
        O(v)->kind = APY_BYTES_K;
        return v;
    }
}

/* `n.to_bytes(length, byteorder)`. Big-endian unless told otherwise, which is
   the argument every caller passes and the reason it has no default here. */
APY_API apy_value apy_to_bytes_n(apy_value v, apy_value length,
                                 apy_value order) {
    int64_t n, i;
    uint64_t m;
    char *buf;
    int big;
    if (!apy_is_int_like(v))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'to_bytes'%s",
                         apy_kind_name(v), "");
    if (!apy_is_int_like(length))
        return apy_fail("TypeError", "to_bytes() length must be an integer");
    n = O(length)->v.i;
    if (n < 0 || n > 1024)
        return apy_fail("OverflowError", "int too big to convert");
    big = !(O(order)->kind == APY_STR_K
            && strcmp(APY_CSTR(order), "little") == 0);
    if (O(v)->v.i < 0)
        return apy_fail("OverflowError",
                        "can't convert negative int to unsigned");
    m = (uint64_t)O(v)->v.i;
    buf = (char *)calloc((size_t)(n ? n : 1) + 1, 1);
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) {
        buf[big ? n - 1 - i : i] = (char)(m & 0xFF);
        m >>= 8;
    }
    if (m) {
        free(buf);
        return apy_fail("OverflowError", "int too big to convert");
    }
    {
        apy_value out = apy_str_take(buf, n);
        O(out)->kind = APY_BYTES_K;
        return out;
    }
}

/* `x.as_integer_ratio()` -- the EXACT fraction the double holds, in lowest
   terms. `0.1` is not one tenth, and this is the method that says so. */
APY_API apy_value apy_as_integer_ratio(apy_value v) {
    double d;
    int64_t num, den = 1;
    apy_value out;
    if (apy_is_int_like(v)) {
        out = apy_tuple_new(2);
        apy_seq_push(out, v);
        apy_seq_push(out, apy_from_int(1));
        return out;
    }
    if (O(v)->kind != APY_FLOAT_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute "
                         "'as_integer_ratio'%s", apy_kind_name(v), "");
    d = O(v)->v.f;
    if (d != d || d - d != 0.0)
        return apy_fail("OverflowError",
                        "cannot convert Infinity to integer ratio");
    while (d != floor(d) && den < (int64_t)1 << 60) { d *= 2.0; den *= 2; }
    num = (int64_t)d;
    out = apy_tuple_new(2);
    apy_seq_push(out, apy_from_int(num));
    apy_seq_push(out, apy_from_int(den));
    return out;
}

/* `s.expandtabs(n)` -- tabs to the next multiple of `n`, counting from the
   last newline. Not a fixed number of spaces per tab: the whole point is that
   columns line up. */
APY_API apy_value apy_str_expandtabs(apy_value s, apy_value width) {
    int64_t n, i, col = 0, out = 0, cap;
    int64_t w = apy_is_int_like(width) ? O(width)->v.i : 8;
    char *buf;
    if (O(s)->kind != APY_STR_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'expandtabs'%s",
                         apy_kind_name(s), "");
    if (w < 1) w = 1;
    n = O(s)->v.s.n;
    cap = n * (w > 1 ? w : 1) + 8;
    buf = (char *)malloc((size_t)cap + 1);
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) {
        char c = APY_CSTR(s)[i];
        if (c == '\t') {
            int64_t pad = w - (col % w);
            while (pad-- > 0 && out < cap) { buf[out++] = ' '; col++; }
        } else {
            if (out < cap) buf[out++] = c;
            col = (c == '\n' || c == '\r') ? 0 : col + 1;
        }
    }
    buf[out] = 0;
    return apy_str_take(buf, out);
}

/* `x.is_integer()` -- a float method, and true for an int too, because
   `(5).is_integer()` is True in Python 3.12 and later. */
APY_API apy_value apy_is_integer(apy_value v) {
    if (apy_is_int_like(v) || apy_is_big(v)) return apy_from_bool(1);
    if (O(v)->kind != APY_FLOAT_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'is_integer'%s",
                         apy_kind_name(v), "");
    return apy_from_bool(O(v)->v.f == floor(O(v)->v.f)
                         && O(v)->v.f - O(v)->v.f == 0.0);
}

/* `z.conjugate()`. Defined on the whole numeric tower, not only on complex:
   `(5).conjugate()` is 5, which is what makes it usable without a kind test. */
APY_API apy_value apy_conjugate(apy_value v) {
    if (O(v)->kind == APY_COMPLEX_K)
        return apy_from_complex(O(v)->v.z.re, -O(v)->v.z.im);
    /* A BOOL ANSWERS AN int, not itself: `True.conjugate()` is `1`,
       because `bool` inherits the method from `int` and the method is
       defined to answer an int. */
    if (O(v)->kind == APY_BOOL_K) return apy_from_int(O(v)->v.i);
    if (apy_is_int_like(v) || apy_is_big(v) || O(v)->kind == APY_FLOAT_K)
        return v;
    return apy_fail2("AttributeError",
                     "'%s' object has no attribute 'conjugate'%s",
                     apy_kind_name(v), "");
}

"""
