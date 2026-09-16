/* <stddef.h> -- the types the language itself needs a name for.
   uasm C frontend. See frontends/c/include/README.md. */
#ifndef _UASM_STDDEF_H
#define _UASM_STDDEF_H
/* THE VERSION MACRO C23 ASKS FOR. The standard gives several headers one so
   a program can test whether THIS header has its C23 contents rather than
   asking the compiler how old it is -- the two answers come apart when a
   library is older than its compiler. Every header here has one, including
   the few the standard may not name: the `__STDC_` prefix is reserved to
   the implementation, so an extra one cannot collide with a program, and
   each of them answers truthfully. A missing one is the failure that
   matters, and there are none. */
#define __STDC_VERSION_STDDEF_H__ 202311L

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

/* The most strictly aligned type that is not over-aligned. SIXTEEN bytes
   here, which is `long double`'s: it is the only one that wants more than
   eight, and a hosted x86-64 compiler says sixteen for the same reason. */
typedef union { long long __ll; long double __ld; void *__p; } max_align_t;

/* C23's own two. `nullptr_t` IS THE TYPE OF `nullptr` AND OF NOTHING ELSE,
   which is why it is spelled with `typeof` rather than as an alias for
   `void *`: a `nullptr_t` converts to any pointer and no pointer converts
   back, and an alias would lose that.

   `unreachable()` IS A PROMISE rather than a statement -- reaching it is
   undefined behaviour, which is what lets a compiler drop the code after
   it and what makes a `default:` that cannot happen free. */
typedef typeof(nullptr) nullptr_t;

#define unreachable() __builtin_unreachable()

#endif
