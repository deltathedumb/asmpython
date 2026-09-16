/* The allocator, where `<string.h>` and `<stdlib.h>` can both have it.

   `malloc` IS AN ARENA WITH A FREE LIST, and `objects/floor.py` explains why
   that is enough: the floor's `plat_heap` hands out regions that are NOT
   guaranteed contiguous, so an allocator over it has to chain them rather
   than assume one growing block. This one does, never returns a region to the
   platform, and does not coalesce adjacent free blocks -- a program that
   allocates and frees in a pattern designed to fragment it will use more
   memory than glibc would. Every allocation is 16-byte aligned, which is what
   `max_align_t` asks for.

   IT IS NOT IN `<stdlib.h>` because `strdup` is in `<string.h>` and
   `<stdlib.h>` already includes `<string.h>`. See `__uasm_mem.h` for the
   other half of that circle. A program sees these names by including
   `<stdlib.h>`, as C says. */
#ifndef _UASM_ALLOC_H
#define _UASM_ALLOC_H

#include <stddef.h>
#include <__uasm_mem.h>
#include <__uasm_base.h>

/* ── the allocator ────────────────────────────────────────────────────── */
struct __blk { size_t size; struct __blk *next; };

static struct __blk *__free_list;
static char *__heap_next;
static char *__heap_stop;

#define __ALLOC_CHUNK 262144

/* THE OVER-ALIGNED MARKER IS A SIZE NO REAL BLOCK CAN HAVE. Every block's
   size is rounded up to a multiple of 16, so `(size_t)-1` is not one of
   them, and a header carrying it means "the real block starts where `next`
   points". `free` and `realloc` both look, which is what makes a pointer
   from `aligned_alloc` an ordinary one to everything else. */
#define __ALIGN_MARK ((size_t)-1)

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
    if (b->size == __ALIGN_MARK) b = (struct __blk *)((char *)b->next - 16);
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
    size_t have;
    void *q;
    if (__p == NULL) return malloc(__n);
    if (__n == 0) { free(__p); return NULL; }
    b = (struct __blk *)((char *)__p - 16);
    if (b->size == __ALIGN_MARK) {
        /* AN OVER-ALIGNED BLOCK KNOWS ITS OWN SIZE THE LONG WAY: the real
           block's header says how much was taken, and the aligned pointer
           says how much of it was skipped to get there. */
        char *base = (char *)b->next;
        have = ((struct __blk *)(base - 16))->size
             - (size_t)((char *)__p - base);
    } else {
        have = b->size;
        if (have >= __n) return __p;
    }
    q = malloc(__n);
    if (q == NULL) return NULL;
    memcpy(q, __p, have < __n ? have : __n);
    free(__p);
    return q;
}

/* ── over-aligned blocks ──────────────────────────────────────────────── */
static void *aligned_alloc(size_t __align, size_t __n)
{
    char *base, *p;
    struct __blk *b;
    size_t off;
    if (__align == 0 || (__align & (__align - 1)) != 0) return NULL;
    if (__align <= 16) return malloc(__n);
    base = (char *)malloc(__n + __align + 16);
    if (base == NULL) return NULL;
    off = (size_t)((unsigned long)(base + 16)) & (__align - 1);
    p = base + 16 + (off ? __align - off : 0);
    b = (struct __blk *)(p - 16);
    b->size = __ALIGN_MARK;
    b->next = (struct __blk *)base;
    return (void *)p;
}

static void free_sized(void *__p, size_t __n) { (void)__n; free(__p); }
static void free_aligned_sized(void *__p, size_t __align, size_t __n)
{ (void)__align; (void)__n; free(__p); }

/* THE ALIGNMENT A POINTER ACTUALLY HAS, which is its lowest set bit: C23
   added it so that a program handed a buffer can ask rather than assume. */
static size_t memalignment(const void *__p)
{
    unsigned long v = (unsigned long)__p;
    if (v == 0) return 0;
    return (size_t)(v & (~v + 1UL));
}

#endif
