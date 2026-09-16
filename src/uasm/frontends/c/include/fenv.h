/* <fenv.h> -- one rounding mode and no exception flags.

   The IR has no instruction that reads or writes a floating-point control
   register, so the environment is whatever the backend's machine does by
   default: round to nearest, ties to even. `fesetround` refuses any other
   mode rather than accepting it and rounding the old way, which is the
   difference between a limitation and a wrong answer. */
#ifndef _UASM_FENV_H
#define _UASM_FENV_H

#define FE_TONEAREST 0
#define FE_DOWNWARD 1
#define FE_UPWARD 2
#define FE_TOWARDZERO 3

#define FE_INVALID 1
#define FE_DIVBYZERO 2
#define FE_OVERFLOW 4
#define FE_UNDERFLOW 8
#define FE_INEXACT 16
#define FE_ALL_EXCEPT 31

typedef int fenv_t;
typedef int fexcept_t;

#define FE_DFL_ENV ((const fenv_t *)0)

static int fegetround(void) { return FE_TONEAREST; }
static int fesetround(int __mode) { return __mode == FE_TONEAREST ? 0 : -1; }
static int feclearexcept(int __e) { (void)__e; return 0; }
static int fetestexcept(int __e) { (void)__e; return 0; }
static int feraiseexcept(int __e) { (void)__e; return -1; }
static int fegetenv(fenv_t *__e) { if (__e) *__e = 0; return 0; }
static int fesetenv(const fenv_t *__e) { (void)__e; return 0; }
static int feholdexcept(fenv_t *__e) { if (__e) *__e = 0; return 0; }
static int feupdateenv(const fenv_t *__e) { (void)__e; return 0; }
static int fegetexceptflag(fexcept_t *__f, int __e)
{ (void)__e; if (__f) *__f = 0; return 0; }
static int fesetexceptflag(const fexcept_t *__f, int __e)
{ (void)__f; (void)__e; return 0; }

#endif
