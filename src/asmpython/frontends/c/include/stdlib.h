/* <stdlib.h> -- asmpython C frontend.

   `malloc` IS AN ARENA WITH A FREE LIST, and `objects/floor.py` explains why
   that is enough: the floor's `plat_heap` hands out regions that are NOT
   guaranteed contiguous, so an allocator over it has to chain them rather
   than assume one growing block. This one does, never returns a region to the
   platform, and does not coalesce adjacent free blocks -- a program that
   allocates and frees in a pattern designed to fragment it will use more
   memory than glibc would. Every allocation is 16-byte aligned, which is what
   `max_align_t` asks for. */
#ifndef _ASMPYTHON_STDLIB_H
#define _ASMPYTHON_STDLIB_H

#include <stddef.h>
#include <string.h>
#include <__asmpython_base.h>

#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1
#define RAND_MAX 2147483647
#define MB_CUR_MAX 1

typedef struct { int quot; int rem; } div_t;
typedef struct { long quot; long rem; } ldiv_t;
typedef struct { long long quot; long long rem; } lldiv_t;

/* ── the allocator ────────────────────────────────────────────────────── */
struct __blk { size_t size; struct __blk *next; };

static struct __blk *__free_list;
static char *__heap_next;
static char *__heap_stop;

#define __ALLOC_CHUNK 262144

static void *malloc(size_t __n)
{
    struct __blk *b, **link;
    size_t want;
    char *p;
    if (__n == 0) __n = 1;
    want = (__n + 15u) & ~(size_t)15u;
    /* FIRST FIT. A best-fit search over a list this simple costs more than
       the fragmentation it saves, and an exact-fit cache would be a second
       structure to keep right. */
    link = &__free_list;
    b = __free_list;
    while (b) {
        if (b->size >= want) {
            *link = b->next;
            return (void *)((char *)b + 16);
        }
        link = &b->next;
        b = b->next;
    }
    if (__heap_next == 0 || __heap_next + want + 16 > __heap_stop) {
        size_t ask = want + 16 > __ALLOC_CHUNK ? want + 16 : __ALLOC_CHUNK;
        p = (char *)plat_heap((long)ask);
        if (p == 0) return NULL;
        __heap_next = p;
        __heap_stop = p + ask;
    }
    b = (struct __blk *)__heap_next;
    __heap_next += want + 16;
    b->size = want;
    b->next = 0;
    return (void *)((char *)b + 16);
}

static void free(void *__p)
{
    struct __blk *b;
    if (__p == NULL) return;
    b = (struct __blk *)((char *)__p - 16);
    b->next = __free_list;
    __free_list = b;
}

static void *calloc(size_t __n, size_t __size)
{
    size_t total = __n * __size;
    void *p;
    /* THE OVERFLOW CHECK IS THE POINT OF `calloc`. Without it a caller asking
       for 2^61 elements of 8 bytes gets a 0-byte block and writes over
       whatever follows it. */
    if (__n != 0 && total / __n != __size) return NULL;
    p = malloc(total);
    if (p) memset(p, 0, total);
    return p;
}

static void *realloc(void *__p, size_t __n)
{
    struct __blk *b;
    void *q;
    if (__p == NULL) return malloc(__n);
    if (__n == 0) { free(__p); return NULL; }
    b = (struct __blk *)((char *)__p - 16);
    if (b->size >= __n) return __p;
    q = malloc(__n);
    if (q == NULL) return NULL;
    memcpy(q, __p, b->size);
    free(__p);
    return q;
}

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

static double strtod(const char *__s, char **__end)
{
    const char *p = __s;
    double v = 0.0, scale;
    int neg = 0, any = 0, esign, eval, i;
    while (__isspace_c((unsigned char)*p)) p++;
    if (*p == '+' || *p == '-') { neg = *p == '-'; p++; }
    while (*p >= '0' && *p <= '9') { v = v * 10.0 + (double)(*p - '0'); p++; any = 1; }
    if (*p == '.') {
        double f = 0.1;
        p++;
        while (*p >= '0' && *p <= '9') { v += (double)(*p - '0') * f; f *= 0.1; p++; any = 1; }
    }
    if (any && (*p == 'e' || *p == 'E')) {
        const char *save = p;
        p++;
        esign = 0;
        if (*p == '+' || *p == '-') { esign = *p == '-'; p++; }
        if (*p >= '0' && *p <= '9') {
            eval = 0;
            while (*p >= '0' && *p <= '9') { eval = eval * 10 + (*p - '0'); p++; }
            if (eval > 400) eval = 400;
            scale = 1.0;
            for (i = 0; i < eval; i++) scale *= 10.0;
            if (esign) v /= scale; else v *= scale;
        } else {
            p = save;
        }
    }
    if (__end) *__end = (char *)(any ? p : __s);
    return neg ? -v : v;
}
static float strtof(const char *__s, char **__end)
{ return (float)strtod(__s, __end); }
static long double strtold(const char *__s, char **__end)
{ return (long double)strtod(__s, __end); }
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

/* ── the environment there is not ─────────────────────────────────────── */
static char *getenv(const char *__name)
{
    /* The platform floor cannot ask the host for an environment. NULL is
       what a real `getenv` returns for a name that is not set, so a program
       that checks gets a correct answer rather than a wrong one. */
    (void)__name;
    return NULL;
}

static int system(const char *__cmd)
{
    /* Zero means "no command processor is available", which is exactly the
       situation. */
    (void)__cmd;
    return 0;
}

#endif
