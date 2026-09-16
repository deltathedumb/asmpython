/* <stddef.h> -- the types the language itself needs a name for.
   asmpython C frontend. See frontends/c/include/README.md. */
#ifndef _ASMPYTHON_STDDEF_H
#define _ASMPYTHON_STDDEF_H

/* THESE SPELLINGS COME FROM THE COMPILER, not from this file. `__SIZE_TYPE__`
   and the rest are predefined by `preprocess.py` from the same table the
   frontend computes `sizeof` with, so a header cannot disagree with the
   compiler about what `size_t` is. */
typedef __SIZE_TYPE__ size_t;
typedef __PTRDIFF_TYPE__ ptrdiff_t;
typedef __WCHAR_TYPE__ wchar_t;

#ifndef NULL
#define NULL ((void *)0)
#endif

#define offsetof(type, member) __builtin_offsetof(type, member)

/* The most strictly aligned type that is not over-aligned. Eight bytes here:
   `double` and every pointer want that and nothing wants more. */
typedef union { long long __ll; long double __ld; void *__p; } max_align_t;

#endif
