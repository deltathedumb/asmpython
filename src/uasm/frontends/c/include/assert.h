/* <assert.h>.

   NO INCLUDE GUARD ON THE MACRO, deliberately: the standard says `assert` is
   redefined every time this header is included, according to whether NDEBUG
   is defined at that moment. A guard would make the second include a no-op
   and `#define NDEBUG` between two includes would stop working -- which is
   the one thing this header is expected to do. */
#include <stddef.h>
#include <__uasm_base.h>

#ifndef _UASM_ASSERT_DECLARED
#define _UASM_ASSERT_DECLARED

static void __assert_write(const char *__s)
{
    long n = 0;
    while (__s[n]) n++;
    plat_write(2, __s, n);
}

static _Noreturn void __assert_fail(const char *__expr, const char *__file,
                                    int __line, const char *__func)
{
    char digits[24];
    int i = 0, j;
    char out[26];
    unsigned int v = (unsigned int)(__line < 0 ? 0 : __line);
    __assert_write(__file);
    __assert_write(":");
    if (v == 0) digits[i++] = '0';
    while (v) { digits[i++] = (char)('0' + (v % 10u)); v /= 10u; }
    j = 0;
    while (i) out[j++] = digits[--i];
    out[j] = 0;
    __assert_write(out);
    __assert_write(": ");
    __assert_write(__func);
    __assert_write(": Assertion `");
    __assert_write(__expr);
    __assert_write("' failed.\n");
    plat_exit(134);
    for (;;) { }
}

#endif

#undef assert
#ifdef NDEBUG
#define assert(e) ((void)0)
#else
#define assert(e) ((e) ? (void)0 : __assert_fail(#e, __FILE__, __LINE__, __func__))
#endif

#define static_assert _Static_assert
