/* <stdarg.h> -- the one standard header that cannot be written in C.
   uasm C frontend.

   A variadic function's arguments live in an area the frontend gives it as a
   hidden trailing parameter: a block of 8-byte slots, one per argument after
   the last named one. `va_list` is a cursor into that block, so it is a
   `char *` and nothing more exotic -- but `va_start` has to be able to NAME
   the hidden parameter, and no C expression can. That is what the builtins
   are for; see `lower.py` on the argument-area convention. */
#ifndef _UASM_STDARG_H
#define _UASM_STDARG_H
#define __STDC_VERSION_STDARG_H__ 202311L

typedef char *va_list;

/* C23 allows `va_start(ap)` with no second argument, and the second was never
   used for anything here: the area's address is a property of the frame, not
   of the last named parameter. Both spellings work. */
#define va_start(ap, ...) __builtin_va_start(ap)
#define va_arg(ap, type)  __builtin_va_arg(ap, type)
#define va_end(ap)        __builtin_va_end(ap)
#define va_copy(dst, src) __builtin_va_copy(dst, src)

#define __va_copy(d, s) va_copy(d, s)

#endif
