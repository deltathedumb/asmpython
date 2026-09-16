/* <stddef.h> -- the types the language itself needs a name for.
   uasm C frontend. See frontends/c/include/README.md. */
#ifndef _UASM_STDDEF_H
#define _UASM_STDDEF_H

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
