/* <tgmath.h> -- one name per operation, whichever type it is given.

   Each macro is a `_Generic` that picks the `float` function for a `float`
   argument and the `double` one for everything else. The `double` case is
   written `(sqrt)(x)` rather than `sqrt(x)`: a function-like macro name is
   only invoked when a `(` follows it, so the parentheses name the FUNCTION
   that `<math.h>` declared instead of re-entering this macro. (The hide set
   would stop the recursion anyway -- see `preprocess.py` -- but a reader
   should not have to know that to read this file.)

   There are no `long double` cases because `long double` IS `double` here. */
#ifndef _ASMPYTHON_TGMATH_H
#define _ASMPYTHON_TGMATH_H

#include <math.h>

#define __tg1(f, ff, x) _Generic((x), float: ff, default: f)(x)
#define __tg2(f, ff, x, y) _Generic((x) + (y), float: ff, default: f)(x, y)

#define sqrt(x)  __tg1((sqrt), sqrtf, x)
#define sin(x)   __tg1((sin), sinf, x)
#define cos(x)   __tg1((cos), cosf, x)
#define tan(x)   __tg1((tan), tanf, x)
#define exp(x)   __tg1((exp), expf, x)
#define log(x)   __tg1((log), logf, x)
#define log10(x) __tg1((log10), log10f, x)
#define fabs(x)  __tg1((fabs), fabsf, x)
#define floor(x) __tg1((floor), floorf, x)
#define ceil(x)  __tg1((ceil), ceilf, x)
#define pow(x, y)   __tg2((pow), powf, x, y)
#define fmod(x, y)  __tg2((fmod), fmodf, x, y)
#define atan2(y, x) __tg2((atan2), atan2f, y, x)

#endif
