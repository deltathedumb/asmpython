"""The object runtime, in C: the string methods.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * string methods
  * case
  * predicates
  * strip
  * split and join
  * padding
"""

C = r"""/* --- string methods ----------------------------------------------------- */
/* Pure functions over the str cell: nothing here mutates, because a Python str
   cannot be mutated, so every one of them builds a new cell.

   TWO DIVERGENCES, BOTH STATED RATHER THAN LEFT TO BE FOUND:

   * INDICES AND LENGTHS ARE BYTES, not characters. That is the limitation the
     top of this file records for indexing and slicing, and every method here
     inherits it: `'café'.find('é')` is 3 and CPython says 3 as well only
     because the accent is the last character. A method that returns a
     POSITION is wrong for any string with a multi-byte character before that
     position. It is consistent -- a position from `find` can be fed back to
     the slicer -- and it is not what CPython reports.

   * CASE AND CLASSIFICATION ARE ASCII. `'ß'.upper()` is 'SS' in CPython and
     'ß' here, and `'²'.isdigit()` is True there and False here. Doing better
     needs Unicode's case-mapping and category tables, which are 30k of data
     this runtime does not carry. Every ASCII answer is exact.

   The bounds rules are Python's and they are not C's: a negative index counts
   from the end, `end` clamps down to the length, and `start` DOES NOT clamp
   -- `'abc'.find('', 9)` is -1 while `'abc'.find('', 3)` is 3, and an upper
   clamp on `start` would answer 3 to both. */
/* THE EXPORTED HALF, which `runtime/list_cell.py` replaces. The `static`
   below keeps the name its callers use and the cast they do not have to
   write; this body is what the runtime uses when nothing is ported. */
/* Declared here because the delegate is defined below its first use. */
static int apy_str_self(const char *name, apy_value v);
APY_API int64_t apy_str_self_of(apy_value name, apy_value v) {
    if (O(v)->kind == APY_STR_K || O(v)->kind == APY_BYTES_K) return 1;
    apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
              apy_kind_name(v), (const char *)name);
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above is what stands in when nothing is
   ported. The cast to a machine word happens here, once. */
static int apy_str_self(const char *name, apy_value v) {
    return (int)apy_str_self_of((apy_value)(uintptr_t)name, v);
}

/* `find() argument 1 must be str, not int`. TWO ODDITIES OF THIS FAMILY are
   reproduced rather than regularised, because the suite is generated from
   CPython and both are visible: the kind is written WITHOUT quotes, and
   NoneType is written as `None` -- while `startswith`, forty lines down, says
   `not NoneType` for the very same value. `argno` of 0 drops the number,
   which is how `removeprefix` words it. */
APY_API apy_value apy_arg_must_be_str_of(apy_value methv,
                                        int64_t argno, apy_value v) {
    const char *meth = (const char *)methv;
    char buf[160];
    const char *k = O(v)->kind == APY_NONE_K ? "None" : apy_kind_name(v);
    if (argno)
        snprintf(buf, sizeof buf, "%s() argument %d must be str, not %s",
                 meth, argno, k);
    else
        snprintf(buf, sizeof buf, "%s() argument must be str, not %s", meth, k);
    return apy_fail("TypeError", buf);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_arg_must_be_str(const char *meth, int argno,
                                     apy_value v) {
    return apy_arg_must_be_str_of((apy_value)(uintptr_t)meth,
                                  (int64_t)argno, v);
}

APY_API int64_t apy_str_other_of(apy_value methv, int64_t argno,
                                apy_value v) {
    const char *meth = (const char *)methv;
    /* BYTES TOO, for the same reason the receiver may be: `b.replace(b"l",
       b"L")` hands bytes to an operation that reads a pointer and a length.
       Mixing the two is what CPython rejects, and the RECEIVER is what
       decides -- see `apy_str_like`. */
    if (O(v)->kind == APY_STR_K || O(v)->kind == APY_BYTES_K) return 1;
    apy_arg_must_be_str(meth, argno, v);
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_str_other(const char *meth, int argno, apy_value v) {
    return (int)apy_str_other_of((apy_value)(uintptr_t)meth,
                                 (int64_t)argno, v);
}

APY_API int64_t apy_int_arg_of(apy_value v, apy_value out) {
    if (!apy_is_int_like(v)) {
        apy_fail2("TypeError",
                  "'%s' object cannot be interpreted as an integer%s",
                  apy_kind_name(v), "");
        return 0;
    }
    if (apy_is_big(v)) {
        apy_fail("OverflowError",
                 "Python int too large to convert to C ssize_t");
        return 0;
    }
    *(int64_t *)out = O(v)->v.i;
    return 1;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_int_arg(apy_value v, int64_t *out) {
    return (int)apy_int_arg_of(v, (apy_value)(uintptr_t)out);
}


APY_API int64_t apy_slice_arg_of(apy_value v, apy_value out) {
    if (O(v)->kind == APY_NONE_K) return 1;
    if (apy_is_big(v)) {
        *(int64_t *)out = O(v)->v.big.neg
            ? -((int64_t)1 << 62) : ((int64_t)1 << 62);
        return 1;
    }
    return apy_int_arg(v, (int64_t *)out);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_slice_arg(apy_value v, int64_t *out) {
    return (int)apy_slice_arg_of(v, (apy_value)(uintptr_t)out);
}

APY_API void apy_clamp_range_of(int64_t n, apy_value lo,
                                apy_value hi) {
    int64_t *a = (int64_t *)lo, *b = (int64_t *)hi;
    if (*a < 0) { *a += n; if (*a < 0) *a = 0; }
    if (*b < 0) { *b += n; if (*b < 0) *b = 0; }
    if (*b > n) *b = n;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static void apy_clamp_range(int64_t n, int64_t *lo, int64_t *hi) {
    apy_clamp_range_of(n, (apy_value)(uintptr_t)lo,
                       (apy_value)(uintptr_t)hi);
}

/* The first occurrence of `sub` in `s[lo:hi]`, as an absolute index, or -1.
   An EMPTY needle matches at `lo` -- but only if `lo` is inside the window,
   which is the whole reason this takes `hi` rather than assuming the end. */
APY_API int64_t apy_find_at(apy_value s, apy_value sub,
                           int64_t lo, int64_t hi) {
    int64_t m = O(sub)->v.s.n, i;
    if (m == 0) return lo <= hi ? lo : -1;
    for (i = lo; i + m <= hi; i++)
        if (memcmp(O(s)->v.s.p + i, O(sub)->v.s.p, (size_t)m) == 0) return i;
    return -1;
}

APY_API int64_t apy_rfind_at(apy_value s, apy_value sub,
                            int64_t lo, int64_t hi) {
    int64_t m = O(sub)->v.s.n, i;
    if (m == 0) return lo <= hi ? hi : -1;
    for (i = hi - m; i >= lo; i--)
        if (memcmp(O(s)->v.s.p + i, O(sub)->v.s.p, (size_t)m) == 0) return i;
    return -1;
}

APY_API apy_value apy_str_slice_of(apy_value s, int64_t lo,
                                  int64_t hi) {
    if (hi < lo) hi = lo;
    return apy_str_copy(O(s)->v.s.p + lo, hi - lo);
}

/* find / rfind / index / rindex, all four from one place. `want_index` picks
   the -1-on-failure form from the raise-on-failure one; that is the only
   difference between `find` and `index`, and CPython's message for the second
   is `substring not found` with no mention of what was looked for. */
/* The byte offset of character `ci`, and the character index of byte offset
   `bo`. Every position a str method takes or answers is in CHARACTERS -- the
   search itself works in bytes, because that is what `memcmp` compares -- so
   these two are the boundary between the two counts. For an all-ASCII string
   both are the identity, which is why the distinction stayed invisible. */
static int64_t apy_char_to_byte(apy_value s, int64_t ci) {
    const unsigned char *p = (const unsigned char *)O(s)->v.s.p;
    int64_t bytes = O(s)->v.s.n, at = 0, seen = 0, used;
    while (seen < ci && at < bytes) {
        apy_utf8_at(p, bytes, at, &used);
        at += used;
        seen++;
    }
    return at;
}

static int64_t apy_byte_to_char(apy_value s, int64_t bo) {
    const unsigned char *p = (const unsigned char *)O(s)->v.s.p;
    int64_t bytes = O(s)->v.s.n, at = 0, seen = 0, used;
    while (at < bo && at < bytes) {
        apy_utf8_at(p, bytes, at, &used);
        at += used;
        seen++;
    }
    return seen;
}

static apy_value apy_str_search(apy_value s, apy_value sub, apy_value start,
                                apy_value end, int from_right, int want_index) {
    int64_t lo = 0, hi, at;
    const char *meth = want_index ? (from_right ? "rindex" : "index")
                                  : (from_right ? "rfind" : "find");
    if (!apy_str_self(meth, s)) return 0;
    if (!apy_str_other(meth, 1, sub)) return 0;
    hi = apy_str_chars(s);
    if (start && !apy_slice_arg(start, &lo)) return 0;
    if (end && !apy_slice_arg(end, &hi)) return 0;
    /* THE BOUNDS ARRIVE IN CHARACTERS and the search runs in bytes, so they
       are clamped against the character count and then converted. Answering a
       byte offset made `"héllo".find("ll")` say 3 where CPython says 2.
    */
    apy_clamp_range(apy_str_chars(s), &lo, &hi);
    lo = apy_char_to_byte(s, lo);
    hi = apy_char_to_byte(s, hi);
    at = from_right ? apy_rfind_at(s, sub, lo, hi) : apy_find_at(s, sub, lo, hi);
    if (at < 0 && want_index)
        return apy_fail("ValueError", "substring not found");
    return apy_from_int(at < 0 ? at : apy_byte_to_char(s, at));
}

APY_API apy_value apy_str_find(apy_value s, apy_value sub) {
    return apy_str_search(s, sub, 0, 0, 0, 0);
}
APY_API apy_value apy_str_find2(apy_value s, apy_value sub, apy_value start) {
    return apy_str_search(s, sub, start, 0, 0, 0);
}
APY_API apy_value apy_str_find3(apy_value s, apy_value sub, apy_value start,
                                apy_value end) {
    return apy_str_search(s, sub, start, end, 0, 0);
}
APY_API apy_value apy_str_rfind(apy_value s, apy_value sub) {
    return apy_str_search(s, sub, 0, 0, 1, 0);
}
APY_API apy_value apy_str_rfind2(apy_value s, apy_value sub, apy_value start) {
    return apy_str_search(s, sub, start, 0, 1, 0);
}
APY_API apy_value apy_str_rfind3(apy_value s, apy_value sub, apy_value start,
                                 apy_value end) {
    return apy_str_search(s, sub, start, end, 1, 0);
}
APY_API apy_value apy_str_rindex(apy_value s, apy_value sub) {
    return apy_str_search(s, sub, 0, 0, 1, 1);
}

/* Reached from `apy_index_of` and `apy_count_of` when the receiver is a str,
   so that `'abcabc'.index('bc')` looks for a SUBSTRING rather than for an
   element equal to it -- the sequence versions would answer only for
   single-character needles and would silently do so. */
static apy_value apy_str_index_of(apy_value s, apy_value sub) {
    return apy_str_search(s, sub, 0, 0, 0, 1);
}

/* NON-OVERLAPPING, which is what makes `'aaaa'.count('aa')` 2 and not 3, and
   an empty needle counts the gaps: `'abc'.count('')` is 4. */
APY_API apy_value apy_str_count_in_of(apy_value s, apy_value sub,
                                     apy_value start, apy_value end) {
    int64_t lo = 0, hi, m, i, hits = 0;
    if (!apy_str_other("count", 1, sub)) return 0;
    hi = O(s)->v.s.n;
    if (start && !apy_slice_arg(start, &lo)) return 0;
    if (end && !apy_slice_arg(end, &hi)) return 0;
    apy_clamp_range(O(s)->v.s.n, &lo, &hi);
    m = O(sub)->v.s.n;
    if (m == 0) return apy_from_int(hi >= lo ? hi - lo + 1 : 0);
    for (i = lo; i + m <= hi; ) {
        if (memcmp(O(s)->v.s.p + i, O(sub)->v.s.p, (size_t)m) == 0) {
            hits++;
            i += m;
        } else i++;
    }
    return apy_from_int(hits);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_str_count_in(apy_value s, apy_value sub,
                                  apy_value start, apy_value end) {
    return apy_str_count_in_of(s, sub, start, end);
}

APY_API apy_value apy_str_count2(apy_value s, apy_value sub, apy_value start) {
    if (!apy_str_self("count", s)) return 0;
    return apy_str_count_in(s, sub, start, 0);
}
APY_API apy_value apy_str_count3(apy_value s, apy_value sub, apy_value start,
                                 apy_value end) {
    if (!apy_str_self("count", s)) return 0;
    return apy_str_count_in(s, sub, start, end);
}

/* --- case ---------------------------------------------------------------
   ASCII rules, as the section header says. A byte outside ASCII is copied
   unchanged, which is at least stable and never corrupts UTF-8: every byte of
   a multi-byte sequence has its high bit set, so none of them can be mistaken
   for a letter to map. */
/* PROMOTED FROM `static` so the subset can name them. The five ASCII
   predicates are what every case transform and every `str.isalpha`
   family member rests on, and `runtime/str_cell.py` records the case
   transforms as blocked -- this is the half of that blockage which is
   not the Unicode table.

   `int64_t` RATHER THAN `unsigned char` AND `int`, because those are
   not types `signatures()` can describe: it knows `apy_value`,
   `int64_t`, `double` and `void`, and a runtime symbol the frontend
   cannot describe is one it cannot call. Every caller passes an
   `unsigned char`, which widens to `int64_t` on its own. */
APY_API int64_t apy_c_lower(int64_t c) { return c >= 'a' && c <= 'z'; }
APY_API int64_t apy_c_upper(int64_t c) { return c >= 'A' && c <= 'Z'; }
APY_API int64_t apy_c_alpha(int64_t c) { return apy_c_lower(c) || apy_c_upper(c); }
APY_API int64_t apy_c_digit(int64_t c) { return c >= '0' && c <= '9'; }
APY_API int64_t apy_c_space(int64_t c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f'
        || c == '\v';
}

enum { APY_UPPER, APY_LOWER, APY_TITLE, APY_CAPITAL, APY_SWAP, APY_FOLD };

/* THE OUTPUT CAN BE LONGER THAN THE INPUT, which is why this does not write
   in place over a same-sized buffer: 'ß'.upper() is 'SS', one character
   becoming two. Only Latin-1 is mapped -- the two-byte sequences starting
   0xC3, which covers the accented letters and the one length-changing case
   that programs actually meet. Anything above that is left alone rather than
   half-done; a full Unicode case table is not here. */
static apy_value apy_str_case(apy_value s, int mode) {
    int64_t n = O(s)->v.s.n, i;
    /* Room for every byte to become two, which is the worst this can do. */
    char *buf = (char *)malloc((size_t)n * 2 + 1);
    int64_t out_n = 0;
    int prev_cased = 0;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)O(s)->v.s.p[i];
        unsigned char out = c;
        /* A LATIN-1 LETTER: 0xC3 then the low byte. Uppercase runs
           0x80..0x9E and lowercase 0xA0..0xBE, offset by 0x20 exactly as
           ASCII is by 32 -- with 0x97 and 0xB7 the multiplication and
           division signs, which are not letters, and 0x9F the sharp s,
           which is lowercase despite sitting in the uppercase run. */
        if (c == 0xC3 && i + 1 < n) {
            unsigned char d = (unsigned char)O(s)->v.s.p[i + 1];
            int is_upper = d >= 0x80 && d <= 0x9E && d != 0x97;
            int is_lower = d >= 0xA0 && d <= 0xBE && d != 0xB7;
            /* U+00DF IS LOWERCASE despite sitting one past the end of
               the uppercase run, so `is_lower` -- which is a range test --
               misses it, and `swapcase` left it alone where Python raises it
               to 'SS'. */
            int raise = mode == APY_UPPER
                || (mode == APY_SWAP && (is_lower || d == 0x9F))
                || (mode == APY_CAPITAL && i == 0)
                || (mode == APY_TITLE && !prev_cased);
            if (d == 0x9F && (raise || mode == APY_FOLD)) {
                /* 'ß' has no single uppercase form: it becomes 'SS', and
                   casefold gives 'ss' so that the two match caselessly. */
                /* TITLE-CASING AN EXPANSION RAISES ONLY ITS FIRST
                   LETTER: `title` and `capitalize` of U+00DF both give 'Ss'
                   where `upper` gives 'SS'. Both letters came from one
                   character, so "the first character" of the result means
                   the first letter of what that character became. */
                buf[out_n++] = raise ? 'S' : 's';
                buf[out_n++] = (raise && mode != APY_TITLE
                                && mode != APY_CAPITAL) ? 'S' : 's';
                prev_cased = 1;
                i++;
                continue;
            }
            if (raise && is_lower) d = (unsigned char)(d - 0x20);
            else if (!raise && is_upper) d = (unsigned char)(d + 0x20);
            buf[out_n++] = (char)c;
            buf[out_n++] = (char)d;
            prev_cased = is_upper || is_lower || d == 0x9F;
            i++;
            continue;
        }
        switch (mode) {
        case APY_UPPER: if (apy_c_lower(c)) out = (unsigned char)(c - 32); break;
        /* `casefold` IS lowercasing for ASCII -- the pair it exists for,
           'ß' against 'ss', is handled in the Latin-1 branch above. */
        case APY_FOLD:
        case APY_LOWER: if (apy_c_upper(c)) out = (unsigned char)(c + 32); break;
        case APY_SWAP:
            if (apy_c_lower(c)) out = (unsigned char)(c - 32);
            else if (apy_c_upper(c)) out = (unsigned char)(c + 32);
            break;
        case APY_CAPITAL:
            /* Only the FIRST character is raised and the whole rest is
               lowered -- `'hello World'.capitalize()` is 'Hello world', not
               'Hello World'. */
            if (i == 0) { if (apy_c_lower(c)) out = (unsigned char)(c - 32); }
            else if (apy_c_upper(c)) out = (unsigned char)(c + 32);
            break;
        default:
            /* `title` tracks whether the PREVIOUS character was cased, which
               is why `'a1b'` titles to 'A1B' and `"don't"` to "Don'T": a digit
               and an apostrophe are both uncased, so the letter after either
               starts a new word. Anything simpler -- splitting on spaces, or
               on non-alphanumerics -- disagrees with one of those two. */
            if (prev_cased) { if (apy_c_upper(c)) out = (unsigned char)(c + 32); }
            else if (apy_c_lower(c)) out = (unsigned char)(c - 32);
            break;
        }
        prev_cased = apy_c_alpha(c);
        buf[out_n++] = (char)out;
    }
    buf[out_n] = '\0';
    return apy_str_take(buf, out_n);
}

APY_API apy_value apy_str_upper(apy_value s) {
    if (!apy_str_self("upper", s)) return 0;
    return apy_str_case(s, APY_UPPER);
}
APY_API apy_value apy_str_lower(apy_value s) {
    if (!apy_str_self("lower", s)) return 0;
    return apy_str_case(s, APY_LOWER);
}
APY_API apy_value apy_str_title(apy_value s) {
    if (!apy_str_self("title", s)) return 0;
    return apy_str_case(s, APY_TITLE);
}
APY_API apy_value apy_str_capitalize(apy_value s) {
    if (!apy_str_self("capitalize", s)) return 0;
    return apy_str_case(s, APY_CAPITAL);
}
APY_API apy_value apy_str_swapcase(apy_value s) {
    if (!apy_str_self("swapcase", s)) return 0;
    return apy_str_case(s, APY_SWAP);
}
/* `casefold` is aggressive lowercasing for caseless matching, and for ASCII
   it IS lowercasing. It differs from `lower` on the pair it exists for --
   'ß' folds to 'ss' where lowering leaves it alone -- so the two are not the
   same mode even though they agree on everything a plain program prints. */
APY_API apy_value apy_str_casefold(apy_value s) {
    if (!apy_str_self("casefold", s)) return 0;
    return apy_str_case(s, APY_FOLD);
}

/* --- predicates ---------------------------------------------------------
   All of them are False for the EMPTY string except `isascii`, which is True
   -- that is not an accident of the loop, it is Python's rule, and writing
   the loop so that "no character failed" means True would get every one of
   them wrong for ''. */
enum { APY_ISALPHA, APY_ISDIGIT, APY_ISALNUM, APY_ISSPACE, APY_ISLOWER,
       APY_ISUPPER, APY_ISTITLE, APY_ISPRINTABLE, APY_ISIDENT, APY_ISASCII,
       APY_ISDECIMAL, APY_ISNUMERIC };

/* The UTF-8 decoder lives with the codecs, far below the predicates that
   walk a string by code point. */
static int64_t apy_utf8_step(const unsigned char *p, int64_t n, int64_t i,
                             uint32_t *out);

/* @UNICODE_TABLE@ */

/* The classes ONE CODE POINT belongs to. ASCII is decided here -- it is the
   dense half of the range and the table starts past it -- and everything
   above is a lookup in the generated runs. */
APY_API int64_t apy_char_class_of(int64_t cp) {
    unsigned m = 0;
    if (cp >= 0x80) return (int64_t)apy_uc_mask((uint32_t)cp);
    if ((cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z'))
        m |= APY_UC_ALPHA | APY_UC_XIDSTART | APY_UC_XIDCONT;
    if (cp >= '0' && cp <= '9')
        m |= APY_UC_DECIMAL | APY_UC_DIGIT | APY_UC_NUMERIC | APY_UC_XIDCONT;
    if (cp >= 'a' && cp <= 'z') m |= APY_UC_LOWER;
    if (cp >= 'A' && cp <= 'Z') m |= APY_UC_UPPER;
    if (cp == ' ' || cp == '\t' || cp == '\n' || cp == '\v' || cp == '\f'
        || cp == '\r') m |= APY_UC_SPACE;
    if (cp >= 0x20 && cp < 0x7f) m |= APY_UC_PRINTABLE;
    if (cp == '_') m |= APY_UC_XIDSTART | APY_UC_XIDCONT;
    return (int64_t)m;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. The exported
   half takes and returns a plain word because the subset has one integer
   width and `uint32_t` is not it. */
static unsigned apy_char_class(uint32_t cp) {
    return (unsigned)apy_char_class_of((int64_t)cp);
}

/* IS THIS CHARACTER PRINTABLE, for anyone outside this file.
   `repr` needs the answer and sits in an EARLIER part of the source, so it
   forward-declares this rather than the table it rests on -- which stays
   private to the part that generates it. The `' '` is the same exception
   `str.isprintable` makes: a space is printable and no other whitespace
   is. */
APY_API int64_t apy_cp_printable_of(int64_t cp) {
    return cp == ' ' || (apy_char_class_of(cp) & APY_UC_PRINTABLE) != 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate. */
static int apy_cp_printable(uint32_t cp) {
    return (int)apy_cp_printable_of((int64_t)cp);
}

static apy_value apy_str_is(apy_value s, int which) {
    int64_t n = O(s)->v.s.n, i;
    const unsigned char *p = (const unsigned char *)O(s)->v.s.p;
    int cased = 0, prev_cased = 0, ok = 1, first = 1, any = 0;
    if (which == APY_ISASCII) {
        for (i = 0; i < n; i++)
            if (p[i] > 0x7f) return apy_from_bool(0);
        return apy_from_bool(1);
    }
    if (which == APY_ISPRINTABLE && n == 0) return apy_from_bool(1);
    if (n == 0) return apy_from_bool(0);
    /* BY CODE POINT, not by byte. Every predicate below asks a question about
       a CHARACTER, and a multi-byte one walked as bytes was asked about its
       continuation bytes -- which belong to no class, so every non-ASCII
       string answered False. */
    for (i = 0; i < n; ) {
        uint32_t cp;
        int64_t used = apy_utf8_step(p, n, i, &cp);
        unsigned m;
        if (!used) { cp = 0xFFFD; used = 1; }
        i += used;
        any = 1;
        m = apy_char_class(cp);
        switch (which) {
        case APY_ISALPHA: if (!(m & APY_UC_ALPHA)) ok = 0; break;
        case APY_ISDIGIT: if (!(m & APY_UC_DIGIT)) ok = 0; break;
        case APY_ISDECIMAL: if (!(m & APY_UC_DECIMAL)) ok = 0; break;
        case APY_ISNUMERIC: if (!(m & APY_UC_NUMERIC)) ok = 0; break;
        case APY_ISALNUM:
            if (!(m & (APY_UC_ALPHA | APY_UC_NUMERIC | APY_UC_DIGIT
                       | APY_UC_DECIMAL))) ok = 0;
            break;
        case APY_ISSPACE: if (!(m & APY_UC_SPACE)) ok = 0; break;
        case APY_ISPRINTABLE:
            /* A SPACE IS PRINTABLE and no other whitespace is, which is the
               one place this differs from "not a control character". */
            if (cp != ' ' && !(m & APY_UC_PRINTABLE)) ok = 0;
            break;
        case APY_ISIDENT:
            if (first) {
                if (!(m & APY_UC_XIDSTART)) ok = 0;
            } else if (!(m & APY_UC_XIDCONT)) ok = 0;
            break;
        case APY_ISLOWER:
            /* "no uppercase AND at least one lowercase" -- `'ab1'.islower()`
               is True and `'123'.islower()` is False. A plain "every
               character is lowercase" answers the second one wrongly. */
            if (m & (APY_UC_UPPER | APY_UC_TITLE)) ok = 0;
            if (m & APY_UC_LOWER) cased = 1;
            break;
        case APY_ISUPPER:
            if (m & (APY_UC_LOWER | APY_UC_TITLE)) ok = 0;
            if (m & APY_UC_UPPER) cased = 1;
            break;
        default:
            /* `istitle`: an upper- or title-case character may only follow an
               uncased one, and a lowercase one may only follow a cased one. */
            if (m & (APY_UC_UPPER | APY_UC_TITLE)) {
                if (prev_cased) ok = 0;
                cased = 1;
            } else if (m & APY_UC_LOWER) {
                if (!prev_cased) ok = 0;
                cased = 1;
            }
            prev_cased = (m & (APY_UC_UPPER | APY_UC_LOWER
                               | APY_UC_TITLE)) != 0;
            break;
        }
        first = 0;
        if (!ok) return apy_from_bool(0);
    }
    if (!any) return apy_from_bool(0);
    if (which == APY_ISLOWER || which == APY_ISUPPER || which == APY_ISTITLE)
        return apy_from_bool(cased);
    return apy_from_bool(1);
}

APY_API apy_value apy_str_isalpha(apy_value s) {
    if (!apy_str_self("isalpha", s)) return 0;
    return apy_str_is(s, APY_ISALPHA);
}
APY_API apy_value apy_str_isdigit(apy_value s) {
    if (!apy_str_self("isdigit", s)) return 0;
    return apy_str_is(s, APY_ISDIGIT);
}
/* `isdecimal`, `isdigit` and `isnumeric` are three different questions
   outside ASCII: U+00B2 is a digit and numeric but not decimal, and U+2167 is
   numeric alone. They shared one test while there was no table to tell them
   apart; there is one now, so they do not. */
APY_API apy_value apy_str_isdecimal(apy_value s) {
    if (!apy_str_self("isdecimal", s)) return 0;
    return apy_str_is(s, APY_ISDECIMAL);
}
APY_API apy_value apy_str_isnumeric(apy_value s) {
    if (!apy_str_self("isnumeric", s)) return 0;
    return apy_str_is(s, APY_ISNUMERIC);
}
APY_API apy_value apy_str_isalnum(apy_value s) {
    if (!apy_str_self("isalnum", s)) return 0;
    return apy_str_is(s, APY_ISALNUM);
}
APY_API apy_value apy_str_isspace(apy_value s) {
    if (!apy_str_self("isspace", s)) return 0;
    return apy_str_is(s, APY_ISSPACE);
}
APY_API apy_value apy_str_islower(apy_value s) {
    if (!apy_str_self("islower", s)) return 0;
    return apy_str_is(s, APY_ISLOWER);
}
APY_API apy_value apy_str_isupper(apy_value s) {
    if (!apy_str_self("isupper", s)) return 0;
    return apy_str_is(s, APY_ISUPPER);
}
APY_API apy_value apy_str_istitle(apy_value s) {
    if (!apy_str_self("istitle", s)) return 0;
    return apy_str_is(s, APY_ISTITLE);
}
APY_API apy_value apy_str_isprintable(apy_value s) {
    if (!apy_str_self("isprintable", s)) return 0;
    return apy_str_is(s, APY_ISPRINTABLE);
}
APY_API apy_value apy_str_isidentifier(apy_value s) {
    if (!apy_str_self("isidentifier", s)) return 0;
    return apy_str_is(s, APY_ISIDENT);
}
APY_API apy_value apy_str_isascii(apy_value s) {
    if (!apy_str_self("isascii", s)) return 0;
    return apy_str_is(s, APY_ISASCII);
}

/* --- strip -------------------------------------------------------------- */
/* `chars` is a SET of characters to remove, not a prefix to match:
   `'xyabyx'.strip('xy')` is 'ab'. A null `chars` means whitespace. */
/* BY CODE POINT, not by byte, and both halves of that matter.
   WHITESPACE: `apy_c_space` knows the six ASCII bytes, so `strip()` left
   U+00A0 and U+2003 in place where Python removes them. The table already
   answers this question for `str.isspace`, and now it answers it here.
   THE SET: `chars` was compared a byte at a time, so a multi-byte character
   in it matched the HALVES of other characters -- `'ab'.strip('é')`
   would have eaten a 0xC3 lead byte and left a dangling continuation. */
static int apy_in_chars(apy_value chars, uint32_t cp) {
    const unsigned char *p;
    int64_t n, i = 0;
    if (!chars) return (apy_char_class(cp) & APY_UC_SPACE) != 0;
    p = (const unsigned char *)O(chars)->v.s.p;
    n = O(chars)->v.s.n;
    while (i < n) {
        uint32_t c2;
        int64_t used = apy_utf8_step(p, n, i, &c2);
        if (!used) { c2 = 0xFFFD; used = 1; }
        if (c2 == cp) return 1;
        i += used;
    }
    return 0;
}

static apy_value apy_str_trim(apy_value s, apy_value chars, const char *meth,
                              int left, int right) {
    int64_t lo = 0, hi = O(s)->v.s.n;
    /* BYTES TOO, for a bytes receiver: `b'abc'.strip(b'a')` is Python and
       this refused it. The receiver and the argument still have to agree --
       `b'abc'.strip('a')` is a TypeError in CPython as well -- which the
       kind comparison below gets by asking whether they MATCH rather than
       whether the argument is a str. */
    if (chars && O(chars)->kind != APY_NONE_K && O(chars)->kind != O(s)->kind) {
        /* Its own wording, naming NEITHER the offending kind nor a position:
           `strip arg must be None or str`. */
        char buf[80];
        snprintf(buf, sizeof buf, "%s arg must be None or str", meth);
        return apy_fail("TypeError", buf);
    }
    /* ONE FORWARD PASS DECIDES BOTH ENDS. Walking in from the right would
       mean stepping UTF-8 backwards, which needs its own scan for a lead
       byte; remembering where the last non-stripped character ended costs
       one variable and no second way to be wrong. */
    {
        const unsigned char *p = (const unsigned char *)O(s)->v.s.p;
        int64_t i, last;
        if (left) {
            for (i = 0; i < hi; ) {
                uint32_t cp;
                int64_t used = apy_utf8_step(p, hi, i, &cp);
                if (!used) { cp = 0xFFFD; used = 1; }
                if (!apy_in_chars(chars, cp)) break;
                i += used;
            }
            lo = i;
        }
        if (right) {
            last = lo;
            for (i = lo; i < hi; ) {
                uint32_t cp;
                int64_t used = apy_utf8_step(p, hi, i, &cp);
                if (!used) { cp = 0xFFFD; used = 1; }
                i += used;
                if (!apy_in_chars(chars, cp)) last = i;
            }
            hi = last;
        }
    }
    return apy_str_slice_of(s, lo, hi);
}

APY_API apy_value apy_str_strip(apy_value s) {
    if (!apy_str_self("strip", s)) return 0;
    return apy_str_trim(s, 0, "strip", 1, 1);
}
APY_API apy_value apy_str_lstrip(apy_value s) {
    if (!apy_str_self("lstrip", s)) return 0;
    return apy_str_trim(s, 0, "lstrip", 1, 0);
}
APY_API apy_value apy_str_rstrip(apy_value s) {
    if (!apy_str_self("rstrip", s)) return 0;
    return apy_str_trim(s, 0, "rstrip", 0, 1);
}
APY_API apy_value apy_str_strip_chars(apy_value s, apy_value chars) {
    if (!apy_str_self("strip", s)) return 0;
    if (O(chars)->kind == APY_NONE_K) return apy_str_trim(s, 0, "strip", 1, 1);
    return apy_str_trim(s, chars, "strip", 1, 1);
}
APY_API apy_value apy_str_lstrip_chars(apy_value s, apy_value chars) {
    if (!apy_str_self("lstrip", s)) return 0;
    if (O(chars)->kind == APY_NONE_K) return apy_str_trim(s, 0, "lstrip", 1, 0);
    return apy_str_trim(s, chars, "lstrip", 1, 0);
}
APY_API apy_value apy_str_rstrip_chars(apy_value s, apy_value chars) {
    if (!apy_str_self("rstrip", s)) return 0;
    if (O(chars)->kind == APY_NONE_K) return apy_str_trim(s, 0, "rstrip", 0, 1);
    return apy_str_trim(s, chars, "rstrip", 0, 1);
}

APY_API apy_value apy_str_removeprefix(apy_value s, apy_value p) {
    if (!apy_str_self("removeprefix", s)) return 0;
    if (!apy_str_other("removeprefix", 0, p)) return 0;
    if (O(p)->v.s.n && O(p)->v.s.n <= O(s)->v.s.n
        && memcmp(O(s)->v.s.p, O(p)->v.s.p, (size_t)O(p)->v.s.n) == 0)
        return apy_str_slice_of(s, O(p)->v.s.n, O(s)->v.s.n);
    return s;
}

APY_API apy_value apy_str_removesuffix(apy_value s, apy_value p) {
    if (!apy_str_self("removesuffix", s)) return 0;
    if (!apy_str_other("removesuffix", 0, p)) return 0;
    if (O(p)->v.s.n && O(p)->v.s.n <= O(s)->v.s.n
        && memcmp(O(s)->v.s.p + O(s)->v.s.n - O(p)->v.s.n,
                  O(p)->v.s.p, (size_t)O(p)->v.s.n) == 0)
        return apy_str_slice_of(s, 0, O(s)->v.s.n - O(p)->v.s.n);
    return s;
}

/* --- split and join ------------------------------------------------------
   THE TWO SPLIT MODES ARE DIFFERENT ALGORITHMS, not one with a default
   separator, and the case that shows it is `'  a  b  '`: with no argument it
   splits on RUNS of whitespace and drops the empty pieces at both ends, giving
   ['a', 'b']; with `' '` it splits on each single space and keeps them, giving
   ['', '', 'a', '', 'b', '', '']. A default of `' '` would answer the second
   to both. */
APY_API apy_value apy_split_ws_of(apy_value s, int64_t maxsplit,
                                 int64_t from_right) {
    apy_value out = apy_seq_new(APY_LIST_K, 8);
    int64_t n = O(s)->v.s.n, i, j;
    if (!from_right) {
        i = 0;
        while (i < n) {
            while (i < n && apy_c_space((unsigned char)O(s)->v.s.p[i])) i++;
            if (i >= n) break;
            if (maxsplit >= 0 && O(out)->v.q.n == maxsplit) {
                /* The remainder goes in WHOLE, INCLUDING its trailing
                   whitespace: `'  a  b  '.split(None, 1)` is ['a', 'b  '].
                   Only the whitespace BEFORE a piece is skipped, and that
                   already happened above. Right-stripping the remainder as
                   well looks tidier and answers ['a', 'b'], which is wrong --
                   and invisible unless a case splits a string that has
                   trailing space. */
                apy_q_append(out, apy_str_slice_of(s, i, n));
                return out;
            }
            j = i;
            while (j < n && !apy_c_space((unsigned char)O(s)->v.s.p[j])) j++;
            apy_q_append(out, apy_str_slice_of(s, i, j));
            i = j;
        }
        return out;
    }
    i = n;
    while (i > 0) {
        while (i > 0 && apy_c_space((unsigned char)O(s)->v.s.p[i - 1])) i--;
        if (i <= 0) break;
        if (maxsplit >= 0 && O(out)->v.q.n == maxsplit) {
            /* Mirror image of the forward case: the remainder keeps its
               LEADING whitespace. `'  a  b  '.rsplit(None, 1)` is
               ['  a', 'b']. */
            apy_q_append(out, apy_str_slice_of(s, 0, i));
            break;
        }
        j = i;
        while (j > 0 && !apy_c_space((unsigned char)O(s)->v.s.p[j - 1])) j--;
        apy_q_append(out, apy_str_slice_of(s, j, i));
        i = j;
    }
    /* Built back to front, so reverse it. */
    for (i = 0, j = O(out)->v.q.n - 1; i < j; i++, j--) {
        apy_value t = O(out)->v.q.items[i];
        O(out)->v.q.items[i] = O(out)->v.q.items[j];
        O(out)->v.q.items[j] = t;
    }
    return out;
}

APY_API apy_value apy_split_sep_of(apy_value s, apy_value sep,
                                  int64_t maxsplit, int64_t from_right) {
    apy_value out;
    int64_t n = O(s)->v.s.n, m = O(sep)->v.s.n, at, i, j;
    if (m == 0) return apy_fail("ValueError", "empty separator");
    out = apy_seq_new(APY_LIST_K, 8);
    if (!from_right) {
        i = 0;
        while (maxsplit < 0 || O(out)->v.q.n < maxsplit) {
            at = apy_find_at(s, sep, i, n);
            if (at < 0) break;
            apy_q_append(out, apy_str_slice_of(s, i, at));
            i = at + m;
        }
        apy_q_append(out, apy_str_slice_of(s, i, n));
        return out;
    }
    i = n;
    while (maxsplit < 0 || O(out)->v.q.n < maxsplit) {
        at = apy_rfind_at(s, sep, 0, i);
        if (at < 0) break;
        apy_q_append(out, apy_str_slice_of(s, at + m, i));
        i = at;
    }
    apy_q_append(out, apy_str_slice_of(s, 0, i));
    for (i = 0, j = O(out)->v.q.n - 1; i < j; i++, j--) {
        apy_value t = O(out)->v.q.items[i];
        O(out)->v.q.items[i] = O(out)->v.q.items[j];
        O(out)->v.q.items[j] = t;
    }
    return out;
}

APY_API apy_value apy_str_split_impl_of(apy_value s, apy_value sep,
                                       apy_value limit,
                                       int64_t from_right) {
    int64_t maxsplit = -1;
    if (limit && !apy_int_arg(limit, &maxsplit)) return 0;
    if (maxsplit < 0) maxsplit = -1;      /* any negative means "no limit" */
    if (!sep || O(sep)->kind == APY_NONE_K)
        return apy_split_ws_of(s, maxsplit, from_right);
    /* BYTES TOO -- `b"a,b".split(b",")` is the same operation. The receiver
       decides the result's kind; see `apy_str_like`. */
    if (O(sep)->kind != APY_STR_K && O(sep)->kind != APY_BYTES_K)
        return apy_fail2("TypeError", "must be str or None, not %s%s",
                         apy_kind_name(sep), "");
    return apy_split_sep_of(s, sep, maxsplit, from_right);
}

/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_str_split_impl(apy_value s, apy_value sep,
                                    apy_value limit, int from_right) {
    return apy_str_split_impl_of(s, sep, limit, (int64_t)from_right);
}

APY_API apy_value apy_str_split_ws(apy_value s) {
    if (!apy_str_self("split", s)) return 0;
    return apy_str_split_impl(s, 0, 0, 0);
}
APY_API apy_value apy_str_split(apy_value s, apy_value sep) {
    if (!apy_str_self("split", s)) return 0;
    return apy_str_split_impl(s, sep, 0, 0);
}
APY_API apy_value apy_str_split_n(apy_value s, apy_value sep, apy_value limit) {
    if (!apy_str_self("split", s)) return 0;
    return apy_str_split_impl(s, sep, limit, 0);
}
APY_API apy_value apy_str_rsplit_ws(apy_value s) {
    if (!apy_str_self("rsplit", s)) return 0;
    return apy_str_split_impl(s, 0, 0, 1);
}
APY_API apy_value apy_str_rsplit(apy_value s, apy_value sep) {
    if (!apy_str_self("rsplit", s)) return 0;
    return apy_str_split_impl(s, sep, 0, 1);
}
APY_API apy_value apy_str_rsplit_n(apy_value s, apy_value sep, apy_value limit) {
    if (!apy_str_self("rsplit", s)) return 0;
    return apy_str_split_impl(s, sep, limit, 1);
}

/* `splitlines` breaks on \n, \r and \r\n. CPython also breaks on \v, \f,
   \x1c-\x1e and three Unicode separators; those are not here, and a text
   containing one comes back as a single line. Stated, not silent. */
APY_API apy_value apy_splitlines_impl_of(apy_value s, int64_t keepends) {
    apy_value out = apy_seq_new(APY_LIST_K, 8);
    int64_t n = O(s)->v.s.n, i = 0, start;
    while (i < n) {
        start = i;
        while (i < n && O(s)->v.s.p[i] != '\n' && O(s)->v.s.p[i] != '\r') i++;
        {
            int64_t stop = i;
            if (i < n) {
                if (O(s)->v.s.p[i] == '\r' && i + 1 < n
                    && O(s)->v.s.p[i + 1] == '\n') i += 2;
                else i++;
            }
            apy_q_append(out, apy_str_slice_of(s, start, keepends ? i : stop));
        }
    }
    return out;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_splitlines_impl(apy_value s, int keepends) {
    return apy_splitlines_impl_of(s, (int64_t)keepends);
}

APY_API apy_value apy_str_splitlines(apy_value s) {
    if (!apy_str_self("splitlines", s)) return 0;
    return apy_splitlines_impl(s, 0);
}
APY_API apy_value apy_str_splitlines_keep(apy_value s, apy_value keep) {
    if (!apy_str_self("splitlines", s)) return 0;
    return apy_splitlines_impl(s, apy_truth(keep));
}

/* `partition` returns three pieces ALWAYS. On a miss the original goes in the
   first slot and the other two are empty; `rpartition` puts it in the LAST,
   which is the only asymmetry between them and is easy to get backwards. */
static apy_value apy_partition_impl(apy_value s, apy_value sep, int from_right) {
    apy_value out = apy_seq_new(APY_TUPLE_K, 3);
    int64_t n = O(s)->v.s.n, m, at;
    /* `must be str, not int` -- no method name at all, which is how CPython
       words this one and unlike every other method in this file. */
    /* BYTES TOO -- `b"abc".partition(b"b")` is the same operation. */
    if (O(sep)->kind != APY_STR_K && O(sep)->kind != APY_BYTES_K)
        return apy_fail2("TypeError", "must be str, not %s%s",
                         apy_kind_name(sep), "");
    m = O(sep)->v.s.n;
    if (m == 0) return apy_fail("ValueError", "empty separator");
    at = from_right ? apy_rfind_at(s, sep, 0, n) : apy_find_at(s, sep, 0, n);
    if (at < 0) {
        apy_q_append(out, from_right ? apy_lit("") : s);
        apy_q_append(out, apy_lit(""));
        apy_q_append(out, from_right ? s : apy_lit(""));
        return out;
    }
    apy_q_append(out, apy_str_slice_of(s, 0, at));
    apy_q_append(out, sep);
    apy_q_append(out, apy_str_slice_of(s, at + m, n));
    return out;
}

APY_API apy_value apy_str_partition(apy_value s, apy_value sep) {
    if (!apy_str_self("partition", s)) return 0;
    return apy_partition_impl(s, sep, 0);
}
APY_API apy_value apy_str_rpartition(apy_value s, apy_value sep) {
    if (!apy_str_self("rpartition", s)) return 0;
    return apy_partition_impl(s, sep, 1);
}

/* `sep.join(parts)`. The receiver is the SEPARATOR, which reads backwards
   until you have written it once. Any iterable of str; a non-str element is
   reported with its position, because in a long list that is the only useful
   half of the message. */
APY_API apy_value apy_str_join(apy_value sep, apy_value parts) {
    int64_t n, i, len = 0, out = 0;
    apy_value *got;
    char *buf;
    if (!apy_str_self("join", sep)) return 0;
    /* ANY iterable, not just an indexable one. The walk below is by index, so
       a generator has to be drained first -- and once generator expressions
       became real generators, `sep.join(f(x) for x in xs)` started arriving
       here as one. It reported "can only join an iterable" about something
       that plainly was one. */
    parts = apy_iterable(parts);
    if (!parts) return 0;
    /* The iterability check is written out rather than left to `apy_raw_len`,
       whose message names the kind (`'int' object is not iterable`) where
       `join`'s does not (`can only join an iterable`). Letting raw_len report
       it would also mean clearing an already-set flag to replace the text,
       which is exactly what the sticky-first-error rule forbids. */
    if (O(parts)->kind != APY_STR_K && !apy_is_seq(parts)
        && !apy_is_set(parts) && O(parts)->kind != APY_DICT_K)
        return apy_fail("TypeError", "can only join an iterable");
    n = apy_raw_len(parts);
    if (apy_error_occurred()) return 0;
    got = (apy_value *)malloc((size_t)(n ? n : 1) * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        got[i] = apy_key_at(parts, i);
        if (!got[i]) { free(got); return 0; }
        /* BYTES TOO -- `b"-".join([b"a", b"b"])` joins bytes. The receiver
           decides the result's kind; see `apy_str_like`. */
        if (O(got[i])->kind != APY_STR_K
                && O(got[i])->kind != APY_BYTES_K) {
            char msg[128];
            snprintf(msg, sizeof msg,
                     "sequence item %lld: expected str instance, %s found",
                     (long long)i, apy_kind_name(got[i]));
            free(got);
            return apy_fail("TypeError", msg);
        }
        len += O(got[i])->v.s.n;
    }
    if (n > 1) len += O(sep)->v.s.n * (n - 1);
    buf = (char *)malloc((size_t)len + 1);
    for (i = 0; i < n; i++) {
        if (i) {
            memcpy(buf + out, O(sep)->v.s.p, (size_t)O(sep)->v.s.n);
            out += O(sep)->v.s.n;
        }
        memcpy(buf + out, O(got[i])->v.s.p, (size_t)O(got[i])->v.s.n);
        out += O(got[i])->v.s.n;
    }
    buf[out] = '\0';
    free(got);
    return apy_str_take(buf, out);
}

/* `replace`. An EMPTY `old` matches in every gap, so `'aaa'.replace('', '-')`
   is '-a-a-a-' -- four replacements in a three-character string. That is the
   case the obvious scan-for-a-match loop cannot express, which is why it is
   written as its own branch instead of falling out of the general one. */
static apy_value apy_replace_impl(apy_value s, apy_value old, apy_value new_,
                                  int64_t limit) {
    int64_t n = O(s)->v.s.n, m = O(old)->v.s.n, k = O(new_)->v.s.n;
    int64_t i, out = 0, hits = 0, cap;
    char *buf;
    cap = (n + 1) * (k + 1) + n + 1;
    buf = (char *)malloc((size_t)cap + 1);
    if (m == 0) {
        for (i = 0; i <= n; i++) {
            if (limit < 0 || hits < limit) {
                memcpy(buf + out, O(new_)->v.s.p, (size_t)k);
                out += k;
                hits++;
            }
            if (i < n) buf[out++] = O(s)->v.s.p[i];
        }
        buf[out] = '\0';
        return apy_str_take(buf, out);
    }
    for (i = 0; i < n; ) {
        if ((limit < 0 || hits < limit) && i + m <= n
            && memcmp(O(s)->v.s.p + i, O(old)->v.s.p, (size_t)m) == 0) {
            memcpy(buf + out, O(new_)->v.s.p, (size_t)k);
            out += k;
            i += m;
            hits++;
        } else buf[out++] = O(s)->v.s.p[i++];
    }
    buf[out] = '\0';
    return apy_str_take(buf, out);
}

APY_API apy_value apy_str_replace(apy_value s, apy_value old, apy_value new_) {
    if (!apy_str_self("replace", s)) return 0;
    if (!apy_str_other("replace", 1, old)) return 0;
    if (!apy_str_other("replace", 2, new_)) return 0;
    return apy_replace_impl(s, old, new_, -1);
}

APY_API apy_value apy_str_replace_n(apy_value s, apy_value old, apy_value new_,
                                    apy_value count) {
    int64_t limit;
    if (!apy_str_self("replace", s)) return 0;
    if (!apy_str_other("replace", 1, old)) return 0;
    if (!apy_str_other("replace", 2, new_)) return 0;
    if (!apy_int_arg(count, &limit)) return 0;
    /* A NEGATIVE count means "all", not "none": `replace(a, b, -1)` replaces
       everything and `replace(a, b, 0)` replaces nothing. */
    return apy_replace_impl(s, old, new_, limit < 0 ? -1 : limit);
}

/* `startswith` / `endswith`, which accept a TUPLE of candidates and answer
   True if any of them matches -- `s.startswith(('a', 'file'))`. A tuple is
   the only container they accept; a list is a TypeError in CPython. */
APY_API int64_t apy_affix1_of(apy_value s, apy_value fix, int64_t lo,
                              int64_t hi, int64_t at_end) {
    int64_t m = O(fix)->v.s.n;
    if (m > hi - lo) return 0;
    return memcmp(O(s)->v.s.p + (at_end ? hi - m : lo),
                  O(fix)->v.s.p, (size_t)m) == 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_affix1(apy_value s, apy_value fix, int64_t lo, int64_t hi,
                      int at_end) {
    return (int)apy_affix1_of(s, fix, lo, hi, at_end);
}

APY_API apy_value apy_affix_of(apy_value s, apy_value fix, apy_value start,
                               apy_value end, int64_t at_end) {
    const char *meth = at_end ? "endswith" : "startswith";
    int64_t lo = 0, hi, i;
    if (!apy_str_self(meth, s)) return 0;
    hi = O(s)->v.s.n;
    if (start && !apy_slice_arg(start, &lo)) return 0;
    if (end && !apy_slice_arg(end, &hi)) return 0;
    apy_clamp_range(O(s)->v.s.n, &lo, &hi);
    if (O(fix)->kind == APY_TUPLE_K) {
        for (i = 0; i < O(fix)->v.q.n; i++) {
            apy_value one = O(fix)->v.q.items[i];
            if (O(one)->kind != APY_STR_K)
                return apy_fail2("TypeError",
                                 "tuple for %s must only contain str, not %s",
                                 meth, apy_kind_name(one));
            if (apy_affix1(s, one, lo, hi, at_end)) return apy_from_bool(1);
        }
        return apy_from_bool(0);
    }
    /* BYTES TOO -- `b"ab".startswith(b"a")` is the same
       operation on the same layout. */
    if (O(fix)->kind != APY_STR_K && O(fix)->kind != APY_BYTES_K)
        return apy_fail2("TypeError",
                         "%s first arg must be str or a tuple of str, not %s",
                         meth, apy_kind_name(fix));
    return apy_from_bool(apy_affix1(s, fix, lo, hi, at_end));
}

/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now, and
   the exported half above stands in when nothing is ported. */
static apy_value apy_affix(apy_value s, apy_value fix, apy_value start,
                           apy_value end, int at_end) {
    return apy_affix_of(s, fix, start, end, (int64_t)at_end);
}

APY_API apy_value apy_str_startswith(apy_value s, apy_value fix) {
    return apy_affix(s, fix, 0, 0, 0);
}
APY_API apy_value apy_str_startswith2(apy_value s, apy_value fix, apy_value start) {
    return apy_affix(s, fix, start, 0, 0);
}
APY_API apy_value apy_str_startswith3(apy_value s, apy_value fix, apy_value start,
                                      apy_value end) {
    return apy_affix(s, fix, start, end, 0);
}
APY_API apy_value apy_str_endswith(apy_value s, apy_value fix) {
    return apy_affix(s, fix, 0, 0, 1);
}
APY_API apy_value apy_str_endswith2(apy_value s, apy_value fix, apy_value start) {
    return apy_affix(s, fix, start, 0, 1);
}
APY_API apy_value apy_str_endswith3(apy_value s, apy_value fix, apy_value start,
                                    apy_value end) {
    return apy_affix(s, fix, start, end, 1);
}

/* --- padding ------------------------------------------------------------ */
/* TWO different messages for two different mistakes: a fill that is not a str
   at all, and a str that is not exactly one character. Collapsing them reports
   `'ab'` as the wrong type and `1` as the wrong length, each of which sends
   the reader looking in the wrong place. */
/* ONE CHARACTER, WHICH MAY BE SEVERAL BYTES. This checked `v.s.n != 1` --
   a BYTE count -- so every fill character above U+007F was rejected as "not
   exactly one character long", which is a sentence about something the
   caller did not do. It answers the fill's bytes now, and the width its
   caller pads with. */
static int apy_fill_char(apy_value fill, const char **out, int64_t *nbytes) {
    /* BYTES TOO, because the receiver may be bytes: `b'ab'.ljust(4, b'*')`
       is Python and this refused it, naming the kind it had just been
       handed. A bytes fill is one ELEMENT when it is one byte, which is the
       same rule `apy_str_chars` applies to a str -- so only the counting
       differs, not the check. */
    if (O(fill)->kind != APY_STR_K && O(fill)->kind != APY_BYTES_K) {
        apy_fail2("TypeError",
                  "The fill character must be a unicode character, not %s%s",
                  apy_kind_name(fill), "");
        return 0;
    }
    if ((O(fill)->kind == APY_BYTES_K ? O(fill)->v.s.n
                                      : apy_str_chars(fill)) != 1) {
        apy_fail("TypeError",
                 "The fill character must be exactly one character long");
        return 0;
    }
    *out = O(fill)->v.s.p;
    *nbytes = O(fill)->v.s.n;
    return 1;
}

enum { APY_LJUST, APY_RJUST, APY_CENTER };

static apy_value apy_pad(apy_value s, apy_value width, apy_value fill, int how) {
    /* A WIDTH IS COUNTED IN CHARACTERS, and this counted bytes: `'e'.ljust(3)`
       with an accented e produced three BYTES, which is one character of
       padding where Python gives two. The receiver's byte length is still
       needed -- it is what gets copied -- so both are kept. */
    int64_t n = apy_str_chars(s), nb = O(s)->v.s.n, w, pad, left;
    int64_t fb = 1, i, out = 0, total;
    const char *fp = " ";
    char *buf;
    if (!apy_int_arg(width, &w)) return 0;
    if (fill && !apy_fill_char(fill, &fp, &fb)) return 0;
    if (w <= n) return s;          /* already wide enough: Python returns it */
    pad = w - n;
    /* CPython's own split for `center`, which is NOT `pad / 2`: it biases the
       extra character to the RIGHT for an even width and to the LEFT for an
       odd one, so `'ab'.center(7, '*')` is '***ab**' and `'ab'.center(3)` is
       ' ab'. Halving alone gets both of those backwards. */
    left = how == APY_RJUST ? pad
         : how == APY_LJUST ? 0
         : pad / 2 + (pad & w & 1);
    /* THE RESULT IS `w` CHARACTERS AND NOT `w` BYTES, so the buffer is the
       receiver's bytes plus one fill character per pad position. `memset` is
       gone with the single-byte assumption it stood on. */
    total = nb + pad * fb;
    buf = (char *)malloc((size_t)total + 1);
    for (i = 0; i < left; i++) { memcpy(buf + out, fp, (size_t)fb); out += fb; }
    memcpy(buf + out, O(s)->v.s.p, (size_t)nb); out += nb;
    for (i = left; i < pad; i++) { memcpy(buf + out, fp, (size_t)fb); out += fb; }
    buf[out] = '\0';
    return apy_str_take(buf, out);
}

APY_API apy_value apy_str_ljust(apy_value s, apy_value w) {
    if (!apy_str_self("ljust", s)) return 0;
    return apy_pad(s, w, 0, APY_LJUST);
}
APY_API apy_value apy_str_ljust_fill(apy_value s, apy_value w, apy_value f) {
    if (!apy_str_self("ljust", s)) return 0;
    return apy_pad(s, w, f, APY_LJUST);
}
APY_API apy_value apy_str_rjust(apy_value s, apy_value w) {
    if (!apy_str_self("rjust", s)) return 0;
    return apy_pad(s, w, 0, APY_RJUST);
}
APY_API apy_value apy_str_rjust_fill(apy_value s, apy_value w, apy_value f) {
    if (!apy_str_self("rjust", s)) return 0;
    return apy_pad(s, w, f, APY_RJUST);
}
APY_API apy_value apy_str_center(apy_value s, apy_value w) {
    if (!apy_str_self("center", s)) return 0;
    return apy_pad(s, w, 0, APY_CENTER);
}
APY_API apy_value apy_str_center_fill(apy_value s, apy_value w, apy_value f) {
    if (!apy_str_self("center", s)) return 0;
    return apy_pad(s, w, f, APY_CENTER);
}

/* `zfill` is not `rjust(w, '0')`: a leading sign stays in FRONT of the zeros,
   so `'-5'.zfill(3)` is '-05' and not '0-5'. */
APY_API apy_value apy_str_zfill(apy_value s, apy_value width) {
    int64_t n, nb, w, pad;
    char *buf;
    int signed_ = 0;
    if (!apy_str_self("zfill", s)) return 0;
    if (!apy_int_arg(width, &w)) return 0;
    /* IN CHARACTERS TOO, for the same reason as `apy_pad` above. A zero is
       one byte, so only the receiver's two lengths can differ here. */
    n = apy_str_chars(s);
    nb = O(s)->v.s.n;
    if (w <= n) return s;
    signed_ = nb > 0 && (O(s)->v.s.p[0] == '-' || O(s)->v.s.p[0] == '+');
    pad = w - n;
    buf = (char *)malloc((size_t)(nb + pad) + 1);
    memset(buf, '0', (size_t)(nb + pad));
    if (signed_) {
        buf[0] = O(s)->v.s.p[0];
        memcpy(buf + 1 + pad, O(s)->v.s.p + 1, (size_t)(nb - 1));
    } else {
        memcpy(buf + pad, O(s)->v.s.p, (size_t)nb);
    }
    buf[nb + pad] = '\0';
    return apy_str_take(buf, nb + pad);
}

"""
