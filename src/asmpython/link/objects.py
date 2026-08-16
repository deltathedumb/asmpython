"""The dynamic object runtime: what a Python value IS at run time.

The Python frontend began as an annotated subset -- `int`, `float`, `bool`,
`None`, every parameter annotated, every expression's type known at compile
time. That is a real language and it compiles well, and it is not Python. A
Python file has unannotated functions, strings, lists, and values whose type is
decided by what flows into them, and none of that can be expressed by a static
type per expression.

So there are two representations, and the boundary between them is the single
most important thing in this file to understand:

  * A function whose parameters and return are ALL annotated stays on the
    static path. Its `int` is a machine word, its `float` an xmm register, and
    nothing here is involved.
  * Everything else -- the module's top-level statements, any function with an
    unannotated parameter -- is DYNAMIC. Every value is an `apy_value`, every
    operation is a call into this file, and the value's type is a field it
    carries rather than a fact the compiler knows.

WHY UNIFORM BOXING, and not NaN-boxing or tagged pointers, which are both
faster: the conformance suite's TAXONOMY.md names "the representation follows
the declared type of the slot the value is stored in, rather than the value"
as the dominant defect of the compiler this replaces -- one root cause that
surfaces as a dozen unrelated-looking bugs, because the symptom depends only on
how the result is read. A single representation for every value, with the type
inside it, makes that failure mode unreachable rather than unlikely. This
compiler is measured on agreeing with CPython, not on speed.

THE CELL. Every value is a pointer to one `struct apy_obj`. `NULL` is never a
valid value, so a null return is unambiguously "an error was set". `None`,
`True` and `False` are single shared cells: a program comparing `x is None`
compares pointers, and three statics cost nothing.

MEMORY IS NEVER FREED. Every constructor mallocs and nothing collects. That is
a real limitation and it is stated rather than left to be discovered: a program
that loops a million string concatenations will grow without bound. It is not a
correctness problem for anything the conformance suite runs, and a collector
needs a stack map the IR does not carry yet.

ERRORS are a sticky flag, not a longjmp. An operation that fails sets it and
returns NULL; the frontend checks it where it needs to. That keeps the policy
question -- how does an exception propagate, what does `try` do -- in the
frontend where it belongs, instead of this file inventing an answer that
`try`/`except` would then have to be built around. Until the frontend grows
real exception handling it calls `apy_fatal_if_error`, which writes
`TypeError: ...` to STDERR and exits 1. Deliberately not stdout: the suite
diffs stdout, and a traceback printed there turns a correctly-failing program
into a wrong answer.

THE FIRST ERROR WINS. `apy_fail` does not overwrite a flag that is already
set, so a frontend that lets a null value flow into a second operation still
reports the ORIGINAL failure rather than whatever the second one made of a
null. Every operation that can fail returns 0 (never a valid value) when it
does, so "did this fail" is answerable without the flag as well.

INTEGERS ARE ARBITRARY PRECISION. This entry used to head the list below --
"64-bit and wrap, `2 ** 64` is 0, the largest single divergence in the file"
-- and the second integer kind the `kind` field was left room for is now
there. A big is NEVER a value that fits an int64: every result demotes, so
each integer has exactly one representation and nothing downstream has to
maintain agreement between two of them. See the arbitrary-precision section.

WHAT IS DELIBERATELY NOT RIGHT YET, stated here rather than left to be found:

  * `len` counts characters, but INDEXING AND SLICING WOULD COUNT BYTES.
    Neither exists in v1; when they arrive they need the same UTF-8 walk
    `apy_str_chars` does.
  * A NEGATIVE BASE WITH A FRACTIONAL EXPONENT is a complex number in Python
    and there is no complex kind here, so `apy_pow` reports a ValueError
    instead of answering. It is the one place this file knowingly raises
    where CPython returns a value.
  * A SET ITERATES IN INSERTION ORDER and CPython's in hash-table slot order,
    so `print(set([3, 1, 2]))` differs. Reproducing CPython's would mean
    reproducing its table growth, its probe sequence and a str hash that is
    salted per process -- there is no fixed answer to match. See the set
    section for the full argument.
  * STRING CASE AND CLASSIFICATION ARE ASCII, and every method that returns a
    POSITION returns a byte offset. `'ß'.upper()` is 'SS' in CPython and 'ß'
    here. Both follow from the two entries above about `len` and indexing.

Everything else in here is differentially tested against CPython by
tools/objects_diff.py, which compiles this source with a C driver and diffs
it case by case. That tool is the reason to trust the sign rules and the
float formatting; the counts it reports are in its docstring.
"""
from __future__ import annotations

#: Storage class for every function the IR can call. Empty for the linked
#: runtime, `static` for the C backend, whose output is one self-contained
#: translation unit. One macro rather than a marker on sixty definitions --
#: the C is then byte-identical in both builds, so they cannot drift.
_API_TOKEN = "@APY_API@"

OBJECTS_C = r"""/* asmpython dynamic object runtime. Generated -- edit link/objects.py. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>

#define APY_API @APY_API@

typedef struct apy_obj apy_obj;

/* The public type is `uintptr_t`, not `apy_obj *`, because that is what the
   IR's `ptr` is and what every backend emits for it -- the C backend types a
   pointer register `uintptr_t` and generates its `extern` declarations from
   the IR signature. A public signature taking `apy_obj *` would be
   ABI-identical and textually disagree with the declaration the backend
   writes, which newer C compilers reject outright. `O()` and `V()` cross the
   two, and nothing outside this file sees an `apy_obj`. */
typedef uintptr_t apy_value;
#define O(v) ((apy_obj *)(v))
#define V(p) ((apy_value)(p))

enum {
    APY_NONE_K = 0, APY_BOOL_K, APY_INT_K, APY_FLOAT_K, APY_STR_K,
    APY_LIST_K, APY_TUPLE_K, APY_DICT_K, APY_EXC_K, APY_SET_K, APY_FROZEN_K,
    APY_FUNC_K, APY_CELL_K, APY_TYPE_K, APY_INST_K, APY_SUPER_K, APY_BIG_K,
    /* `bytes`. Shares the str layout -- a pointer and a length -- because
       that is exactly what it is; what differs is that its length and its
       indexing are in BYTES rather than characters, its repr wears a `b`
       and escapes non-printables, and indexing yields an int. Sharing the
       layout means `apy_str_take` and the slicing arithmetic work on it
       unchanged. */
    APY_BYTES_K,
    /* `complex`. Two doubles, and NOT part of the numeric tower's ordering:
       int/float/bool compare with `<` and complex does not, which is the one
       rule that keeps it from being just a third float. */
    APY_COMPLEX_K,
    /* An ITERATOR: what `iter(x)` returns and `next(it)` advances.

       A container and a position, because iteration here is by INDEX -- there
       is no iterator protocol underneath, so an iterator is a cursor over
       something already indexable rather than a thing with a `__next__`. That
       is a real limitation and a visible one: an iterator over a list sees
       later mutations of it, where CPython's does too, but a generator has no
       container to be a cursor over and is why `yield` needs more than this.
     */
    APY_ITER_K,
    /* `...`. A SINGLETON, like None and the two bools, because `... is
       Ellipsis` is the test programs write and a fresh cell per literal would
       answer False. It has no payload: being itself is all it does. */
    APY_ELLIPSIS_K,
    /* A SUSPENDED FUNCTION. Its locals live here rather than in registers,
       because a register does not survive the return a `yield` compiles to --
       see `apy_gen_new`. */
    APY_GEN_K
};

/* WHAT A CURSOR DOES on the way. A plain `iter(x)` walks; the rest apply
   something as they go, which is what makes `map(f, xs)` lazy -- `f` runs
   when the result is walked, not when it is made. */
enum apy_iter_mode {
    APY_IT_PLAIN = 0,
    APY_IT_MAP,
    APY_IT_FILTER,
    APY_IT_ENUMERATE,
    APY_IT_ZIP
};

struct apy_obj {
    int kind;
    union {
        int64_t i;          /* int, and bool as 0/1 */
        double  f;
        /* An integer too big for `i`. Sign and magnitude, base 2**32, limbs
           little-endian. A big NEVER holds a value that fits `i` -- see the
           arbitrary-precision section for why that invariant is the whole
           design and not an optimisation. */
        struct { uint32_t *limb; int64_t n; int neg; } big;
        struct { const char *p; int64_t n; } s;   /* str: bytes + length */
        struct { double re, im; } z;             /* complex */
        /* A CURSOR. `src` is what it walks and `i` where it is; `fn` and
           `mode` are what it does on the way. `map`, `filter`, `enumerate`
           and `zip` are cursors rather than lists because they are LAZY --
           `map(f, xs)` calls `f` when the result is walked, not when it is
           made, and a program with a side-effecting `f` can tell. */
        struct {
            apy_value src, fn;
            int64_t i;
            int mode;
        } it;
        /* A GENERATOR: the step function, the frame its locals live in, where
           to resume, and what `send` last passed in. */
        struct {
            apy_value step, sent, *slots;
            /* What a LENGTH QUERY drained. `sum(g)` and `sorted(g)` walk by
               index and an index walk needs a length, so the first one to ask
               consumes the generator into a list and every later index reads
               from it -- which keeps the two questions answering about the
               same elements. */
            apy_value cache;
            /* WHAT `return` GAVE, waiting to become `StopIteration.value`.
               A generator's return value is not the call's result -- the call
               already returned the generator -- so this is the only place it
               can live between the `return` and the exception that carries
               it. */
            apy_value result;
            /* An exception to raise AT THE SUSPENSION POINT, from `throw` or
               `close`. Delivered by the resume block rather than by the
               caller, so a `try` inside the generator body catches it -- which
               is the whole difference between `throw` and raising at the call
               site. */
            apy_value pending;
            int64_t n, state;
            int running;
        } g;
        /* list, tuple, set and frozenset share ONE layout. They differ in
           what is allowed -- a tuple never grows, a set never holds two
           equal elements -- and in how they print, and in nothing else; two
           layouts would mean two copies of indexing, repr and equality. */
        struct { apy_value *items; int64_t n, cap; } q;
        struct { apy_value *keys, *vals; int64_t n, cap; } d;
        /* An exception: the type's NAME (a static string from the
           hierarchy table, so comparing types is comparing text) and
           the single argument `str(e)` returns. */
        /* An exception: the type's NAME, the single argument `str(e)`
           returns, and WHETHER there was one. The flag is not redundant with
           `arg`: `E()` and `E(None)` both leave `arg` holding None, and
           `e.args` must be `()` for the first and `(None,)` for the second.
           Without it the second lost its argument. */
        struct {
            const char *name; apy_value arg; int has_arg;
            /* CHAINING. `__context__` is whatever was being handled when this
               one was raised, set implicitly; `__cause__` is what `raise X
               from Y` said, set explicitly. They are separate because
               `raise ... from None` SUPPRESSES the context without having a
               cause, which one field could not express -- and that
               suppression is the whole of PEP 409.

               `notes` is what `add_note` appends, a list or 0. */
            apy_value context, cause, notes;
            int suppress;
            /* WHETHER `arg` is the already-formatted message rather than the
               object the program raised. `apy_error_value` rebuilds an Exc
               from the message text when the error came from an OPERATION,
               and that text is already `'k'` for a KeyError -- so the
               KeyError repr rule below must not apply it a second time. */
            int rendered;
        } e;
        /* A CALLABLE. `code` is the compiled entry, from the IR's FUNC_ADDR;
           `arity` is the count of DECLARED Python parameters, which includes
           `self` for a method. `cells` are the closure boxes this particular
           function object captured -- two closures over one variable share
           the box, which is what makes them see each other's writes.
           `bound` is the receiver of a bound method, or 0.

           DEFAULTS AND `*rest` LIVE IN THE VALUE, not at the call site. A
           direct call resolves both at compile time and never looks here; a
           call through `apy_call` cannot, because it reaches a function whose
           definition the caller never saw -- and every method call is one of
           those. Without this a method could not have a default, which rules
           out `def __init__(self, v=0)`. */
        struct {
            uintptr_t code; int64_t arity; apy_value name;
            apy_value *cells; int64_t ncells; apy_value bound;
            apy_value *defaults; int64_t ndefaults; int vararg;
            /* WHETHER the last declared parameter is `**kw`. A flag and not a
               name, because the only thing any caller needs to know is where
               to put the keywords it could not place -- and that the slot is
               filled even when there are none. */
            int kwarg;
            /* How many TRAILING declared parameters are KEYWORD-ONLY. A
               position cannot reach one, so positional filling stops short of
               them and only a name arrives -- the whole of `*` in a
               signature. Zero for an ordinary function, so the arithmetic
               below is unchanged for every ordinary call. */
            int kwonly;
            /* How many LEADING declared parameters are POSITIONAL-ONLY. Their
               names ARE recorded -- so a call that passes one by keyword can
               be told which mistake it made -- but the matcher skips them,
               which is the whole of `/`. */
            int posonly;
            /* PARAMETER NAMES, in declaration order and including `self`.
               Only a KEYWORD argument needs them, and only a call through a
               value does -- a direct call matches names at compile time. They
               live here because that is the only place the name is still
               known: `apy_call` reaches a function whose `def` the caller
               never saw, so `C(1, swallow=True)` had nowhere to look and
               silently took the default instead. NULL when the frontend did
               not record them, which a keyword call reports rather than
               guesses at. */
            apy_value *pnames;
            /* The DOCSTRING, or 0. Recorded because `f.__doc__` is the one
               piece of a `def` a program routinely reads back, and the
               frontend would otherwise drop it as the bare string statement
               it is. */
            apy_value doc;
        } fn;
        /* One closure variable's box. A captured local lives HERE instead of
           in a register, so the enclosing function and every closure over it
           read and write the same storage. */
        struct { apy_value slot; } cell;
        /* A CLASS: its name as a str value (so `__name__` is just a field),
           its single base or 0, and a dict of everything its body bound --
           methods and class attributes alike. */
        struct { apy_value name, base, dict; } t;
        /* An INSTANCE: what class made it, and its own attribute dict. */
        struct { apy_value cls, dict; } o;
        /* What `super()` evaluates to: the class the calling method was
           DEFINED in, plus the receiver. Attribute lookup starts at that
           class's base, so a two-level hierarchy does not recurse forever. */
        struct { apy_value from, self; } sup;
    } v;
};

/* --- errors ----------------------------------------------------------- */
/* One slot, not a stack: an operation that fails is followed by a check
   before another can fail, because the frontend inserts the check. */
static const char *apy_err_type;
static char apy_err_msg[256];
/* The exception OBJECT, when one was raised rather than an operation failing.
   The type and the message text are enough to report an error and to match a
   handler, and they were all this kept -- so `except E as e` rebuilt an
   exception from them and `e.args[0]` came back as the STRING the payload had
   been rendered to. `raise E(42)` then caught an `E('42')`, which is a
   different value of a different type.

   Zero when the pending error came from an operation rather than a `raise`;
   `apy_error_value` builds one from the text in that case, which is right --
   there was no object. */
static apy_value apy_err_value;

static apy_value apy_fail(const char *type, const char *msg) {
    if (!apy_err_type) {          /* first error wins, like a real traceback */
        apy_err_type = type;
        apy_err_value = 0;
        snprintf(apy_err_msg, sizeof apy_err_msg, "%s", msg);
    }
    return 0;
}

/* An EXPLICIT `raise`, which replaces whatever was pending.
   `apy_fail` keeps the first writer, and that is right for an OPERATION
   failing: after one error the frontend may run more operations before it
   checks, and the second failure is a consequence of the first rather than
   the cause. A `raise` statement is not that -- reaching one means control
   deliberately got there, and Python's rule is that the new exception wins:

       try:
           raise ValueError("original")
       finally:
           raise KeyError("from-finally")   # this is what propagates

   Keeping the first here meant the ValueError escaped and the KeyError was
   discarded, which is the opposite of what the program says.

   The displaced exception becomes the new one's `__context__` in CPython.
   That is not recorded yet, so `e.__context__` is not available; the
   REPLACEMENT is what changes which exception a handler sees, and that part
   is now right. */
static apy_value apy_fail_replacing(const char *type, const char *msg) {
    apy_err_type = type;
    apy_err_value = 0;
    snprintf(apy_err_msg, sizeof apy_err_msg, "%s", msg);
    return 0;
}

static apy_value apy_fail2(const char *type, const char *fmt,
                           const char *a, const char *b) {
    char buf[256];
    snprintf(buf, sizeof buf, fmt, a, b);
    return apy_fail(type, buf);
}

APY_API int64_t apy_error_occurred(void) { return apy_err_type != NULL; }
APY_API void apy_error_clear(void) {
    apy_err_type = NULL;
    apy_err_value = 0;
}

APY_API void apy_fatal_if_error(void) {
    if (!apy_err_type) return;
    fflush(stdout);
    fprintf(stderr, "%s: %s\n", apy_err_type, apy_err_msg);
    exit(1);
}

/* `apy_error_type` / `apy_error_message` are down in the construction section
   instead of here, because they return STR VALUES and so need the allocator.
   The state and the failure path stay up here where every operation that sets
   them can see them. */

/* --- construction ------------------------------------------------------ */
static apy_obj apy_none_cell = { APY_NONE_K, { 0 } };
static apy_obj apy_ellipsis_cell = { APY_ELLIPSIS_K, { 0 } };
static apy_obj apy_true_cell = { APY_BOOL_K, { 1 } };
static apy_obj apy_false_cell = { APY_BOOL_K, { 0 } };

static apy_obj *apy_alloc(int kind) {
    apy_obj *o = (apy_obj *)malloc(sizeof(apy_obj));
    if (!o) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    o->kind = kind;
    /* ZEROED, not merely tagged. An exception carries `context`, `cause` and
       `notes` that most of them never set, and every one of the four places
       that builds one would otherwise have to remember all three -- which is
       the kind of thing that gets remembered in three places and forgotten in
       the fourth, where it reads uninitialised memory as a value. */
    memset(&o->v, 0, sizeof o->v);
    return o;
}

APY_API apy_value apy_none(void) { return V(&apy_none_cell); }

/* `...` and the name `Ellipsis` -- one cell, so `is` answers True. */
APY_API apy_value apy_ellipsis(void) { return V(&apy_ellipsis_cell); }

APY_API apy_value apy_from_bool(int64_t b) {
    return V(b ? &apy_true_cell : &apy_false_cell);
}

/* Small ints are SHARED cells, exactly as CPython caches -5..256, and for the
   same observable reason: `a = 1; b = 1; a is b` is True in CPython, and a
   fresh cell per literal would make it False. The range is CPython's own
   because the answer to `x is y` has to agree at the boundary too -- 256 is
   shared and 257 is not, and a program can see that.

   The table is filled lazily rather than at startup: there is no init hook to
   hang a loop on, and a NULL slot is a cheaper test than a "have I run yet"
   flag. */
#define APY_SMALL_LO (-5)
#define APY_SMALL_HI 256
static apy_obj *apy_small[APY_SMALL_HI - APY_SMALL_LO + 1];

APY_API apy_value apy_from_int(int64_t i) {
    apy_obj *o;
    if (i >= APY_SMALL_LO && i <= APY_SMALL_HI) {
        apy_obj **slot = &apy_small[i - APY_SMALL_LO];
        if (!*slot) { *slot = apy_alloc(APY_INT_K); (*slot)->v.i = i; }
        return V(*slot);
    }
    o = apy_alloc(APY_INT_K);
    o->v.i = i;
    return V(o);
}

APY_API apy_value apy_from_float(double f) {
    apy_obj *o = apy_alloc(APY_FLOAT_K);
    o->v.f = f;
    return V(o);
}

/* A string literal: static storage, so the bytes are borrowed rather than
   copied. Every str the runtime BUILDS mallocs instead; nothing mutates a str,
   so the two are indistinguishable to a reader. */
/* A C string literal, for the runtime's own use. `apy_from_cstr` is the same
   thing for the IR, which hands the address over as an integer because that is
   what a `global_addr` produces and how the C backend types a pointer. */
static apy_value apy_lit(const char *p) {
    apy_obj *o = apy_alloc(APY_STR_K);
    o->v.s.p = p;
    o->v.s.n = (int64_t)strlen(p);
    return V(o);
}

APY_API apy_value apy_from_cstr(apy_value p) {
    apy_obj *o = apy_alloc(APY_STR_K);
    o->v.s.p = (const char *)p;
    o->v.s.n = (int64_t)strlen(o->v.s.p);
    return V(o);
}

/* The same, with the length given rather than found. `apy_from_cstr` stops at
   the first NUL and a Python string may CONTAIN one -- `'\0a'` is a perfectly
   ordinary two-character string, and through `strlen` it becomes the empty
   string, silently and at every use. A literal whose bytes include a NUL has
   to come through here instead; the frontend knows the length at compile
   time, so this costs it nothing. */
/* A bytes LITERAL, from static storage -- the same borrowing `apy_from_cstr`
   does, and for the same reason: the bytes are in the binary's read-only data
   and copying them at every evaluation would allocate for a constant.

   The length is passed rather than measured. A bytes literal may contain a
   NUL (`b"a\\0b"` is three bytes) and `strlen` would silently truncate it,
   which is the one thing bytes must not do -- carrying arbitrary octets is
   what distinguishes it from str. */
/* One HALF of a complex, formatted.

   `repr(1j)` is `1j`, not `1.0j` -- a complex's parts are rendered without
   the trailing `.0` that a bare float gets. CPython does this by passing
   `PyOS_double_to_string` a different flag; here the suffix is removed after
   the fact, which reaches the same text and keeps ONE shortest-round-trip
   implementation rather than two.

   Only an exact `.0` at the end is removed, so `0.5` and `1e-05` are
   untouched and `inf`/`nan` never had one. */
static void apy_complex_part(char *out, size_t n, double v) {
    size_t len;
    py_repr_double(out, n, v);
    len = strlen(out);
    if (len > 2 && out[len - 2] == '.' && out[len - 1] == '0')
        out[len - 2] = 0;
}

APY_API apy_value apy_from_complex(double re, double im) {
    apy_obj *o = apy_alloc(APY_COMPLEX_K);
    o->v.z.re = re;
    o->v.z.im = im;
    return V(o);
}

APY_API apy_value apy_bytes_literal(apy_value p, int64_t n) {
    apy_obj *o = apy_alloc(APY_BYTES_K);
    o->v.s.p = (const char *)(uintptr_t)p;
    o->v.s.n = n;
    return V(o);
}

APY_API apy_value apy_from_bytes(apy_value p, int64_t n) {
    apy_obj *o = apy_alloc(APY_STR_K);
    o->v.s.p = (const char *)p;
    o->v.s.n = n;
    return V(o);
}

static apy_value apy_str_take(char *p, int64_t n) {
    apy_obj *o = apy_alloc(APY_STR_K);
    o->v.s.p = p;
    o->v.s.n = n;
    return V(o);
}

static apy_value apy_str_copy(const char *p, int64_t n) {
    char *buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    memcpy(buf, p, (size_t)n);
    buf[n] = '\0';
    return apy_str_take(buf, n);
}

/* The same bytes under a different kind. Written in terms of `apy_str_copy`
   rather than beside it: the allocation, the NUL and the out-of-memory exit
   are one implementation, and a second copy of them would be a second thing
   to keep right. */
static apy_value apy_bytes_copy(const char *p, int64_t n) {
    apy_value v = apy_str_copy(p, n);
    O(v)->kind = APY_BYTES_K;
    return v;
}


/* The error accessors live down here rather than beside the error state,
   because they hand back STR VALUES and the allocator is only in scope from
   this point on. Both answer None when nothing is set, so a frontend that
   calls them unguarded gets a value rather than a null it would have to
   test -- and None is also what `sys.exc_info()` would say. */
APY_API apy_value apy_error_type(void) {
    return apy_err_type ? apy_lit(apy_err_type) : V(&apy_none_cell);
}

APY_API apy_value apy_error_message(void) {
    return apy_err_type
        ? apy_str_copy(apy_err_msg, (int64_t)strlen(apy_err_msg))
        : V(&apy_none_cell);
}

static uint64_t apy_abs64(int64_t v);
static apy_value apy_lit(const char *p);
static apy_value apy_str_take(char *p, int64_t n);

/* --- arbitrary precision integers --------------------------------------- */
/* Python has ONE integer type and it has no width. Until now this file had a
   64-bit one that wrapped, which the module docstring called the largest
   single divergence in it: `2 ** 64` was 0. This is the second integer kind
   that the `kind` field was left room for.

   THE ONE INVARIANT EVERYTHING ELSE RESTS ON: a big is NEVER a value that
   fits in an int64. Every constructor ends in `apy_big_done`, which trims the
   leading zero limbs and then, if what is left fits, throws the big away and
   returns `apy_from_int` instead. So each integer value has exactly ONE
   representation, and the cross-boundary properties the rest of the runtime
   would otherwise have to maintain by hand fall out for free:

     * `2 ** 100 // 2 ** 100 * 5` IS the small-int cell for 5 -- the same
       pointer `a = 5` gets, so even `is` agrees;
     * equality, ordering, `hash`, `repr` and dict-key identity between "a
       small 5" and "a 5 that came back from a big computation" cannot
       disagree, because there is no second 5 to disagree with;
     * `apy_eq_raw` needs no int-versus-big case at all: different kinds here
       mean different values, always.

   Maintaining that instead -- letting a big hold 5 and teaching six
   operations to compare across the boundary -- is the shape of bug this
   whole file was written to make unreachable, and it would be invisible
   until a program used one as a dict key.

   SIGN AND MAGNITUDE, base 2**32, limbs little-endian. Not two's complement:
   the magnitude algorithms are the ones written down in Knuth and are hard
   enough without a sign folded into every carry, and `&`/`|`/`^` -- the only
   operations Python defines in two's complement -- convert at the edge and
   back, which is 30 lines in one place.

   WHY 32-BIT LIMBS when the machine is 64: multiply and divide both need a
   product twice as wide as a limb, and `uint64_t` is that for a 32-bit limb
   on every C99 toolchain. 64-bit limbs would need `unsigned __int128`, which
   this file already refuses in `apy_int_quot` for the same reason -- it is
   not portable, and this source is compiled by whatever toolchain the target
   uses.

   COST. Multiplication and division are schoolbook, O(n*m); decimal
   conversion is repeated division by 10**9, so O(n**2). No Karatsuba, no
   divide-and-conquer base conversion. `2 ** 500` costs nothing measurable
   and a million-digit number would be slow; the suite's largest is
   `2 ** 1000`. Stated so the next person replaces it deliberately.

   MEMORY IS STILL NEVER FREED -- see the head of this file. Every temporary
   limb array here leaks, including the ones a single `a % b` allocates. */
typedef uint32_t apy_limb;
#define APY_LIMB_BITS 32
#define APY_LIMB_BASE ((uint64_t)1 << 32)

/* The cap on how big a big may get, in limbs -- about 1.2 million decimal
   digits. Python has no such limit and would simply take longer; the reason
   there is one here is that `10 ** 10 ** 9` should report something rather
   than allocate until the machine dies, and an OverflowError naming the
   operation is recoverable where an OOM kill is not. Nothing the conformance
   suite runs comes within four orders of magnitude of it. */
#define APY_BIG_MAX_LIMBS 131072

static apy_value apy_big_too_large(void) {
    return apy_fail("OverflowError",
                    "integer result too large for this implementation");
}

static int apy_is_big(apy_value v) { return O(v)->kind == APY_BIG_K; }

/* A `None` where a slice bound is expected means NOT GIVEN, not an error:
   `'ab'.find('a', None)` is 0. That is how CPython's own argument clinic
   spells an optional index, so every method taking start/end accepts it. */
/* An integer argument that has to fit a MACHINE INDEX. Widening
   `apy_is_int_like` to admit a big made every `O(v)->v.i` behind it a pointer
   read as an integer -- silently, and with a plausible-looking huge number
   coming out. There is no answer to give: a list cannot have 2**100 elements
   and a string cannot be padded to 2**100 columns, so CPython reports, and so
   does this.

   THREE REPORTS, and the pairing is not derivable from anything -- it is what
   CPython happens to raise at each of the three places it converts, so it is
   written out rather than reasoned about:
     APY_IDX_SUB     `[1, 2][2 ** 100]`   IndexError,    "index-sized"
     APY_IDX_REPEAT  `[1, 2] * (2 ** 100)` OverflowError, "index-sized"
     APY_IDX_SIZE    `'ab'.ljust(2 ** 100)` OverflowError, "C ssize_t" */
enum { APY_IDX_SUB, APY_IDX_REPEAT, APY_IDX_SIZE };

static int apy_index_arg(apy_value v, int64_t *out, int form) {
    if (apy_is_big(v)) {
        apy_fail(form == APY_IDX_SUB ? "IndexError" : "OverflowError",
                 form == APY_IDX_SIZE
                   ? "Python int too large to convert to C ssize_t"
                   : "cannot fit 'int' into an index-sized integer");
        return 0;
    }
    *out = O(v)->v.i;
    return 1;
}

static apy_obj *apy_big_alloc(int64_t n) {
    apy_obj *o = apy_alloc(APY_BIG_K);
    if (n < 1) n = 1;
    o->v.big.limb = (apy_limb *)calloc((size_t)n, sizeof(apy_limb));
    if (!o->v.big.limb) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    o->v.big.n = n;
    o->v.big.neg = 0;
    return o;
}

/* Drop leading zero limbs, then DEMOTE if the value fits an int64 -- the
   invariant at the top of this section, enforced in the one place every
   result passes through. A zero-limb magnitude is the integer 0. */
static apy_value apy_big_done(apy_obj *o) {
    int64_t n = o->v.big.n;
    while (n > 0 && o->v.big.limb[n - 1] == 0) n--;
    o->v.big.n = n;
    if (n == 0) return apy_from_int(0);
    if (n <= 2) {
        uint64_t m = o->v.big.limb[0];
        if (n == 2) m |= (uint64_t)o->v.big.limb[1] << 32;
        if (!o->v.big.neg) {
            if (m <= (uint64_t)9223372036854775807ULL)
                return apy_from_int((int64_t)m);
        } else if (m <= (uint64_t)9223372036854775808ULL) {
            /* -2**63 is representable and +2**63 is not, which is why the
               bound differs by one between the two branches. Negating
               through unsigned because negating INT64_MIN is undefined. */
            return apy_from_int((int64_t)(0u - m));
        }
    }
    return V(o);
}

/* An int64 as a magnitude plus a sign, for feeding a mixed operation into the
   big path. Never normalised -- it is an operand, not a result. */
static apy_obj *apy_big_of_i64(int64_t v) {
    apy_obj *o = apy_big_alloc(2);
    uint64_t m = apy_abs64(v);
    o->v.big.limb[0] = (apy_limb)(m & 0xffffffffu);
    o->v.big.limb[1] = (apy_limb)(m >> 32);
    o->v.big.neg = v < 0;
    if (o->v.big.limb[1] == 0) o->v.big.n = o->v.big.limb[0] ? 1 : 0;
    return o;
}

/* Either integer kind as a big object. A bool arrives here too -- `True` is
   1 for arithmetic -- which is why this reads `v.i` rather than checking for
   APY_INT_K alone. */
static apy_obj *apy_as_big(apy_value v) {
    if (O(v)->kind == APY_BIG_K) return O(v);
    return apy_big_of_i64(O(v)->v.i);
}

static int apy_mag_cmp(const apy_obj *a, const apy_obj *b) {
    int64_t i;
    if (a->v.big.n != b->v.big.n) return a->v.big.n < b->v.big.n ? -1 : 1;
    for (i = a->v.big.n - 1; i >= 0; i--)
        if (a->v.big.limb[i] != b->v.big.limb[i])
            return a->v.big.limb[i] < b->v.big.limb[i] ? -1 : 1;
    return 0;
}

static apy_obj *apy_mag_add(const apy_obj *a, const apy_obj *b) {
    int64_t na = a->v.big.n, nb = b->v.big.n, i;
    int64_t n = (na > nb ? na : nb) + 1;
    apy_obj *r = apy_big_alloc(n);
    uint64_t carry = 0;
    for (i = 0; i < n; i++) {
        uint64_t t = carry;
        if (i < na) t += a->v.big.limb[i];
        if (i < nb) t += b->v.big.limb[i];
        r->v.big.limb[i] = (apy_limb)t;
        carry = t >> APY_LIMB_BITS;
    }
    return r;
}

/* |a| - |b|, and the CALLER has established |a| >= |b|. A borrow out of the
   top would mean it had not. */
static apy_obj *apy_mag_sub(const apy_obj *a, const apy_obj *b) {
    int64_t na = a->v.big.n, nb = b->v.big.n, i;
    apy_obj *r = apy_big_alloc(na);
    int64_t borrow = 0;
    for (i = 0; i < na; i++) {
        int64_t t = (int64_t)a->v.big.limb[i] + borrow;
        if (i < nb) t -= (int64_t)b->v.big.limb[i];
        r->v.big.limb[i] = (apy_limb)t;
        /* An arithmetic shift, so this is 0 or -1. C leaves the sign of `>>`
           on a negative value implementation-defined and every toolchain this
           targets makes it arithmetic; `apy_intop` already depends on that
           for Python's `-1 >> 999`. */
        borrow = t >> APY_LIMB_BITS;
    }
    return r;
}

static apy_obj *apy_mag_mul(const apy_obj *a, const apy_obj *b) {
    int64_t na = a->v.big.n, nb = b->v.big.n, i, j;
    apy_obj *r;
    if (na == 0 || nb == 0) return apy_big_alloc(0);
    r = apy_big_alloc(na + nb);
    for (i = 0; i < na; i++) {
        uint64_t carry = 0, ai = a->v.big.limb[i];
        if (!ai) continue;
        for (j = 0; j < nb; j++) {
            uint64_t t = ai * b->v.big.limb[j] + r->v.big.limb[i + j] + carry;
            r->v.big.limb[i + j] = (apy_limb)t;
            carry = t >> APY_LIMB_BITS;
        }
        /* The carry cannot run past `na + nb` limbs: the product of an
           na-limb and an nb-limb number needs at most that many. */
        r->v.big.limb[i + nb] = (apy_limb)((uint64_t)r->v.big.limb[i + nb] + carry);
    }
    return r;
}

/* Drop leading zero limbs. EVERY magnitude that another magnitude routine
   will read has to come through here, not just the ones that become results:
   `apy_mag_cmp` compares limb COUNTS first, and Knuth's normalisation step
   spins forever on a top limb of zero. The shifts allocate a limb they may
   not need, so they are the two that must trim before returning -- found by
   `10 ** 30 / 7` hanging, where the shifted divisor had a zero on top. */
static apy_obj *apy_mag_trim(apy_obj *o) {
    while (o->v.big.n > 0 && o->v.big.limb[o->v.big.n - 1] == 0) o->v.big.n--;
    return o;
}

static apy_obj *apy_mag_shl(const apy_obj *a, int64_t bits) {
    int64_t words = bits / APY_LIMB_BITS, off = bits % APY_LIMB_BITS, i;
    apy_obj *r;
    if (a->v.big.n == 0) return apy_big_alloc(0);
    r = apy_big_alloc(a->v.big.n + words + 1);
    for (i = 0; i < a->v.big.n; i++) {
        uint64_t t = (uint64_t)a->v.big.limb[i] << off;
        r->v.big.limb[i + words] |= (apy_limb)t;
        /* `off` of 0 would make the second store a shift by 32, which is
           undefined in C -- the same trap `apy_intop` documents for `<< 64`.
           Skipping it is correct as well as safe: there is nothing to carry. */
        if (off) r->v.big.limb[i + words + 1] |= (apy_limb)(t >> APY_LIMB_BITS);
    }
    return apy_mag_trim(r);
}

/* A LOGICAL right shift of the magnitude. `lost` reports whether any 1 bit
   fell off the bottom, which is what the arithmetic `>>` needs to floor a
   negative value correctly. */
static apy_obj *apy_mag_shr(const apy_obj *a, int64_t bits, int *lost) {
    int64_t words = bits / APY_LIMB_BITS, off = bits % APY_LIMB_BITS, i;
    apy_obj *r;
    *lost = 0;
    for (i = 0; i < words && i < a->v.big.n; i++)
        if (a->v.big.limb[i]) *lost = 1;
    if (off && words < a->v.big.n
        && (a->v.big.limb[words] & (((apy_limb)1 << off) - 1))) *lost = 1;
    if (words >= a->v.big.n) return apy_big_alloc(0);
    r = apy_big_alloc(a->v.big.n - words);
    for (i = 0; i + words < a->v.big.n; i++) {
        uint64_t t = a->v.big.limb[i + words] >> off;
        if (off && i + words + 1 < a->v.big.n)
            t |= (uint64_t)a->v.big.limb[i + words + 1] << (APY_LIMB_BITS - off);
        r->v.big.limb[i] = (apy_limb)t;
    }
    return apy_mag_trim(r);
}

static int64_t apy_mag_bits(const apy_obj *a) {
    apy_limb top;
    int64_t bits;
    if (a->v.big.n == 0) return 0;
    top = a->v.big.limb[a->v.big.n - 1];
    bits = (a->v.big.n - 1) * (int64_t)APY_LIMB_BITS;
    while (top) { bits++; top >>= 1; }
    return bits;
}

/* Knuth's Algorithm D, with the single-limb divisor split out because that
   case is most of the traffic (`% 97`, `// 7`, and the decimal conversion's
   `/ 10**9`) and Algorithm D would be pure overhead for it.

   Magnitudes only. Python's floor-toward-negative-infinity rules are applied
   by the callers, which already know how to do that for int64. */
static void apy_mag_divmod(const apy_obj *a, const apy_obj *b,
                           apy_obj **qo, apy_obj **ro) {
    int64_t n = b->v.big.n, m, i, j;
    int sh = 0, lost;
    apy_obj *u, *vv, *q;
    if (apy_mag_cmp(a, b) < 0) {
        /* The quotient is 0 and the remainder is the dividend, whole. */
        apy_obj *r = apy_big_alloc(a->v.big.n ? a->v.big.n : 1);
        for (i = 0; i < a->v.big.n; i++) r->v.big.limb[i] = a->v.big.limb[i];
        *qo = apy_big_alloc(0);
        *ro = r;
        return;
    }
    if (n == 1) {
        uint64_t d = b->v.big.limb[0], rem = 0;
        apy_obj *r;
        q = apy_big_alloc(a->v.big.n);
        for (i = a->v.big.n - 1; i >= 0; i--) {
            uint64_t cur = (rem << APY_LIMB_BITS) | a->v.big.limb[i];
            q->v.big.limb[i] = (apy_limb)(cur / d);
            rem = cur % d;
        }
        r = apy_big_alloc(1);
        r->v.big.limb[0] = (apy_limb)rem;
        *qo = q;
        *ro = r;
        return;
    }
    /* NORMALISE so the divisor's top limb has its high bit set. That is what
       bounds the trial quotient's error to at most 2, which is what makes the
       correction below a fixed two steps rather than a search. */
    {
        apy_limb top = b->v.big.limb[n - 1];
        while (!(top & 0x80000000u)) { top <<= 1; sh++; }
    }
    u = apy_mag_shl(a, sh);
    vv = apy_mag_shl(b, sh);
    vv->v.big.n = n;                 /* the shift cannot lengthen the divisor */
    while (vv->v.big.n > 0 && vv->v.big.limb[vv->v.big.n - 1] == 0) vv->v.big.n--;
    n = vv->v.big.n;
    m = a->v.big.n - n;
    /* `u` needs a->n + 1 limbs so that u[j+n] always exists; apy_mag_shl
       allocated a->n + 1 already. */
    u->v.big.n = a->v.big.n + 1;
    q = apy_big_alloc(m + 1);
    for (j = m; j >= 0; j--) {
        uint64_t num = ((uint64_t)u->v.big.limb[j + n] << APY_LIMB_BITS)
                     | u->v.big.limb[j + n - 1];
        uint64_t qhat = num / vv->v.big.limb[n - 1];
        uint64_t rhat = num % vv->v.big.limb[n - 1];
        int64_t borrow = 0;
        uint64_t carry = 0;
        while (qhat >= APY_LIMB_BASE
               || (n >= 2
                   && qhat * vv->v.big.limb[n - 2]
                      > ((rhat << APY_LIMB_BITS) | u->v.big.limb[j + n - 2]))) {
            qhat--;
            rhat += vv->v.big.limb[n - 1];
            if (rhat >= APY_LIMB_BASE) break;
        }
        for (i = 0; i < n; i++) {
            uint64_t p = qhat * vv->v.big.limb[i] + carry;
            int64_t t;
            carry = p >> APY_LIMB_BITS;
            t = (int64_t)u->v.big.limb[i + j] - (int64_t)(apy_limb)p + borrow;
            u->v.big.limb[i + j] = (apy_limb)t;
            borrow = t >> APY_LIMB_BITS;
        }
        {
            int64_t t = (int64_t)u->v.big.limb[j + n] - (int64_t)carry + borrow;
            u->v.big.limb[j + n] = (apy_limb)t;
            borrow = t >> APY_LIMB_BITS;
        }
        if (borrow) {
            /* The trial quotient was one too big after all -- which happens
               for about one divisor in 2**31, so it is nearly dead code and
               is exactly the branch a hand-written test would never reach.
               objects_diff runs enough random pairs to. */
            uint64_t c = 0;
            qhat--;
            for (i = 0; i < n; i++) {
                uint64_t t = (uint64_t)u->v.big.limb[i + j]
                           + vv->v.big.limb[i] + c;
                u->v.big.limb[i + j] = (apy_limb)t;
                c = t >> APY_LIMB_BITS;
            }
            u->v.big.limb[j + n] = (apy_limb)((uint64_t)u->v.big.limb[j + n] + c);
        }
        q->v.big.limb[j] = (apy_limb)qhat;
    }
    u->v.big.n = n;                  /* the remainder is what is left in u */
    *qo = q;
    *ro = apy_mag_shr(u, sh, &lost);
}

/* --- big: signed arithmetic --------------------------------------------- */
/* `bneg` is passed rather than read, so that subtraction can flip the sign of
   the right operand without mutating a value some other name still holds. */
static apy_value apy_big_addsub(apy_obj *a, apy_obj *b, int aneg, int bneg) {
    apy_obj *r;
    if (aneg == bneg) {
        r = apy_mag_add(a, b);
        r->v.big.neg = aneg;
    } else {
        int c = apy_mag_cmp(a, b);
        if (c == 0) return apy_from_int(0);
        if (c > 0) { r = apy_mag_sub(a, b); r->v.big.neg = aneg; }
        else       { r = apy_mag_sub(b, a); r->v.big.neg = bneg; }
    }
    if (r->v.big.n > APY_BIG_MAX_LIMBS) return apy_big_too_large();
    return apy_big_done(r);
}

static apy_value apy_big_mul(apy_obj *a, apy_obj *b) {
    apy_obj *r;
    if ((a->v.big.n + b->v.big.n) > APY_BIG_MAX_LIMBS)
        return apy_big_too_large();
    r = apy_mag_mul(a, b);
    r->v.big.neg = a->v.big.neg != b->v.big.neg;
    return apy_big_done(r);
}

/* `//` and `%` together, because Python's floor rule needs both: the quotient
   is decremented and the remainder shifted by the divisor exactly when the
   signs differ and the division was not exact. This is the same correction
   the int64 path makes, over magnitudes instead of over C's truncation. */
static void apy_big_floordivmod(apy_obj *a, apy_obj *b,
                                apy_value *qout, apy_value *rout) {
    apy_obj *q, *r;
    int neg = a->v.big.neg != b->v.big.neg;
    apy_mag_divmod(a, b, &q, &r);
    {
        int64_t i, rn = r->v.big.n;
        int nonzero = 0;
        for (i = 0; i < rn; i++) if (r->v.big.limb[i]) { nonzero = 1; break; }
        q->v.big.neg = neg;
        r->v.big.neg = a->v.big.neg;
        if (neg && nonzero) {
            /* floor(-x) is -(x) - 1 when the division left something over,
               and the remainder becomes divisor - |r| with the DIVISOR's
               sign. `-7 // 2` is -4 and `-7 % 2` is 1. */
            apy_obj *one = apy_big_of_i64(1);
            apy_obj *q2 = apy_mag_add(q, one);
            apy_obj *r2 = apy_mag_sub(b, r);
            q2->v.big.neg = 1;
            r2->v.big.neg = b->v.big.neg;
            q = q2;
            r = r2;
        }
    }
    *qout = apy_big_done(q);
    *rout = apy_big_done(r);
}

/* --- big: text ----------------------------------------------------------- */
/* Repeated division by 10**9, nine decimal digits at a time. O(n**2) and
   said so at the top of the section. Nine and not ten because 10**9 is the
   largest power of ten that fits a limb, which is what keeps the inner
   division single-limb. */
static apy_value apy_big_text(const apy_obj *o) {
    int64_t n = o->v.big.n, cap, out = 0, i, nw = n;
    apy_limb *w;
    char *buf, *rev;
    if (n == 0) return apy_lit("0");
    /* A limb is 32 bits, so it is worth at most 9.633 decimal digits; ten per
       limb plus the sign and the NUL is always enough. */
    cap = n * 10 + 4;
    w = (apy_limb *)malloc((size_t)n * sizeof(apy_limb));
    buf = (char *)malloc((size_t)cap + 1);
    rev = (char *)malloc((size_t)cap + 1);
    for (i = 0; i < n; i++) w[i] = o->v.big.limb[i];
    while (nw > 0) {
        uint64_t rem = 0;
        int k;
        for (i = nw - 1; i >= 0; i--) {
            uint64_t cur = (rem << APY_LIMB_BITS) | w[i];
            w[i] = (apy_limb)(cur / 1000000000u);
            rem = cur % 1000000000u;
        }
        while (nw > 0 && w[nw - 1] == 0) nw--;
        /* Every chunk but the LAST is zero-padded to nine digits: the leading
           zeros are real digits in the middle of the number. Only the most
           significant chunk drops them, and it is the one produced last. */
        for (k = 0; k < 9; k++) {
            rev[out++] = (char)('0' + (int)(rem % 10));
            rem /= 10;
            if (nw == 0 && rem == 0) break;
        }
    }
    if (o->v.big.neg) rev[out++] = '-';
    for (i = 0; i < out; i++) buf[i] = rev[out - 1 - i];
    buf[out] = '\0';
    free(w);
    free(rev);
    return apy_str_take(buf, out);
}

/* `int('...')` for a decimal string of any length. Nine digits at a time for
   the same reason the other direction takes nine: one limb-sized multiply
   and add per chunk instead of one per digit. Returns 0 with no error set
   when a character is not a digit, so the caller can report the whole
   literal rather than the position. */
static apy_value apy_big_from_digits(const char *p, int64_t n, int neg) {
    apy_obj *acc;
    int64_t i = 0, used = 0, cap;
    if (n == 0) return 0;
    if (n > (int64_t)APY_BIG_MAX_LIMBS * 9) return apy_big_too_large();
    /* Sized ONCE, from the digit count. A decimal digit is under 3.33 bits,
       so n digits need at most n/9.63 limbs; n/9 + 2 is that with room to
       spare. The first version grew the accumulator by two limbs per chunk
       and copied it each time, which is quadratic in MEMORY as well as in
       time -- fine for the 31 digits of `2 ** 100` and several terabytes of
       leaked intermediates at the limb cap, on a runtime that frees
       nothing. */
    cap = n / 9 + 2;
    acc = apy_big_alloc(cap);
    while (i < n) {
        uint64_t chunk = 0, scale = 1, carry;
        int64_t j;
        for (j = 0; j < 9 && i < n; j++, i++) {
            if (p[i] < '0' || p[i] > '9') return 0;
            chunk = chunk * 10 + (uint64_t)(p[i] - '0');
            scale *= 10;
        }
        /* `limb * scale` is under 2**62 -- a 32-bit limb by a scale under
           2**30 -- so the running carry never leaves a uint64. */
        carry = chunk;
        for (j = 0; j < used; j++) {
            uint64_t t = (uint64_t)acc->v.big.limb[j] * scale + carry;
            acc->v.big.limb[j] = (apy_limb)t;
            carry = t >> APY_LIMB_BITS;
        }
        while (carry && used < cap) {
            acc->v.big.limb[used++] = (apy_limb)carry;
            carry >>= APY_LIMB_BITS;
        }
    }
    acc->v.big.n = used;
    acc->v.big.neg = neg;
    return apy_big_done(acc);
}

/* An integer LITERAL too large for a machine word.

   The frontend cannot emit one as a constant: `9223372036854775808` is one
   more than int64 holds, and the IR's `const` is a machine word. So a literal
   outside the 64-bit range travels as its DECIMAL TEXT and is parsed here --
   the same parser `int('...')` uses, so the two cannot disagree about a
   number that appears both ways in one program.

   `apy_big_from_digits` normalises: a value that fits a word comes back as an
   ordinary int, which is what keeps `5` and a promoted `5` indistinguishable.
   That property is the one most easily lost when big integers are added, and
   it is why this does not simply always build a big. */
APY_API apy_value apy_int_literal(apy_value digits, int64_t n, int64_t neg) {
    return apy_big_from_digits((const char *)(uintptr_t)digits, n, (int)neg);
}

/* --- big: floats --------------------------------------------------------- */
/* The magnitude as a double, correctly rounded, or an infinity the caller
   turns into an OverflowError. Built from the top 64 bits with a sticky bit
   for everything below, then rounded to 53 once -- the same shape as
   `apy_int_quot`, and for the same reason: rounding twice is how a
   last-digit disagreement gets in. */
/* The 64 bits starting at bit `from`, counting from the bottom of the
   magnitude, zero-filled past the top. Reading a bit RANGE rather than a run
   of limbs is the whole point: the top limb of a big carries between 1 and 32
   significant bits, so "the top two limbs" is between 33 and 64 bits of value
   and only sometimes the 64 that a correct rounding needs. Taking the top two
   limbs was the first version, and `float(10 ** 30)` came out as
   9.99999999994923e+29 -- a 4-bit top limb meant 28 bits of the number were
   dropped into the sticky flag instead of into the mantissa. */
static uint64_t apy_mag_window(const apy_obj *o, int64_t from) {
    int64_t w = from / APY_LIMB_BITS, off = from % APY_LIMB_BITS;
    uint64_t r;
#define APY_L(k) ((uint64_t)((k) < o->v.big.n && (k) >= 0 ? o->v.big.limb[k] : 0))
    r = APY_L(w) >> off;
    r |= APY_L(w + 1) << (APY_LIMB_BITS - off);
    /* Only when `off` is non-zero does a third limb reach into the window --
       and only then is the shift below 64, which is what makes it defined. */
    if (off) r |= APY_L(w + 2) << (2 * APY_LIMB_BITS - off);
    return r;
#undef APY_L
}

static double apy_big_double(const apy_obj *o) {
    int64_t nbits = apy_mag_bits(o), from, i, w, off;
    uint64_t head;
    int sticky = 0;
    if (nbits == 0) return 0.0;
    if (nbits <= 64) {
        /* Exact in a uint64, and C's uint64-to-double conversion rounds to
           nearest, so there is nothing left for this function to decide. */
        head = apy_mag_window(o, 0);
        return o->v.big.neg ? -(double)head : (double)head;
    }
    from = nbits - 64;
    head = apy_mag_window(o, from);        /* top bit set, so exactly 64 bits */
    w = from / APY_LIMB_BITS;
    off = from % APY_LIMB_BITS;
    for (i = 0; i < w; i++) if (o->v.big.limb[i]) { sticky = 1; break; }
    if (!sticky && off && (o->v.big.limb[w] & (((apy_limb)1 << off) - 1)))
        sticky = 1;
    {
        /* Round 64 bits down to 53, once, nearest-even -- and a tie is only a
           tie when nothing nonzero was dropped below it, which is what
           `sticky` records. Same rule as `apy_int_quot`. */
        int drop = 64 - 53;
        uint64_t mask = ((uint64_t)1 << drop) - 1;
        uint64_t low = head & mask, half = (uint64_t)1 << (drop - 1);
        head >>= drop;
        from += drop;
        if (low > half || (low == half && (sticky || (head & 1)))) head++;
    }
    return o->v.big.neg ? -ldexp((double)head, (int)from)
                        : ldexp((double)head, (int)from);
}

/* A double whose magnitude is at least 2**63, and therefore an exact integer,
   as a big. `frexp` gives the mantissa and exponent without any rounding, so
   nothing here can lose a bit. */
static apy_value apy_big_from_double(double f) {
    int e;
    double m = frexp(fabs(f), &e);
    uint64_t mant = (uint64_t)ldexp(m, 53);
    apy_obj *o;
    e -= 53;
    o = apy_big_of_i64((int64_t)mant);
    if (e > 0) {
        apy_obj *sh = apy_mag_shl(o, e);
        o = sh;
    }
    o->v.big.neg = f < 0;
    return apy_big_done(o);
}

/* --- big: bitwise -------------------------------------------------------- */
/* `&`, `|` and `^` are the only integer operations Python defines in INFINITE
   TWO'S COMPLEMENT rather than on the magnitude: `-1` is an endless run of
   1 bits, so `5 & -1` is 5 and `~0` is -1. Sign-magnitude cannot express
   that, so both operands are converted to a two's-complement limb array one
   limb longer than either needs -- which guarantees the top limb is pure sign
   and the result's sign bit is unambiguous -- and converted back after. */
static void apy_to_twos(const apy_obj *o, apy_limb *out, int64_t n) {
    int64_t i;
    uint64_t carry = 1;
    for (i = 0; i < n; i++) {
        apy_limb w = i < o->v.big.n ? o->v.big.limb[i] : 0;
        if (o->v.big.neg) {
            uint64_t t = (uint64_t)(apy_limb)~w + carry;
            out[i] = (apy_limb)t;
            carry = t >> APY_LIMB_BITS;
        } else {
            out[i] = w;
        }
    }
}

static apy_value apy_big_bitop(apy_obj *a, apy_obj *b, int which) {
    int64_t n = (a->v.big.n > b->v.big.n ? a->v.big.n : b->v.big.n) + 1, i;
    apy_limb *ua = (apy_limb *)malloc((size_t)n * sizeof(apy_limb));
    apy_limb *ub = (apy_limb *)malloc((size_t)n * sizeof(apy_limb));
    apy_obj *r = apy_big_alloc(n);
    int neg;
    apy_to_twos(a, ua, n);
    apy_to_twos(b, ub, n);
    for (i = 0; i < n; i++) {
        switch (which) {
        case 0: r->v.big.limb[i] = ua[i] & ub[i]; break;
        case 1: r->v.big.limb[i] = ua[i] | ub[i]; break;
        default: r->v.big.limb[i] = ua[i] ^ ub[i]; break;
        }
    }
    free(ua);
    free(ub);
    neg = (r->v.big.limb[n - 1] & 0x80000000u) != 0;
    if (neg) {
        /* Back out of two's complement: negate, which is complement plus one,
           and record the sign separately. */
        uint64_t carry = 1;
        for (i = 0; i < n; i++) {
            uint64_t t = (uint64_t)(apy_limb)~r->v.big.limb[i] + carry;
            r->v.big.limb[i] = (apy_limb)t;
            carry = t >> APY_LIMB_BITS;
        }
        r->v.big.neg = 1;
    }
    return apy_big_done(r);
}

/* `<<` is an exact multiply by a power of two, so sign-magnitude handles it
   untouched. `>>` FLOORS, which for a negative value is not the same as
   shifting the magnitude: `-1 >> 10` is -1, not 0, because flooring rounds
   away from zero. Hence the `lost` bit -- if anything fell off the bottom of
   a negative value, the magnitude gains one. */
static apy_value apy_big_shift(apy_obj *a, int64_t bits, int left) {
    apy_obj *r;
    int lost = 0;
    if (left) {
        if ((a->v.big.n + bits / APY_LIMB_BITS + 2) > APY_BIG_MAX_LIMBS)
            return apy_big_too_large();
        r = apy_mag_shl(a, bits);
    } else {
        r = apy_mag_shr(a, bits, &lost);
        if (a->v.big.neg && lost) {
            apy_obj *one = apy_big_of_i64(1);
            r = apy_mag_add(r, one);
        }
    }
    r->v.big.neg = a->v.big.neg;
    return apy_big_done(r);
}

/* --- big: division to a float -------------------------------------------- */
/* `a / b` for two integers, correctly rounded, when either is too big for
   `apy_int_quot`. Same shape as that function and for the same reason: the
   quotient of the two EXACT integers, rounded once. Dividing the two doubles
   instead would round three times, and past 2**53 the conversions alone are
   already wrong.

   The dividend is shifted left far enough that the quotient carries at least
   55 bits -- two more than a double's significand, which is what leaves a
   guard bit and a round bit to decide the last one with. */
static double apy_big_quot(apy_obj *a, apy_obj *b) {
    int64_t ba = apy_mag_bits(a), bb = apy_mag_bits(b);
    int64_t shift = 55 + bb - ba, e, i, qn;
    apy_obj *num, *den, *q, *r;
    uint64_t head = 0;
    int sticky = 0, drop, hb;
    if (ba == 0) return 0.0;
    if (shift > 0) { num = apy_mag_shl(a, shift); den = b; }
    else           { num = a; den = apy_mag_shl(b, -shift); }
    apy_mag_divmod(num, den, &q, &r);
    for (i = 0; i < r->v.big.n; i++) if (r->v.big.limb[i]) { sticky = 1; break; }
    while (q->v.big.n > 0 && q->v.big.limb[q->v.big.n - 1] == 0) q->v.big.n--;
    qn = q->v.big.n;
    if (qn == 0) return 0.0;
    e = -shift;
    if (qn == 1) {
        head = q->v.big.limb[0];
    } else {
        head = ((uint64_t)q->v.big.limb[qn - 1] << APY_LIMB_BITS)
             | q->v.big.limb[qn - 2];
        for (i = qn - 3; i >= 0; i--)
            if (q->v.big.limb[i]) { sticky = 1; break; }
        e += (qn - 2) * (int64_t)APY_LIMB_BITS;
    }
    hb = 0;
    { uint64_t t = head; while (t) { hb++; t >>= 1; } }
    drop = hb - 53;
    if (drop > 0) {
        uint64_t mask = ((uint64_t)1 << drop) - 1;
        uint64_t low = head & mask, half = (uint64_t)1 << (drop - 1);
        head >>= drop;
        e += drop;
        if (low > half || (low == half && (sticky || (head & 1)))) head++;
    }
    return ldexp((double)head, (int)e);
}

/* --- big: comparison ----------------------------------------------------- */
static int apy_big_cmp(const apy_obj *a, const apy_obj *b) {
    int c;
    if (a->v.big.neg != b->v.big.neg) return a->v.big.neg ? -1 : 1;
    c = apy_mag_cmp(a, b);
    return a->v.big.neg ? -c : c;
}

/* --- big: base 2, 8 and 16 ----------------------------------------------- */
/* A power-of-two base needs no division at all: each output digit is a fixed
   run of bits. That is why `bin`, `oct` and `hex` are cheap on a big where
   `str` is quadratic. */
static apy_value apy_big_base_text(const apy_obj *o, int bits_per,
                                   const char *prefix) {
    int64_t nbits = apy_mag_bits(o), ndig, i, out = 0;
    char *buf;
    if (nbits == 0) ndig = 1;
    else ndig = (nbits + bits_per - 1) / bits_per;
    buf = (char *)malloc((size_t)ndig + 4);
    if (o->v.big.neg) buf[out++] = '-';
    buf[out++] = prefix[0];
    buf[out++] = prefix[1];
    for (i = ndig - 1; i >= 0; i--) {
        int64_t bit = i * bits_per;
        int64_t w = bit / APY_LIMB_BITS, off = bit % APY_LIMB_BITS;
        uint64_t chunk = w < o->v.big.n ? (o->v.big.limb[w] >> off) : 0;
        if (off && w + 1 < o->v.big.n)
            chunk |= (uint64_t)o->v.big.limb[w + 1] << (APY_LIMB_BITS - off);
        buf[out++] = "0123456789abcdef"[chunk & (((uint64_t)1 << bits_per) - 1)];
    }
    buf[out] = '\0';
    return apy_str_take(buf, out);
}

static int64_t apy_big_popcount(const apy_obj *o) {
    int64_t i, n = 0;
    for (i = 0; i < o->v.big.n; i++) {
        apy_limb w = o->v.big.limb[i];
        while (w) { n += w & 1; w >>= 1; }
    }
    return n;
}

/* --- sequences --------------------------------------------------------- */
static int apy_is_int_like(apy_value v);
static int apy_eq_raw(apy_value a, apy_value b);
static apy_value apy_text(apy_value v, int quoted);

/* `str(e)` is the ARGUMENT alone and `repr(e)` is `ValueError('x')`.
   Printing an exception shows its message, which is why the two differ
   here and not for any other kind. */
static apy_value apy_exc_text(apy_value v, int quoted) {
    apy_value arg = O(v)->v.e.arg;
    /* WHETHER there was an argument, not whether it is None. `str(E())` is
       empty and `str(E(None))` is "None"; `repr` shows `E()` and `E(None)`.
       Testing the argument's kind conflated the two, so an exception
       deliberately carrying None lost it. */
    int has = O(v)->v.e.has_arg;
    if (!quoted)
        /* `str(KeyError('k'))` is `"'k'"` -- the REPR of the argument, not the
           argument. KeyError alone does this, so that a missing key whose text
           is empty or is itself a message is still visible in the report. */
        return !has ? apy_lit("")
                    : apy_text(arg, !O(v)->v.e.rendered
                                    && strcmp(O(v)->v.e.name, "KeyError") == 0);
    {
        apy_value shown = !has ? apy_lit("") : apy_text(arg, 1);
        int64_t n = (int64_t)strlen(O(v)->v.e.name) + O(shown)->v.s.n + 2;
        char *buf = (char *)malloc((size_t)n + 1);
        int64_t out = (int64_t)strlen(O(v)->v.e.name);
        memcpy(buf, O(v)->v.e.name, (size_t)out);
        buf[out++] = '(';
        memcpy(buf + out, O(shown)->v.s.p, (size_t)O(shown)->v.s.n);
        out += O(shown)->v.s.n;
        buf[out++] = ')';
        buf[out] = 0;
        return apy_str_take(buf, out);
    }
}
APY_API apy_value apy_repr(apy_value v);
static apy_value apy_lit(const char *p);
APY_API apy_value apy_getitem(apy_value seq, apy_value index);
static apy_value apy_dict_text(apy_value v);
/* Declared with APY_API, not `static`: these two are host functions the IR
   can call, so their storage class has to match their definition -- and in
   the linked build that is external. A `static` forward declaration of an
   external definition is a C error, not a warning. */
APY_API apy_value apy_str(apy_value v);
APY_API apy_value apy_none(void);
static apy_value apy_fail(const char *type, const char *msg);
static apy_value apy_fail2(const char *type, const char *fmt,
                           const char *a, const char *b);
static apy_value apy_dict_get(apy_value d, apy_value key);
APY_API apy_value apy_dict_set(apy_value d, apy_value key, apy_value val);
static const char *apy_kind_name(apy_value v);
static apy_value apy_bytes_repr(apy_value v);
static apy_value apy_str_copy(const char *p, int64_t n);
/* The class machinery, declared here and defined at the very bottom of this
   file. The order is deliberate and it is the reverse of the dependency: the
   operators -- `+`, `str`, `len`, `==` -- were all written against a closed
   set of kinds, and an instance dispatching to a user method is a hook INTO
   each of them. Defining the hooks first would put four hundred lines of
   class machinery between the reader and the arithmetic; declaring them here
   costs eight lines and leaves each operator reading as itself with one
   `if (...) dispatch` at the top. */
static apy_value apy_dunder(apy_value v, const char *name);
static apy_value apy_unary_dunder(apy_value v, const char *name);
static apy_value apy_method1(apy_value v, const char *name, apy_value arg);
static apy_value apy_binary_dunder(apy_value a, apy_value b,
                                   const char *name, const char *rname);
static int apy_either_inst(apy_value a, apy_value b);
static apy_value apy_call_n(apy_value f, apy_value *argv, int64_t argc);
static apy_value apy_invoke(apy_value f, apy_value *a, int64_t n);
static apy_value apy_type_of(apy_value v);
APY_API apy_value apy_copy(apy_value v);
APY_API apy_value apy_update(apy_value target, apy_value src);
APY_API apy_value apy_extend(apy_value seq, apy_value other);
APY_API apy_value apy_bitor(apy_value a, apy_value b);
APY_API apy_value apy_bitand(apy_value a, apy_value b);
APY_API apy_value apy_bitxor(apy_value a, apy_value b);
APY_API apy_value apy_sub(apy_value a, apy_value b);
APY_API apy_value apy_mul(apy_value a, apy_value b);
APY_API apy_value apy_add(apy_value a, apy_value b);
APY_API apy_value apy_to_dict(apy_value src);
APY_API apy_value apy_iter(apy_value v);
APY_API apy_value apy_getiter(apy_value v);
APY_API apy_value apy_step(apy_value it);
APY_API apy_value apy_stop(void);
static apy_value apy_drain_cursor(apy_value it);
static apy_value apy_cursor(apy_value src, apy_value fn, int mode,
                            int64_t start);
APY_API apy_value apy_gen_next(apy_value g, apy_value fallback,
                               int64_t has_default);
APY_API apy_value apy_gen_drain(apy_value g);
static apy_value apy_gen_step(apy_value g, apy_value sent, int *done);
APY_API apy_value apy_iterable(apy_value v);
APY_API apy_value apy_isinstance(apy_value v, apy_value type_name);
APY_API int64_t apy_error_matches(apy_value handler);
APY_API void apy_error_clear(void);
APY_API apy_value apy_getitem(apy_value seq, apy_value index);
APY_API apy_value apy_to_bytes(apy_value src);
static apy_value apy_name(const char *text);
static apy_value apy_class_find(apy_value cls, apy_value name);
static apy_value apy_bind(apy_value f, apy_value self);
static int apy_type_is_sub(apy_value of, apy_value cls);

/* A str value's bytes as a NUL-terminated C string, for comparing an
   attribute name against a literal. Every str cell keeps a NUL after its
   bytes (see `apy_str_take`), so this is the pointer itself.

   The parameter is `x` and NOT `v`. With `v` the body's `->v.s.p` is itself a
   use of the parameter, so `APY_CSTR(name)` expanded to `O(name)->name.s.p`
   and every call site failed with "no member named 'name'" -- a diagnostic
   that points at the macro's argument rather than at the macro. */
#define APY_CSTR(x) (O(x)->v.s.p)
/* The set section is above the numeric tower and reports a bad operand pair
   the same way `+` does; the shared wording lives down there with the
   arithmetic that first needed it. */
static apy_value apy_binop_error(const char *op, apy_value a, apy_value b);

static int apy_is_seq(apy_value v) {
    return O(v)->kind == APY_LIST_K || O(v)->kind == APY_TUPLE_K;
}

/* Deliberately NOT part of `apy_is_seq`. A set shares the `v.q` layout but is
   not a sequence in any way a caller of `apy_is_seq` means: it has no order to
   index, `+` and `*` do not apply, and `list == tuple` is False while
   `set == frozenset` is True. Every place that wants "has v.q" rather than "is
   a sequence" says so by calling both. */
static int apy_is_set(apy_value v) {
    return O(v)->kind == APY_SET_K || O(v)->kind == APY_FROZEN_K;
}

static apy_value apy_seq_new(int kind, int64_t cap) {
    apy_obj *o = apy_alloc(kind);
    if (cap < 1) cap = 1;
    o->v.q.items = (apy_value *)malloc((size_t)cap * sizeof(apy_value));
    o->v.q.n = 0;
    o->v.q.cap = cap;
    return V(o);
}

APY_API apy_value apy_list_new(int64_t cap) { return apy_seq_new(APY_LIST_K, cap); }
APY_API apy_value apy_tuple_new(int64_t cap) { return apy_seq_new(APY_TUPLE_K, cap); }

/* Append with NO checking at all -- not that the cell is a list, not that a
   set already holds an equal element. Split out from `apy_seq_push` because
   the set code appends to a `v.q` that `apy_is_seq` rejects, and because a set
   operation whose inputs are already sets cannot produce a duplicate and so
   must not pay for the scan that would prove it. */
static void apy_q_append(apy_value q, apy_value item) {
    apy_obj *o = O(q);
    if (o->v.q.n == o->v.q.cap) {
        o->v.q.cap *= 2;
        o->v.q.items = (apy_value *)realloc(
            o->v.q.items, (size_t)o->v.q.cap * sizeof(apy_value));
    }
    o->v.q.items[o->v.q.n++] = item;
}

/* Used both to BUILD a literal and to implement `list.append`. A tuple is
   built with it too and then never appended to again: immutability is a rule
   the frontend enforces, not a property of the cell. */
APY_API apy_value apy_seq_push(apy_value seq, apy_value item) {
    if (!apy_is_seq(seq))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'append'%s",
                         apy_kind_name(seq), "");
    apy_q_append(seq, item);
    return apy_none();
}

/* `b'ab'[0]` is 97, not `b'a'`. The one place bytes differs from str in KIND
   rather than in units: indexing a str gives a one-character str and indexing
   bytes gives the integer octet, while SLICING either gives the same kind
   back. Getting this wrong is invisible until something does arithmetic on
   the result. */
static apy_value apy_bytes_getitem(apy_value seq, int64_t i) {
    int64_t n = O(seq)->v.s.n;
    if (i < 0) i += n;
    if (i < 0 || i >= n)
        return apy_fail("IndexError", "index out of range");
    return apy_from_int((int64_t)(unsigned char)O(seq)->v.s.p[i]);
}

/* `b'ab' * 3`. A negative or zero count gives empty, as every sequence
   repetition in Python does -- not an error. */
static apy_value apy_bytes_repeat(apy_value v, apy_value count) {
    int64_t k, n, i;
    if (!apy_index_arg(count, &k, APY_IDX_SUB)) return 0;
    if (k < 0) k = 0;
    n = O(v)->v.s.n;
    { char *out = (char *)malloc((size_t)(n * k) + 1);
      if (!out) { fputs("asmpython: out of memory\n", stderr); exit(1); }
      for (i = 0; i < k; i++) memcpy(out + i * n, O(v)->v.s.p, (size_t)n);
      out[n * k] = 0;
      { apy_value r = apy_str_take(out, n * k);
        O(r)->kind = APY_BYTES_K;
        return r; } }
}


APY_API apy_value apy_getitem(apy_value seq, apy_value index) {
    int64_t i, n;
    if (O(seq)->kind == APY_BYTES_K && apy_is_int_like(index)) {
        if (!apy_index_arg(index, &i, APY_IDX_SUB)) return 0;
        return apy_bytes_getitem(seq, i);
    }
    if (O(seq)->kind == APY_INST_K)
        /* No fallthrough: a class without `__getitem__` is "not
           subscriptable", and `apy_method1` answering 0 with no error set
           lands on exactly that message below. */
        { apy_value r = apy_method1(seq, "__getitem__", index);
          if (r || apy_error_occurred()) return r; }
    if (O(seq)->kind == APY_DICT_K) return apy_dict_get(seq, index);
    /* "Is this subscriptable at all" is asked BEFORE "is the index an int",
       because a set answers the first question and CPython reports that:
       `{1, 2}[0]` is `'set' object is not subscriptable`, not a complaint
       about the index. Testing the index first put every non-subscriptable
       kind on the wrong message whenever the index happened to be an int. */
    if (!apy_is_seq(seq) && O(seq)->kind != APY_STR_K)
        return apy_fail2("TypeError", "'%s' object is not subscriptable%s",
                         apy_kind_name(seq), "");
    if (!apy_is_int_like(index) && O(index)->kind == APY_INST_K) {
        /* `__index__` -- how a user object BECOMES an index. PEP 357, and the
           reason it is a separate dunder from `__int__`: a float has `__int__`
           and is still not a valid subscript, so accepting the general
           conversion here would make `xs[1.5]` work. */
        apy_value got = apy_unary_dunder(index, "__index__");
        if (apy_error_occurred()) return 0;
        if (got && apy_is_int_like(got)) index = got;
    }
    if (!apy_is_int_like(index)) {
        /* Two texts, not one with a substituted noun. CPython says
           `list indices must be integers or slices, not float` for a list or
           a tuple -- both of which DO accept a slice -- and
           `string indices must be integers, not 'float'` for a str, with the
           kind quoted and no mention of slices. The single generic form was
           wrong for whichever half it was not written from. */
        if (O(seq)->kind == APY_STR_K)
            return apy_fail2("TypeError",
                             "string indices must be integers, not '%s'%s",
                             apy_kind_name(index), "");
        return apy_fail2("TypeError",
                         "%s indices must be integers or slices, not %s",
                         apy_kind_name(seq), apy_kind_name(index));
    }
    if (!apy_index_arg(index, &i, APY_IDX_SUB)) return 0;
    if (apy_is_seq(seq)) {
        n = O(seq)->v.q.n;
        /* A negative index counts from the end, and it is the LENGTH that
           decides whether it lands: `xs[-1]` on an empty list is an
           IndexError, not element -1. */
        if (i < 0) i += n;
        if (i < 0 || i >= n)
            return apy_fail("IndexError", "list index out of range");
        return O(seq)->v.q.items[i];
    }
    if (O(seq)->kind == APY_STR_K) {
        n = O(seq)->v.s.n;
        if (i < 0) i += n;
        if (i < 0 || i >= n)
            return apy_fail("IndexError", "string index out of range");
        return apy_str_copy(O(seq)->v.s.p + i, 1);
    }
    return apy_fail2("TypeError", "'%s' object is not subscriptable%s",
                     apy_kind_name(seq), "");   /* unreachable; see the guard */
}

APY_API apy_value apy_str(apy_value v) { return apy_text(v, 0); }

/* --- printing ---------------------------------------------------------- */
/* `items` is the ADDRESS of an array of n values -- the frontend builds one in
   a stack slot, because the IR has no varargs and `print` takes any number. */
/* `print(a, b, sep='-', end='!')`. The separator and the terminator are
   VALUES, not constants, because they are ordinary keyword arguments and a
   program may compute them.

   `sep=None` and `end=None` mean the defaults, which is what CPython accepts
   and what an omitted one lowers to -- so "not given" and "given as None" are
   the same request, and neither needs a second entry point. */
APY_API void apy_print_with(apy_value items, int64_t n, apy_value sep,
                            apy_value end) {
    const apy_value *a = (const apy_value *)items;
    int64_t i;
    const char *sp = " ";
    int64_t spn = 1;
    if (O(sep)->kind == APY_STR_K) { sp = APY_CSTR(sep); spn = O(sep)->v.s.n; }
    for (i = 0; i < n; i++) {
        apy_value s;
        if (i) fwrite(sp, 1, (size_t)spn, stdout);
        s = apy_str(a[i]);
        if (!s) return;
        fwrite(O(s)->v.s.p, 1, (size_t)O(s)->v.s.n, stdout);
    }
    if (O(end)->kind == APY_STR_K)
        fwrite(APY_CSTR(end), 1, (size_t)O(end)->v.s.n, stdout);
    else
        fputc('\n', stdout);
}

APY_API void apy_print(apy_value items, int64_t n) {
    const apy_value *a = (const apy_value *)items;
    int64_t i;
    for (i = 0; i < n; i++) {
        apy_value s;
        if (i) fputc(' ', stdout);
        s = apy_str(a[i]);
        fwrite(O(s)->v.s.p, 1, (size_t)O(s)->v.s.n, stdout);
    }
    fputc('\n', stdout);
}

APY_API apy_value apy_setitem(apy_value seq, apy_value index, apy_value item) {
    int64_t i, n;
    if (O(seq)->kind == APY_INST_K) {
        /* Two arguments, so this cannot go through `apy_method1`. */
        apy_value m = apy_dunder(seq, "__setitem__"), args[2];
        if (m) { args[0] = index; args[1] = item;
                 return apy_call_n(m, args, 2); }
    }
    if (O(seq)->kind == APY_DICT_K) return apy_dict_set(seq, index, item);
    if (O(seq)->kind != APY_LIST_K)
        return apy_fail2("TypeError",
                         "'%s' object does not support item assignment%s",
                         apy_kind_name(seq), "");
    if (!apy_is_int_like(index))
        return apy_fail2("TypeError",
                         "list indices must be integers or slices, not %s%s",
                         apy_kind_name(index), "");
    if (!apy_index_arg(index, &i, APY_IDX_SUB)) return 0;
    n = O(seq)->v.q.n;
    if (i < 0) i += n;
    if (i < 0 || i >= n)
        return apy_fail("IndexError", "list assignment index out of range");
    O(seq)->v.q.items[i] = item;
    return apy_none();
}

/* The length as a machine word, for the frontend's own use -- a `for` loop
   bound, not a value the program ever sees. `apy_len` is the builtin. */
APY_API int64_t apy_raw_len(apy_value v) {
    if (O(v)->kind == APY_DICT_K) return O(v)->v.d.n;
    if (apy_is_seq(v) || apy_is_set(v)) return O(v)->v.q.n;
    if (O(v)->kind == APY_BYTES_K) return O(v)->v.s.n;
    if (O(v)->kind == APY_STR_K) return O(v)->v.s.n;
    if (O(v)->kind == APY_ITER_K) {
        /* A PLAIN cursor over a real container knows WHAT REMAINS without
           walking it, and answering cheaply keeps ordinary code off the slow
           path. Anything that transforms as it goes -- map, filter, zip --
           does not: filtering may drop any number of elements, so the only
           way to know is to walk. Those are DRAINED, which turns the cursor
           into a plain one over what it produced, exactly as asking a
           generator for its length does. */
        int src_kind = O(O(v)->v.it.src)->kind;
        if (O(v)->v.it.mode == APY_IT_PLAIN && src_kind != APY_GEN_K
                && src_kind != APY_ITER_K && src_kind != APY_INST_K) {
            int64_t n = apy_raw_len(O(v)->v.it.src);
            return n > O(v)->v.it.i ? n - O(v)->v.it.i : 0;
        }
        {
            apy_value got = apy_drain_cursor(v);
            if (!got) return 0;
            return O(got)->v.q.n;
        }
    }
    /* A GENERATOR has no length until it has been run, so asking for one
       DRAINS it -- see the `cache` field. `apy_key_at` then reads the same
       list, so the length and the elements agree. */
    if (O(v)->kind == APY_GEN_K) {
        if (!O(v)->v.g.cache) {
            apy_value got = apy_gen_drain(v);
            if (!got) return 0;
            O(v)->v.g.cache = got;
        }
        return O(O(v)->v.g.cache)->v.q.n;
    }
    /* A user object with `__len__`. Together with `apy_key_at` falling
       through to `__getitem__`, that is the whole `__len__`/`__getitem__`
       iteration protocol -- the one a sequence-like class implements, and the
       one this runtime's index-based walk fits exactly. */
    if (O(v)->kind == APY_INST_K) {
        apy_value n = apy_unary_dunder(v, "__len__");
        if (n && apy_is_int_like(n)) return O(n)->v.i;
        if (apy_error_occurred()) return 0;
    }
    apy_fail2("TypeError", "'%s' object is not iterable%s",
              apy_kind_name(v), "");
    return 0;
}

/* --- dict -------------------------------------------------------------- */
/* An association LIST, not a hash table. Lookup is linear.

   That is a deliberate v1: a hash table needs a hash function per kind and a
   resize policy, and both are places to be subtly wrong, while the property
   the conformance suite actually measures is that `{1: 'a'}[1]` is 'a' and
   that iteration order is insertion order. Linear scan gets both right in
   forty lines. It is O(n) per lookup and that is written here so the next
   person replaces it on purpose rather than discovering it.

   Insertion order is preserved because entries are appended and a re-assigned
   key keeps its original slot -- which is not an implementation accident in
   Python but a guarantee since 3.7.

   Keys compare with `==`, so `d[1]` and `d[True]` are the same entry, exactly
   as in CPython. */
static apy_value apy_dict_new_cap(int64_t cap) {
    apy_obj *o = apy_alloc(APY_DICT_K);
    if (cap < 1) cap = 1;
    o->v.d.keys = (apy_value *)malloc((size_t)cap * sizeof(apy_value));
    o->v.d.vals = (apy_value *)malloc((size_t)cap * sizeof(apy_value));
    o->v.d.n = 0;
    o->v.d.cap = cap;
    return V(o);
}

APY_API apy_value apy_dict_new(int64_t cap) { return apy_dict_new_cap(cap); }

static int64_t apy_dict_find(apy_value d, apy_value key) {
    int64_t i;
    for (i = 0; i < O(d)->v.d.n; i++)
        if (apy_eq_raw(O(d)->v.d.keys[i], key)) return i;
    return -1;
}

/* An unhashable key is a TypeError in CPython even though nothing here needs
   to hash it -- accepting one would let `{[1]: 2}` run and then disagree.

   RECURSIVE, because hashability is: a tuple is hashable only if everything
   in it is, so `{(1, [2]): 3}` is a TypeError in CPython and a shallow
   "tuples are fine" test let it through. Returns the kind name of the
   INNERMOST offender, or NULL when the value is hashable -- the name is what
   the message needs, and finding it is the same walk as deciding. */
static const char *apy_unhashable(apy_value v) {
    int64_t i;
    if (O(v)->kind == APY_LIST_K || O(v)->kind == APY_DICT_K
        || O(v)->kind == APY_SET_K)
        return apy_kind_name(v);
    /* A FROZENSET is hashable and is not walked, unlike a tuple: every element
       it holds was checked on the way in, so there is nothing a walk could
       find. A tuple has no such gate -- `(1, [2])` is a perfectly ordinary
       tuple -- which is why that one is recursive. */
    if (O(v)->kind == APY_TUPLE_K) {
        for (i = 0; i < O(v)->v.q.n; i++) {
            const char *bad = apy_unhashable(O(v)->v.q.items[i]);
            if (bad) return bad;
        }
    }
    return NULL;
}

/* CPython 3.14 wraps the plain "unhashable type" text when the value is used
   AS A DICT KEY: `cannot use 'tuple' as a dict key (unhashable type:
   'list')`, naming the key's own kind and then the innermost offender. 3.13
   and earlier said only the inner half, which is still what a search finds
   and what this file used to report. The suite is generated from 3.14. */
static apy_value apy_unhashable_key(apy_value key, const char *inner) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "cannot use '%s' as a dict key (unhashable type: '%s')",
             apy_kind_name(key), inner);
    return apy_fail("TypeError", buf);
}

APY_API apy_value apy_dict_set(apy_value d, apy_value key, apy_value val) {
    int64_t at;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("TypeError",
                         "'%s' object does not support item assignment%s",
                         apy_kind_name(d), "");
    {
        const char *bad = apy_unhashable(key);
        if (bad) return apy_unhashable_key(key, bad);
    }
    at = apy_dict_find(d, key);
    if (at >= 0) {
        /* A re-assigned key keeps its ORIGINAL position. Python's insertion
           order is about first insertion, not last write. */
        O(d)->v.d.vals[at] = val;
        return apy_none();
    }
    if (O(d)->v.d.n == O(d)->v.d.cap) {
        O(d)->v.d.cap *= 2;
        O(d)->v.d.keys = (apy_value *)realloc(
            O(d)->v.d.keys, (size_t)O(d)->v.d.cap * sizeof(apy_value));
        O(d)->v.d.vals = (apy_value *)realloc(
            O(d)->v.d.vals, (size_t)O(d)->v.d.cap * sizeof(apy_value));
    }
    O(d)->v.d.keys[O(d)->v.d.n] = key;
    O(d)->v.d.vals[O(d)->v.d.n] = val;
    O(d)->v.d.n++;
    return apy_none();
}

static apy_value apy_dict_get(apy_value d, apy_value key) {
    int64_t at;
    {
        const char *bad = apy_unhashable(key);
        if (bad) return apy_unhashable_key(key, bad);
    }
    at = apy_dict_find(d, key);
    if (at < 0) {
        apy_value shown = apy_repr(key);
        char buf[200];
        snprintf(buf, sizeof buf, "%.*s",
                 (int)O(shown)->v.s.n, O(shown)->v.s.p);
        return apy_fail("KeyError", buf);
    }
    return O(d)->v.d.vals[at];
}

/* `{'a': 1}` -- key and value both with repr, separated by ": ". An empty
   dict is `{}`, which is why the length is checked before anything is built. */
static apy_value apy_dict_text(apy_value v) {
    int64_t n = O(v)->v.d.n, i, len = 3, out = 0;
    apy_value *parts;
    char *buf;
    if (n == 0) return apy_lit("{}");
    parts = (apy_value *)malloc((size_t)n * 2 * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        parts[i * 2] = apy_text(O(v)->v.d.keys[i], 1);
        parts[i * 2 + 1] = apy_text(O(v)->v.d.vals[i], 1);
        len += O(parts[i * 2])->v.s.n + O(parts[i * 2 + 1])->v.s.n + 4;
    }
    buf = (char *)malloc((size_t)len + 1);
    buf[out++] = '{';
    for (i = 0; i < n * 2; i++) {
        if (i) {
            buf[out++] = (i & 1) ? ':' : ',';
            buf[out++] = ' ';
        }
        memcpy(buf + out, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
        out += O(parts[i])->v.s.n;
    }
    buf[out++] = '}';
    buf[out] = 0;
    free(parts);
    return apy_str_take(buf, out);
}

/* The i'th key, for `for k in d`. Index-based like the sequence loop,
   and for the same reason: there is no iterator protocol yet.
   A set goes through here rather than through `apy_getitem`, which refuses it:
   a set is iterable and not subscriptable, and this is the function that means
   "iterate". */
APY_API apy_value apy_key_at(apy_value v, int64_t i) {
    if (O(v)->kind == APY_GEN_K)
        /* From what the length query drained -- see `apy_raw_len`. */
        return O(v)->v.g.cache ? apy_key_at(O(v)->v.g.cache, i) : apy_none();
    if (O(v)->kind == APY_DICT_K) return O(v)->v.d.keys[i];
    if (apy_is_set(v)) return O(v)->v.q.items[i];
    /* AN ITERATOR IGNORES `i` AND ADVANCES.

       Every consumer here walks by index: read the length once, then ask for
       0, 1, 2... An iterator has a position of its own, and a consumer that
       indexed it from zero would replay elements it had already yielded and
       would leave it unconsumed afterwards -- `for v in it` twice would run
       twice.

       So the cursor is what answers, and `i` is ignored. That is exact for a
       sequential walk, which is the only kind anything does, and wrong for a
       random-access one, which nothing does. If a consumer ever indexes out of
       order this is where it will go wrong, and it will go wrong silently --
       hence saying so here rather than in a commit message. */
    if (O(v)->kind == APY_ITER_K) {
        apy_value src = O(v)->v.it.src;
        int64_t n = apy_raw_len(src);
        if (apy_error_occurred() || O(v)->v.it.i >= n) return apy_none();
        return apy_key_at(src, O(v)->v.it.i++);
    }
    return apy_getitem(v, apy_from_int(i));
}

/* --- set and frozenset -------------------------------------------------- */
/* The list layout again (`v.q`), with uniqueness kept by a LINEAR SCAN on
   insert -- the same association-list decision the dict above documents, made
   for the same reason and with the same cost. Insertion is O(n), so building
   an n-element set is O(n**2). Stated so the next person replaces it on
   purpose.

   set and frozenset are two kinds rather than one kind with a flag, so that
   `apy_kind_name` -- which feeds `type(x).__name__`, `isinstance` and every
   error message -- stays a switch on the kind and cannot forget to look at a
   flag. Everything else about them is identical, INCLUDING equality:
   `{1, 2} == frozenset([1, 2])` is True in Python, where `[1] == (1,)` is
   False. Sets compare by membership; sequences compare by type and order.

   ORDER IS INSERTION ORDER, AND CPYTHON'S IS NOT. CPython iterates the slots
   of a hash table, so `set([3, 1, 2])` prints `{1, 2, 3}` and this prints
   `{3, 1, 2}`. That is a real divergence and it is visible to any program that
   prints a set it did not build in sorted order. It is not reproducible
   without reproducing CPython's table size, growth policy, probe sequence and
   per-kind hash -- and for str the hash is salted per process, so there is no
   fixed answer to reproduce. The conformance suite sorts every set it prints
   for exactly that reason (see cases/sets/operations-are-sorted-for-
   determinism), and the only set reprs it expects verbatim are `{1, 2}` and
   `frozenset({1, 2})`, where the two orders coincide.

   ELEMENTS ARE COMPARED WITH `==`, so `{1, 1.0, True}` holds ONE element and
   it is the FIRST one inserted -- `len` is 1 and the element prints as `1`.
   A linear scan that stops at the first equal element gets that for free. */
static apy_value apy_unhashable_elem(apy_value item, const char *inner) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "cannot use '%s' as a set element (unhashable type: '%s')",
             apy_kind_name(item), inner);
    return apy_fail("TypeError", buf);
}

static int64_t apy_set_find(apy_value s, apy_value item) {
    int64_t i;
    for (i = 0; i < O(s)->v.q.n; i++)
        if (apy_eq_raw(O(s)->v.q.items[i], item)) return i;
    return -1;
}

/* 1 when the element was added, 0 when an equal one was already there, -1 when
   it is unhashable and the error flag has been set. Three outcomes because
   `.add` and `.discard` need to tell "already present" from "refused", and a
   caller that only cares about failure can test for a negative. */
static int apy_set_insert(apy_value s, apy_value item) {
    const char *bad = apy_unhashable(item);
    if (bad) { apy_unhashable_elem(item, bad); return -1; }
    if (apy_set_find(s, item) >= 0) return 0;
    apy_q_append(s, item);
    return 1;
}

APY_API apy_value apy_set_new(int64_t cap) { return apy_seq_new(APY_SET_K, cap); }
APY_API apy_value apy_frozenset_new(int64_t cap) { return apy_seq_new(APY_FROZEN_K, cap); }

/* Building a DISPLAY -- `{a, b}` -- and nothing else. It does not refuse a
   frozenset receiver, because `frozenset([...])` is built by filling a fresh
   frozen cell here before any program can see it. `.add` is `apy_set_add`,
   which does refuse. */
APY_API apy_value apy_set_push(apy_value s, apy_value item) {
    if (apy_set_insert(s, item) < 0) return 0;
    return apy_none();
}

/* `set(x)` / `frozenset(x)` over anything iterable. Goes through
   `apy_raw_len`/`apy_key_at` rather than reading `v.q` directly, so a dict
   contributes its KEYS and a str its characters, which is what Python's
   constructors do. */
static apy_value apy_set_from(int kind, apy_value src) {
    int64_t n = apy_raw_len(src), i;
    apy_value out;
    if (apy_error_occurred()) return 0;
    out = apy_seq_new(kind, n + 1);
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(src, i);
        if (!item) return 0;
        if (apy_set_insert(out, item) < 0) return 0;
    }
    return out;
}

APY_API apy_value apy_to_set(apy_value v) { return apy_set_from(APY_SET_K, v); }
APY_API apy_value apy_to_frozenset(apy_value v) {
    /* `frozenset(f)` is allowed to hand back the same object, and CPython
        does; there is nothing to copy because nothing can change it. */
    if (O(v)->kind == APY_FROZEN_K) return v;
    return apy_set_from(APY_FROZEN_K, v);
}

/* `{1, 2}`, `frozenset({1, 2})`, and `set()` / `frozenset()` when empty. The
   empty set is NOT `{}` -- that is the empty dict, and printing it as `{}`
   would print a set that reads back as a dict. */
static apy_value apy_set_text(apy_value v) {
    int frozen = O(v)->kind == APY_FROZEN_K;
    int64_t n = O(v)->v.q.n, i, len = frozen ? 13 : 3, out = 0;
    apy_value *parts;
    char *buf;
    if (n == 0) return apy_lit(frozen ? "frozenset()" : "set()");
    parts = (apy_value *)malloc((size_t)n * sizeof(apy_value));
    for (i = 0; i < n; i++) {
        parts[i] = apy_text(O(v)->v.q.items[i], 1);
        len += O(parts[i])->v.s.n + 2;
    }
    buf = (char *)malloc((size_t)len + 1);
    if (frozen) { memcpy(buf, "frozenset(", 10); out = 10; }
    buf[out++] = '{';
    for (i = 0; i < n; i++) {
        if (i) { buf[out++] = ','; buf[out++] = ' '; }
        memcpy(buf + out, O(parts[i])->v.s.p, (size_t)O(parts[i])->v.s.n);
        out += O(parts[i])->v.s.n;
    }
    buf[out++] = '}';
    if (frozen) buf[out++] = ')';
    buf[out] = 0;
    free(parts);
    return apy_str_take(buf, out);
}

/* Every element of `a` is in `b`. The whole of set ordering rests on this:
   `<=` is subset, `<` is proper subset, and two sets neither of which contains
   the other are INCOMPARABLE rather than an error. */
static int apy_subset(apy_value a, apy_value b) {
    int64_t i;
    for (i = 0; i < O(a)->v.q.n; i++)
        if (apy_set_find(b, O(a)->v.q.items[i]) < 0) return 0;
    return 1;
}

enum { APY_UNION, APY_INTER, APY_DIFF, APY_SYMDIFF };

/* The four algebra operations, in one place because they differ by four
   lines. `strict` is the OPERATOR/METHOD distinction and it is not cosmetic:
   `{1} | [2]` is a TypeError and `{1}.union([2])` is `{1, 2}`. CPython's
   operators demand two sets so that `|` cannot silently mean two things; its
   methods take any iterable because they were named.

   THE RESULT'S KIND IS THE LEFT OPERAND'S: `frozenset([1]) | {2}` is a
   frozenset and `{2} | frozenset([1])` is a set. Both directions are asserted
   by cases/sets/disjoint-and-frozen-ops.

   The result is appended to RAW. Both inputs are already duplicate-free and
   every branch below adds each element at most once, so the O(n) scan
   `apy_set_insert` would do could not find anything -- it would only make an
   O(n**2) operation O(n**3). */
static apy_value apy_set_algebra(const char *op, apy_value a, apy_value b,
                                 int which, int strict) {
    apy_value rhs, out;
    int64_t i;
    if (!apy_is_set(a) || (strict && !apy_is_set(b)))
        return apy_binop_error(op, a, b);
    rhs = apy_is_set(b) ? b : apy_set_from(APY_SET_K, b);
    if (!rhs) return 0;
    out = apy_seq_new(O(a)->kind, O(a)->v.q.n + O(rhs)->v.q.n + 1);
    if (which == APY_INTER) {
        /* WHICH OF THE TWO EQUAL ELEMENTS ENDS UP IN THE RESULT IS VISIBLE,
           because `1`, `1.0` and `True` are equal and print differently:
           `{1, 2} & {True}` is `{True}` in CPython and `{False, True} & {1}`
           is `{1}`. CPython iterates the SMALLER of the two sets and keeps
           the element it iterated, swapping only when the right side is
           strictly larger -- so on a tie the RIGHT operand's element wins.
           Keeping the left's unconditionally is the natural implementation
           and it disagrees with CPython on half of these.

           THE SWAP ONLY APPLIES WHEN THE RIGHT SIDE IS REALLY A SET. Given
           any other iterable CPython walks it whatever its length, so
           `{False, True}.intersection([1, 2, 3])` is `{1}` even though the
           list is longer. Applying the size rule to the coerced set answered
           `{True}` there. */
        int swap = apy_is_set(b) && O(rhs)->v.q.n > O(a)->v.q.n;
        apy_value from = swap ? a : rhs;
        apy_value test = swap ? rhs : a;
        for (i = 0; i < O(from)->v.q.n; i++)
            if (apy_set_find(test, O(from)->v.q.items[i]) >= 0)
                apy_q_append(out, O(from)->v.q.items[i]);
        return out;
    }
    /* Union and both differences keep the LEFT operand's element for anything
       the two have in common -- `{1, 2} | {True}` is `{1, 2}`, not
       `{True, 2}`. That falls out of walking `a` first. */
    for (i = 0; i < O(a)->v.q.n; i++) {
        apy_value item = O(a)->v.q.items[i];
        int there = apy_set_find(rhs, item) >= 0;
        if (!there || which == APY_UNION) apy_q_append(out, item);
    }
    if (which == APY_UNION || which == APY_SYMDIFF)
        for (i = 0; i < O(rhs)->v.q.n; i++)
            if (apy_set_find(a, O(rhs)->v.q.items[i]) < 0)
                apy_q_append(out, O(rhs)->v.q.items[i]);
    return out;
}

/* The METHOD forms. Any iterable on the right; a receiver that is not a set
   is an AttributeError, because `[1].union(...)` is a missing attribute in
   CPython and not a bad operand. */
static apy_value apy_set_method(const char *name, apy_value a, apy_value b,
                                int which) {
    if (!apy_is_set(a))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(a), name);
    return apy_set_algebra(name, a, b, which, 0);
}

APY_API apy_value apy_set_union(apy_value a, apy_value b) {
    return apy_set_method("union", a, b, APY_UNION);
}
APY_API apy_value apy_set_intersection(apy_value a, apy_value b) {
    return apy_set_method("intersection", a, b, APY_INTER);
}
APY_API apy_value apy_set_difference(apy_value a, apy_value b) {
    return apy_set_method("difference", a, b, APY_DIFF);
}
APY_API apy_value apy_set_symdiff(apy_value a, apy_value b) {
    return apy_set_method("symmetric_difference", a, b, APY_SYMDIFF);
}

/* `issubset` / `issuperset` / `isdisjoint`, which take any iterable where the
   operators `<=` / `>=` demand a set -- the same split as the algebra. */
static apy_value apy_set_relate(const char *name, apy_value a, apy_value b,
                                int which) {
    apy_value rhs;
    int64_t i;
    if (!apy_is_set(a))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(a), name);
    rhs = apy_is_set(b) ? b : apy_set_from(APY_SET_K, b);
    if (!rhs) return 0;
    if (which == 0) return apy_from_bool(apy_subset(a, rhs));
    if (which == 1) return apy_from_bool(apy_subset(rhs, a));
    for (i = 0; i < O(a)->v.q.n; i++)
        if (apy_set_find(rhs, O(a)->v.q.items[i]) >= 0) return apy_from_bool(0);
    return apy_from_bool(1);
}

APY_API apy_value apy_set_issubset(apy_value a, apy_value b) {
    return apy_set_relate("issubset", a, b, 0);
}
APY_API apy_value apy_set_issuperset(apy_value a, apy_value b) {
    return apy_set_relate("issuperset", a, b, 1);
}
APY_API apy_value apy_set_isdisjoint(apy_value a, apy_value b) {
    return apy_set_relate("isdisjoint", a, b, 2);
}

/* The mutators. Every one of them refuses a frozenset by NAME -- a frozenset
   has no `add` attribute at all in CPython, so the report is an AttributeError
   about a missing attribute and not a TypeError about immutability. */
static int apy_mutable_set(const char *name, apy_value s) {
    if (O(s)->kind == APY_SET_K) return 1;
    apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
              apy_kind_name(s), name);
    return 0;
}

APY_API apy_value apy_set_add(apy_value s, apy_value item) {
    if (!apy_mutable_set("add", s)) return 0;
    if (apy_set_insert(s, item) < 0) return 0;
    return apy_none();
}

/* `discard` on an absent element is a no-op; `remove` on one is a KeyError.
   That is the only difference between them, and it is why `remove` cannot
   simply call this. */
APY_API apy_value apy_set_discard(apy_value s, apy_value item) {
    int64_t at, k;
    if (!apy_mutable_set("discard", s)) return 0;
    {
        const char *bad = apy_unhashable(item);
        if (bad) return apy_unhashable_elem(item, bad);
    }
    at = apy_set_find(s, item);
    if (at >= 0) {
        for (k = at; k + 1 < O(s)->v.q.n; k++)
            O(s)->v.q.items[k] = O(s)->v.q.items[k + 1];
        O(s)->v.q.n--;
    }
    return apy_none();
}

/* `s.remove(x)` -- reached through `apy_list_remove`, which dispatches on the
   receiver's kind. The KeyError's message is the element's REPR, exactly as
   for a missing dict key: `{1}.remove(9)` says `KeyError: 9`. */
static apy_value apy_set_remove(apy_value s, apy_value item) {
    int64_t at;
    {
        const char *bad = apy_unhashable(item);
        if (bad) return apy_unhashable_elem(item, bad);
    }
    at = apy_set_find(s, item);
    if (at < 0) {
        apy_value shown = apy_repr(item);
        char buf[200];
        snprintf(buf, sizeof buf, "%.*s",
                 (int)O(shown)->v.s.n, O(shown)->v.s.p);
        return apy_fail("KeyError", buf);
    }
    return apy_set_discard(s, item);
}

/* `s.pop()` -- reached through `apy_list_pop`. Python's takes an ARBITRARY
   element and specifies nothing about which; this takes the first, which is
   the oldest inserted. cases/sets/mutation-methods checks only that what came
   out was in the set, which is the guarantee the language actually gives. */
static apy_value apy_set_pop(apy_value s) {
    apy_value taken;
    if (!apy_mutable_set("pop", s)) return 0;
    if (O(s)->v.q.n == 0)
        /* The quotes are IN the message on purpose. A KeyError's message is
           the repr of its argument -- that is why a missing str dict key
           reports `KeyError: 'k'` -- and CPython's argument here is the
           string "pop from an empty set", so the repr of it carries quotes.
           Writing it bare would print a traceback line CPython never does. */
        return apy_fail("KeyError", "'pop from an empty set'");
    taken = O(s)->v.q.items[0];
    apy_set_discard(s, taken);
    return taken;
}

/* --- exceptions -------------------------------------------------------- */
/* An exception VALUE, so `except ValueError as e:` has something to bind and
   `type(e).__name__` and `str(e)` can answer. It carries the type's name and
   the argument, which is all the suite ever asks of one.

   The error FLAG and the error VALUE are separate on purpose: an operation
   deep in the runtime sets the flag with a static string and no allocation,
   and the value is built only when a handler actually catches -- so the
   common path, where nothing fails, allocates nothing. */
static const char *apy_exc_parent(const char *name);

APY_API apy_value apy_make_exc(apy_value type_name, apy_value arg) {
    apy_obj *o = apy_alloc(APY_EXC_K);
    o->v.e.name = O(type_name)->v.s.p;
    o->v.e.arg = arg;
    o->v.e.has_arg = 1;
    return V(o);
}

/* `raise E` and `except E:` -- an exception with NO argument, as distinct from
   one whose argument is None. See the `e` layout for why that distinction has
   to be carried rather than inferred. */
APY_API apy_value apy_make_exc0(apy_value type_name) {
    apy_obj *o = apy_alloc(APY_EXC_K);
    o->v.e.name = O(type_name)->v.s.p;
    o->v.e.arg = apy_none();
    o->v.e.has_arg = 0;
    return V(o);
}

/* THE exception being handled right now, for implicit chaining: a `raise`
   inside an `except` block records it as the new exception's `__context__`.
   One slot rather than a stack, so a raise from inside a handler nested in
   another handler chains to the inner one only -- which is what the chain
   `e.__context__.__context__` would otherwise show, and is the part this does
   not have. */
static apy_value apy_handling;

APY_API apy_value apy_error_handling(apy_value exc) {
    apy_value was = apy_handling;
    apy_handling = O(exc)->kind == APY_EXC_K ? exc : 0;
    return was ? was : apy_none();
}

/* `e.add_note(text)` -- PEP 678. Appended to a list made on first use, so an
   exception that never gets one carries no allocation. */
APY_API apy_value apy_add_note(apy_value exc, apy_value text) {
    if (O(exc)->kind != APY_EXC_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'add_note'%s",
                         apy_kind_name(exc), "");
    if (O(text)->kind != APY_STR_K)
        return apy_fail("TypeError", "note must be a str");
    if (!O(exc)->v.e.notes) O(exc)->v.e.notes = apy_list_new(2);
    apy_seq_push(O(exc)->v.e.notes, text);
    return apy_none();
}

APY_API apy_value apy_raise(apy_value exc);
APY_API apy_value apy_error_value(void);

/* `raise X from Y`. The cause is EXPLICIT, and `from None` suppresses the
   implicit context rather than setting a cause -- so the two are recorded
   separately and `has_cause` tells them apart. */
APY_API apy_value apy_raise_from(apy_value exc, apy_value cause,
                                 int64_t has_cause) {
    if (O(exc)->kind == APY_EXC_K) {
        O(exc)->v.e.suppress = 1;
        O(exc)->v.e.cause = has_cause && O(cause)->kind == APY_EXC_K ? cause : 0;
    }
    return apy_raise(exc);
}

/* `raise X(...)` and `raise X`. The message is the argument's str(), which is
   what CPython prints for an uncaught one and what `str(e)` returns. */
APY_API apy_value apy_raise(apy_value exc) {
    const char *name;
    apy_value shown;
    if (O(exc)->kind != APY_EXC_K)
        return apy_fail2("TypeError",
                         "exceptions must derive from BaseException, not '%s'%s",
                         apy_kind_name(exc), "");
    /* The exception being HANDLED becomes this one's `__context__`, unless a
       `raise ... from` already spoke for the chain. Set here rather than at
       the `except` because only a raise creates a link. */
    /* Set even when `from` suppressed it. `raise X from Y` records BOTH: the
       cause is what to print and `__suppress_context__` is whether to print
       the context, not whether to have one. Skipping it here made
       `e.__context__` None for every `raise ... from`, which is a different
       object graph from the one CPython builds. */
    if (!O(exc)->v.e.context && apy_handling && apy_handling != exc)
        O(exc)->v.e.context = apy_handling;
    /* A raise while an error is still PENDING -- `try: raise A finally: raise
       B` -- chains too, and nothing was "being handled" there: the A is
       in flight rather than caught. Taken before `apy_fail_replacing` clears
       the cell, which is the only moment it exists. */
    if (!O(exc)->v.e.context && apy_err_type) {
        apy_value pending = apy_error_value();
        if (pending && pending != exc && O(pending)->kind == APY_EXC_K)
            O(exc)->v.e.context = pending;
    }
    name = O(exc)->v.e.name;
    shown = O(exc)->v.e.arg;
    if (!O(exc)->v.e.has_arg) {
        apy_fail_replacing(name, "");
        apy_err_value = exc;
        return 0;
    }
    shown = apy_str(shown);
    {
        char buf[256];
        snprintf(buf, sizeof buf, "%.*s", (int)O(shown)->v.s.n, O(shown)->v.s.p);
        apy_fail_replacing(name, buf);
        /* AFTER `apy_fail_replacing`, which clears it: the text is what an
           uncaught error reports and what a handler matches on, and the object
           is what `except ... as e` binds. Both, not either. */
        apy_err_value = exc;
        return 0;
    }
}

/* The builtin exception hierarchy, as a parent chain. `except Exception:` has
   to catch a ValueError, and `except LookupError:` a KeyError, so a handler
   matching by name alone would be wrong for every non-leaf class -- which is
   most of the ones people actually write.
   NULL parent means the root. A name not in the table is treated as its own
   root: a user-defined exception class matches only itself, which is right
   until user classes exist and can declare a base. */
static const char *const APY_EXC_TREE[][2] = {
    {"Exception", "BaseException"},
    {"SystemExit", "BaseException"},
    {"KeyboardInterrupt", "BaseException"},
    {"GeneratorExit", "BaseException"},
    {"ArithmeticError", "Exception"},
    {"ZeroDivisionError", "ArithmeticError"},
    {"OverflowError", "ArithmeticError"},
    {"FloatingPointError", "ArithmeticError"},
    {"LookupError", "Exception"},
    {"IndexError", "LookupError"},
    {"KeyError", "LookupError"},
    {"NameError", "Exception"},
    {"UnboundLocalError", "NameError"},
    {"AttributeError", "Exception"},
    {"TypeError", "Exception"},
    {"ValueError", "Exception"},
    {"UnicodeError", "ValueError"},
    {"RuntimeError", "Exception"},
    {"NotImplementedError", "RuntimeError"},
    {"RecursionError", "RuntimeError"},
    {"AssertionError", "Exception"},
    {"ImportError", "Exception"},
    {"ModuleNotFoundError", "ImportError"},
    {"OSError", "Exception"},
    {"FileNotFoundError", "OSError"},
    {"StopIteration", "Exception"},
    {"StopAsyncIteration", "Exception"},
    {"MemoryError", "Exception"},
    {"EOFError", "Exception"},
    {"SyntaxError", "Exception"},
    {"IndentationError", "SyntaxError"},
};

/* A `class MyError(ValueError):` written by the PROGRAM, registered at the
   point its `class` statement runs. The built-in tree above is static; this
   grows, so the two are searched in turn.

   WHY A NAME AND NOT A TYPE OBJECT. An exception here carries a type NAME and
   one argument -- there is no class pointer in an APY_EXC_K cell, and adding
   one would mean `raise`, `except`, the parent walk and `apy_error_matches`
   all learning about two kinds of exception type. Registering the name into
   the same tree the builtins live in means `except ValueError:` catches a
   MyError through exactly the code path that already made `except
   LookupError:` catch a KeyError.

   WHAT THAT COSTS, stated rather than discovered: such a class is a NAME in a
   hierarchy and not an object. It has no methods and no attributes, so the
   frontend refuses one whose body is anything but `pass` or a docstring --
   silently dropping a method would be the bad half of this trade. */
#define APY_USER_EXC_MAX 64
static const char *apy_user_exc[APY_USER_EXC_MAX][2];
static int apy_user_exc_n;

APY_API apy_value apy_exc_register(apy_value name, apy_value parent) {
    int i;
    const char *n = O(name)->v.s.p;
    for (i = 0; i < apy_user_exc_n; i++)
        if (strcmp(apy_user_exc[i][0], n) == 0) return apy_none();
    if (apy_user_exc_n >= APY_USER_EXC_MAX)
        return apy_fail("RuntimeError",
                        "too many user-defined exception classes");
    apy_user_exc[apy_user_exc_n][0] = n;
    apy_user_exc[apy_user_exc_n][1] = O(parent)->v.s.p;
    apy_user_exc_n++;
    return apy_none();
}

static const char *apy_exc_parent(const char *name) {
    size_t i;
    int u;
    for (i = 0; i < sizeof APY_EXC_TREE / sizeof APY_EXC_TREE[0]; i++)
        if (strcmp(APY_EXC_TREE[i][0], name) == 0) return APY_EXC_TREE[i][1];
    for (u = 0; u < apy_user_exc_n; u++)
        if (strcmp(apy_user_exc[u][0], name) == 0) return apy_user_exc[u][1];
    return NULL;
}

/* Does the CURRENT error match a handler named `handler`? Walks the chain up
   from the raised type, so a base class catches every derived one. */
APY_API int64_t apy_error_matches(apy_value handler) {
    const char *want = O(handler)->v.s.p;
    const char *have = apy_err_type;
    if (!have) return 0;
    while (have) {
        if (strcmp(have, want) == 0) return 1;
        have = apy_exc_parent(have);
    }
    return 0;
}

/* The current error as a value, for `except ... as e`. Built here rather than
   at the raise, so a program that never catches never allocates one. */
/* A LOCAL READ BEFORE IT WAS ASSIGNED.

   Null is the runtime's "no value" and is never a legitimate one, so a null
   here means the assignment has not run -- which is a different thing from
   having been assigned None, and CPython distinguishes them too. Emitted only
   for locals the frontend could not prove, so an ordinary read pays nothing. */
APY_API apy_value apy_check_bound(apy_value v, apy_value name) {
    if (v) return v;
    return apy_fail2("UnboundLocalError",
                     "cannot access local variable '%s' where it is not "
                     "associated with a value%s", APY_CSTR(name), "");
}

APY_API apy_value apy_error_value(void) {
    apy_obj *o;
    if (!apy_err_type) return apy_none();
    /* The object that was RAISED, when there was one. Rebuilding from the
       message text would replace the payload with its own repr -- `raise
       E(42)` caught as `E('42')`. */
    if (apy_err_value) return apy_err_value;
    o = apy_alloc(APY_EXC_K);
    o->v.e.name = apy_err_type;
    o->v.e.rendered = 1;
    o->v.e.arg = apy_err_msg[0]
        ? apy_str_copy(apy_err_msg, (int64_t)strlen(apy_err_msg))
        : apy_none();
    o->v.e.has_arg = apy_err_msg[0] != 0;
    return V(o);
}

/* --- extraction -------------------------------------------------------- */
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
APY_API int64_t apy_index(apy_value v) {
    if (apy_is_int_like(v)) return O(v)->v.i;
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__index__");
        if (apy_error_occurred()) return 0;
        if (got && apy_is_int_like(got)) return O(got)->v.i;
    }
    if (apy_is_big(v)) {
        apy_fail("OverflowError",
                 "cannot fit 'int' into an index-sized integer");
        return 0;
    }
    apy_fail2("TypeError",
              "'%s' object cannot be interpreted as an integer%s",
              apy_kind_name(v), "");
    return 0;
}
APY_API double  apy_as_float(apy_value v) { return O(v)->v.f; }
APY_API int64_t apy_as_bool(apy_value v) { return O(v)->v.i != 0; }

/* --- inspection -------------------------------------------------------- */
/* `b'ab'`, with CPython's escaping rules.

   Which are NOT the same as str's, and the differences are the whole function:
   every byte outside printable ASCII becomes `\\xNN` (never `\\uNNNN`, since
   there is no character here to have a code point), `\\t`, `\\n` and `\\r` keep
   their short forms, and the quote is single unless the value contains one and
   no double. */
static apy_value apy_bytes_repr(apy_value v) {
    const unsigned char *p = (const unsigned char *)O(v)->v.s.p;
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

static const char *apy_kind_name(apy_value v) {
    switch (O(v)->kind) {
    case APY_NONE_K:  return "NoneType";
    case APY_BOOL_K:  return "bool";
    case APY_INT_K:   return "int";
    case APY_FLOAT_K: return "float";
    case APY_DICT_K:  return "dict";
    case APY_EXC_K:   return O(v)->v.e.name;
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
    case APY_FUNC_K:  return "function";
    case APY_CELL_K:  return "cell";
    case APY_SUPER_K: return "super";
    case APY_BYTES_K: return "bytes";
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
    case APY_GEN_K: return "generator";
    default:          return "str";
    }
}

APY_API apy_value apy_type_name(apy_value v) {
    /* The class's own name value, not a fresh copy: `type(a).__name__ is
       type(b).__name__` for two instances of one class, as in CPython. */
    if (O(v)->kind == APY_INST_K) return O(O(v)->v.o.cls)->v.t.name;
    if (O(v)->kind == APY_TYPE_K) return apy_lit("type");
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
static apy_value apy_text(apy_value v, int quoted);

/* What `__str__` or `__repr__` gave back, which MUST be a str.

   Converting it instead -- calling `apy_text` on the result -- looks more
   forgiving and is a trap: `def __str__(self): return self` would then
   recurse until the C stack ran out, and a stack overflow is not a diagnosis.
   CPython raises here, so this does, with CPython's wording. */
static apy_value apy_text_result(apy_value r, const char *which) {
    char buf[128];
    if (O(r)->kind == APY_STR_K) return r;
    snprintf(buf, sizeof buf, "%s returned non-string (type %s)",
             which, apy_kind_name(r));
    return apy_fail("TypeError", buf);
}

/* A container always shows its ELEMENTS with repr, whichever of str/repr was
   asked of the container: `print(['a'])` is `['a']`, not `[a]`. A one-element
   tuple keeps its trailing comma, because `(1)` is not a tuple. */
static apy_value apy_seq_text(apy_value v) {
    int tup = O(v)->kind == APY_TUPLE_K;
    int64_t n = O(v)->v.q.n, i, len = 2, out = 0;
    apy_value *parts = (apy_value *)malloc((size_t)(n ? n : 1) * sizeof(apy_value));
    char *buf;
    for (i = 0; i < n; i++) {
        parts[i] = apy_text(O(v)->v.q.items[i], 1);
        len += O(parts[i])->v.s.n + 2;
    }
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

static apy_value apy_text(apy_value v, int quoted) {
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
    case APY_BIG_K:   return apy_big_text(O(v));
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
    case APY_ELLIPSIS_K: return apy_lit("Ellipsis");
    case APY_EXC_K:   return apy_exc_text(v, quoted);
    case APY_LIST_K:
    case APY_TUPLE_K: return apy_seq_text(v);
    case APY_SET_K:
    case APY_FROZEN_K: return apy_set_text(v);
    case APY_INST_K: {
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
        snprintf(buf, sizeof buf, "<class '%s'>",
                 APY_CSTR(O(v)->v.t.name));
        return apy_str_copy(buf, (int64_t)strlen(buf));
    case APY_FUNC_K:
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
        for (i = 0; i < n; i++) {
            unsigned char c = (unsigned char)p[i];
            if (c == (unsigned char)q || c == '\\') {
                buf2[out++] = '\\'; buf2[out++] = (char)c;
            } else if (c == '\n') { buf2[out++] = '\\'; buf2[out++] = 'n'; }
            else if (c == '\r') { buf2[out++] = '\\'; buf2[out++] = 'r'; }
            else if (c == '\t') { buf2[out++] = '\\'; buf2[out++] = 't'; }
            else if (c < 0x20 || c == 0x7f) {
                out += (int64_t)sprintf(buf2 + out, "\\x%02x", c);
            } else buf2[out++] = (char)c;
        }
        buf2[out++] = q;
        buf2[out] = '\0';
        return apy_str_take(buf2, out);
    }
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
    if (!apy_index_arg(key, &i, APY_IDX_SUB)) return 0;
    if (i < 0) i += O(seq)->v.q.n;
    if (i < 0 || i >= O(seq)->v.q.n)
        return apy_fail("IndexError", "list assignment index out of range");
    for (; i + 1 < O(seq)->v.q.n; i++)
        O(seq)->v.q.items[i] = O(seq)->v.q.items[i + 1];
    O(seq)->v.q.n--;
    return apy_none();
}


/* --- the numeric tower ------------------------------------------------- */
/* `bool` is an `int` for arithmetic and a distinct type for everything else,
   which is exactly CPython: `True + 1` is 2, and `type(True)` is not `int`. */
static int apy_is_num(apy_value v) {
    return O(v)->kind == APY_BOOL_K || O(v)->kind == APY_INT_K
        || O(v)->kind == APY_FLOAT_K || O(v)->kind == APY_BIG_K;
}
static int apy_is_int_like(apy_value v) {
    return O(v)->kind == APY_BOOL_K || O(v)->kind == APY_INT_K
        || O(v)->kind == APY_BIG_K;
}
/* A big is either operand of a MIXED int/big pair, which the int64 fast paths
   cannot take. Every arithmetic operation asks this before reading `v.i`,
   because `v.i` on a big is a pointer read as an integer. */
static int apy_either_big(apy_value a, apy_value b) {
    return O(a)->kind == APY_BIG_K || O(b)->kind == APY_BIG_K;
}
static double apy_num_f(apy_value v) {
    if (O(v)->kind == APY_FLOAT_K) return O(v)->v.f;
    if (O(v)->kind == APY_BIG_K) return apy_big_double(O(v));
    return (double)O(v)->v.i;
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

static apy_value apy_binop_error(const char *op, apy_value a, apy_value b) {
    char buf[256];
    snprintf(buf, sizeof buf,
             "unsupported operand type(s) for %s: '%s' and '%s'",
             op, apy_kind_name(a), apy_kind_name(b));
    return apy_fail("TypeError", buf);
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
    { NULL, NULL, NULL },
};

/* Where the arithmetic operators end when neither operand is a kind they know.
   Ask the user's class first; report the operand pair only if nothing
   answered. `apy_binop_error` itself is kept for the two call sites in
   `sorted`/`min`, which want the REPORT and not another dispatch -- they have
   already decided the comparison failed and are naming why. */
static apy_value apy_binop_fallback(const char *op, apy_value a, apy_value b) {
    int i;
    if (apy_either_inst(a, b))
        for (i = 0; APY_OP_DUNDERS[i][0]; i++)
            if (strcmp(APY_OP_DUNDERS[i][0], op) == 0) {
                apy_value r = apy_binary_dunder(a, b, APY_OP_DUNDERS[i][1],
                                                APY_OP_DUNDERS[i][2]);
                if (r || apy_error_occurred()) return r;
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
          return r; }
    }
    if (O(a)->kind == APY_BYTES_K || O(b)->kind == APY_BYTES_K) {
        if (O(a)->kind != O(b)->kind) return 0;
        return O(a)->v.s.n == O(b)->v.s.n
            && memcmp(O(a)->v.s.p, O(b)->v.s.p, (size_t)O(a)->v.s.n) == 0;
    }
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

static uint64_t apy_abs64(int64_t v) {
    /* Not `-v`: INT64_MIN has no positive counterpart and negating it is
       undefined. Negating the UNSIGNED value is defined and gives 2**63. */
    return v < 0 ? -(uint64_t)v : (uint64_t)v;
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
        int64_t q = O(a)->v.i / O(b)->v.i, r = O(a)->v.i % O(b)->v.i;
        /* INT64_MIN / -1 is the one signed division C leaves undefined, and
           it is the one case where the quotient does not fit. It is a big. */
        if (O(a)->v.i == (-9223372036854775807LL - 1) && O(b)->v.i == -1) {
            apy_value qq, rr;
            apy_big_floordivmod(apy_as_big(a), apy_as_big(b), &qq, &rr);
            return qq;
        }
        if (r != 0 && ((r < 0) != (O(b)->v.i < 0))) q--;
        return apy_from_int(q);
    }
}

APY_API apy_value apy_mod(apy_value a, apy_value b) {
    if (apy_either_complex(a, b))
        return apy_fail2("TypeError",
                         "can't take floor or mod of complex number%s%s",
                         "", "");
    /* `%` on a str is PRINTF-STYLE FORMATTING in Python, not arithmetic, so
       CPython never reports it as an unsupported operand -- it tries to
       format and complains about the arguments. Formatting is not implemented
       here; reporting CPython's message is what a program that reaches this
       will see, and it is at least the message for the operation it actually
       asked for. */
    if (O(a)->kind == APY_STR_K)
        return apy_fail("TypeError",
                        "not all arguments converted during string formatting");
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
           the last printed digit -- see POW_INT_C in link/runtime.py for the
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

APY_API apy_value apy_bitand(apy_value a, apy_value b) { return apy_intop("&", a, b, 0); }
APY_API apy_value apy_bitor(apy_value a, apy_value b) {
    /* `d1 | d2` MERGES, with the right-hand side winning -- PEP 584. */
    if (O(a)->kind == APY_DICT_K && O(b)->kind == APY_DICT_K) {
        apy_value out = apy_copy(a);
        if (!out || !apy_update(out, b)) return 0;
        return out;
    }
    return apy_intop("|", a, b, 1);
}
APY_API apy_value apy_bitxor(apy_value a, apy_value b) { return apy_intop("^", a, b, 2); }
APY_API apy_value apy_lshift(apy_value a, apy_value b) { return apy_intop("<<", a, b, 3); }
APY_API apy_value apy_rshift(apy_value a, apy_value b) { return apy_intop(">>", a, b, 4); }

/* --- comparison -------------------------------------------------------- */
/* Equality is TOTAL: every pair of objects can be compared, and a pair with
   nothing in common is simply unequal. Ordering is not -- `7 < 'ab'` is a
   TypeError, and answering False there would be a wrong answer rather than a
   missing feature. */
static int apy_str_cmp(apy_value a, apy_value b) {
    int64_t n = O(a)->v.s.n < O(b)->v.s.n ? O(a)->v.s.n : O(b)->v.s.n;
    int c = n ? memcmp(O(a)->v.s.p, O(b)->v.s.p, (size_t)n) : 0;
    if (c) return c < 0 ? -1 : 1;
    if (O(a)->v.s.n == O(b)->v.s.n) return 0;
    return O(a)->v.s.n < O(b)->v.s.n ? -1 : 1;
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
static int apy_cmp_int_double(int64_t i, double f) {
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

static int apy_num_order(apy_value a, apy_value b) {
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

static int apy_eq_raw(apy_value a, apy_value b);

/* Element-by-element, and only between the SAME kind: a list never equals a
   tuple in Python even when their contents match. */
static int apy_seq_eq(apy_value a, apy_value b) {
    int64_t i;
    if (O(a)->kind != O(b)->kind) return 0;
    if (O(a)->v.q.n != O(b)->v.q.n) return 0;
    for (i = 0; i < O(a)->v.q.n; i++)
        if (!apy_eq_raw(O(a)->v.q.items[i], O(b)->v.q.items[i])) return 0;
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
        if (!apy_eq_raw(O(a)->v.d.vals[i], O(b)->v.d.vals[at])) return 0;
    }
    return 1;
}

static int apy_eq_raw(apy_value a, apy_value b) {
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
        return a == b;
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
    if (O(a)->kind == APY_STR_K || O(b)->kind == APY_STR_K)
        return O(a)->kind == O(b)->kind && apy_str_cmp(a, b) == 0;
    if (O(a)->kind == APY_NONE_K || O(b)->kind == APY_NONE_K)
        return O(a)->kind == O(b)->kind;
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
                if (apy_eq_raw(needle, item)) return apy_from_bool(1);
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
            if (apy_eq_raw(needle, O(hay)->v.d.keys[i]))
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
            if (apy_eq_raw(needle, O(hay)->v.q.items[i]))
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
static int apy_order(apy_value a, apy_value b) {
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
            int c = apy_order(O(a)->v.q.items[i], O(b)->v.q.items[i]);
            if (c == 2) return 2;
            if (c) return c;
        }
        if (O(a)->v.q.n == O(b)->v.q.n) return 0;
        return O(a)->v.q.n < O(b)->v.q.n ? -1 : 1;
    }
    if (!apy_is_num(a) || !apy_is_num(b)) return 2;
    return apy_num_order(a, b);
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

/* --- string methods ----------------------------------------------------- */
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
static int apy_str_self(const char *name, apy_value v) {
    if (O(v)->kind == APY_STR_K) return 1;
    apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
              apy_kind_name(v), name);
    return 0;
}

/* `find() argument 1 must be str, not int`. TWO ODDITIES OF THIS FAMILY are
   reproduced rather than regularised, because the suite is generated from
   CPython and both are visible: the kind is written WITHOUT quotes, and
   NoneType is written as `None` -- while `startswith`, forty lines down, says
   `not NoneType` for the very same value. `argno` of 0 drops the number,
   which is how `removeprefix` words it. */
static apy_value apy_arg_must_be_str(const char *meth, int argno, apy_value v) {
    char buf[160];
    const char *k = O(v)->kind == APY_NONE_K ? "None" : apy_kind_name(v);
    if (argno)
        snprintf(buf, sizeof buf, "%s() argument %d must be str, not %s",
                 meth, argno, k);
    else
        snprintf(buf, sizeof buf, "%s() argument must be str, not %s", meth, k);
    return apy_fail("TypeError", buf);
}

static int apy_str_other(const char *meth, int argno, apy_value v) {
    if (O(v)->kind == APY_STR_K) return 1;
    apy_arg_must_be_str(meth, argno, v);
    return 0;
}

static int apy_int_arg(apy_value v, int64_t *out) {
    if (!apy_is_int_like(v)) {
        apy_fail2("TypeError",
                  "'%s' object cannot be interpreted as an integer%s",
                  apy_kind_name(v), "");
        return 0;
    }
    /* A width or a count that does not fit a machine word. `'ab'.ljust(2 **
       100)` asks for a string longer than the address space. */
    if (apy_is_big(v)) {
        apy_fail("OverflowError",
                 "Python int too large to convert to C ssize_t");
        return 0;
    }
    *out = O(v)->v.i;
    return 1;
}


static int apy_slice_arg(apy_value v, int64_t *out) {
    if (O(v)->kind == APY_NONE_K) return 1;
    /* A SLICE BOUND SATURATES where a width or a subscript refuses:
       `'abc'.find('a', 2 ** 100)` is -1 and `'abc'.find('a', -(2 ** 100))` is
       0, because a bound past either end is just a bound past that end. The
       saturation value is 2**62 rather than INT64_MAX so that the `+= n`
       inside `apy_clamp_range` cannot itself overflow. */
    if (apy_is_big(v)) {
        *out = O(v)->v.big.neg ? -((int64_t)1 << 62) : ((int64_t)1 << 62);
        return 1;
    }
    return apy_int_arg(v, out);
}

static void apy_clamp_range(int64_t n, int64_t *lo, int64_t *hi) {
    if (*lo < 0) { *lo += n; if (*lo < 0) *lo = 0; }
    if (*hi < 0) { *hi += n; if (*hi < 0) *hi = 0; }
    if (*hi > n) *hi = n;
}

/* The first occurrence of `sub` in `s[lo:hi]`, as an absolute index, or -1.
   An EMPTY needle matches at `lo` -- but only if `lo` is inside the window,
   which is the whole reason this takes `hi` rather than assuming the end. */
static int64_t apy_find_at(apy_value s, apy_value sub, int64_t lo, int64_t hi) {
    int64_t m = O(sub)->v.s.n, i;
    if (m == 0) return lo <= hi ? lo : -1;
    for (i = lo; i + m <= hi; i++)
        if (memcmp(O(s)->v.s.p + i, O(sub)->v.s.p, (size_t)m) == 0) return i;
    return -1;
}

static int64_t apy_rfind_at(apy_value s, apy_value sub, int64_t lo, int64_t hi) {
    int64_t m = O(sub)->v.s.n, i;
    if (m == 0) return lo <= hi ? hi : -1;
    for (i = hi - m; i >= lo; i--)
        if (memcmp(O(s)->v.s.p + i, O(sub)->v.s.p, (size_t)m) == 0) return i;
    return -1;
}

static apy_value apy_str_slice_of(apy_value s, int64_t lo, int64_t hi) {
    if (hi < lo) hi = lo;
    return apy_str_copy(O(s)->v.s.p + lo, hi - lo);
}

/* find / rfind / index / rindex, all four from one place. `want_index` picks
   the -1-on-failure form from the raise-on-failure one; that is the only
   difference between `find` and `index`, and CPython's message for the second
   is `substring not found` with no mention of what was looked for. */
static apy_value apy_str_search(apy_value s, apy_value sub, apy_value start,
                                apy_value end, int from_right, int want_index) {
    int64_t lo = 0, hi, at;
    const char *meth = want_index ? (from_right ? "rindex" : "index")
                                  : (from_right ? "rfind" : "find");
    if (!apy_str_self(meth, s)) return 0;
    if (!apy_str_other(meth, 1, sub)) return 0;
    hi = O(s)->v.s.n;
    if (start && !apy_slice_arg(start, &lo)) return 0;
    if (end && !apy_slice_arg(end, &hi)) return 0;
    apy_clamp_range(O(s)->v.s.n, &lo, &hi);
    at = from_right ? apy_rfind_at(s, sub, lo, hi) : apy_find_at(s, sub, lo, hi);
    if (at < 0 && want_index)
        return apy_fail("ValueError", "substring not found");
    return apy_from_int(at);
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
static apy_value apy_str_count_in(apy_value s, apy_value sub, apy_value start,
                                  apy_value end) {
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
static int apy_c_lower(unsigned char c) { return c >= 'a' && c <= 'z'; }
static int apy_c_upper(unsigned char c) { return c >= 'A' && c <= 'Z'; }
static int apy_c_alpha(unsigned char c) { return apy_c_lower(c) || apy_c_upper(c); }
static int apy_c_digit(unsigned char c) { return c >= '0' && c <= '9'; }
static int apy_c_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f'
        || c == '\v';
}

enum { APY_UPPER, APY_LOWER, APY_TITLE, APY_CAPITAL, APY_SWAP };

static apy_value apy_str_case(apy_value s, int mode) {
    int64_t n = O(s)->v.s.n, i;
    char *buf = (char *)malloc((size_t)n + 1);
    int prev_cased = 0;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)O(s)->v.s.p[i];
        unsigned char out = c;
        switch (mode) {
        case APY_UPPER: if (apy_c_lower(c)) out = (unsigned char)(c - 32); break;
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
        buf[i] = (char)out;
    }
    buf[n] = '\0';
    return apy_str_take(buf, n);
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
   it IS lowercasing. The pair it exists for -- 'ß' folding to 'ss' -- is
   exactly the non-ASCII case this runtime does not do. */
APY_API apy_value apy_str_casefold(apy_value s) {
    if (!apy_str_self("casefold", s)) return 0;
    return apy_str_case(s, APY_LOWER);
}

/* --- predicates ---------------------------------------------------------
   All of them are False for the EMPTY string except `isascii`, which is True
   -- that is not an accident of the loop, it is Python's rule, and writing
   the loop so that "no character failed" means True would get every one of
   them wrong for ''. */
enum { APY_ISALPHA, APY_ISDIGIT, APY_ISALNUM, APY_ISSPACE, APY_ISLOWER,
       APY_ISUPPER, APY_ISTITLE, APY_ISPRINTABLE, APY_ISIDENT, APY_ISASCII };

static apy_value apy_str_is(apy_value s, int which) {
    int64_t n = O(s)->v.s.n, i;
    int cased = 0, prev_cased = 0, ok = 1;
    if (which == APY_ISASCII) {
        for (i = 0; i < n; i++)
            if ((unsigned char)O(s)->v.s.p[i] > 0x7f) return apy_from_bool(0);
        return apy_from_bool(1);
    }
    if (which == APY_ISPRINTABLE) {
        /* Printable is about the absence of control characters, so '' and
           ' ' are both True while '\n' is not. */
        for (i = 0; i < n; i++) {
            unsigned char c = (unsigned char)O(s)->v.s.p[i];
            if (c < 0x20 || c == 0x7f) return apy_from_bool(0);
        }
        return apy_from_bool(1);
    }
    if (which == APY_ISIDENT) {
        if (n == 0) return apy_from_bool(0);
        if (!apy_c_alpha((unsigned char)O(s)->v.s.p[0])
            && O(s)->v.s.p[0] != '_') return apy_from_bool(0);
        for (i = 1; i < n; i++) {
            unsigned char c = (unsigned char)O(s)->v.s.p[i];
            if (!apy_c_alpha(c) && !apy_c_digit(c) && c != '_')
                return apy_from_bool(0);
        }
        return apy_from_bool(1);
    }
    if (n == 0) return apy_from_bool(0);
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)O(s)->v.s.p[i];
        switch (which) {
        case APY_ISALPHA: if (!apy_c_alpha(c)) ok = 0; break;
        case APY_ISDIGIT: if (!apy_c_digit(c)) ok = 0; break;
        case APY_ISALNUM: if (!apy_c_alpha(c) && !apy_c_digit(c)) ok = 0; break;
        case APY_ISSPACE: if (!apy_c_space(c)) ok = 0; break;
        case APY_ISLOWER:
            /* "no uppercase AND at least one lowercase" -- `'ab1'.islower()`
               is True and `'123'.islower()` is False. A plain "every
               character is lowercase" answers the second one wrongly. */
            if (apy_c_upper(c)) ok = 0;
            if (apy_c_lower(c)) cased = 1;
            break;
        case APY_ISUPPER:
            if (apy_c_lower(c)) ok = 0;
            if (apy_c_upper(c)) cased = 1;
            break;
        default:
            if (apy_c_upper(c)) { if (prev_cased) ok = 0; cased = 1; }
            else if (apy_c_lower(c)) { if (!prev_cased) ok = 0; cased = 1; }
            prev_cased = apy_c_alpha(c);
            break;
        }
        if (!ok) return apy_from_bool(0);
    }
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
/* `isdecimal` and `isnumeric` differ from `isdigit` only outside ASCII --
   '²' is a digit and numeric but not decimal, and 'Ⅶ' is numeric alone. In
   ASCII all three are the same test, so they share it rather than pretending
   to a distinction this runtime cannot draw. */
APY_API apy_value apy_str_isdecimal(apy_value s) {
    if (!apy_str_self("isdecimal", s)) return 0;
    return apy_str_is(s, APY_ISDIGIT);
}
APY_API apy_value apy_str_isnumeric(apy_value s) {
    if (!apy_str_self("isnumeric", s)) return 0;
    return apy_str_is(s, APY_ISDIGIT);
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
static int apy_in_chars(apy_value chars, unsigned char c) {
    int64_t i;
    if (!chars) return apy_c_space(c);
    for (i = 0; i < O(chars)->v.s.n; i++)
        if ((unsigned char)O(chars)->v.s.p[i] == c) return 1;
    return 0;
}

static apy_value apy_str_trim(apy_value s, apy_value chars, const char *meth,
                              int left, int right) {
    int64_t lo = 0, hi = O(s)->v.s.n;
    if (chars && O(chars)->kind != APY_STR_K) {
        /* Its own wording, naming NEITHER the offending kind nor a position:
           `strip arg must be None or str`. */
        char buf[80];
        snprintf(buf, sizeof buf, "%s arg must be None or str", meth);
        return apy_fail("TypeError", buf);
    }
    if (left)
        while (lo < hi && apy_in_chars(chars, (unsigned char)O(s)->v.s.p[lo])) lo++;
    if (right)
        while (hi > lo && apy_in_chars(chars, (unsigned char)O(s)->v.s.p[hi - 1])) hi--;
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
static apy_value apy_split_ws(apy_value s, int64_t maxsplit, int from_right) {
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

static apy_value apy_split_sep(apy_value s, apy_value sep, int64_t maxsplit,
                               int from_right) {
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

static apy_value apy_str_split_impl(apy_value s, apy_value sep, apy_value limit,
                                    int from_right) {
    int64_t maxsplit = -1;
    if (limit && !apy_int_arg(limit, &maxsplit)) return 0;
    if (maxsplit < 0) maxsplit = -1;      /* any negative means "no limit" */
    if (!sep || O(sep)->kind == APY_NONE_K)
        return apy_split_ws(s, maxsplit, from_right);
    if (O(sep)->kind != APY_STR_K)
        return apy_fail2("TypeError", "must be str or None, not %s%s",
                         apy_kind_name(sep), "");
    return apy_split_sep(s, sep, maxsplit, from_right);
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
static apy_value apy_splitlines_impl(apy_value s, int keepends) {
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
    if (O(sep)->kind != APY_STR_K)
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
        if (O(got[i])->kind != APY_STR_K) {
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
static int apy_affix1(apy_value s, apy_value fix, int64_t lo, int64_t hi,
                      int at_end) {
    int64_t m = O(fix)->v.s.n;
    if (m > hi - lo) return 0;
    return memcmp(O(s)->v.s.p + (at_end ? hi - m : lo),
                  O(fix)->v.s.p, (size_t)m) == 0;
}

static apy_value apy_affix(apy_value s, apy_value fix, apy_value start,
                           apy_value end, int at_end) {
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
    if (O(fix)->kind != APY_STR_K)
        return apy_fail2("TypeError",
                         "%s first arg must be str or a tuple of str, not %s",
                         meth, apy_kind_name(fix));
    return apy_from_bool(apy_affix1(s, fix, lo, hi, at_end));
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
static int apy_fill_char(apy_value fill, char *out) {
    if (O(fill)->kind != APY_STR_K) {
        apy_fail2("TypeError",
                  "The fill character must be a unicode character, not %s%s",
                  apy_kind_name(fill), "");
        return 0;
    }
    if (O(fill)->v.s.n != 1) {
        apy_fail("TypeError",
                 "The fill character must be exactly one character long");
        return 0;
    }
    *out = O(fill)->v.s.p[0];
    return 1;
}

enum { APY_LJUST, APY_RJUST, APY_CENTER };

static apy_value apy_pad(apy_value s, apy_value width, apy_value fill, int how) {
    int64_t n = O(s)->v.s.n, w, pad, left;
    char c = ' ', *buf;
    if (!apy_int_arg(width, &w)) return 0;
    if (fill && !apy_fill_char(fill, &c)) return 0;
    if (w <= n) return s;          /* already wide enough: Python returns it */
    pad = w - n;
    /* CPython's own split for `center`, which is NOT `pad / 2`: it biases the
       extra character to the RIGHT for an even width and to the LEFT for an
       odd one, so `'ab'.center(7, '*')` is '***ab**' and `'ab'.center(3)` is
       ' ab'. Halving alone gets both of those backwards. */
    left = how == APY_RJUST ? pad
         : how == APY_LJUST ? 0
         : pad / 2 + (pad & w & 1);
    buf = (char *)malloc((size_t)w + 1);
    memset(buf, c, (size_t)w);
    memcpy(buf + left, O(s)->v.s.p, (size_t)n);
    buf[w] = '\0';
    return apy_str_take(buf, w);
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
    int64_t n, w, pad;
    char *buf;
    int signed_ = 0;
    if (!apy_str_self("zfill", s)) return 0;
    if (!apy_int_arg(width, &w)) return 0;
    n = O(s)->v.s.n;
    if (w <= n) return s;
    signed_ = n > 0 && (O(s)->v.s.p[0] == '-' || O(s)->v.s.p[0] == '+');
    pad = w - n;
    buf = (char *)malloc((size_t)w + 1);
    memset(buf, '0', (size_t)w);
    if (signed_) {
        buf[0] = O(s)->v.s.p[0];
        memcpy(buf + 1 + pad, O(s)->v.s.p + 1, (size_t)(n - 1));
    } else {
        memcpy(buf + pad, O(s)->v.s.p, (size_t)n);
    }
    buf[w] = '\0';
    return apy_str_take(buf, w);
}

/* --- integer methods and the base builtins ------------------------------ */
/* `pow(a, b, m)`, which is not `a ** b % m` and cannot be: `pow(2, 1000,
   1000003)` is instant and `2 ** 1000 % 1000003` builds a 302-digit number
   first. Reducing at every step is the whole point of the three-argument
   form, and it is the reason it exists in the language. */
APY_API apy_value apy_pow3(apy_value a, apy_value b, apy_value m);

APY_API apy_value apy_pow3(apy_value a, apy_value b, apy_value m) {
    apy_value r, base;
    int64_t n;
    if (!apy_is_int_like(a) || !apy_is_int_like(b) || !apy_is_int_like(m))
        return apy_fail("TypeError",
                        "pow() 3rd argument not allowed unless all arguments "
                        "are integers");
    if (!apy_is_big(m) && O(m)->v.i == 0)
        return apy_fail("ValueError", "pow() 3rd argument cannot be 0");
    /* A NEGATIVE exponent is the MODULAR INVERSE raised to its magnitude,
       which CPython grew in 3.8. Done here with the extended Euclidean
       algorithm, written in terms of the public operators rather than in
       limbs: every one of them already promotes and already floors the way
       Python does, so there is nothing about big integers left to get wrong
       in it, and the whole algorithm is the six lines it is on paper. */
    if ((apy_is_big(b) && O(b)->v.big.neg) || (!apy_is_big(b) && O(b)->v.i < 0)) {
        apy_value tt = apy_from_int(0), newt = apy_from_int(1);
        apy_value rr = m, newr = apy_mod(a, m);
        if (!newr) return 0;
        while (!apy_is_big(newr) && O(newr)->v.i != 0) {
            apy_value q = apy_floordiv(rr, newr), st, sr;
            if (!q) return 0;
            st = apy_sub(tt, apy_mul(q, newt));
            sr = apy_sub(rr, apy_mul(q, newr));
            if (!st || !sr) return 0;
            tt = newt; newt = st;
            rr = newr; newr = sr;
        }
        /* A base with a common factor with the modulus has NO inverse, and
           `gcd != 1` is how that shows: 2 has none mod 4, and neither does 0
           mod anything. CPython reports rather than answering 0. */
        if (apy_is_big(rr) || (O(rr)->v.i != 1 && O(rr)->v.i != -1))
            return apy_fail("ValueError",
                            "base is not invertible for the given modulus");
        if (!apy_is_big(rr) && O(rr)->v.i == -1) tt = apy_neg(tt);
        tt = apy_mod(tt, m);
        if (!tt) return 0;
        /* And now the positive-exponent case, on the inverse. */
        return apy_pow3(tt, apy_neg(b), m);
    }
    if (apy_is_big(b)) return apy_big_too_large();
    n = O(b)->v.i;
    r = apy_mod(apy_from_int(1), m);
    if (!r) return 0;
    base = apy_mod(a, m);
    if (!base) return 0;
    while (n) {
        if (n & 1) {
            r = apy_mul(r, base);
            if (r) r = apy_mod(r, m);
            if (!r) return 0;
        }
        n >>= 1;
        if (n) {
            base = apy_mul(base, base);
            if (base) base = apy_mod(base, m);
            if (!base) return 0;
        }
    }
    return r;
}

/* `divmod(a, b)`. NOT two calls: the quotient and the remainder come out of
   one division, and for two bigs that division is the expensive part -- doing
   it twice is the whole cost twice. It is also the only way to be sure the
   two answers are consistent, which is the property `divmod(a, b)[0] * b +
   divmod(a, b)[1] == a` is asserting. Floats go through the existing `//` and
   `%`, which already carry CPython's transcribed `float_divmod`. */
APY_API apy_value apy_divmod(apy_value a, apy_value b) {
    apy_value out, q, r;
    if (!apy_is_num(a) || !apy_is_num(b))
        return apy_binop_error("divmod()", a, b);
    if (apy_is_int_like(a) && apy_is_int_like(b) && apy_either_big(a, b)) {
        if (!apy_is_big(b) && O(b)->v.i == 0)
            return apy_fail("ZeroDivisionError", APY_DIV0);
        apy_big_floordivmod(apy_as_big(a), apy_as_big(b), &q, &r);
    } else {
        q = apy_floordiv(a, b);
        if (!q) return 0;
        r = apy_mod(a, b);
        if (!r) return 0;
    }
    if (!q || !r) return 0;
    out = apy_seq_new(APY_TUPLE_K, 2);
    apy_q_append(out, q);
    apy_q_append(out, r);
    return out;
}

/* `n.bit_length()` -- the bits needed for the MAGNITUDE, so `(-255)` and
   `255` both answer 8 and `0` answers 0. */
APY_API apy_value apy_bit_length(apy_value v) {
    if (!apy_is_int_like(v))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'bit_length'%s",
                         apy_kind_name(v), "");
    if (apy_is_big(v)) return apy_from_int(apy_mag_bits(O(v)));
    {
        uint64_t m = apy_abs64(O(v)->v.i);
        int64_t n = 0;
        while (m) { n++; m >>= 1; }
        return apy_from_int(n);
    }
}

/* `n.bit_count()` -- the number of ONE bits in the magnitude, again ignoring
   the sign, which is what CPython counts. */
APY_API apy_value apy_bit_count(apy_value v) {
    if (!apy_is_int_like(v))
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'bit_count'%s",
                         apy_kind_name(v), "");
    if (apy_is_big(v)) return apy_from_int(apy_big_popcount(O(v)));
    {
        uint64_t m = apy_abs64(O(v)->v.i);
        int64_t n = 0;
        while (m) { n += (int64_t)(m & 1); m >>= 1; }
        return apy_from_int(n);
    }
}

/* `bin`, `oct` and `hex`. The prefix goes AFTER the sign -- `bin(-10)` is
   `-0b1010` and not `0b-1010` -- which is the only thing about these that is
   easy to get backwards. */
static apy_value apy_base_text(apy_value v, int bits_per, const char *prefix,
                               const char *fn) {
    /* `hex(obj)` goes through `__index__` -- PEP 357 names these three as the
       operations it exists for, alongside subscripting. */
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__index__");
        if (apy_error_occurred()) return 0;
        if (got && apy_is_int_like(got)) v = got;
    }
    if (!apy_is_int_like(v))
        return apy_fail2("TypeError",
                         "%s() argument can't be interpreted as an integer%s",
                         fn, "");
    if (apy_is_big(v)) return apy_big_base_text(O(v), bits_per, prefix);
    {
        uint64_t m = apy_abs64(O(v)->v.i);
        char buf[80];
        int out = 0, i, start;
        if (O(v)->v.i < 0) buf[out++] = '-';
        buf[out++] = prefix[0];
        buf[out++] = prefix[1];
        start = out;
        if (m == 0) buf[out++] = '0';
        while (m) {
            buf[out++] = "0123456789abcdef"[m & (((uint64_t)1 << bits_per) - 1)];
            m >>= bits_per;
        }
        for (i = 0; i < (out - start) / 2; i++) {
            char c = buf[start + i];
            buf[start + i] = buf[out - 1 - i];
            buf[out - 1 - i] = c;
        }
        buf[out] = '\0';
        return apy_str_copy(buf, out);
    }
}

APY_API apy_value apy_bin(apy_value v) { return apy_base_text(v, 1, "0b", "bin"); }
APY_API apy_value apy_oct(apy_value v) { return apy_base_text(v, 3, "0o", "oct"); }
APY_API apy_value apy_hex(apy_value v) { return apy_base_text(v, 4, "0x", "hex"); }

/* `int(s, base)` for ANY base from 2 to 36, and base 0 -- which means "read
   the prefix", so `int('0x1f', 0)` is 31 and `int('17', 0)` is 17.

   Multiply-and-add rather than the shift the power-of-two bases allow: base
   36 has no shift, and having one loop means base 3 cannot be the one nobody
   tested. The multiply goes through `apy_mul` so the promotion to a big
   integer is the one the operators already know how to do. */
APY_API apy_value apy_to_int_base(apy_value v, apy_value base) {
    int64_t b, i, lo, hi;
    int neg = 0;
    apy_value acc;
    if (O(v)->kind != APY_STR_K)
        return apy_fail2("TypeError",
                         "int() can't convert non-string with explicit base%s%s",
                         "", "");
    if (!apy_int_arg(base, &b)) return 0;
    if (b != 0 && (b < 2 || b > 36))
        return apy_fail("ValueError", "int() base must be >= 2 and <= 36, or 0");
    lo = 0;
    hi = O(v)->v.s.n;
    while (lo < hi && apy_is_space(O(v)->v.s.p[lo])) lo++;
    while (hi > lo && apy_is_space(O(v)->v.s.p[hi - 1])) hi--;
    if (lo < hi && (O(v)->v.s.p[lo] == '+' || O(v)->v.s.p[lo] == '-')) {
        neg = O(v)->v.s.p[lo] == '-';
        lo++;
    }
    /* The `0x`/`0o`/`0b` prefix is OPTIONAL when the base says the same
       thing, and DECIDES the base when the base is 0 -- which is the whole of
       what base 0 means. */
    if (hi - lo >= 2 && O(v)->v.s.p[lo] == '0') {
        char c = O(v)->v.s.p[lo + 1];
        int said = (c == 'x' || c == 'X') ? 16
                 : (c == 'o' || c == 'O') ? 8
                 : (c == 'b' || c == 'B') ? 2 : 0;
        if (said && (b == 0 || b == said)) { b = said; lo += 2; }
    }
    if (b == 0) b = 10;
    if (lo >= hi) {
        char msg[128];
        snprintf(msg, sizeof msg, "invalid literal for int() with base %lld: ",
                 (long long)b);
        return apy_conv_error(msg, v);
    }
    acc = apy_from_int(0);
    for (i = lo; i < hi; i++) {
        unsigned char c = (unsigned char)O(v)->v.s.p[i];
        int d;
        if (c == '_') continue;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'z') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'Z') d = c - 'A' + 10;
        else d = 99;
        if (d >= b) {
            char msg[128];
            snprintf(msg, sizeof msg,
                     "invalid literal for int() with base %lld: ", (long long)b);
            return apy_conv_error(msg, v);
        }
        acc = apy_mul(acc, apy_from_int(b));
        if (acc) acc = apy_add(acc, apy_from_int(d));
        if (!acc) return 0;
    }
    return neg ? apy_neg(acc) : acc;
}

/* --- builtins over sequences ------------------------------------------- */
/* `sorted`, `min`, `max`, `sum`, `reversed`, `enumerate`, `zip` all produce a
   LIST here, not an iterator. Python's return an iterator for the last three,
   and the difference is observable -- `type(enumerate(x)).__name__` is
   'enumerate' and a second pass over one yields nothing. That is a stated
   divergence, not an oversight: a real iterator needs a resumable frame, and
   every other use of these is a `for` loop or a `list(...)`, which a list
   satisfies exactly. */

APY_API apy_value apy_sorted(apy_value seq) {
    int64_t n = apy_raw_len(seq), i, j;
    apy_value out;
    if (apy_error_occurred()) return 0;
    out = apy_seq_new(APY_LIST_K, n + 1);
    for (i = 0; i < n; i++) apy_seq_push(out, apy_key_at(seq, i));
    /* Insertion sort, which is STABLE -- equal elements keep their input
       order, and Python guarantees that. A quicksort here would be faster and
       would quietly reorder them. */
    for (i = 1; i < n; i++) {
        apy_value key = O(out)->v.q.items[i];
        j = i - 1;
        while (j >= 0) {
            int c = apy_order(key, O(out)->v.q.items[j]);
            if (c == 2) {
                apy_binop_error("<", key, O(out)->v.q.items[j]);
                return 0;
            }
            if (c >= 0) break;
            O(out)->v.q.items[j + 1] = O(out)->v.q.items[j];
            j--;
        }
        O(out)->v.q.items[j + 1] = key;
    }
    return out;
}

/* `sorted(xs, key=f)` and `sorted(xs, reverse=True)`.

   The key is computed ONCE PER ELEMENT, before the sort, not inside the
   comparison. That is what CPython does and it is observable: a key function
   with a side effect runs exactly n times, and one that raises does so before
   any comparison happens.

   Reversal is applied by flipping the comparison rather than by reversing the
   result, because reversing afterwards also reverses the order of EQUAL
   elements -- and `sorted(reverse=True)` is still stable. That distinction is
   invisible until two items compare equal.

   `keyfn` of 0 means no key, so this is also the plain sort; keeping one
   implementation means the stability argument above only has to be right
   once. */
static apy_value apy_sort_with(apy_value seq, apy_value keyfn, int reverse) {
    int64_t n = apy_raw_len(seq), i, j;
    apy_value out, keys;
    if (apy_error_occurred()) return 0;
    out = apy_seq_new(APY_LIST_K, n + 1);
    keys = apy_seq_new(APY_LIST_K, n + 1);
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(seq, i);
        if (!item) return 0;
        apy_seq_push(out, item);
        if (keyfn) {
            apy_value k = apy_call_n(keyfn, &item, 1);
            if (!k) return 0;
            apy_seq_push(keys, k);
        } else {
            apy_seq_push(keys, item);
        }
    }
    for (i = 1; i < n; i++) {
        apy_value item = O(out)->v.q.items[i];
        apy_value k = O(keys)->v.q.items[i];
        j = i - 1;
        while (j >= 0) {
            int c = apy_order(k, O(keys)->v.q.items[j]);
            if (c == 2) {
                apy_binop_error("<", k, O(keys)->v.q.items[j]);
                return 0;
            }
            if (reverse ? c <= 0 : c >= 0) break;
            O(out)->v.q.items[j + 1] = O(out)->v.q.items[j];
            O(keys)->v.q.items[j + 1] = O(keys)->v.q.items[j];
            j--;
        }
        O(out)->v.q.items[j + 1] = item;
        O(keys)->v.q.items[j + 1] = k;
    }
    return out;
}

/* `iter(x)`. An iterator OVER an iterator is itself, which is what makes
   `for v in it` work on a partly-consumed one and what `iter(iter(x)) is
   iter(x)` asserts. */
/* `dict(pairs)` -- a sequence of two-element sequences. Not `dict(**kw)` and
   not `dict(mapping)`; both are shapes nothing here can produce yet. */
/* `{*xs, y}` -- every element of a sequence, ADDED to a set. `apy_extend`
   appends and would let a duplicate through; the distinction is the whole
   difference between a set display and a list one. */
APY_API apy_value apy_set_update(apy_value target, apy_value src) {
    int64_t i, n = apy_raw_len(src);
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(src, i);
        if (!item || !apy_set_push(target, item)) return 0;
    }
    return apy_none();
}

/* The VARIADIC builtins, reached through a value rather than a call site:
   `map(print, xs)`, `key=dict`. Each takes the argument tuple a `*rest`
   thunk was handed, because a value-form has no compile-time argument count
   -- which is exactly what kept these three out of `_VALUE_BUILTINS` until
   the thunk learned to be variadic.

   The bodies are the SAME operations the direct call sites emit, reached by
   one more hop, so there is no second implementation to drift. */
/* `map(f, xs)` and `filter(f, xs)`.

   The calls all happen HERE and the result is a cursor over what they
   returned, where CPython's are lazy. That difference is visible: a `map` over
   a side-effecting function runs it eagerly, and one over an infinite source
   would not terminate. Laziness needs a resumable frame, which is the same
   thing `yield` needs and neither has yet.

   `filter(None, xs)` keeps the truthy elements -- a real form, and the reason
   the callable is tested for None rather than simply called. */
APY_API apy_value apy_map(apy_value fn, apy_value seq) {
    apy_value src = apy_getiter(seq);
    if (!src) return 0;
    return apy_cursor(src, fn, APY_IT_MAP, 0);
}

APY_API apy_value apy_filter(apy_value fn, apy_value seq) {
    apy_value src = apy_getiter(seq);
    if (!src) return 0;
    return apy_cursor(src, fn, APY_IT_FILTER, 0);
}

APY_API apy_value apy_print_seq(apy_value args) {
    apy_print((apy_value)O(args)->v.q.items, O(args)->v.q.n);
    return apy_none();
}

APY_API apy_value apy_dict_of(apy_value args) {
    if (O(args)->v.q.n == 0) return apy_dict_new(1);
    return apy_to_dict(O(args)->v.q.items[0]);
}

APY_API apy_value apy_bytes_of(apy_value args) {
    if (O(args)->v.q.n == 0) return apy_bytes_literal((apy_value)"", 0);
    return apy_to_bytes(O(args)->v.q.items[0]);
}

/* `dict.fromkeys(keys, value)` -- one dict with every key mapped to the SAME
   value. The sharing is the point and the trap: `dict.fromkeys(ks, [])` gives
   every key the same list, and appending through one key is visible through
   all of them. */
APY_API apy_value apy_dict_fromkeys(apy_value keys, apy_value value) {
    int64_t n = apy_raw_len(keys), i;
    apy_value out;
    if (apy_error_occurred()) return 0;
    out = apy_dict_new(n + 1);
    for (i = 0; i < n; i++) {
        apy_value k = apy_key_at(keys, i);
        if (!k || !apy_dict_set(out, k, value)) return 0;
    }
    return out;
}

/* `int.from_bytes(b, byteorder)`. The inverse of `to_bytes`, and unsigned:
   the signed form takes a keyword this does not offer, and guessing at it
   would turn a large positive number negative. */
APY_API apy_value apy_from_bytes_n(apy_value b, apy_value order) {
    int64_t i, n;
    uint64_t acc = 0;
    int big;
    if (O(b)->kind != APY_BYTES_K)
        return apy_fail2("TypeError",
                         "cannot convert '%s' object to bytes%s",
                         apy_kind_name(b), "");
    big = !(O(order)->kind == APY_STR_K
            && strcmp(APY_CSTR(order), "little") == 0);
    n = O(b)->v.s.n;
    if (n > 8) return apy_fail("OverflowError", "int too big to convert");
    for (i = 0; i < n; i++) {
        unsigned char byte = (unsigned char)
            O(b)->v.s.p[big ? i : n - 1 - i];
        acc = (acc << 8) | byte;
    }
    return apy_from_int((int64_t)acc);
}

APY_API apy_value apy_to_dict(apy_value src) {
    int64_t i, n;
    apy_value out;
    /* A COPY, not the same dict. `dict(d)` is a constructor and the result is
       a new object -- which only became visible once `|=` mutated in place,
       and then `c = dict(a); c |= b` changed `a` too. */
    if (O(src)->kind == APY_DICT_K) return apy_copy(src);
    n = apy_raw_len(src);
    if (apy_error_occurred()) return 0;
    out = apy_dict_new(n + 1);
    for (i = 0; i < n; i++) {
        apy_value pair = apy_key_at(src, i);
        if (!pair) return 0;
        /* TWO DIFFERENT ERRORS, and Python distinguishes them: an element
           that is not a sequence at all is a TypeError -- `dict([3, 1])`
           cannot convert an int to a pair -- while one that IS a sequence of
           the wrong length is a ValueError. Reporting ValueError for both
           meant `except TypeError:` did not catch the first. */
        /* A STR IS A SEQUENCE HERE. `dict(['ab', 'cd'])` is `{'a': 'b', 'c':
           'd'}` -- each element is walked as a pair of characters -- so the
           length check applies to it and the TypeError does not. */
        {
            int64_t plen = apy_is_seq(pair) ? O(pair)->v.q.n
                : (O(pair)->kind == APY_STR_K || O(pair)->kind == APY_BYTES_K)
                    ? apy_raw_len(pair) : -1;
            if (plen < 0) {
                char b[128];
                snprintf(b, sizeof b,
                         "cannot convert dictionary update sequence element "
                         "#%d to a sequence", (int)i);
                return apy_fail("TypeError", b);
            }
            if (plen != 2) {
                char b[128];
                snprintf(b, sizeof b,
                         "dictionary update sequence element #%d has length "
                         "%d; 2 is required", (int)i, (int)plen);
                return apy_fail("ValueError", b);
            }
            {
                apy_value k = apy_key_at(pair, 0), v = apy_key_at(pair, 1);
                if (!k || !v) return 0;
                if (!apy_dict_set(out, k, v)) return 0;
            }
        }
    }
    return out;
}

/* `bytes(xs)` -- a sequence of integers in range(256). `bytes(str)` needs an
   encoding argument in Python 3 and is a TypeError without one, which is what
   the non-integer path below reports. */
APY_API apy_value apy_to_bytes(apy_value src) {
    int64_t i, n;
    char *buf;
    if (O(src)->kind == APY_BYTES_K) return src;
    if (O(src)->kind == APY_STR_K)
        return apy_fail("TypeError", "string argument without an encoding");
    n = apy_raw_len(src);
    if (apy_error_occurred()) return 0;
    buf = (char *)malloc((size_t)(n ? n : 1) + 1);
    if (!buf) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    for (i = 0; i < n; i++) {
        int64_t byte;
        apy_value item = apy_key_at(src, i);
        if (!item) { free(buf); return 0; }
        if (!apy_index_arg(item, &byte, APY_IDX_SUB)) { free(buf); return 0; }
        if (byte < 0 || byte > 255) {
            free(buf);
            return apy_fail("ValueError", "bytes must be in range(0, 256)");
        }
        buf[i] = (char)byte;
    }
    buf[n] = 0;
    { apy_value r = apy_str_take(buf, n);
      O(r)->kind = APY_BYTES_K;
      return r; }
}

APY_API apy_value apy_iter(apy_value v) {
    apy_obj *o;
    if (O(v)->kind == APY_ITER_K) return v;
    /* `iter(g)` IS `g`, so a half-consumed generator handed to `iter` keeps
       its position -- which is what makes `for v in g` after two `next`s
       start from the third. */
    if (O(v)->kind == APY_GEN_K) return v;
    if (O(v)->kind == APY_INST_K) {
        /* `iter(obj)` answers what `__iter__` did, unchanged, so that
           `iter(it) is it` holds for a class that returns self -- the identity
           `for v in it` on a half-consumed iterator relies on. */
        apy_value got = apy_unary_dunder(v, "__iter__");
        if (got) return got;
        if (apy_error_occurred()) return 0;
        got = apy_iterable(v);
        if (!got) return 0;
        if (got != v) return apy_iter(got);
    }
    if (!apy_is_seq(v) && !apy_is_set(v) && O(v)->kind != APY_STR_K
        && O(v)->kind != APY_BYTES_K && O(v)->kind != APY_DICT_K)
        return apy_fail2("TypeError", "'%s' object is not iterable%s",
                         apy_kind_name(v), "");
    o = apy_alloc(APY_ITER_K);
    o->v.it.src = v;
    o->v.it.i = 0;
    return V(o);
}

/* WHAT TO WALK. Answers `v` itself for anything this runtime can index, and
   for a user object DRAINS the iterator protocol into a list.

   Draining is eager where CPython is lazy, and that is visible: a `for` over
   an infinite `__next__` never starts rather than never ending, and a
   generator-shaped class's side effects all happen before the first pass.
   Laziness needs a resumable frame, which is the same thing `yield` needs.

   Called once where a loop begins, so the cost is one pass and not one per
   element -- and so a class with `__len__` and `__getitem__` is left alone
   entirely, because the index walk below already IS its protocol. */
APY_API apy_value apy_iterable(apy_value v) {
    apy_value it, out;
    int64_t guard;
    /* A GENERATOR is drained: the walk below is by index and an index walk
       needs a length. See `apy_gen_drain` for what that costs. */
    if (O(v)->kind == APY_GEN_K) return apy_gen_drain(v);
    if (O(v)->kind != APY_INST_K) return v;
    it = apy_unary_dunder(v, "__iter__");
    if (apy_error_occurred()) return 0;
    if (!it) {
        /* No `__iter__`. `__len__` plus `__getitem__` is the older protocol
           and the index walk is already it; `__getitem__` alone is walked
           until it reports IndexError, which is how CPython ends that one. */
        if (apy_unary_dunder(v, "__len__")) return v;
        if (apy_error_occurred()) return 0;
        if (!apy_class_find(O(v)->v.o.cls, apy_name("__getitem__")))
            return apy_fail2("TypeError", "'%s' object is not iterable%s",
                             apy_kind_name(v), "");
        out = apy_seq_new(APY_LIST_K, 8);
        for (guard = 0; guard < 1000000; guard++) {
            apy_value got = apy_getitem(v, apy_from_int(guard));
            if (!got) {
                if (apy_error_matches(apy_lit("IndexError"))) {
                    apy_error_clear();
                    break;
                }
                return 0;
            }
            apy_seq_push(out, got);
        }
        return out;
    }
    if (O(it)->kind != APY_INST_K
        || !apy_class_find(O(it)->v.o.cls, apy_name("__next__")))
        /* `__iter__` handed back something already walkable -- a list, or a
           real iterator. */
        return apy_iterable(it);
    out = apy_seq_new(APY_LIST_K, 8);
    for (guard = 0; guard < 1000000; guard++) {
        apy_value got = apy_unary_dunder(it, "__next__");
        if (!got) {
            if (apy_error_matches(apy_lit("StopIteration"))) {
                apy_error_clear();
                break;
            }
            return 0;
        }
        apy_seq_push(out, got);
    }
    return out;
}

/* `next(it)`, and `next(it, default)` when `has_default`.

   Exhaustion is a StopIteration, which is an ordinary exception here -- so a
   `try: next(it) except StopIteration:` works, and so does a `for` loop that
   never sees one because it counts instead. */
APY_API apy_value apy_next(apy_value it, apy_value fallback,
                           int64_t has_default) {
    /* ONE STEP, through the same protocol `for` uses. Anything with a
       position -- a generator, a cursor, a user iterator -- advances the same
       way, so `next(map(f, xs))` calls `f` once rather than draining.
       Exhaustion is a StopIteration here and a sentinel there, which is the
       only difference between the two spellings. */
    apy_value got;
    if (O(it)->kind != APY_GEN_K && O(it)->kind != APY_ITER_K
        && O(it)->kind != APY_INST_K)
        return apy_fail2("TypeError", "'%s' object is not an iterator%s",
                         apy_kind_name(it), "");
    got = apy_step(it);
    if (!got) return 0;
    if (got == apy_stop()) {
        if (has_default) return fallback;
        return apy_fail("StopIteration", "");
    }
    return got;
}

APY_API apy_value apy_sorted_by(apy_value seq, apy_value keyfn,
                                apy_value reverse) {
    /* `key=None` is "no key", which is what an omitted one lowers to. */
    apy_value fn = O(keyfn)->kind == APY_NONE_K ? 0 : keyfn;
    return apy_sort_with(seq, fn, apy_truth(reverse) != 0);
}

/* `min(xs, key=f)` / `max(xs, key=f)`. The FIRST extreme wins for min and the
   LAST for max, which is CPython's tie-breaking and the reason the two
   comparisons below are not symmetric. */
static apy_value apy_extreme_by(apy_value seq, apy_value keyfn, int want_max) {
    int64_t n = apy_raw_len(seq), i;
    apy_value best = 0, best_key = 0;
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        apy_value item = apy_key_at(seq, i), k;
        if (!item) return 0;
        k = keyfn ? apy_call_n(keyfn, &item, 1) : item;
        if (!k) return 0;
        if (!best) { best = item; best_key = k; continue; }
        {
            int c = apy_order(k, best_key);
            if (c == 2) { apy_binop_error("<", k, best_key); return 0; }
            if (want_max ? c > 0 : c < 0) { best = item; best_key = k; }
        }
    }
    if (!best)
        return apy_fail(want_max ? "ValueError" : "ValueError",
                        want_max ? "max() iterable argument is empty"
                                 : "min() iterable argument is empty");
    return best;
}

APY_API apy_value apy_min_by(apy_value seq, apy_value keyfn) {
    return apy_extreme_by(seq, O(keyfn)->kind == APY_NONE_K ? 0 : keyfn, 0);
}

APY_API apy_value apy_max_by(apy_value seq, apy_value keyfn) {
    return apy_extreme_by(seq, O(keyfn)->kind == APY_NONE_K ? 0 : keyfn, 1);
}

static apy_value apy_extreme(apy_value seq, int want_max) {
    int64_t n = apy_raw_len(seq), i;
    apy_value best;
    if (apy_error_occurred()) return 0;
    if (n == 0)
        return apy_fail(want_max ? "ValueError" : "ValueError",
                        want_max ? "max() iterable argument is empty"
                                 : "min() iterable argument is empty");
    best = apy_key_at(seq, 0);
    for (i = 1; i < n; i++) {
        apy_value item = apy_key_at(seq, i);
        int c = apy_order(item, best);
        if (c == 2) { apy_binop_error("<", item, best); return 0; }
        /* Strict, so that on a tie the EARLIER element wins -- which is what
           CPython does and is observable when the elements are equal but
           distinguishable. */
        if (want_max ? c > 0 : c < 0) best = item;
    }
    return best;
}

APY_API apy_value apy_min(apy_value seq) { return apy_extreme(seq, 0); }
APY_API apy_value apy_max(apy_value seq) { return apy_extreme(seq, 1); }

APY_API apy_value apy_sum(apy_value seq) {
    int64_t n = apy_raw_len(seq), i;
    apy_value total;
    if (apy_error_occurred()) return 0;
    /* Starts at the INT zero, so `sum([])` is 0 and not 0.0, and so that a
       list of ints sums to an int. */
    total = apy_from_int(0);
    for (i = 0; i < n; i++) {
        total = apy_add(total, apy_key_at(seq, i));
        if (!total) return 0;
    }
    return total;
}

/* `sum(xs, start)`. The start is what an empty sequence returns and what sets
   the result's TYPE -- `sum([], 0.0)` is `0.0` and `sum([Vec()], Vec())` is a
   Vec, neither of which an int zero could produce. */
APY_API apy_value apy_sum_from(apy_value seq, apy_value start) {
    int64_t n = apy_raw_len(seq), i;
    apy_value total = start;
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        total = apy_add(total, apy_key_at(seq, i));
        if (!total) return 0;
    }
    return total;
}

/* `min(a, b, ...)` / `max(a, b, ...)` -- the MULTI-ARGUMENT form, which is a
   different function from the one-iterable form and not sugar for it:
   `min([3, 1])` is 1 and `min([3], [1])` is `[1]`. Two arguments already
   distinguish them, so the frontend picks by argument count. */
APY_API apy_value apy_extreme_n(apy_value buf, int64_t n, int64_t want_max) {
    apy_value *argv = (apy_value *)buf;
    apy_value best;
    int64_t i;
    if (n < 1) return apy_fail("TypeError", "min expected at least 1 argument");
    best = argv[0];
    for (i = 1; i < n; i++) {
        int c = apy_order(argv[i], best);
        if (c == 2) { apy_binop_error("<", argv[i], best); return 0; }
        if (want_max ? c > 0 : c < 0) best = argv[i];
    }
    return best;
}

/* `min(xs, default=v)` / `max(xs, default=v)`. Only an EMPTY iterable reaches
   the default; a non-empty one ignores it, which is why this cannot simply be
   "the answer or `v`" applied to the existing entry point -- that one has
   already reported a ValueError by then. */
APY_API apy_value apy_extreme_or(apy_value seq, apy_value keyfn,
                                 apy_value fallback, int64_t want_max) {
    int64_t n = apy_raw_len(seq);
    if (apy_error_occurred()) return 0;
    if (n == 0) return fallback;
    if (O(keyfn)->kind == APY_NONE_K)
        return want_max ? apy_max(seq) : apy_min(seq);
    return want_max ? apy_max_by(seq, keyfn) : apy_min_by(seq, keyfn);
}

APY_API apy_value apy_reversed(apy_value seq) {
    int64_t n = apy_raw_len(seq), i;
    apy_value out;
    if (apy_error_occurred()) return 0;
    out = apy_seq_new(APY_LIST_K, n + 1);
    for (i = n - 1; i >= 0; i--) apy_seq_push(out, apy_key_at(seq, i));
    return out;
}

APY_API apy_value apy_enumerate(apy_value seq, int64_t start) {
    apy_value src = apy_getiter(seq);
    if (!src) return 0;
    return apy_cursor(src, 0, APY_IT_ENUMERATE, start);
}

/* `zip(...)` for any number of iterables, including none -- `zip()` is empty,
   which is not the same as an error, and `zip(xs)` yields 1-tuples.

   `strict=1` makes an uneven zip a ValueError instead of a silent truncation,
   which is the whole point of PEP 618: the lossiness is useful and is also
   the bug, so the caller says which it meant. */
APY_API apy_value apy_zip_n(apy_value buf, int64_t argc, int64_t strict) {
    apy_value *argv = (apy_value *)buf;
    apy_value cursors = apy_seq_new(APY_LIST_K, argc + 1);
    int64_t k;
    for (k = 0; k < argc; k++) {
        apy_value got = apy_getiter(argv[k]);
        if (!got) return 0;
        apy_seq_push(cursors, got);
    }
    return apy_cursor(cursors, apy_from_bool(strict != 0), APY_IT_ZIP, 0);
}

/* `delattr(o, name)` and `del o.name`. Removing an attribute that is not
   there is an AttributeError, not a no-op: the two are different programs and
   only one of them is asking for something that exists. */
APY_API apy_value apy_default_delattr(apy_value obj, apy_value name);

APY_API apy_value apy_delattr(apy_value obj, apy_value name) {
    /* `__delattr__`, the same rule as `__setattr__`. */
    if (O(obj)->kind == APY_INST_K) {
        apy_value hook = apy_class_find(O(obj)->v.o.cls,
                                        apy_name("__delattr__"));
        if (hook) return apy_call_n(apy_bind(hook, obj), &name, 1);
    }
    return apy_default_delattr(obj, name);
}

APY_API apy_value apy_default_delattr(apy_value obj, apy_value name) {
    if (O(obj)->kind != APY_INST_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(obj), APY_CSTR(name));
    if (apy_dict_find(O(obj)->v.o.dict, name) < 0)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute '%s'",
                         apy_kind_name(obj), APY_CSTR(name));
    return apy_delitem(O(obj)->v.o.dict, name);
}

APY_API apy_value apy_zip2(apy_value a, apy_value b) {
    apy_value pair[2];
    pair[0] = a;
    pair[1] = b;
    return apy_zip_n((apy_value)pair, 2, 0);
}

/* `range` as a VALUE, materialised as a list. Python's is a lazy sequence
   with its own type, so `type(range(3)).__name__` and the memory of
   `range(10**9)` both differ -- a stated divergence. A `for` header does NOT
   come here: it lowers to a counter loop with no allocation at all, which is
   the case that matters for cost. */
APY_API apy_value apy_range(int64_t start, int64_t stop, int64_t step) {
    apy_value out;
    int64_t i, n;
    if (step == 0) return apy_fail("ValueError", "range() arg 3 must not be zero");
    n = step > 0 ? (stop - start + step - 1) / step
                 : (start - stop - step - 1) / (-step);
    if (n < 0) n = 0;
    out = apy_seq_new(APY_LIST_K, n + 1);
    for (i = start; step > 0 ? i < stop : i > stop; i += step)
        apy_seq_push(out, apy_from_int(i));
    return out;
}

APY_API apy_value apy_abs(apy_value v) {
    if (O(v)->kind == APY_INST_K) {
        apy_value r = apy_unary_dunder(v, "__abs__");
        if (r || apy_error_occurred()) return r;
    }
    if (O(v)->kind == APY_FLOAT_K) return apy_from_float(fabs(O(v)->v.f));
    if (apy_is_big(v))
        return O(v)->v.big.neg ? apy_neg(v) : v;
    if (apy_is_int_like(v))
        /* `abs(INT64_MIN)` does not fit an int64, so it goes through the
           negation that knows how to promote rather than through `-v`. */
        return O(v)->v.i < 0 ? apy_neg(v) : apy_from_int(O(v)->v.i);
    /* `abs(complex)` is its MODULUS, and a float -- the one kind for which
       abs changes the type rather than the sign. */
    if (O(v)->kind == APY_COMPLEX_K)
        return apy_from_float(sqrt(O(v)->v.z.re * O(v)->v.z.re
                                   + O(v)->v.z.im * O(v)->v.z.im));
    return apy_fail2("TypeError", "bad operand type for abs(): '%s'%s",
                     apy_kind_name(v), "");
}

/* `round` is round-HALF-TO-EVEN, which C's `round` is not: C rounds half away
   from zero, so it answers 3 for round(2.5) where Python answers 2. And
   `round(x)` with no digits returns an INT. */
APY_API apy_value apy_round(apy_value v) {
    double x, down, frac;
    if (apy_is_big(v)) return v;      /* already whole, and already exact */
    if (apy_is_int_like(v)) return apy_from_int(O(v)->v.i);
    if (O(v)->kind != APY_FLOAT_K)
        return apy_fail2("TypeError",
                         "type '%s' doesn't define __round__ method%s",
                         apy_kind_name(v), "");
    x = O(v)->v.f;
    down = floor(x);
    frac = x - down;
    if (frac > 0.5) down += 1.0;
    else if (frac == 0.5 && fmod(down, 2.0) != 0.0) down += 1.0;
    /* `round(1e30)` is an integer with 100 bits, and casting it to int64 is
       undefined rather than merely wrong. */
    if (down >= 9223372036854775808.0 || down < -9223372036854775808.0)
        return apy_big_from_double(down);
    return apy_from_int((int64_t)down);
}

/* `round(x, n)`. A different function from `round(x)` in more than precision:
   the one-argument form returns an INT and this one returns a float, so
   `round(2.5)` is `2` and `round(2.5, 0)` is `2.0`.

   The rounding goes through the C library's decimal conversion and back,
   which is what CPython does (`_Py_dg_dtoa` in mode 3, then `strtod`) and the
   only way to get `round(2.675, 2) == 2.67` right: 2.675 is not 2.675 but
   2.67499999999999982..., so any scale-multiply-round-divide gets 2.68 and
   disagrees with Python on a number every tutorial uses as the example. */
APY_API apy_value apy_round_to(apy_value v, apy_value nd) {
    int64_t n;
    double x, p, y, r;
    char buf[512];
    if (O(nd)->kind == APY_NONE_K) return apy_round(v);
    if (!apy_is_int_like(nd))
        return apy_fail2("TypeError",
                         "'%s' object cannot be interpreted as an integer%s",
                         apy_kind_name(nd), "");
    n = O(nd)->v.i;
    if (apy_is_big(v)) return v;
    if (apy_is_int_like(v)) {
        /* An int stays an int at ANY precision. A negative one rounds to a
           multiple of a power of ten, half to even like everything else. */
        int64_t i = O(v)->v.i, scale = 1, k, half, rem;
        if (n >= 0) return apy_from_int(i);
        for (k = 0; k < -n; k++) {
            if (scale > 922337203685477580LL) return apy_from_int(0);
            scale *= 10;
        }
        rem = i % scale;
        if (rem < 0) rem += scale;
        half = scale / 2;
        i -= rem;
        if (rem > half || (rem == half && ((i / scale) & 1))) i += scale;
        return apy_from_int(i);
    }
    if (O(v)->kind != APY_FLOAT_K)
        return apy_fail2("TypeError",
                         "type '%s' doesn't define __round__ method%s",
                         apy_kind_name(v), "");
    x = O(v)->v.f;
    if (x != x || x - x != 0.0) return apy_from_float(x);   /* nan, inf */
    if (n > 300) return apy_from_float(x);   /* finer than a double is */
    if (n >= 0) {
        snprintf(buf, sizeof buf, "%.*f", (int)n, x);
        return apy_from_float(strtod(buf, NULL));
    }
    if (n < -300) return apy_from_float(x < 0 ? -0.0 : 0.0);
    p = pow(10.0, (double)-n);
    y = x / p;
    r = floor(y);
    {
        double frac = y - r;
        if (frac > 0.5) r += 1.0;
        else if (frac == 0.5 && fmod(r, 2.0) != 0.0) r += 1.0;
    }
    return apy_from_float(r * p);
}

/* `issubclass(a, b)`. Only for user classes and only by the base chain, which
   is all single inheritance can be asked. A non-class first argument is a
   TypeError and not False -- `issubclass(1, int)` raises, where
   `isinstance(1, int)` answers. */
APY_API apy_value apy_is_subclass(apy_value a, apy_value b) {
    if (O(a)->kind != APY_TYPE_K)
        return apy_fail("TypeError", "issubclass() arg 1 must be a class");
    if (O(b)->kind != APY_TYPE_K)
        return apy_fail("TypeError",
                        "issubclass() arg 2 must be a class or tuple of "
                        "classes");
    if (apy_type_is_sub(a, b)) return apy_from_bool(1);
    /* An EXCEPTION type has no base pointer -- the builtin hierarchy is a
       table of names, not a chain of type objects, because `raise` and
       `except` match on the name and never hold a class. So the same question
       is asked again, of that table: `issubclass(KeyError, LookupError)` is
       the hierarchy `except LookupError:` already walks. */
    {
        const char *have = APY_CSTR(O(a)->v.t.name);
        const char *want = APY_CSTR(O(b)->v.t.name);
        while (have) {
            if (strcmp(have, want) == 0) return apy_from_bool(1);
            have = apy_exc_parent(have);
        }
    }
    return apy_from_bool(0);
}

/* A builtin exception NAME used as a value -- `issubclass(KeyError, ...)`,
   `except (A, B)`, `e.__class__`. Interned by `apy_type_of`, so the same name
   is the same object and `type(e) is ValueError` holds. */
APY_API apy_value apy_exc_type(apy_value name) {
    apy_obj *o = apy_alloc(APY_EXC_K);
    o->v.e.name = APY_CSTR(name);
    o->v.e.arg = apy_none();
    o->v.e.has_arg = 0;
    return apy_type_of(V(o));
}

/* `vars(obj)` -- the instance's own attribute dict, which is a VIEW in
   CPython and a copy here. The difference shows only when a program writes
   through the result, which the suite does not; a copy is honest about what
   this runtime can offer, where a silently-detached view would not be. */
APY_API apy_value apy_vars(apy_value obj) {
    /* A CLASS has a `__dict__` too, holding the names its body bound --
       methods and class attributes -- and `"x" in vars(C)` is how a program
       asks whether the class itself defines one. */
    if (O(obj)->kind == APY_TYPE_K) return apy_copy(O(obj)->v.t.dict);
    if (O(obj)->kind != APY_INST_K)
        return apy_fail2("TypeError",
                         "vars() argument must have __dict__ attribute%s%s",
                         "", "");
    return apy_copy(O(obj)->v.o.dict);
}

/* `iter(f, sentinel)` -- the CALLABLE form, which keeps calling `f` until it
   answers `sentinel`. Nothing lazy underneath, so the calls all happen here
   and the result is a cursor over what they returned. A generator would be
   the honest shape and is what `yield` would need. */
APY_API apy_value apy_iter_until(apy_value fn, apy_value sentinel) {
    apy_value out = apy_seq_new(APY_LIST_K, 8);
    int64_t guard;
    for (guard = 0; guard < 1000000; guard++) {
        apy_value v = apy_call_n(fn, NULL, 0);
        if (!v) return 0;
        if (apy_truth(apy_eq(v, sentinel))) break;
        apy_seq_push(out, v);
    }
    return apy_iter(out);
}

/* `isinstance(v, T)` where T is named by a string the frontend supplies. A
   real type object would be better and does not exist yet; the name is enough
   to answer every question the suite asks, including that `True` is an `int`
   -- bool is a SUBCLASS of int in Python, so this is not simply a name
   comparison. */
/* The second argument is EITHER a str naming a built-in kind OR a real type
   object, and the frontend picks per call site: a name it can see is a class
   travels as the class, anything else as its text. Two entry points would be
   tidier and would put the choice in the frontend twice -- once to pick the
   symbol and once to build the argument -- so the parameter carries it.

   Comparing NAMES would have been enough right up until user classes existed,
   and then two classes both called `Node` in one program would be instances
   of each other. */
APY_API apy_value apy_isinstance(apy_value v, apy_value type_name) {
    const char *want;
    const char *have;
    /* A TUPLE OF TYPES means ANY OF THESE, and there is no ambiguity with
       asking about the tuple type itself: `isinstance(x, tuple)` arrives as
       the STRING "tuple", because a builtin kind has no value form. So a
       tuple HERE is always the multi-type form -- including one built at run
       time and held in a variable, which is what makes
       `isinstance(node, self.KINDS)` work. */
    if (O(type_name)->kind == APY_TUPLE_K) {
        int64_t i;
        for (i = 0; i < O(type_name)->v.q.n; i++) {
            apy_value got = apy_isinstance(v, O(type_name)->v.q.items[i]);
            if (!got) return 0;
            if (apy_truth(got)) return apy_from_bool(1);
        }
        return apy_from_bool(0);
    }
    if (O(type_name)->kind == APY_TYPE_K)
        return apy_from_bool(O(v)->kind == APY_INST_K
                             && apy_type_is_sub(O(v)->v.o.cls, type_name));
    if (O(type_name)->kind != APY_STR_K)
        return apy_fail("TypeError", "isinstance() arg 2 must be a type, "
                                     "a tuple of types, or a union");
    want = O(type_name)->v.s.p;
    have = apy_kind_name(v);
    /* An INSTANCE never matches a built-in name. Its `apy_kind_name` is its
       class's name, so without this a class called `int` -- or, far more
       likely, `object` two lines down -- would answer True. */
    if (O(v)->kind == APY_INST_K) return apy_from_bool(strcmp(want, "object") == 0);
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
APY_API apy_value apy_slice(apy_value seq, int64_t start, int64_t stop,
                            int64_t step, int64_t has_start, int64_t has_stop) {
    int64_t n, i;
    apy_value out;
    if (step == 0) return apy_fail("ValueError", "slice step cannot be zero");
    if (O(seq)->kind == APY_STR_K || O(seq)->kind == APY_BYTES_K)
        n = O(seq)->v.s.n;
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

    if (O(seq)->kind == APY_STR_K || O(seq)->kind == APY_BYTES_K) {
        char *buf = (char *)malloc((size_t)(n > 0 ? n : 1) + 1);
        int64_t out_n = 0;
        for (i = start; step > 0 ? i < stop : i > stop; i += step)
            buf[out_n++] = O(seq)->v.s.p[i];
        buf[out_n] = 0;
        { apy_value r = apy_str_take(buf, out_n);
          /* A slice of bytes is bytes. Indexing gives an int and slicing does
             not, which is the one asymmetry a reader will not expect. */
          O(r)->kind = O(seq)->kind;
          return r; }
    }
    out = apy_seq_new(O(seq)->kind, n + 1);
    for (i = start; step > 0 ? i < stop : i > stop; i += step)
        apy_seq_push(out, O(seq)->v.q.items[i]);
    return out;
}

/* --- list, dict and set methods ----------------------------------------- */
/* SEVERAL OF THESE SERVE MORE THAN ONE KIND, and that is forced rather than
   chosen: the frontend's method table is keyed by (method name, argument
   count) alone -- there is no receiver type to key on, because a dynamic value
   does not have one until run time. So `pop`, `remove`, `count` and `index`
   each get ONE symbol, and the dispatch on what the receiver actually is
   happens here. Splitting them into `apy_list_pop` and `apy_set_pop` at the
   ABI would mean the frontend deciding, which it cannot. */
APY_API apy_value apy_list_pop(apy_value seq, apy_value index, int64_t given) {
    int64_t i, n, k;
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
    int64_t i, n;
    /* A str receiver means SUBSTRING search, not element search. Falling
       through to the element loop below would answer correctly for a
       one-character needle and silently wrongly for every other one, because
       `apy_key_at` on a str yields single characters. */
    if (O(seq)->kind == APY_STR_K) return apy_str_index_of(seq, item);
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
    int64_t i, n, hits = 0;
    /* Substring counting for a str, for the same reason `index` splits. */
    if (O(seq)->kind == APY_STR_K) return apy_str_count_in(seq, item, 0, 0);
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
    int64_t i;
    apy_value out;
    if (O(d)->kind != APY_DICT_K)
        return apy_fail2("AttributeError", "'%s' object has no attribute%s",
                         apy_kind_name(d), "");
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
APY_API apy_value apy_str_encode(apy_value s, apy_value encoding) {
    (void)encoding;
    apy_value out;
    if (O(s)->kind != APY_STR_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'encode'%s",
                         apy_kind_name(s), "");
    out = apy_str_copy(O(s)->v.s.p, O(s)->v.s.n);
    O(out)->kind = APY_BYTES_K;
    return out;
}

APY_API apy_value apy_bytes_decode(apy_value b, apy_value encoding) {
    (void)encoding;
    apy_value out;
    if (O(b)->kind != APY_BYTES_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'decode'%s",
                         apy_kind_name(b), "");
    out = apy_str_copy(O(b)->v.s.p, O(b)->v.s.n);
    O(out)->kind = APY_STR_K;
    return out;
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
    if (apy_is_int_like(v) || apy_is_big(v) || O(v)->kind == APY_FLOAT_K)
        return v;
    return apy_fail2("AttributeError",
                     "'%s' object has no attribute 'conjugate'%s",
                     apy_kind_name(v), "");
}

/* --- format specs ------------------------------------------------------- */
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
    int alt, zero, width, precision, has_precision;
} apy_spec;

static int apy_spec_parse(const char *p, int64_t n, apy_spec *out) {
    int64_t i = 0;
    out->fill = ' '; out->align = 0; out->sign = 0; out->type = 0;
    out->group = 0; out->alt = 0; out->zero = 0; out->width = 0;
    out->precision = 0; out->has_precision = 0;
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
            body[0] = (char)iv;
            return apy_spec_pad(body, 1, &sp, 0);
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
        if (!type) {
            /* No type at all: `str(v)`, the shortest round-tripping form, and
               NOT `%g` -- `f"{0.1:>8}"` must still say `0.1`. */
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
                if (flen >= (int64_t)sizeof field) flen = sizeof field - 1;
                memcpy(field, p + start, (size_t)flen);
                field[flen] = 0;
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
                    apy_value key = apy_lit(field);
                    int64_t at = apy_dict_find(kw, key);
                    if (at < 0) {
                        free(out);
                        return apy_fail2("KeyError", "'%s'%s", field, "");
                    }
                    value = O(kw)->v.d.vals[at];
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
            if (conv == 'r' || conv == 'a') value = apy_repr(value);
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

APY_API apy_value apy_str_format(apy_value fmt, apy_value args, apy_value kw) {
    int64_t auto_at = 0;
    int auto_used = 0, explicit_used = 0;
    return apy_format_at(fmt, args, kw, &auto_at, &auto_used, &explicit_used);
}

/* --- math --------------------------------------------------------------- */
/* `import math`. Every one of these is a function of its arguments alone, so
   the module needs no state and each member is an ordinary runtime call --
   which is what lets `import math` be a handful of instructions at the
   statement rather than a compilation unit.

   THE INTEGER-PRESERVING ONES ARE THE POINT. `math.floor(-2.5)` is the INT
   -3, not the float -3.0, and `math.trunc` and `math.ceil` are the same; a
   float result there would print differently and compare differently. The
   ones that are genuinely real-valued (`sqrt`, `log`) answer floats. */

static double apy_math_arg(apy_value v, const char *fn) {
    if (apy_is_num(v)) return apy_num_f(v);
    apy_fail2("TypeError", "must be real number, not %s%s",
              apy_kind_name(v), "");
    return 0.0;
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

/* --- generators --------------------------------------------------------- */
/* `def g(): yield 1` -- a function that SUSPENDS.

   The IR models a function as a block graph with one entry, so there is no
   way to stop in the middle of one and come back. A generator is therefore
   compiled as TWO functions and an object that carries what would otherwise
   be a live frame:

     * the CONSTRUCTOR keeps the name the `def` binds. Calling it allocates a
       generator, stores the arguments into its slots, and returns it having
       run none of the body -- which is why `g()` on a generator with a
       `print` at the top prints nothing.
     * the STEP function is the body, re-entered once per `next`. It begins
       with a dispatch on the saved state and jumps to the block after the
       `yield` that last returned.

   EVERY LOCAL LIVES IN THE OBJECT, not in a register, because a register does
   not survive the return that a `yield` compiles to. That costs a load per
   read and a store per write and it needs no liveness analysis to be correct
   -- and being correct without one is worth more here than the loads.

   `state` is 0 before the first step, k while suspended at yield k, and -1
   once the body has finished. The step sets it; nothing else needs to know
   what the numbers mean. */

APY_API apy_value apy_gen_new(apy_value step, int64_t nslots) {
    apy_obj *o = apy_alloc(APY_GEN_K);
    o->v.g.step = step;
    o->v.g.n = nslots;
    o->v.g.slots = nslots > 0
        ? (apy_value *)calloc((size_t)nslots, sizeof(apy_value)) : NULL;
    o->v.g.state = 0;
    o->v.g.sent = apy_none();
    o->v.g.running = 0;
    return V(o);
}

/* An UNSET slot reads as None rather than as a null. A local a `yield` has
   not reached yet is not an error to read here -- the frontend has already
   refused a genuine use-before-assignment -- and a null would be taken for a
   pending exception by the next operation that touched it. */
APY_API apy_value apy_gen_slot(apy_value g, int64_t i) {
    apy_value v;
    if (i < 0 || i >= O(g)->v.g.n) return apy_none();
    v = O(g)->v.g.slots[i];
    return v ? v : apy_none();
}

APY_API apy_value apy_gen_set(apy_value g, int64_t i, apy_value v) {
    if (i >= 0 && i < O(g)->v.g.n) O(g)->v.g.slots[i] = v;
    return apy_none();
}

APY_API int64_t apy_gen_state(apy_value g) { return O(g)->v.g.state; }

/* A slot holding a RAW machine word rather than a value.

   A `for` inside a generator keeps its index in the frame for the same reason
   the locals are there -- the register does not survive the return a `yield`
   compiles to -- but the index is a count, not an object, and boxing it would
   allocate once per iteration. Read only through these two, never through
   `apy_gen_slot`, which would take the bits for a pointer. */
APY_API int64_t apy_gen_iget(apy_value g, int64_t i) {
    if (i < 0 || i >= O(g)->v.g.n) return 0;
    return (int64_t)O(g)->v.g.slots[i];
}

APY_API apy_value apy_gen_iset(apy_value g, int64_t i, int64_t v) {
    if (i >= 0 && i < O(g)->v.g.n) O(g)->v.g.slots[i] = (apy_value)v;
    return apy_none();
}

/* `return v` inside the body. Held until the step that ran it reports
   exhaustion, which is when it becomes `StopIteration.value`. */
APY_API apy_value apy_gen_result(apy_value g, apy_value v) {
    O(g)->v.g.result = v;
    return apy_none();
}

/* What a delegated generator RETURNED, for `yield from` to answer with.

   Read off the object rather than caught as a StopIteration because the
   delegation drains rather than stepping -- see `_dyn_yield_from` -- so there
   is no exception to catch by the time the loop ends. Anything that is not a
   generator returned nothing, which is None. */
APY_API apy_value apy_gen_taken(apy_value g) {
    if (O(g)->kind != APY_GEN_K || !O(g)->v.g.result) return apy_none();
    return O(g)->v.g.result;
}

APY_API apy_value apy_gen_goto(apy_value g, int64_t k) {
    O(g)->v.g.state = k;
    return apy_none();
}

/* What `send` passed in -- the value a `yield` EXPRESSION evaluates to. None
   for a plain `next`, which is what `next(it)` and `it.send(None)` share. */
APY_API apy_value apy_gen_sent(apy_value g) { return O(g)->v.g.sent; }

/* Is an exception waiting to be raised here? Asked by every resume block, and
   CLEARING, so it fires once. */
APY_API int64_t apy_gen_throwing(apy_value g) {
    return O(g)->v.g.pending != 0;
}

APY_API apy_value apy_gen_pending(apy_value g) {
    apy_value v = O(g)->v.g.pending;
    O(g)->v.g.pending = 0;
    return v ? v : apy_none();
}

/* One step. `sent` is what a `send` supplied, ignored by the body unless it
   reads the yield expression's value.

   A generator ALREADY RUNNING cannot be re-entered: `next(g)` from inside `g`
   is a ValueError, and without the guard it would corrupt the slots it is
   halfway through writing. */
/* The exhaustion signal, carrying whatever `return` gave. A generator that
   returned nothing raises a bare StopIteration, which is not the same as one
   that returned None -- `e.value` is None either way, but only the second had
   a `return` statement, and `has_arg` keeps that distinction for `repr`. */
static apy_value apy_gen_stop(apy_value g) {
    apy_value carried = O(g)->v.g.result;
    if (!carried || O(carried)->kind == APY_NONE_K)
        return apy_fail("StopIteration", "");
    return apy_raise(apy_make_exc(apy_lit("StopIteration"), carried));
}

static apy_value apy_gen_step(apy_value g, apy_value sent, int *done) {
    apy_value out, arg = g;
    *done = 0;
    if (O(g)->kind != APY_GEN_K) {
        apy_fail2("TypeError", "'%s' object is not a generator%s",
                  apy_kind_name(g), "");
        return 0;
    }
    if (O(g)->v.g.running) {
        apy_fail("ValueError", "generator already executing");
        return 0;
    }
    if (O(g)->v.g.state < 0) { *done = 1; return apy_none(); }
    O(g)->v.g.sent = sent;
    O(g)->v.g.running = 1;
    out = apy_invoke(O(g)->v.g.step, &arg, 1);
    O(g)->v.g.running = 0;
    if (!out) { O(g)->v.g.state = -1; return 0; }
    /* The body sets the state to -1 on its way out, so "did this call finish
       the generator" is a question about the state AFTER it, not about the
       value -- a generator may legitimately yield None. */
    if (O(g)->v.g.state < 0) *done = 1;
    return out;
}

APY_API apy_value apy_gen_next(apy_value g, apy_value fallback,
                               int64_t has_default) {
    int done;
    apy_value out = apy_gen_step(g, apy_none(), &done);
    if (!out) return 0;
    if (done) {
        if (has_default) return fallback;
        return apy_gen_stop(g);
    }
    return out;
}

APY_API apy_value apy_gen_send(apy_value g, apy_value v) {
    int done;
    apy_value out;
    if (O(g)->kind == APY_GEN_K && O(g)->v.g.state == 0
        && O(g)->kind == APY_GEN_K && O(v)->kind != APY_NONE_K)
        return apy_fail("TypeError",
                        "can't send non-None value to a just-started "
                        "generator");
    out = apy_gen_step(g, v, &done);
    if (!out) return 0;
    if (done) return apy_gen_stop(g);
    return out;
}

/* `g.close()`. There is no way to resume the body at its `yield` and raise
   there -- that needs the exception to enter a frame this design does not
   keep -- so the generator is simply marked finished, which is what `close`
   leaves behind and what every later `next` on it must see. */
/* `g.throw(exc)` -- raise AT the suspension point.

   The generator is resumed with the exception pending, so a `try` around the
   `yield` inside the body catches it. One that does not lets it out, and the
   generator is finished either way it does not yield again. */
APY_API apy_value apy_gen_throw(apy_value g, apy_value exc) {
    int done;
    apy_value out;
    if (O(g)->kind != APY_GEN_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'throw'%s",
                         apy_kind_name(g), "");
    /* NOT YET STARTED, or already finished: there is no suspension point to
       raise at, so it is raised here. */
    if (O(g)->v.g.state <= 0) {
        O(g)->v.g.state = -1;
        apy_raise(exc);
        return 0;
    }
    O(g)->v.g.pending = exc;
    out = apy_gen_step(g, apy_none(), &done);
    if (!out) return 0;
    if (done) return apy_fail("StopIteration", "");
    return out;
}

/* `g.close()` -- a GeneratorExit at the suspension point, so a `finally` in
   the body runs. The exception is SWALLOWED if it comes back out, which is
   what makes `close` quiet; anything else the body raised instead propagates,
   as CPython's does. */
APY_API apy_value apy_gen_close(apy_value g) {
    int done;
    if (O(g)->kind != APY_GEN_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'close'%s",
                         apy_kind_name(g), "");
    if (O(g)->v.g.state > 0) {
        apy_value exc = apy_make_exc(apy_lit("GeneratorExit"), apy_none());
        O(g)->v.g.pending = exc;
        apy_gen_step(g, apy_none(), &done);
        if (apy_error_occurred()) {
            if (apy_error_matches(apy_lit("GeneratorExit"))) apy_error_clear();
            else { O(g)->v.g.state = -1; return 0; }
        }
    }
    O(g)->v.g.state = -1;
    return apy_none();
}

/* Everything a generator will yield, as a list.

   EAGER, where CPython is lazy, and that is the visible limit of this design:
   a `for` over an infinite generator never starts rather than never ending.
   The lazy path exists -- `next(g)` steps once -- but `for` walks by index
   here, and an index walk needs a length. */
APY_API apy_value apy_gen_drain(apy_value g) {
    apy_value out = apy_seq_new(APY_LIST_K, 8);
    int64_t guard;
    for (guard = 0; guard < 1000000; guard++) {
        int done;
        apy_value v = apy_gen_step(g, apy_none(), &done);
        if (!v) return 0;
        if (done) break;
        apy_seq_push(out, v);
    }
    return out;
}

/* --- the iteration protocol --------------------------------------------- */
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

/* Every cursor is built here, so no site forgets a field. */
static apy_value apy_cursor(apy_value src, apy_value fn, int mode,
                            int64_t start) {
    apy_obj *o = apy_alloc(APY_ITER_K);
    o->v.it.src = src;
    o->v.it.fn = fn;
    o->v.it.mode = mode;
    o->v.it.i = start;
    return V(o);
}

APY_API apy_value apy_getiter(apy_value v) {
    /* A generator IS its own cursor: stepping it resumes it, and that is the
       whole of the lazy path. */
    if (O(v)->kind == APY_GEN_K) return v;
    if (O(v)->kind == APY_ITER_K) return v;
    if (O(v)->kind == APY_INST_K) {
        apy_value got = apy_unary_dunder(v, "__iter__");
        if (apy_error_occurred()) return 0;
        if (got) {
            /* What `__iter__` returned drives the walk. An object with
               `__next__` is stepped directly; anything else is walked as the
               container it must be. */
            if (O(got)->kind == APY_INST_K
                && apy_class_find(O(got)->v.o.cls, apy_name("__next__")))
                return got;
            return apy_getiter(got);
        }
        /* No `__iter__`: `__len__` plus `__getitem__`, or `__getitem__`
           walked until it reports IndexError. A cursor over the object does
           both, since `apy_step` reads through `apy_getitem`. */
        if (!apy_class_find(O(v)->v.o.cls, apy_name("__getitem__")))
            return apy_fail2("TypeError", "'%s' object is not iterable%s",
                             apy_kind_name(v), "");
    } else if (!apy_is_seq(v) && !apy_is_set(v) && O(v)->kind != APY_STR_K
               && O(v)->kind != APY_BYTES_K && O(v)->kind != APY_DICT_K) {
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

/* --- callables, classes and instances ----------------------------------- */
/* Everything below exists to answer one question the rest of this file never
   had to: what is a value that can be CALLED, and what does calling it do.
   Until now a call was a fixed symbol the frontend picked at compile time, so
   `def` produced no value at all and a method was a name in a table here.
   `class` breaks both: a method is looked up on an object at run time, and
   `C(...)` calls something the compiler cannot name.

   THE CALLING CONVENTION, which every backend and the interpreter must agree
   on. A dynamic Python function compiles to an IR function

       f(env, p0, p1, ..., p[arity-1]) -> apy_value

   `env` is THE FUNCTION OBJECT THROUGH WHICH THE CALL WAS MADE, and it is
   first for one reason: a closure has to reach its captured boxes, and the
   only thing that knows which boxes THIS closure got is the closure itself.
   Two `def bump()` values made by two calls to the same enclosing function
   have the same `code` and different `cells`, so passing the code address
   alone would make them indistinguishable -- every closure would share one
   set of variables, which is the classic wrong answer here and one that
   passes every single-instance test.

   A function that captures nothing never reads `env`. That is not a special
   case in the ABI, just an unused parameter, and paying one register for
   uniformity is worth not having two calling conventions that could be
   confused for each other.

   WHY THE INDIRECT CALL LIVES HERE AND NOT IN THE IR. `apy_call` casts
   `code` to a function pointer and calls it. The IR has CALL_PTR and could
   express that directly, but then the arity switch below would be emitted by
   the frontend at every call site, once per possible argument count. One
   switch in C, reached by every backend through an ordinary call, is the same
   dispatch written once. */

APY_API apy_value apy_getattr(apy_value obj, apy_value name);
static apy_value apy_call_n(apy_value f, apy_value *argv, int64_t argc);
static apy_value apy_type_of(apy_value v);

/* Interned dunder names. Building a str value per lookup would allocate on
   every `+` between instances; these are made once and compared by content
   like any other str. */
static apy_value apy_name_cache[48];
static const char *apy_name_text[48];
static int apy_name_count;

static apy_value apy_name(const char *text) {
    int i;
    for (i = 0; i < apy_name_count; i++)
        if (strcmp(apy_name_text[i], text) == 0) return apy_name_cache[i];
    if (apy_name_count >= 48) return apy_lit(text);   /* cache full: still correct */
    apy_name_text[apy_name_count] = text;
    apy_name_cache[apy_name_count] = apy_lit(text);
    return apy_name_cache[apy_name_count++];
}

/* --- cells -------------------------------------------------------------- */
/* A captured variable's box. `functions/closure-cell-is-shared` is the case
   that decides this design: two closures made over one `n` must see each
   other's writes, so what they capture is the BOX and not the value in it.
   Copying the value at capture time passes every test where only one closure
   exists and fails that one. */

APY_API apy_value apy_cell_new(apy_value initial) {
    apy_obj *o = apy_alloc(APY_CELL_K);
    o->v.cell.slot = initial;
    return V(o);
}

APY_API apy_value apy_cell_get(apy_value c) { return O(c)->v.cell.slot; }

APY_API apy_value apy_cell_set(apy_value c, apy_value v) {
    O(c)->v.cell.slot = v;
    return apy_none();
}

/* --- function objects --------------------------------------------------- */

/* `code` is typed `apy_value` and is NOT one: it is the address the IR's
   FUNC_ADDR produced. Both are `uintptr_t`, and taking it as the IR's own
   pointer type is what lets the frontend pass a FUNC_ADDR register straight
   in -- an `int64_t` parameter would be the same bits and a type the backend
   would have to convert to, for no gain. */
APY_API apy_value apy_func_new(apy_value code, int64_t arity, apy_value name,
                               int64_t ncells, int64_t ndefaults,
                               int64_t vararg) {
    apy_obj *o = apy_alloc(APY_FUNC_K);
    o->v.fn.code = (uintptr_t)code;
    o->v.fn.arity = arity;
    o->v.fn.name = name;
    o->v.fn.ncells = ncells;
    o->v.fn.bound = 0;
    o->v.fn.ndefaults = ndefaults;
    o->v.fn.vararg = (int)vararg;
    o->v.fn.cells = ncells > 0
        ? (apy_value *)calloc((size_t)ncells, sizeof(apy_value)) : NULL;
    o->v.fn.defaults = ndefaults > 0
        ? (apy_value *)calloc((size_t)ndefaults, sizeof(apy_value)) : NULL;
    o->v.fn.pnames = NULL;
    o->v.fn.kwarg = 0;
    o->v.fn.kwonly = 0;
    o->v.fn.posonly = 0;
    o->v.fn.doc = 0;
    return V(o);
}

/* How many trailing parameters are keyword-only. Set after the object
   exists for the same reason the names and defaults are: the IR has no
   varargs, so each fact about a signature is its own call. */
APY_API apy_value apy_func_kwonly(apy_value f, int64_t n) {
    O(f)->v.fn.kwonly = (int)n;
    return f;
}

/* The docstring. Set after the object exists, like the names and defaults. */
APY_API apy_value apy_func_doc(apy_value f, apy_value text) {
    O(f)->v.fn.doc = text;
    return f;
}

/* How many leading parameters are positional-only. */
APY_API apy_value apy_func_posonly(apy_value f, int64_t n) {
    O(f)->v.fn.posonly = (int)n;
    return f;
}

/* Mark the function as taking `**kw`. Set after the object exists for the
   same reason the parameter names and defaults are: the IR has no varargs. */
APY_API apy_value apy_func_kwarg(apy_value f, int64_t on) {
    O(f)->v.fn.kwarg = on != 0;
    return f;
}

/* One parameter NAME, at its declared index. Installed after the object
   exists for the same reason the cells and defaults are: the IR has no
   varargs, so each one is its own call. */
APY_API apy_value apy_func_param(apy_value f, int64_t i, apy_value name) {
    if (i < 0 || i >= O(f)->v.fn.arity) return f;
    if (!O(f)->v.fn.pnames)
        O(f)->v.fn.pnames = (apy_value *)calloc(
            (size_t)O(f)->v.fn.arity, sizeof(apy_value));
    if (O(f)->v.fn.pnames) O(f)->v.fn.pnames[i] = name;
    return f;
}

/* One default value, at index `i` among the LAST `ndefaults` parameters.
   Installed after the object exists for the same reason the cells are: the IR
   has no varargs. */
APY_API apy_value apy_func_default(apy_value f, int64_t i, apy_value value) {
    if (i >= 0 && i < O(f)->v.fn.ndefaults) O(f)->v.fn.defaults[i] = value;
    return f;
}

/* Install one captured box. Separate from `apy_func_new` because the IR has
   no varargs, and because a closure that captures itself -- a recursive inner
   `def` -- needs the function object to exist before the box naming it can be
   filled in. */
APY_API apy_value apy_func_cell(apy_value f, int64_t i, apy_value cell) {
    if (i >= 0 && i < O(f)->v.fn.ncells) O(f)->v.fn.cells[i] = cell;
    return f;
}

/* Read a captured box out of the env the callee was handed. */
APY_API apy_value apy_env_cell(apy_value env, int64_t i) {
    if (O(env)->kind != APY_FUNC_K || i < 0 || i >= O(env)->v.fn.ncells)
        return apy_fail("SystemError", "closure environment is not the one "
                                       "this function was compiled for");
    return O(env)->v.fn.cells[i];
}

/* A bound method: the same code and the same boxes, with a receiver attached.
   A FRESH cell every time, which is not waste -- CPython also builds a new
   method object per attribute access, and `datamodel/method-objects-are-
   created-per-access` measures exactly that. */
static apy_value apy_bind(apy_value f, apy_value self) {
    apy_obj *o = apy_alloc(APY_FUNC_K);
    o->v.fn = O(f)->v.fn;      /* cells and defaults are SHARED, not copied */
    o->v.fn.bound = self;
    return V(o);
}

/* --- classes and instances ---------------------------------------------- */

APY_API apy_value apy_type_new(apy_value name, apy_value base) {
    apy_obj *o;
    if (base && O(base)->kind != APY_TYPE_K && O(base)->kind != APY_NONE_K)
        return apy_fail2("TypeError",
                         "bases must be types, not '%s'%s",
                         apy_kind_name(base), "");
    o = apy_alloc(APY_TYPE_K);
    o->v.t.name = name;
    o->v.t.base = (base && O(base)->kind == APY_TYPE_K) ? base : 0;
    o->v.t.dict = apy_dict_new(4);
    return V(o);
}

/* SINGLE INHERITANCE ONLY, and deliberately: every lookup below walks a
   straight chain of `base` pointers. C3 linearisation is what Python actually
   does and it is real work -- a merge over every base's own linearisation,
   with a consistency check that can fail -- and the suite's class cases are
   single-base almost without exception. A second base is REFUSED by the
   frontend rather than silently linearised left-to-right, because a wrong MRO
   surfaces as one method resolving to the wrong body, which is exactly the
   kind of plausible wrong answer that never gets reported. */
APY_API apy_value apy_type_set(apy_value cls, apy_value name, apy_value value) {
    if (O(cls)->kind != APY_TYPE_K)
        return apy_fail2("TypeError", "'%s' object is not a class%s",
                         apy_kind_name(cls), "");
    if (!apy_dict_set(O(cls)->v.t.dict, name, value)) return 0;
    return apy_none();
}

/* Walk the base chain for `name`, without binding. Returns 0 and sets NO
   error when absent -- callers distinguish "not there" from "failed", and an
   attribute miss is only an AttributeError once every class in the chain has
   been asked. */
static apy_value apy_class_find(apy_value cls, apy_value name) {
    while (cls && O(cls)->kind == APY_TYPE_K) {
        int64_t at = apy_dict_find(O(cls)->v.t.dict, name);
        if (at >= 0) return O(cls)->v.t.dict ? O(O(cls)->v.t.dict)->v.d.vals[at] : 0;
        cls = O(cls)->v.t.base;
    }
    return 0;
}

APY_API apy_value apy_instance_new(apy_value cls) {
    apy_obj *o;
    if (O(cls)->kind != APY_TYPE_K)
        return apy_fail2("TypeError", "'%s' object is not callable%s",
                         apy_kind_name(cls), "");
    o = apy_alloc(APY_INST_K);
    o->v.o.cls = cls;
    o->v.o.dict = apy_dict_new(4);
    return V(o);
}

/* --- attributes --------------------------------------------------------- */

static apy_value apy_no_attribute(apy_value obj, apy_value name) {
    return apy_fail2("AttributeError", "'%s' object has no attribute '%s'",
                     apy_kind_name(obj), APY_CSTR(name));
}

/* The DEFAULT attribute lookup: instance dict, then class, then
   `__getattr__`. Named separately from `apy_getattr` because a class that
   overrides `__getattribute__` needs a way to do what it overrode -- and
   `object.__getattribute__(self, name)` is how Python spells that. */
APY_API apy_value apy_default_getattr(apy_value obj, apy_value name);

APY_API apy_value apy_getattr(apy_value obj, apy_value name) {
    /* `__getattribute__` INTERCEPTS EVERYTHING, before the instance dict is
       even looked at -- that is what distinguishes it from `__getattr__`,
       which is consulted only after a miss. Asked here rather than inside the
       default lookup so that the default remains callable from inside the
       override without recursing. */
    if (O(obj)->kind == APY_INST_K) {
        apy_value hook = apy_class_find(O(obj)->v.o.cls,
                                        apy_name("__getattribute__"));
        if (hook) {
            apy_value argv[2];
            argv[0] = obj;
            argv[1] = name;
            return apy_call_n(apy_bind(hook, obj), argv + 1, 1);
        }
    }
    return apy_default_getattr(obj, name);
}

APY_API apy_value apy_default_getattr(apy_value obj, apy_value name) {
    const char *want = APY_CSTR(name);
    switch (O(obj)->kind) {
    case APY_INST_K: {
        /* THE INSTANCE DICT WINS over the class. `self.x = 1` in `__init__`
           shadows a class attribute `x`, which is what makes a class body's
           `count = 0` a shared default that each instance can override.
           (CPython puts data DESCRIPTORS ahead of the instance dict; there
           are no descriptors here, so this order is the whole rule.)

           THIS IS THE DEFAULT LOOKUP -- what `object.__getattribute__` is.
           A class overriding `__getattribute__` is asked first by
           `apy_getattr`, and reaches this by calling the default explicitly,
           which is the only way out of the recursion. */
        int64_t at = apy_dict_find(O(obj)->v.o.dict, name);
        apy_value found;
        if (at >= 0) return O(O(obj)->v.o.dict)->v.d.vals[at];
        found = apy_class_find(O(obj)->v.o.cls, name);
        if (found) {
            /* A function found on the CLASS becomes a bound method; anything
               else -- an int, a str, a list -- is handed back as it is. That
               single test is the whole of the "methods take self" rule. */
            return O(found)->kind == APY_FUNC_K ? apy_bind(found, obj) : found;
        }
        if (strcmp(want, "__class__") == 0) return O(obj)->v.o.cls;
        /* THE INSTANCE'S OWN attributes, and the real dict rather than a copy:
           `obj.__dict__["x"] = 1` is how a program sets an attribute
           dynamically, and a copy would accept the write and lose it. */
        if (strcmp(want, "__dict__") == 0) return O(obj)->v.o.dict;
        /* `__getattr__` -- the LAST resort, asked only after the instance
           dict and the class have both missed. That ordering is the whole
           protocol: `__getattribute__` intercepts everything and this
           intercepts nothing that already resolved, which is why a class can
           define it without shadowing its own attributes. */
        {
            apy_value hook = apy_class_find(O(obj)->v.o.cls,
                                            apy_name("__getattr__"));
            if (hook) return apy_call_n(apy_bind(hook, obj), &name, 1);
        }
        return apy_no_attribute(obj, name);
    }
    case APY_TYPE_K: {
        apy_value found;
        if (strcmp(want, "__name__") == 0) return O(obj)->v.t.name;
        /* `C.__dict__` is what the class body bound, not what it inherited --
           which is the difference `"x" in vars(C)` asks about. A copy, because
           a type's dict is a mapping proxy in CPython and is not writable. */
        if (strcmp(want, "__dict__") == 0) return apy_copy(O(obj)->v.t.dict);
        if (strcmp(want, "__bases__") == 0) {
            apy_value out = apy_tuple_new(2);
            if (O(obj)->v.t.base) apy_seq_push(out, O(obj)->v.t.base);
            return out;
        }
        found = apy_class_find(obj, name);
        /* Reached through the CLASS, a method is not bound: `C.m` is a plain
           function and `C.m(x)` passes x as self. */
        if (found) return found;
        return apy_fail2("AttributeError", "type object '%s' has no "
                         "attribute '%s'", APY_CSTR(O(obj)->v.t.name), want);
    }
    case APY_SUPER_K: {
        /* Lookup starts at the BASE of the class the calling method was
           defined in -- not at the base of `type(self)`. With `B(A)` and
           `C(B)`, a `super().m()` inside B's `m` must find A's, and starting
           from `type(self)` would find B's own and loop forever. */
        apy_value from = O(obj)->v.sup.from;
        apy_value found = apy_class_find(O(from)->v.t.base, name);
        if (!found)
            return apy_fail2("AttributeError", "'super' object has no "
                             "attribute '%s'%s", want, "");
        return O(found)->kind == APY_FUNC_K
            ? apy_bind(found, O(obj)->v.sup.self) : found;
    }
    case APY_FUNC_K:
        if (strcmp(want, "__name__") == 0) return O(obj)->v.fn.name;
        /* No qualified name is recorded -- a nested `def` knows its own name
           and not its enclosing scope's -- so the plain one is what there is.
           Saying so beats an AttributeError on an attribute every function
           has. */
        if (strcmp(want, "__qualname__") == 0) return O(obj)->v.fn.name;
        /* `m.__self__` is the RECEIVER of a bound method, and its absence is
           how a program tells a bound method from a plain function. */
        if (strcmp(want, "__self__") == 0) {
            if (!O(obj)->v.fn.bound) return apy_no_attribute(obj, name);
            return O(obj)->v.fn.bound;
        }
        /* `m.__func__` is the UNDERLYING function of a bound method -- the
           one the class holds, without the receiver. `c.m.__func__ is C.m` is
           the identity that says so, which is why this hands back a value
           sharing the code rather than a fresh binding. */
        if (strcmp(want, "__func__") == 0) {
            apy_value receiver = O(obj)->v.fn.bound;
            apy_value found;
            if (!receiver) return apy_no_attribute(obj, name);
            /* THE OBJECT THE CLASS HOLDS, looked up by the method's own name
               -- not a copy of it. `c.m.__func__ is C.m` is the identity that
               says what `__func__` means, and a fresh binding would answer
               False to it. */
            if (O(receiver)->kind == APY_INST_K) {
                found = apy_class_find(O(receiver)->v.o.cls,
                                       O(obj)->v.fn.name);
                if (found) return found;
            }
            return apy_no_attribute(obj, name);
        }
        /* A DOCSTRING is not recorded -- the frontend drops it as the
           statement it is -- so this is None rather than absent: every
           function has `__doc__`, and one without a docstring has None. */
        if (strcmp(want, "__doc__") == 0)
            return O(obj)->v.fn.doc ? O(obj)->v.fn.doc : apy_none();
        /* Annotations are not carried -- they are erased by analysis, which is
           the whole point of the two-path design -- so this is empty rather
           than absent: `f.__annotations__` exists on every function. */
        if (strcmp(want, "__annotations__") == 0) return apy_dict_new(1);
        return apy_no_attribute(obj, name);
    case APY_COMPLEX_K:
        if (strcmp(want, "real") == 0) return apy_from_float(O(obj)->v.z.re);
        if (strcmp(want, "imag") == 0) return apy_from_float(O(obj)->v.z.im);
        return apy_no_attribute(obj, name);
    case APY_EXC_K:
        /* `e.args` is the one attribute the suite reads off an exception. */
        if (strcmp(want, "args") == 0) {
            apy_value out = apy_tuple_new(1);
            /* The FLAG, not "is the argument None": `E(None).args` is
               `(None,)` and `E().args` is `()`. */
            if (O(obj)->v.e.has_arg) apy_seq_push(out, O(obj)->v.e.arg);
            return out;
        }
        /* CHAINING. Both are None when unset, never absent: `e.__cause__` is
           an attribute every exception has, and code that reads it to decide
           whether to print "The above exception was the direct cause" would
           get an AttributeError instead of the answer. */
        /* `e.value` -- what a generator's `return` gave. Every exception has
           it in CPython (it is None unless something set it), so answering
           None rather than reporting keeps `except StopIteration as e:
           e.value` working for the bare form too. */
        if (strcmp(want, "value") == 0)
            return O(obj)->v.e.has_arg ? O(obj)->v.e.arg : apy_none();
        if (strcmp(want, "__context__") == 0)
            return O(obj)->v.e.context ? O(obj)->v.e.context : apy_none();
        if (strcmp(want, "__cause__") == 0)
            return O(obj)->v.e.cause ? O(obj)->v.e.cause : apy_none();
        if (strcmp(want, "__suppress_context__") == 0)
            return apy_from_bool(O(obj)->v.e.suppress);
        if (strcmp(want, "__notes__") == 0) {
            if (!O(obj)->v.e.notes)
                return apy_no_attribute(obj, name);
            return O(obj)->v.e.notes;
        }
        /* There is no traceback OBJECT -- no frames are recorded -- and None
           is what an exception that was never raised carries. A program that
           tests `e.__traceback__ is not None` sees the difference; one that
           merely reads the attribute does not. */
        /* There is no traceback OBJECT -- no frames are recorded -- but an
           exception that was raised HAS one, and `e.__traceback__ is not
           None` is the test programs actually write. An empty tuple is the
           least dishonest stand-in: it is not None, it is not a frame, and
           iterating it yields the nothing this runtime knows. */
        if (strcmp(want, "__traceback__") == 0) return apy_tuple_new(1);
        if (strcmp(want, "__class__") == 0) return apy_type_of(obj);
        return apy_no_attribute(obj, name);
    default:
        return apy_no_attribute(obj, name);
    }
}

/* `ord`, `chr`, `ascii`, `callable`, `hasattr`, `all`, `any` -- the small
   builtins, together because each is a few lines and separating them would be
   a section header per function.

   `ascii` differs from `repr` only for non-ASCII text, which this runtime does
   not represent yet, so it IS repr here. Saying so beats a second
   implementation that is the same code with a different name. */
APY_API apy_value apy_ord(apy_value v) {
    if (O(v)->kind == APY_BYTES_K) {
        if (O(v)->v.s.n != 1)
            return apy_fail("TypeError",
                            "ord() expected a character, but string of "
                            "length != 1 found");
        return apy_from_int((int64_t)(unsigned char)O(v)->v.s.p[0]);
    }
    if (O(v)->kind != APY_STR_K)
        return apy_fail2("TypeError",
                         "ord() expected string of length 1, but %s found%s",
                         apy_kind_name(v), "");
    /* One CHARACTER, not one byte, and the two stopped coinciding when `chr`
       learned to build a multi-byte one: a str is stored as UTF-8, so the
       length test has to count characters and the answer has to DECODE the
       sequence. Testing bytes made `ord(chr(233))` a TypeError about a string
       of length != 1 -- describing a string the program never wrote. */
    {
        const unsigned char *p = (const unsigned char *)O(v)->v.s.p;
        int64_t n = O(v)->v.s.n, want;
        int64_t code;
        if (n < 1) want = 0;
        else if (p[0] < 0x80) { want = 1; code = p[0]; }
        else if ((p[0] & 0xE0) == 0xC0) { want = 2; code = p[0] & 0x1F; }
        else if ((p[0] & 0xF0) == 0xE0) { want = 3; code = p[0] & 0x0F; }
        else if ((p[0] & 0xF8) == 0xF0) { want = 4; code = p[0] & 0x07; }
        else { want = 1; code = p[0]; }        /* a stray continuation byte */
        if (n != want || want == 0)
            return apy_fail("TypeError",
                            "ord() expected a character, but string of "
                            "length != 1 found");
        {
            int64_t i;
            for (i = 1; i < want; i++) code = (code << 6) | (p[i] & 0x3F);
        }
        return apy_from_int(code);
    }
}

APY_API apy_value apy_chr(apy_value v) {
    int64_t code;
    char buf[5];
    int64_t n = 0;
    if (!apy_is_int_like(v))
        return apy_fail2("TypeError",
                         "an integer is required (got type %s)%s",
                         apy_kind_name(v), "");
    code = O(v)->v.i;
    if (code < 0 || code > 0x10FFFF)
        return apy_fail("ValueError", "chr() arg not in range(0x110000)");
    /* UTF-8, because that is how a str is stored here -- so a code point
       becomes one to four bytes and `len` counts characters by decoding
       them again. Refusing anything above ASCII, which is what this did,
       made `chr(233)` an error on a runtime that handles the byte fine. */
    if (code < 0x80) {
        buf[n++] = (char)code;
    } else if (code < 0x800) {
        buf[n++] = (char)(0xC0 | (code >> 6));
        buf[n++] = (char)(0x80 | (code & 0x3F));
    } else if (code < 0x10000) {
        buf[n++] = (char)(0xE0 | (code >> 12));
        buf[n++] = (char)(0x80 | ((code >> 6) & 0x3F));
        buf[n++] = (char)(0x80 | (code & 0x3F));
    } else {
        buf[n++] = (char)(0xF0 | (code >> 18));
        buf[n++] = (char)(0x80 | ((code >> 12) & 0x3F));
        buf[n++] = (char)(0x80 | ((code >> 6) & 0x3F));
        buf[n++] = (char)(0x80 | (code & 0x3F));
    }
    buf[n] = 0;
    return apy_str_copy(buf, n);
}

APY_API apy_value apy_callable(apy_value v) {
    if (O(v)->kind == APY_FUNC_K || O(v)->kind == APY_TYPE_K)
        return apy_from_bool(1);
    if (O(v)->kind == APY_INST_K)
        return apy_from_bool(apy_class_find(O(v)->v.o.cls,
                                            apy_name("__call__")) != 0);
    return apy_from_bool(0);
}

APY_API apy_value apy_hasattr(apy_value v, apy_value name) {
    apy_value got = apy_getattr(v, name);
    if (got) return apy_from_bool(1);
    /* `hasattr` ANSWERS rather than propagating: a missing attribute is False,
       not the AttributeError the lookup raised. */
    apy_error_clear();
    return apy_from_bool(0);
}

APY_API apy_value apy_all(apy_value v) {
    int64_t i, n = apy_raw_len(v);
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        apy_value item = O(v)->kind == APY_DICT_K
            ? O(v)->v.d.keys[i] : apy_getitem(v, apy_from_int(i));
        if (!item) return 0;
        if (!apy_truth(item)) return apy_from_bool(0);
    }
    return apy_from_bool(1);
}

APY_API apy_value apy_any(apy_value v) {
    int64_t i, n = apy_raw_len(v);
    if (apy_error_occurred()) return 0;
    for (i = 0; i < n; i++) {
        apy_value item = O(v)->kind == APY_DICT_K
            ? O(v)->v.d.keys[i] : apy_getitem(v, apy_from_int(i));
        if (!item) return 0;
        if (apy_truth(item)) return apy_from_bool(1);
    }
    return apy_from_bool(0);
}

/* `object.__repr__(x)` -- the default, which a `__repr__` override needs in
   order to show what it overrode. */
APY_API apy_value apy_default_repr(apy_value v) {
    char buf[96];
    if (O(v)->kind != APY_INST_K) return apy_repr(v);
    snprintf(buf, sizeof buf, "<%s object at 0x%llx>",
             APY_CSTR(O(O(v)->v.o.cls)->v.t.name), (unsigned long long)v);
    /* COPIED. `apy_lit` keeps the pointer it is given, which is right for a
       string literal and catastrophic for a stack buffer -- the str outlives
       this frame and would be reading whatever came next. */
    return apy_str_copy(buf, (int64_t)strlen(buf));
}

/* `object.__eq__(a, b)` is IDENTITY, and `object.__hash__(x)` agrees with it.
   That pairing is the contract: two objects that compare equal must hash
   equally, and the default satisfies it by comparing nothing but address. */
APY_API apy_value apy_default_eq(apy_value a, apy_value b) {
    return apy_from_bool(a == b);
}

/* `object.__init__(self)` -- the default, which does nothing. A subclass
   calling it is saying "the base has no state to set up", and answering None
   is exactly that. */
APY_API apy_value apy_default_init(apy_value v) {
    (void)v;
    return apy_none();
}

APY_API apy_value apy_default_hash(apy_value v) {
    return apy_from_int((int64_t)(v >> 3));
}

APY_API apy_value apy_default_setattr(apy_value obj, apy_value name,
                                      apy_value value);

APY_API apy_value apy_setattr(apy_value obj, apy_value name, apy_value value) {
    /* `__setattr__` INTERCEPTS EVERY assignment, the mirror of
       `__getattribute__`. Asked here rather than inside the default so that
       the default stays callable from within the override -- which is what
       `object.__setattr__(self, name, value)` is for, and the only way an
       override can actually store anything. */
    if (O(obj)->kind == APY_INST_K) {
        apy_value hook = apy_class_find(O(obj)->v.o.cls,
                                        apy_name("__setattr__"));
        if (hook) {
            apy_value argv[2];
            argv[0] = name;
            argv[1] = value;
            return apy_call_n(apy_bind(hook, obj), argv, 2);
        }
    }
    return apy_default_setattr(obj, name, value);
}

APY_API apy_value apy_default_setattr(apy_value obj, apy_value name,
                                      apy_value value) {
    if (O(obj)->kind == APY_INST_K) {
        if (!apy_dict_set(O(obj)->v.o.dict, name, value)) return 0;
        return apy_none();
    }
    if (O(obj)->kind == APY_TYPE_K) return apy_type_set(obj, name, value);
    return apy_fail2("AttributeError",
                     "'%s' object has no attribute '%s'",
                     apy_kind_name(obj), APY_CSTR(name));
}

APY_API apy_value apy_super(apy_value from, apy_value self) {
    apy_obj *o;
    if (O(from)->kind != APY_TYPE_K)
        return apy_fail("TypeError", "super(type, obj): obj must be an "
                                     "instance or subtype of type");
    o = apy_alloc(APY_SUPER_K);
    o->v.sup.from = from;
    o->v.sup.self = self;
    return V(o);
}

/* --- calling ------------------------------------------------------------ */

/* The arity switch. Nine is the ceiling because `env` occupies one of the
   platform's argument registers and eight declared parameters is already far
   past anything the suite writes; a tenth would be another line here and no
   new idea. Every cast is to a function of `apy_value` arguments returning
   one, which is what every dynamic function compiles to. */
typedef apy_value (*apy_fn0)(apy_value);
typedef apy_value (*apy_fn1)(apy_value, apy_value);
typedef apy_value (*apy_fn2)(apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn3)(apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn4)(apy_value, apy_value, apy_value, apy_value,
                             apy_value);
typedef apy_value (*apy_fn5)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value);
typedef apy_value (*apy_fn6)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn7)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn8)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value, apy_value,
                             apy_value);

static apy_value apy_invoke(apy_value f, apy_value *a, int64_t n) {
    uintptr_t c = O(f)->v.fn.code;
    switch (n) {
    case 0: return ((apy_fn0)c)(f);
    case 1: return ((apy_fn1)c)(f, a[0]);
    case 2: return ((apy_fn2)c)(f, a[0], a[1]);
    case 3: return ((apy_fn3)c)(f, a[0], a[1], a[2]);
    case 4: return ((apy_fn4)c)(f, a[0], a[1], a[2], a[3]);
    case 5: return ((apy_fn5)c)(f, a[0], a[1], a[2], a[3], a[4]);
    case 6: return ((apy_fn6)c)(f, a[0], a[1], a[2], a[3], a[4], a[5]);
    case 7: return ((apy_fn7)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6]);
    case 8: return ((apy_fn8)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6],
                                a[7]);
    default:
        return apy_fail("TypeError", "a function of more than 8 parameters "
                                     "is not supported");
    }
}

static apy_value apy_arity_error(apy_value f, int64_t got) {
    char buf[192];
    /* POSITIONS, not declared slots. A keyword-only parameter is declared and
       cannot be filled by position, so counting it here told the caller to
       pass more positional arguments than the function accepts. */
    int64_t want = O(f)->v.fn.arity - (O(f)->v.fn.bound ? 1 : 0)
                   - (O(f)->v.fn.vararg ? 1 : 0) - (O(f)->v.fn.kwarg ? 1 : 0)
                   - O(f)->v.fn.kwonly;
    if (want < 0) want = 0;
    snprintf(buf, sizeof buf,
             "%s() takes %lld positional argument%s but %lld %s given",
             APY_CSTR(O(f)->v.fn.name), (long long)want,
             want == 1 ? "" : "s", (long long)got,
             got == 1 ? "was" : "were");
    return apy_fail("TypeError", buf);
}

/* One call, with the `**kw` dict the caller resolved (or 0 for none).

   Threaded as a parameter rather than appended to `argv` by the caller,
   because a callee with BOTH `*rest` and `**kw` packs the surplus positionals
   into `rest` FIRST -- a kw dict sitting in `argv` would be swallowed by that
   packing and arrive as one more element of `rest`. Only this function knows
   where the boundary is, so only it can put the dict past it. */
static apy_value apy_call_nk(apy_value f, apy_value *argv, int64_t argc,
                             apy_value kwrest, int bound) {
    apy_value slots[9];
    int64_t i, n = 0;

    if (O(f)->kind == APY_TYPE_K) {
        /* `C(...)` -- allocate, then run `__init__` if there is one. The
           instance is what the call yields whatever `__init__` returns, which
           is why its result is discarded rather than propagated. */
        apy_value self = apy_instance_new(f);
        apy_value init;
        if (!self) return 0;
        init = apy_class_find(f, apy_name("__init__"));
        if (init) {
            apy_value bound_init = apy_bind(init, self);
            if (!apy_call_nk(bound_init, argv, argc, kwrest, bound)) return 0;
        } else if (argc != 0) {
            char buf[128];
            snprintf(buf, sizeof buf,
                     "%s() takes no arguments", APY_CSTR(O(f)->v.t.name));
            return apy_fail("TypeError", buf);
        }
        return self;
    }
    if (O(f)->kind == APY_INST_K) {
        /* A callable instance: `x(...)` is `type(x).__call__(x, ...)`. */
        apy_value m = apy_class_find(O(f)->v.o.cls, apy_name("__call__"));
        if (!m)
            return apy_fail2("TypeError", "'%s' object is not callable%s",
                             apy_kind_name(f), "");
        return apy_call_nk(apy_bind(m, f), argv, argc, kwrest, bound);
    }
    if (O(f)->kind != APY_FUNC_K)
        return apy_fail2("TypeError", "'%s' object is not callable%s",
                         apy_kind_name(f), "");

    if (O(f)->v.fn.bound) slots[n++] = O(f)->v.fn.bound;
    {   /* `*rest` collects everything past the declared parameters, and it
           occupies one argument slot of its own after them. Packed here
           rather than by the caller, which does not know the callee has
           one. */
        int64_t declared = O(f)->v.fn.arity - (O(f)->v.fn.vararg ? 1 : 0)
                                             - (O(f)->v.fn.kwarg ? 1 : 0);
        /* WHERE POSITIONS STOP. A keyword-only parameter is declared but not
           reachable by position, so a surplus argument belongs to `*rest` --
           or is an error -- rather than landing in it. */
        /* `bound` means the caller ALREADY matched names to slots, so every
           argument here belongs where it is. Re-applying the keyword-only
           limit would truncate a list `apy_call_kw` had just completed and
           then refill the tail from defaults, silently discarding the values
           the keywords supplied. */
        int64_t byslot = bound ? declared : declared - O(f)->v.fn.kwonly;
        int64_t take = argc;
        if (n + argc > byslot) take = byslot - n;
        if (take < 0) take = 0;
        for (i = 0; i < take && n < 9; i++) slots[n++] = argv[i];
        /* A missing trailing argument comes from the default the `def`
           evaluated, which lives in the function object -- see the comment on
           `fn` in `struct apy_obj`. */
        while (n < declared) {
            int64_t d = n - (declared - O(f)->v.fn.ndefaults);
            if (d < 0 || d >= O(f)->v.fn.ndefaults) break;
            slots[n++] = O(f)->v.fn.defaults[d];
        }
        if (O(f)->v.fn.vararg) {
            apy_value rest = apy_tuple_new(argc - take + 1);
            for (i = take; i < argc; i++) apy_seq_push(rest, argv[i]);
            if (n < 9) slots[n++] = rest;
        }
        /* `**kw` is the LAST parameter and is passed even when empty: `def
           f(**kw)` called as `f()` binds `{}`, not nothing. */
        if (O(f)->v.fn.kwarg && n < 9)
            slots[n++] = kwrest ? kwrest : apy_dict_new(1);
    }
    if (n != O(f)->v.fn.arity) return apy_arity_error(f, argc);
    return apy_invoke(f, slots, n);
}

static apy_value apy_call_n(apy_value f, apy_value *argv, int64_t argc) {
    return apy_call_nk(f, argv, argc, 0, 0);
}

/* The frontend's entry point: `argv` is the ADDRESS of an array of values in
   a stack slot, the same shape `apy_print` takes and for the same reason --
   the IR has no varargs. */
APY_API apy_value apy_call(apy_value f, apy_value argv, int64_t argc) {
    return apy_call_n(f, (apy_value *)argv, argc);
}

/* The FUNC_K object a call will actually ENTER, and how many of its declared
   parameters the caller does not supply -- 1 for the `self` of a method or of
   a class's `__init__`, 0 otherwise. Zero when there is nothing to enter.

   Keyword resolution needs this and a plain call does not: `C(n=1)` names a
   parameter of `C.__init__`, so the names have to be read off that function
   and matched against positions shifted past `self`. */
static apy_value apy_call_target(apy_value f, int64_t *skip) {
    *skip = 0;
    if (O(f)->kind == APY_TYPE_K) {
        apy_value init = apy_class_find(f, apy_name("__init__"));
        if (!init || O(init)->kind != APY_FUNC_K) return 0;
        *skip = 1;
        return init;
    }
    if (O(f)->kind == APY_INST_K) {
        apy_value m = apy_class_find(O(f)->v.o.cls, apy_name("__call__"));
        if (!m || O(m)->kind != APY_FUNC_K) return 0;
        *skip = 1;
        return m;
    }
    if (O(f)->kind != APY_FUNC_K) return 0;
    if (O(f)->v.fn.bound) *skip = 1;
    return f;
}

/* `f(a, b, k=v, **d)`. The positional arguments are in `buf`; the keyword
   ones are a DICT, built at the call site, because `**d` merges a dict whose
   keys are not known until it exists -- a compile-time list of names could
   not express that and a second entry point for it would duplicate all of the
   resolution below.

   The keywords are placed into their PARAMETER POSITIONS here and a complete
   argument list is handed to `apy_call_n`, so everything downstream -- the
   arity check, `*rest`, a bound receiver -- stays the one implementation it
   already was. Defaults are filled here too, because a keyword can leave a
   HOLE in the middle (`f(1, c=3)` against `def f(a, b=2, c=3)`) and
   `apy_call_n` only knows how to fill a missing TAIL. */
APY_API apy_value apy_call_kw(apy_value f, apy_value buf, int64_t argc,
                              apy_value kwd) {
    apy_value *raw = (apy_value *)buf;
    apy_value slots[9], rest = 0;
    char filled[9];
    int64_t skip = 0, declared, want, bypos, i, k, kwn;
    apy_value target = apy_call_target(f, &skip);

    if (!target)
        /* No signature to match against: a class with no `__init__`, or a
           value that is not callable at all. `apy_call_n` already words both
           of those, so let it. */
        return apy_call_n(f, raw, argc);
    kwn = apy_raw_len(kwd);
    declared = O(target)->v.fn.arity - (O(target)->v.fn.vararg ? 1 : 0)
                                     - (O(target)->v.fn.kwarg ? 1 : 0);
    want = declared - skip;
    if (want < 0) want = 0;
    if (want > 9) want = 9;
    /* Positions reach only as far as the keyword-only tail; names reach all
       of it, which is why `want` stays whole and only `bypos` shrinks. */
    bypos = want - O(target)->v.fn.kwonly;
    if (bypos < 0) bypos = 0;
    /* Extra positionals with a `*rest` never leave a hole, so they can go
       straight through -- and a keyword alongside them would name a parameter
       already filled, which the loop below reports. */
    for (i = 0; i < want; i++) filled[i] = 0;
    for (i = 0; i < argc && i < bypos; i++) { slots[i] = raw[i]; filled[i] = 1; }
    if (O(target)->v.fn.kwarg) rest = apy_dict_new(kwn + 1);

    for (k = 0; k < kwn; k++) {
        apy_value nm = O(kwd)->v.d.keys[k], val = O(kwd)->v.d.vals[k];
        int64_t at = -1;
        int posonly_hit = 0;
        if (O(target)->v.fn.pnames)
            for (i = 0; i < want; i++) {
                apy_value p = O(target)->v.fn.pnames[i + skip];
                if (!p || strcmp(APY_CSTR(p), APY_CSTR(nm)) != 0) continue;
                /* POSITIONAL-ONLY: the name is recorded so this message can
                   be specific, but it does not match. */
                if (i + skip < O(target)->v.fn.posonly) { posonly_hit = 1; break; }
                at = i;
                break;
            }
        if (at >= 0 && argc <= bypos && !filled[at]) {
            slots[at] = val;
            filled[at] = 1;
            continue;
        }
        if (at >= 0 && argc <= bypos) {
            char b[160];
            snprintf(b, sizeof b, "%s() got multiple values for argument '%s'",
                     APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            return apy_fail("TypeError", b);
        }
        /* Not a declared parameter. `**kw` collects it; without one it is the
           error CPython reports, naming the keyword rather than a count. */
        if (rest) { apy_dict_set(rest, nm, val); continue; }
        {
            char b[192];
            /* NAMED SPECIFICALLY when the parameter exists but is
               positional-only. "unexpected keyword" would send the reader
               looking for a typo in a name that is right there in the
               signature; the mistake is the spelling of the CALL, not of the
               name. */
            if (posonly_hit)
                snprintf(b, sizeof b,
                         "%s() got some positional-only arguments passed as "
                         "keyword arguments: '%s'",
                         APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            else
                snprintf(b, sizeof b,
                         "%s() got an unexpected keyword argument '%s'",
                         APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            return apy_fail("TypeError", b);
        }
    }

    for (i = 0; i < want; i++) {
        int64_t d;
        if (filled[i]) continue;
        d = (i + skip) - (declared - O(target)->v.fn.ndefaults);
        if (d < 0 || d >= O(target)->v.fn.ndefaults) {
            char b[160];
            const char *pn = (O(target)->v.fn.pnames
                              && O(target)->v.fn.pnames[i + skip])
                ? APY_CSTR(O(target)->v.fn.pnames[i + skip]) : "?";
            snprintf(b, sizeof b,
                     "%s() missing 1 required positional argument: '%s'",
                     APY_CSTR(O(target)->v.fn.name), pn);
            return apy_fail("TypeError", b);
        }
        slots[i] = O(target)->v.fn.defaults[d];
    }
    if (argc > bypos) {
        /* `*rest` swallows the surplus; copy them back after the named ones,
           which are now complete. */
        for (i = bypos; i < argc && i < 9; i++) slots[i] = raw[i];
        want = argc < 9 ? argc : 9;
    }
    return apy_call_nk(f, slots, want, rest, 1);
}

/* `f(*xs)`, where the argument COUNT is a value rather than a constant.

   An ordinary call knows its arity at compile time and passes a stack array.
   A starred one cannot: `xs` decides how many arguments there are, and the
   frontend has no number to emit. So the arguments arrive as a list and are
   copied into an argv here, which is the one place that count exists.

   `apy_call_n` then binds them against the callee's own signature -- defaults,
   `*rest`, arity mismatch and all -- so a spread call reports a wrong count
   exactly as a direct one does, at run time rather than at compile time. */
APY_API apy_value apy_call_spread(apy_value f, apy_value args) {
    int64_t n = O(args)->v.q.n;
    apy_value *argv = (apy_value *)malloc(sizeof(apy_value) * (size_t)(n ? n : 1));
    apy_value r;
    if (!argv) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    memcpy(argv, O(args)->v.q.items, sizeof(apy_value) * (size_t)n);
    r = apy_call_n(f, argv, n);
    free(argv);
    return r;
}

/* `xs.extend(other)` in all but name: every element of a sequence, appended.
   Used to flatten a starred argument into the list a spread call builds. */
APY_API apy_value apy_extend(apy_value seq, apy_value other) {
    int64_t i;
    if (O(other)->kind == APY_STR_K || O(other)->kind == APY_BYTES_K
        || O(other)->kind == APY_DICT_K) {
        int64_t n = apy_raw_len(other);
        for (i = 0; i < n; i++) {
            apy_value item = O(other)->kind == APY_DICT_K
                ? O(other)->v.d.keys[i]
                : apy_getitem(other, apy_from_int(i));
            if (!item) return 0;
            apy_seq_push(seq, item);
        }
        return apy_none();
    }
    if (!apy_is_seq(other) && !apy_is_set(other))
        return apy_fail2("TypeError", "'%s' object is not iterable%s",
                         apy_kind_name(other), "");
    for (i = 0; i < O(other)->v.q.n; i++)
        apy_seq_push(seq, O(other)->v.q.items[i]);
    return apy_none();
}

/* Is this a user object? The frontend branches on it before a built-in method
   whose NAME some class in the same program also defines -- `x.add(1)` is a
   set's `add` or the program's, and only the receiver knows which.

   A predicate rather than a check inside each method entry point, because the
   frontend already knows which names collide: the branch is emitted only
   where one actually might, so `xs.append(v)` in a program with no `append`
   method stays a single call and pays nothing. */
APY_API int64_t apy_is_instance(apy_value v) {
    return O(v)->kind == APY_INST_K;
}

/* --- type objects ------------------------------------------------------- */
/* `type(x)` has to be a VALUE now, not the string `apy_type_name` returns:
   `isinstance(p, Point)` names a class, and comparing its name to a string
   would make two different classes with the same name interchangeable.

   Built-in types are INTERNED by name, so `type(1) is type(2)` is True the
   way it is in CPython. Interning by name rather than by kind is what keeps
   each exception type a single object -- every `APY_EXC_K` cell shares one
   kind but names one of thirty types. */
static apy_value apy_type_names[64];
static const char *apy_type_keys[64];
static int apy_type_count;

static apy_value apy_type_of(apy_value v) {
    const char *key;
    int i;
    if (O(v)->kind == APY_INST_K) return O(v)->v.o.cls;
    key = O(v)->kind == APY_TYPE_K ? "type" : apy_kind_name(v);
    for (i = 0; i < apy_type_count; i++)
        if (strcmp(apy_type_keys[i], key) == 0) return apy_type_names[i];
    if (apy_type_count >= 64) return apy_type_new(apy_lit(key), 0);
    apy_type_keys[apy_type_count] = key;
    apy_type_names[apy_type_count] = apy_type_new(apy_lit(key), 0);
    return apy_type_names[apy_type_count++];
}

APY_API apy_value apy_type_object(apy_value v) { return apy_type_of(v); }

/* `with` -- the two halves of the context-manager protocol.

   Separate entry points rather than one `apy_method1` at each call site,
   because the error text is specific: a value with neither method is
   reported as not being a context manager, naming the one it lacks, which is
   what CPython says and what tells the reader which half to write. */
APY_API apy_value apy_enter(apy_value cm) {
    apy_value m = apy_dunder(cm, "__enter__");
    if (!m)
        return apy_fail2("TypeError",
                         "'%s' object does not support the context manager "
                         "protocol%s", apy_kind_name(cm), "");
    return apy_call_n(m, NULL, 0);
}

/* `__exit__(type, value, traceback)`. `exc` is the live exception or None.

   All three arguments come from the one value: the TYPE is what
   `et.__name__` reads, the VALUE is the exception itself, and the traceback
   is None because there are none here. Passing None for the type when there
   is an exception would make `et.__name__` fail in a manager that logs it. */
APY_API apy_value apy_exit(apy_value cm, apy_value exc) {
    apy_value argv[3];
    apy_value m = apy_dunder(cm, "__exit__");
    if (!m)
        return apy_fail2("TypeError",
                         "'%s' object does not support the context manager "
                         "protocol%s", apy_kind_name(cm), "");
    if (O(exc)->kind == APY_EXC_K) {
        argv[0] = apy_type_of(exc);
        argv[1] = exc;
    } else {
        argv[0] = apy_none();
        argv[1] = apy_none();
    }
    argv[2] = apy_none();
    return apy_call_n(m, argv, 3);
}


/* Is `cls` reachable from `of` by base pointers? The `isinstance` rule for
   user classes, and the only thing single inheritance makes cheap. */
static int apy_type_is_sub(apy_value of, apy_value cls) {
    while (of && O(of)->kind == APY_TYPE_K) {
        if (of == cls) return 1;
        of = O(of)->v.t.base;
    }
    return 0;
}

/* --- operator dispatch to user methods ---------------------------------- */
/* `apy_str`, `apy_eq`, `apy_add` and the rest were written against a closed
   set of kinds. An instance is not one of them, and the whole point of
   `class` is that the answer comes from the program rather than from here.

   THE PROTOCOL FOR RETURNING. These helpers answer 0 both for "the class
   defines no such method" and for "it does, and it failed". The two are told
   apart by the ERROR FLAG, which is exactly how every other fallible
   operation in this file already reports, so a caller reads

       r = apy_binary_dunder(...);
       if (r || apy_error_occurred()) return r;

   and otherwise falls through to the TypeError it would have raised anyway.
   A separate out-parameter would be one more thing for a call site to get
   wrong, and there are twenty call sites. */

static apy_value apy_dunder(apy_value v, const char *name) {
    apy_value m;
    if (O(v)->kind != APY_INST_K) return 0;
    m = apy_class_find(O(v)->v.o.cls, apy_name(name));
    return (m && O(m)->kind == APY_FUNC_K) ? apy_bind(m, v) : 0;
}


static apy_value apy_unary_dunder(apy_value v, const char *name) {
    apy_value m = apy_dunder(v, name);
    return m ? apy_call_n(m, NULL, 0) : 0;
}

static apy_value apy_method1(apy_value v, const char *name, apy_value arg) {
    apy_value m = apy_dunder(v, name);
    return m ? apy_call_n(m, &arg, 1) : 0;
}

/* `a + b` asks `a.__add__(b)` first and `b.__radd__(a)` second. The reflected
   form is why `1 + v` can reach a user class at all: the int on the left has
   no idea what `v` is, so the right operand gets the second word. */
static apy_value apy_binary_dunder(apy_value a, apy_value b,
                                   const char *name, const char *rname) {
    apy_value r = apy_method1(a, name, b);
    if (r || apy_error_occurred()) return r;
    return apy_method1(b, rname, a);
}

/* True when either operand is an instance, which is the guard every operator
   below uses before paying for a lookup. */
static int apy_either_inst(apy_value a, apy_value b) {
    return O(a)->kind == APY_INST_K || O(b)->kind == APY_INST_K;
}

"""


#: Every symbol the IR may call. The frontend declares these as imports and
#: the link stage decides whether to pull the runtime in by looking at what is
#: declared, so a name missing here is a link error rather than a wrong answer.
OBJECT_NAMES = (
    #: Callables, classes and instances. `apy_call` is the only one of these
    #: that a program reaches for an ordinary call -- the rest build the values
    #: it dispatches on.
    "apy_cell_new", "apy_cell_get", "apy_cell_set",
    "apy_func_new", "apy_func_cell", "apy_func_default", "apy_env_cell",
    "apy_call",
    "apy_type_new", "apy_type_set", "apy_instance_new",
    "apy_getattr", "apy_setattr", "apy_super", "apy_type_object",
    "apy_is_instance", "apy_exc_register",
    "apy_range", "apy_sorted", "apy_min", "apy_max", "apy_sum", "apy_reversed", "apy_enumerate", "apy_zip2", "apy_abs", "apy_round", "apy_isinstance", "apy_slice", "apy_list_pop", "apy_index_of", "apy_count_of", "apy_list_remove", "apy_dict_parts", "apy_dict_get_or",
    "apy_list_new", "apy_tuple_new", "apy_seq_push", "apy_getitem",
    "apy_dict_new", "apy_dict_set", "apy_key_at",
    "apy_setitem", "apy_raw_len",
    "apy_none", "apy_from_bool", "apy_from_int", "apy_from_float",
    "apy_from_cstr", "apy_from_bytes", "apy_as_int", "apy_as_float", "apy_as_bool",
    "apy_type_name", "apy_truth", "apy_len", "apy_repr", "apy_str",
    "apy_print",
    "apy_add", "apy_sub", "apy_mul", "apy_truediv", "apy_floordiv",
    "apy_mod", "apy_pow", "apy_neg", "apy_pos", "apy_invert",
    "apy_bitand", "apy_bitor", "apy_bitxor", "apy_lshift", "apy_rshift",
    "apy_eq", "apy_ne", "apy_is", "apy_contains", "apy_lt", "apy_le", "apy_gt", "apy_ge",
    "apy_to_int", "apy_to_float", "apy_to_bool",
    "apy_error_occurred", "apy_error_type", "apy_error_message",
    "apy_error_clear", "apy_fatal_if_error",
    "apy_make_exc", "apy_raise", "apy_error_matches", "apy_error_value",
    # set and frozenset
    "apy_set_new", "apy_frozenset_new", "apy_set_push",
    "apy_to_set", "apy_to_frozenset",
    "apy_set_add", "apy_set_discard",
    "apy_set_union", "apy_set_intersection", "apy_set_difference",
    "apy_set_symdiff",
    "apy_set_issubset", "apy_set_issuperset", "apy_set_isdisjoint",
    "apy_update", "apy_clear", "apy_copy", "apy_hash",
    # str methods
    "apy_str_upper", "apy_str_lower", "apy_str_title", "apy_str_capitalize",
    "apy_str_swapcase", "apy_str_casefold",
    "apy_str_isalpha", "apy_str_isdigit", "apy_str_isdecimal",
    "apy_str_isnumeric", "apy_str_isalnum", "apy_str_isspace",
    "apy_str_islower", "apy_str_isupper", "apy_str_istitle",
    "apy_str_isprintable", "apy_str_isidentifier", "apy_str_isascii",
    "apy_str_strip", "apy_str_lstrip", "apy_str_rstrip",
    "apy_str_strip_chars", "apy_str_lstrip_chars", "apy_str_rstrip_chars",
    "apy_str_removeprefix", "apy_str_removesuffix",
    "apy_str_split_ws", "apy_str_split", "apy_str_split_n",
    "apy_str_rsplit_ws", "apy_str_rsplit", "apy_str_rsplit_n",
    "apy_str_splitlines", "apy_str_splitlines_keep",
    "apy_str_partition", "apy_str_rpartition", "apy_str_join",
    "apy_str_replace", "apy_str_replace_n",
    "apy_str_startswith", "apy_str_startswith2", "apy_str_startswith3",
    "apy_str_endswith", "apy_str_endswith2", "apy_str_endswith3",
    "apy_str_find", "apy_str_find2", "apy_str_find3",
    "apy_str_rfind", "apy_str_rfind2", "apy_str_rfind3", "apy_str_rindex",
    "apy_str_count2", "apy_str_count3",
    "apy_str_ljust", "apy_str_ljust_fill", "apy_str_rjust",
    "apy_str_rjust_fill", "apy_str_center", "apy_str_center_fill",
    "apy_str_zfill",
    # arbitrary precision integers
    "apy_pow3", "apy_bit_length", "apy_bit_count",
    "apy_bin", "apy_oct", "apy_hex", "apy_to_int_base", "apy_divmod",
)


#: C type -> the IR type a value of it travels in. `apy_value` is the IR's
#: `ptr`, which is what makes the object runtime's arguments and results
#: opaque to every backend.
_IR_TYPES = {"apy_value": "ptr", "int64_t": "i64", "double": "f64",
             "void": "void"}


def signatures() -> dict:
    """Every `APY_API` symbol, as (argument type names, result type name).

    READ OUT OF THE C rather than listed beside it. The frontend has to declare
    each of these as an import with the right signature, and a hand-kept list
    drifted three times in one afternoon -- each time the same way: a symbol
    added here, not declared there, and the failure arriving as `call to
    unknown function` from the IR verifier or as a link error, neither of which
    names the list that was not updated.

    The C is generated from a string in this module, so its shape is known: one
    `APY_API` per definition, arguments comma-separated, no function pointers
    and no structs by value.
    """
    import re
    out = {}
    for m in re.finditer(r"APY_API\s+([\w ]+?)\s+(apy_\w+)\(([^)]*)\)\s*\{",
                         OBJECTS_C):
        ret, name, raw = m.group(1).strip(), m.group(2), m.group(3).strip()
        if name in out:
            continue
        args = [] if raw in ("void", "") else [
            a.strip().rsplit(" ", 1)[0].strip() for a in raw.split(",")]
        try:
            out[name] = ([_IR_TYPES[a] for a in args], _IR_TYPES[ret])
        except KeyError as exc:
            raise AssertionError(
                f"{name}: no IR type for {exc.args[0]!r}. Add it to _IR_TYPES "
                f"-- a runtime symbol the frontend cannot describe is one it "
                f"cannot call.") from None
    return out


def objects_c(*, static: bool = False) -> str:
    """The object runtime's C source, with its storage class chosen.

    `static` for the C backend, whose output is one self-contained translation
    unit; external for the linked runtime, where the IR's calls have to resolve
    across object files.
    """
    return OBJECTS_C.replace(_API_TOKEN, "static" if static else "")
