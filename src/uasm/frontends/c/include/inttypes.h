/* <inttypes.h> -- the format macros, and the widest-integer functions.

   The macros exist so that `printf("%" PRId64, x)` is portable; here every
   64-bit type is `long`, so every one of them is `l`-prefixed. Written out
   rather than derived, because that is what a header for another target
   would have to change. */
#ifndef _UASM_INTTYPES_H
#define _UASM_INTTYPES_H

#include <stdint.h>

#define PRId8 "d"
#define PRId16 "d"
#define PRId32 "d"
#define PRId64 "ld"
#define PRIi8 "i"
#define PRIi16 "i"
#define PRIi32 "i"
#define PRIi64 "li"
#define PRIu8 "u"
#define PRIu16 "u"
#define PRIu32 "u"
#define PRIu64 "lu"
#define PRIo8 "o"
#define PRIo16 "o"
#define PRIo32 "o"
#define PRIo64 "lo"
#define PRIx8 "x"
#define PRIx16 "x"
#define PRIx32 "x"
#define PRIx64 "lx"
#define PRIX8 "X"
#define PRIX16 "X"
#define PRIX32 "X"
#define PRIX64 "lX"

#define PRIdLEAST8 PRId8
#define PRIdLEAST16 PRId16
#define PRIdLEAST32 PRId32
#define PRIdLEAST64 PRId64
#define PRIuLEAST8 PRIu8
#define PRIuLEAST16 PRIu16
#define PRIuLEAST32 PRIu32
#define PRIuLEAST64 PRIu64
#define PRIdFAST8 PRId8
#define PRIdFAST16 PRId64
#define PRIdFAST32 PRId64
#define PRIdFAST64 PRId64
#define PRIuFAST8 PRIu8
#define PRIuFAST16 PRIu64
#define PRIuFAST32 PRIu64
#define PRIuFAST64 PRIu64
#define PRIdPTR PRId64
#define PRIuPTR PRIu64
#define PRIxPTR PRIx64
#define PRIdMAX PRId64
#define PRIuMAX PRIu64
#define PRIxMAX PRIx64
#define PRIXMAX PRIX64
#define PRIoMAX PRIo64

#define SCNd8 "hhd"
#define SCNd16 "hd"
#define SCNd32 "d"
#define SCNd64 "ld"
#define SCNu8 "hhu"
#define SCNu16 "hu"
#define SCNu32 "u"
#define SCNu64 "lu"
#define SCNx8 "hhx"
#define SCNx16 "hx"
#define SCNx32 "x"
#define SCNx64 "lx"
#define SCNdMAX SCNd64
#define SCNuMAX SCNu64
#define SCNxMAX SCNx64
#define SCNdPTR SCNd64
#define SCNuPTR SCNu64
#define SCNxPTR SCNx64

typedef struct { intmax_t quot; intmax_t rem; } imaxdiv_t;

static intmax_t imaxabs(intmax_t __n) { return __n < 0 ? -__n : __n; }
static imaxdiv_t imaxdiv(intmax_t __a, intmax_t __b)
{ imaxdiv_t r; r.quot = __a / __b; r.rem = __a % __b; return r; }

#include <stdlib.h>
static intmax_t strtoimax(const char *__s, char **__e, int __b)
{ return (intmax_t)strtol(__s, __e, __b); }
static uintmax_t strtoumax(const char *__s, char **__e, int __b)
{ return (uintmax_t)strtoul(__s, __e, __b); }


/* THE TWO WIDE ONES, which C keeps here rather than in `<wchar.h>` because
   `intmax_t` is this header's business. `<wchar.h>`'s `wcstoll` does the
   work; these are the widths named again. */
#include <wchar.h>

static intmax_t wcstoimax(const wchar_t *__s, wchar_t **__e, int __b)
{ return (intmax_t)wcstoll(__s, __e, __b); }

static uintmax_t wcstoumax(const wchar_t *__s, wchar_t **__e, int __b)
{ return (uintmax_t)wcstoull(__s, __e, __b); }

#endif
