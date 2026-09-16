/* The three that move bytes, where the allocator can reach them.

   `calloc` ZEROES AND `realloc` COPIES, so the arena in `__uasm_alloc.h`
   needs `memset` and `memcpy` -- and `<string.h>` needs the arena, for
   `strdup`. One of the two had to come out of `<string.h>` to break that
   circle, and it is these three: they are the ones with no dependency of
   their own, so a header that wants only them need not take the rest of
   `<string.h>` with it. `<string.h>` includes this file and a program that
   includes `<string.h>` sees no difference. */
#ifndef _UASM_MEM_H
#define _UASM_MEM_H

#include <stddef.h>

static void *memcpy(void *__d, const void *__s, size_t __n)
{
    unsigned char *d = (unsigned char *)__d;
    const unsigned char *s = (const unsigned char *)__s;
    size_t i;
    for (i = 0; i < __n; i++) d[i] = s[i];
    return __d;
}

static void *memmove(void *__d, const void *__s, size_t __n)
{
    unsigned char *d = (unsigned char *)__d;
    const unsigned char *s = (const unsigned char *)__s;
    size_t i;
    /* BACKWARDS WHEN THEY OVERLAP THE OTHER WAY. This is the whole difference
       from memcpy, and getting it wrong is invisible until the day two
       objects overlap. */
    if (d < s) {
        for (i = 0; i < __n; i++) d[i] = s[i];
    } else if (d > s) {
        for (i = __n; i > 0; i--) d[i - 1] = s[i - 1];
    }
    return __d;
}

static void *memset(void *__d, int __c, size_t __n)
{
    unsigned char *d = (unsigned char *)__d;
    unsigned char v = (unsigned char)__c;
    size_t i;
    for (i = 0; i < __n; i++) d[i] = v;
    return __d;
}

#endif
