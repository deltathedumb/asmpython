/* <string.h> -- uasm C frontend.

   Every one of these is a loop over memory, which is exactly what the IR
   expresses directly (see objects/floor.py's "WHY THESE THREE AND NOT MORE"),
   so none of them needs anything from the platform. */
#ifndef _UASM_STRING_H
#define _UASM_STRING_H

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

static int memcmp(const void *__a, const void *__b, size_t __n)
{
    const unsigned char *a = (const unsigned char *)__a;
    const unsigned char *b = (const unsigned char *)__b;
    size_t i;
    for (i = 0; i < __n; i++) {
        if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    }
    return 0;
}

static void *memchr(const void *__s, int __c, size_t __n)
{
    const unsigned char *s = (const unsigned char *)__s;
    unsigned char v = (unsigned char)__c;
    size_t i;
    for (i = 0; i < __n; i++) {
        if (s[i] == v) return (void *)(s + i);
    }
    return NULL;
}

static size_t strlen(const char *__s)
{
    size_t n = 0;
    while (__s[n]) n++;
    return n;
}

static char *strcpy(char *__d, const char *__s)
{
    size_t i = 0;
    while ((__d[i] = __s[i]) != 0) i++;
    return __d;
}

static char *strncpy(char *__d, const char *__s, size_t __n)
{
    size_t i = 0;
    while (i < __n && __s[i]) { __d[i] = __s[i]; i++; }
    /* PADS WITH ZEROS TO `n`, and does NOT terminate when the source is
       longer. Both halves surprise people and both are the standard. */
    while (i < __n) { __d[i] = 0; i++; }
    return __d;
}

static char *strcat(char *__d, const char *__s)
{
    size_t i = strlen(__d), j = 0;
    while ((__d[i + j] = __s[j]) != 0) j++;
    return __d;
}

static char *strncat(char *__d, const char *__s, size_t __n)
{
    size_t i = strlen(__d), j = 0;
    while (j < __n && __s[j]) { __d[i + j] = __s[j]; j++; }
    __d[i + j] = 0;
    return __d;
}

static int strcmp(const char *__a, const char *__b)
{
    size_t i = 0;
    /* COMPARED AS `unsigned char`, which is what the standard says and what
       makes a byte above 127 sort after every ASCII one -- on a target whose
       plain `char` is signed, the naive version gets that backwards. */
    while (__a[i] && __a[i] == __b[i]) i++;
    {
        unsigned char x = (unsigned char)__a[i], y = (unsigned char)__b[i];
        return x < y ? -1 : (x > y ? 1 : 0);
    }
}

static int strncmp(const char *__a, const char *__b, size_t __n)
{
    size_t i = 0;
    while (i < __n) {
        unsigned char x = (unsigned char)__a[i], y = (unsigned char)__b[i];
        if (x != y) return x < y ? -1 : 1;
        if (x == 0) return 0;
        i++;
    }
    return 0;
}

static char *strchr(const char *__s, int __c)
{
    char v = (char)__c;
    size_t i = 0;
    for (;;) {
        if (__s[i] == v) return (char *)(__s + i);
        if (__s[i] == 0) return NULL;
        i++;
    }
}

static char *strrchr(const char *__s, int __c)
{
    char v = (char)__c;
    const char *found = NULL;
    size_t i = 0;
    for (;;) {
        if (__s[i] == v) found = __s + i;
        if (__s[i] == 0) break;
        i++;
    }
    return (char *)found;
}

static char *strstr(const char *__h, const char *__n)
{
    size_t nl = strlen(__n), i;
    if (nl == 0) return (char *)__h;
    for (i = 0; __h[i]; i++) {
        size_t j = 0;
        while (j < nl && __h[i + j] == __n[j]) j++;
        if (j == nl) return (char *)(__h + i);
    }
    return NULL;
}

static size_t strspn(const char *__s, const char *__set)
{
    size_t n = 0;
    while (__s[n] && strchr(__set, __s[n]) != NULL) n++;
    return n;
}

static size_t strcspn(const char *__s, const char *__set)
{
    size_t n = 0;
    while (__s[n] && strchr(__set, __s[n]) == NULL) n++;
    return n;
}

static char *strpbrk(const char *__s, const char *__set)
{
    size_t n = strcspn(__s, __set);
    return __s[n] ? (char *)(__s + n) : NULL;
}

static char *__strtok_state;

static char *strtok(char *__s, const char *__set)
{
    char *start;
    if (__s == NULL) __s = __strtok_state;
    if (__s == NULL) return NULL;
    __s += strspn(__s, __set);
    if (*__s == 0) { __strtok_state = NULL; return NULL; }
    start = __s;
    __s += strcspn(__s, __set);
    if (*__s) { *__s = 0; __strtok_state = __s + 1; }
    else __strtok_state = NULL;
    return start;
}

static const char *strerror(int __n)
{
    /* No `errno` values are produced by anything here -- there is no
       filesystem and no syscall to fail -- so this is honest rather than a
       table of messages nothing can return. */
    (void)__n;
    return "no error information is available";
}

#endif
