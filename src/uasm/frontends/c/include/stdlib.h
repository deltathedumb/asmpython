/* <stdlib.h> -- uasm C frontend.

   THE ALLOCATOR IS IN `__uasm_alloc.h`, because `<string.h>` needs it for
   `strdup` and this header needs `<string.h>`; that file says how the arena
   works and why it is one.

   `<stdio.h>` COMES WITH THIS HEADER, which is more than C asks for and is
   what `strfromd` costs: C23 puts it here and it is `snprintf` with one
   conversion in it, so the formatter has to be in scope. Nothing is paid for
   at run time -- `lower.prune` drops every definition the program does not
   reach -- and `<stdio.h>` does not include this file, so there is no
   circle. */
#ifndef _UASM_STDLIB_H
#define _UASM_STDLIB_H

#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <__uasm_alloc.h>
#include <__uasm_wide.h>
#include <errno.h>
#include <__uasm_num.h>
#include <__uasm_base.h>
#include <__uasm_host.h>

#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1
#define RAND_MAX 2147483647
/* FOUR, BECAUSE THE ENCODING IS UTF-8: `<wchar.h>`'s `mbrtowc` decodes it
   and `wcrtomb` writes it, so a character is one to four bytes and a caller
   sizing a buffer by this macro needs to be told so. `MB_LEN_MAX` in
   `<limits.h>` is the same 4. */
#define MB_CUR_MAX 4

typedef struct { int quot; int rem; } div_t;
typedef struct { long quot; long rem; } ldiv_t;
typedef struct { long long quot; long long rem; } lldiv_t;

/* ── ending the program ───────────────────────────────────────────────── */
static void (*__atexit_fns[32])(void);
static int __atexit_count;

static int atexit(void (*__f)(void))
{
    if (__atexit_count >= 32) return -1;
    __atexit_fns[__atexit_count++] = __f;
    return 0;
}

static _Noreturn void exit(int __status)
{
    int i;
    /* IN REVERSE ORDER OF REGISTRATION, which is the standard's rule and the
       one that lets a later handler rely on an earlier one's state. */
    for (i = __atexit_count; i > 0; i--) __atexit_fns[i - 1]();
    plat_exit((long)__status);
    for (;;) { }
}

/* A SECOND LIST, AND THAT IS THE WHOLE POINT: `quick_exit` runs the
   handlers registered with `at_quick_exit` and NOT the ones `atexit`
   registered, so a program can have one path that tears down and another
   that gets out. */
static void (*__quick_fns[32])(void);
static int __quick_count;

static int at_quick_exit(void (*__f)(void))
{
    if (__quick_count >= 32) return -1;
    __quick_fns[__quick_count++] = __f;
    return 0;
}

static _Noreturn void quick_exit(int __status)
{
    int i;
    for (i = __quick_count; i > 0; i--) __quick_fns[i - 1]();
    plat_exit((long)__status);
    for (;;) { }
}

static _Noreturn void _Exit(int __status)
{
    plat_exit((long)__status);
    for (;;) { }
}

static _Noreturn void abort(void)
{
    plat_exit(134);
    for (;;) { }
}

/* ── numbers ──────────────────────────────────────────────────────────── */
static int abs(int __n) { return __n < 0 ? -__n : __n; }
static long labs(long __n) { return __n < 0 ? -__n : __n; }
static long long llabs(long long __n) { return __n < 0 ? -__n : __n; }

static div_t div(int __a, int __b)
{ div_t r; r.quot = __a / __b; r.rem = __a % __b; return r; }
static ldiv_t ldiv(long __a, long __b)
{ ldiv_t r; r.quot = __a / __b; r.rem = __a % __b; return r; }
static lldiv_t lldiv(long long __a, long long __b)
{ lldiv_t r; r.quot = __a / __b; r.rem = __a % __b; return r; }

static int __isspace_c(int c)
{ return c==' '||c=='\t'||c=='\n'||c=='\v'||c=='\f'||c=='\r'; }

static int __digit_value(int c, int base)
{
    int v;
    if (c >= '0' && c <= '9') v = c - '0';
    else if (c >= 'a' && c <= 'z') v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'Z') v = c - 'A' + 10;
    else return -1;
    return v < base ? v : -1;
}

static unsigned long strtoul(const char *__s, char **__end, int __base)
{
    const char *p = __s;
    unsigned long v = 0;
    int neg = 0, any = 0, d;
    while (__isspace_c((unsigned char)*p)) p++;
    if (*p == '+' || *p == '-') { neg = *p == '-'; p++; }
    if ((__base == 0 || __base == 16) && p[0] == '0'
        && (p[1] == 'x' || p[1] == 'X') && __digit_value(p[2], 16) >= 0) {
        p += 2; __base = 16;
    } else if (__base == 0) {
        __base = (p[0] == '0') ? 8 : 10;
    }
    while ((d = __digit_value((unsigned char)*p, __base)) >= 0) {
        v = v * (unsigned long)__base + (unsigned long)d;
        p++; any = 1;
    }
    if (__end) *__end = (char *)(any ? p : __s);
    return neg ? (unsigned long)(0 - v) : v;
}

static long strtol(const char *__s, char **__end, int __base)
{
    return (long)strtoul(__s, __end, __base);
}
static long long strtoll(const char *__s, char **__end, int __base)
{ return (long long)strtol(__s, __end, __base); }
static unsigned long long strtoull(const char *__s, char **__end, int __base)
{ return (unsigned long long)strtoul(__s, __end, __base); }

static int atoi(const char *__s) { return (int)strtol(__s, NULL, 10); }
static long atol(const char *__s) { return strtol(__s, NULL, 10); }
static long long atoll(const char *__s) { return strtoll(__s, NULL, 10); }

/* THE CONVERSION IS `__uasm_num.h`'s, and it is exact: the nearest
   double to the decimal, ties to even, for every input including the ones
   with eight hundred digits in them. It lives there rather than here because
   `<stdio.h>`'s `%f` scanner needs the same grammar and the same answer, and
   two copies of a rounding rule is how they come to disagree. */
static double strtod(const char *__s, char **__end)
{
    return __num_strtod(__s, __end);
}
static float strtof(const char *__s, char **__end)
{ return (float)__num_strtod(__s, __end); }
static long double strtold(const char *__s, char **__end)
{
    /* NOT `(long double)strtod(s)`, which would round the decimal to 53
       bits and then widen the answer -- losing the eleven bits this type
       exists for. `__num_strtold` keeps all 64. */
    return __num_strtold(__s, __end);
}
static double atof(const char *__s) { return strtod(__s, NULL); }

/* ── pseudo-random ────────────────────────────────────────────────────── */
static unsigned long __rand_state = 1;

static void srand(unsigned int __seed) { __rand_state = __seed; }

static int rand(void)
{
    /* The generator C99's own annex gives as an example, widened: its period
       and quality are unremarkable and a program that needs better needs a
       real one, but every implementation agrees on what `rand` costs. */
    __rand_state = __rand_state * 6364136223846793005UL + 1442695040888963407UL;
    return (int)((__rand_state >> 33) & 0x7FFFFFFFu);
}

/* ── searching and sorting ────────────────────────────────────────────── */
static void *bsearch(const void *__key, const void *__base, size_t __n,
                     size_t __size, int (*__cmp)(const void *, const void *))
{
    size_t lo = 0, hi = __n;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        const char *p = (const char *)__base + mid * __size;
        int c = __cmp(__key, (const void *)p);
        if (c == 0) return (void *)p;
        if (c < 0) hi = mid; else lo = mid + 1;
    }
    return NULL;
}

static void __swap_bytes(char *a, char *b, size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) { char t = a[i]; a[i] = b[i]; b[i] = t; }
}

static void qsort(void *__base, size_t __n, size_t __size,
                  int (*__cmp)(const void *, const void *))
{
    /* HEAPSORT, not quicksort. `qsort` names the interface, not the
       algorithm, and heapsort is O(n log n) on every input with no recursion
       and no stack -- which matters here, where a deep recursion is a real
       frame on a backend that may have little of it. The name stays because
       the standard chose it. */
    char *base = (char *)__base;
    size_t i, root, child;
    if (__n < 2 || __size == 0) return;
    for (i = __n / 2; i > 0; i--) {
        root = i - 1;
        for (;;) {
            child = root * 2 + 1;
            if (child >= __n) break;
            if (child + 1 < __n &&
                __cmp(base + child * __size, base + (child + 1) * __size) < 0)
                child++;
            if (__cmp(base + root * __size, base + child * __size) >= 0) break;
            __swap_bytes(base + root * __size, base + child * __size, __size);
            root = child;
        }
    }
    for (i = __n; i > 1; i--) {
        __swap_bytes(base, base + (i - 1) * __size, __size);
        root = 0;
        for (;;) {
            child = root * 2 + 1;
            if (child >= i - 1) break;
            if (child + 1 < i - 1 &&
                __cmp(base + child * __size, base + (child + 1) * __size) < 0)
                child++;
            if (__cmp(base + root * __size, base + child * __size) >= 0) break;
            __swap_bytes(base + root * __size, base + child * __size, __size);
            root = child;
        }
    }
}

/* ── the environment ──────────────────────────────────────────────────── */
/* A `char *` INTO A BUFFER THIS OWNS, which is what C promises and what the
   host service cannot do: it copies into a caller's array and answers the
   length it needed, because a layer that allocated would have to say who
   frees it and the answer differs in every backend. So the buffer is here,
   one of it, and the string in it lasts until the next `getenv` -- which is
   exactly what C allows ("the string pointed to shall not be modified... may
   be overwritten by a subsequent call").

   THE `env` GROUP IS WHAT THIS COSTS. A program that calls `getenv` is
   refused at compile time by a backend whose target has no environment,
   naming the group; a program that does not call it pays nothing, because
   `lower._prune` drops the declaration with the function. */
#define __ENV_MAX 4096
static char __env_buf[__ENV_MAX];

static char *getenv(const char *__name)
{
    long n = 0, got;
    if (__name == NULL) return NULL;
    while (__name[n]) n++;
    got = host_env_get(__name, n, __env_buf, (long)__ENV_MAX - 1);
    if (got < 0) return NULL;             /* not set, or the host refused */
    if (got > (long)__ENV_MAX - 1) got = (long)__ENV_MAX - 1;
    __env_buf[got] = 0;
    return __env_buf;
}

/* ── another program ──────────────────────────────────────────────────── */
/* `/bin/sh -c CMD`, ASSEMBLED HERE, because the host service takes an argv
   and not a command line: `objects/hostsvc.py` says at length why -- quoting
   a list into one string is where command injection comes from, so the
   separation a caller has must not be thrown away. `system` is the one
   function whose argument really IS a shell command, so this is the one
   place that hands three arguments to a shell on purpose.

   WHAT THE CHILD WROTE ARRIVES AT THE END rather than as it is written,
   which is the one visible difference from a hosted `system`: there the
   child shares this program's descriptors, and here the host service runs it
   to completion and hands back what it said. Its stdout is written on to
   this program's stdout, in order, once it has finished.

   ITS STDERR IS CAPTURED AND DROPPED, and that is the contract's shape
   rather than a choice: `host_proc_run` answers ONE number and it is the
   stdout length, so the length of the stderr capture is not knowable here.
   Draining it still matters -- a child whose stderr pipe filled would block
   for ever -- which is why the buffer is passed at all. A program that needs
   the child's diagnostics redirects them: `system("cmd 2>&1")`. */
#define __SYSTEM_CAP 8192
static char __system_out[__SYSTEM_CAP];
static char __system_err[__SYSTEM_CAP];
static char __system_cmd[__SYSTEM_CAP];

static int system(const char *__cmd)
{
    long status = 0, got, n = 0, i;
    const char *sh = "/bin/sh";
    if (__cmd == NULL) {
        /* "IS THERE A COMMAND PROCESSOR" is answered by asking for one: the
           `proc` group either exists on this target or the program was
           refused at compile time, so reaching here means it does. */
        return 1;
    }
    /* THE PACKED ARGV: three NUL-separated words in one buffer, which is the
       shape `host_proc_run` takes. */
    for (i = 0; sh[i] && n < __SYSTEM_CAP - 4; i++) __system_cmd[n++] = sh[i];
    __system_cmd[n++] = 0;
    __system_cmd[n++] = '-';
    __system_cmd[n++] = 'c';
    __system_cmd[n++] = 0;
    for (i = 0; __cmd[i] && n < __SYSTEM_CAP - 1; i++) __system_cmd[n++] = __cmd[i];
    __system_cmd[n++] = 0;
    got = host_proc_run(__system_cmd, 3, n, __system_out, __SYSTEM_CAP,
                        __system_err, __SYSTEM_CAP, &status);
    if (got < 0) { errno = __host_errno(got); return -1; }
    if (got > 0) plat_write(1, __system_out, got > __SYSTEM_CAP
                                             ? __SYSTEM_CAP : got);
    return (int)status;
}


/* ── a number into a buffer, without a stream ─────────────────────────── */
/* `snprintf` WITH ONE CONVERSION IN IT, which is what C says these are: the
   format carries a `%`, an optional precision with no `*` in it, and one of
   `aAeEfFgG`. The point of having them at all is that a program formatting
   one number need not name `<stdio.h>`'s whole machinery -- which it still
   does here, and `lower.prune` drops what it does not reach. */
static int strfromd(char *__s, size_t __n, const char *__format, double __fp)
{ return snprintf(__s, __n, __format, __fp); }

static int strfromf(char *__s, size_t __n, const char *__format, float __fp)
{ return snprintf(__s, __n, __format, (double)__fp); }

static int strfroml(char *__s, size_t __n, const char *__format,
                    long double __fp)
{
    /* THE `L` GOES IN HERE. The format `strfroml` is handed carries NO
       length modifier -- `"%f"` and not `"%Lf"` -- and `snprintf` needs one
       to know which width it is being passed. The conversion character is
       the first letter in the format, because the only other things C
       allows in it are `%`, digits and a `.`. */
    char __f[32];
    size_t __i = 0, __j = 0;
    char __c;
    while (__format[__i] && __j + 2 < sizeof __f) {
        __c = __format[__i++];
        if (__c == 'a' || __c == 'A' || __c == 'e' || __c == 'E'
            || __c == 'f' || __c == 'F' || __c == 'g' || __c == 'G')
            __f[__j++] = 'L';
        __f[__j++] = __c;
    }
    __f[__j] = 0;
    return snprintf(__s, __n, __f, __fp);
}

/* ── multibyte, which is UTF-8 ────────────────────────────────────────── */
/* THE ENCODING HAS NO STATE, so each of these makes a fresh `mbstate_t` and
   the `s == NULL` question -- "does the encoding have shift states?" -- is
   answered 0 by all three of the ones that take it. `<wchar.h>`'s
   `mbrtowc` and `wcrtomb` are the encoder and the decoder; these are the
   five that C keeps here rather than there. */
static int mblen(const char *__s, size_t __n)
{
    mbstate_t __st;
    size_t __r;
    if (__s == NULL) return 0;
    __st.__count = 0; __st.__value = 0;
    __r = mbrtowc(NULL, __s, __n, &__st);
    if (__r == (size_t)-1 || __r == (size_t)-2) return -1;
    return (int)__r;
}

static int mbtowc(wchar_t *__w, const char *__s, size_t __n)
{
    mbstate_t __st;
    size_t __r;
    if (__s == NULL) return 0;
    __st.__count = 0; __st.__value = 0;
    __r = mbrtowc(__w, __s, __n, &__st);
    if (__r == (size_t)-1 || __r == (size_t)-2) return -1;
    return (int)__r;
}

static int wctomb(char *__s, wchar_t __w)
{
    mbstate_t __st;
    size_t __r;
    if (__s == NULL) return 0;
    __st.__count = 0; __st.__value = 0;
    __r = wcrtomb(__s, __w, &__st);
    return __r == (size_t)-1 ? -1 : (int)__r;
}

static size_t mbstowcs(wchar_t *__d, const char *__s, size_t __n)
{
    mbstate_t __st;
    const char *__p = __s;
    size_t __i = 0, __left = strlen(__s) + 1, __k;
    wchar_t __w;
    __st.__count = 0; __st.__value = 0;
    while (__d == NULL || __i < __n) {
        __k = mbrtowc(&__w, __p, __left, &__st);
        if (__k == (size_t)-1 || __k == (size_t)-2) return (size_t)-1;
        if (__k == 0) {
            if (__d) __d[__i] = 0;
            return __i;
        }
        if (__d) __d[__i] = __w;
        __i++;
        __p += __k;
        __left -= __k;
    }
    return __i;
}

static size_t wcstombs(char *__d, const wchar_t *__s, size_t __n)
{
    mbstate_t __st;
    char __buf[8];
    size_t __i = 0, __k, __j;
    __st.__count = 0; __st.__value = 0;
    for (;;) {
        if (*__s == 0) {
            if (__d && __i < __n) __d[__i] = 0;
            return __i;
        }
        __k = wcrtomb(__buf, *__s, &__st);
        if (__k == (size_t)-1) return (size_t)-1;
        if (__d) {
            if (__i + __k > __n) return __i;
            for (__j = 0; __j < __k; __j++) __d[__i + __j] = __buf[__j];
        }
        __i += __k;
        __s++;
    }
}

#endif
