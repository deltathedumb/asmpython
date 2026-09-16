/* <inttypes.h> -- the format macros, and the widest-integer functions.

   The macros exist so that `printf("%" PRId64, x)` is portable; here every
   64-bit type is `long`, so every one of them is `l`-prefixed. Written out
   rather than derived, because that is what a header for another target
   would have to change. */
#ifndef _UASM_INTTYPES_H
#define _UASM_INTTYPES_H
#define __STDC_VERSION_INTTYPES_H__ 202311L

#include <stdint.h>

/* ── the conversion macros ────────────────────────────────────────────── */
/* THE WHOLE TABLE, and it is a table rather than a few lines because the
   standard's is. Eight conversions for `printf` -- C23 added `b` and `B`
   for binary -- and six for `scanf`, each in fourteen widths.

   THE PRINTING AND SCANNING MODIFIERS ARE NOT THE SAME, which is the one
   thing that makes this more than a transcription: `printf` sees an
   argument that default promotion has already widened to `int`, so
   `PRId8` is plain `"d"`; `scanf` writes through a pointer and has to be
   told the real width, so `SCNd8` is `"hhd"`. A macro that used one set
   for both would work for 32 and 64 and corrupt memory at 8 and 16. */
#define PRId8 "d"
#define PRId16 "d"
#define PRId32 "d"
#define PRId64 "ld"
#define PRIi8 "i"
#define PRIi16 "i"
#define PRIi32 "i"
#define PRIi64 "li"
#define PRIo8 "o"
#define PRIo16 "o"
#define PRIo32 "o"
#define PRIo64 "lo"
#define PRIu8 "u"
#define PRIu16 "u"
#define PRIu32 "u"
#define PRIu64 "lu"
#define PRIx8 "x"
#define PRIx16 "x"
#define PRIx32 "x"
#define PRIx64 "lx"
#define PRIX8 "X"
#define PRIX16 "X"
#define PRIX32 "X"
#define PRIX64 "lX"
#define PRIb8 "b"
#define PRIb16 "b"
#define PRIb32 "b"
#define PRIb64 "lb"
#define PRIB8 "B"
#define PRIB16 "B"
#define PRIB32 "B"
#define PRIB64 "lB"

/* The least types ARE the exact types here, so these are aliases. */
#define PRIdLEAST8 PRId8
#define PRIdLEAST16 PRId16
#define PRIdLEAST32 PRId32
#define PRIdLEAST64 PRId64
#define PRIiLEAST8 PRIi8
#define PRIiLEAST16 PRIi16
#define PRIiLEAST32 PRIi32
#define PRIiLEAST64 PRIi64
#define PRIoLEAST8 PRIo8
#define PRIoLEAST16 PRIo16
#define PRIoLEAST32 PRIo32
#define PRIoLEAST64 PRIo64
#define PRIuLEAST8 PRIu8
#define PRIuLEAST16 PRIu16
#define PRIuLEAST32 PRIu32
#define PRIuLEAST64 PRIu64
#define PRIxLEAST8 PRIx8
#define PRIxLEAST16 PRIx16
#define PRIxLEAST32 PRIx32
#define PRIxLEAST64 PRIx64
#define PRIXLEAST8 PRIX8
#define PRIXLEAST16 PRIX16
#define PRIXLEAST32 PRIX32
#define PRIXLEAST64 PRIX64
#define PRIbLEAST8 PRIb8
#define PRIbLEAST16 PRIb16
#define PRIbLEAST32 PRIb32
#define PRIbLEAST64 PRIb64
#define PRIBLEAST8 PRIB8
#define PRIBLEAST16 PRIB16
#define PRIBLEAST32 PRIB32
#define PRIBLEAST64 PRIB64

/* AND THE FAST ONES NAME THE WIDTH OF THE TYPE, not the width asked for:
   `int_fast16_t` is a `long`, so `PRIdFAST16` has to be `PRId64` or
   `printf` reads the wrong half of the argument. */
#define PRIdFAST8 PRId8
#define PRIdFAST16 PRId64
#define PRIdFAST32 PRId64
#define PRIdFAST64 PRId64
#define PRIiFAST8 PRIi8
#define PRIiFAST16 PRIi64
#define PRIiFAST32 PRIi64
#define PRIiFAST64 PRIi64
#define PRIoFAST8 PRIo8
#define PRIoFAST16 PRIo64
#define PRIoFAST32 PRIo64
#define PRIoFAST64 PRIo64
#define PRIuFAST8 PRIu8
#define PRIuFAST16 PRIu64
#define PRIuFAST32 PRIu64
#define PRIuFAST64 PRIu64
#define PRIxFAST8 PRIx8
#define PRIxFAST16 PRIx64
#define PRIxFAST32 PRIx64
#define PRIxFAST64 PRIx64
#define PRIXFAST8 PRIX8
#define PRIXFAST16 PRIX64
#define PRIXFAST32 PRIX64
#define PRIXFAST64 PRIX64
#define PRIbFAST8 PRIb8
#define PRIbFAST16 PRIb64
#define PRIbFAST32 PRIb64
#define PRIbFAST64 PRIb64
#define PRIBFAST8 PRIB8
#define PRIBFAST16 PRIB64
#define PRIBFAST32 PRIB64
#define PRIBFAST64 PRIB64


#define PRIdMAX PRId64
#define PRIiMAX PRIi64
#define PRIoMAX PRIo64
#define PRIuMAX PRIu64
#define PRIxMAX PRIx64
#define PRIXMAX PRIX64
#define PRIbMAX PRIb64
#define PRIBMAX PRIB64
#define PRIdPTR PRId64
#define PRIiPTR PRIi64
#define PRIoPTR PRIo64
#define PRIuPTR PRIu64
#define PRIxPTR PRIx64
#define PRIXPTR PRIX64
#define PRIbPTR PRIb64
#define PRIBPTR PRIB64


#define SCNd8 "hhd"
#define SCNd16 "hd"
#define SCNd32 "d"
#define SCNd64 "ld"
#define SCNi8 "hhi"
#define SCNi16 "hi"
#define SCNi32 "i"
#define SCNi64 "li"
#define SCNo8 "hho"
#define SCNo16 "ho"
#define SCNo32 "o"
#define SCNo64 "lo"
#define SCNu8 "hhu"
#define SCNu16 "hu"
#define SCNu32 "u"
#define SCNu64 "lu"
#define SCNx8 "hhx"
#define SCNx16 "hx"
#define SCNx32 "x"
#define SCNx64 "lx"
#define SCNb8 "hhb"
#define SCNb16 "hb"
#define SCNb32 "b"
#define SCNb64 "lb"


#define SCNdLEAST8 SCNd8
#define SCNdLEAST16 SCNd16
#define SCNdLEAST32 SCNd32
#define SCNdLEAST64 SCNd64
#define SCNiLEAST8 SCNi8
#define SCNiLEAST16 SCNi16
#define SCNiLEAST32 SCNi32
#define SCNiLEAST64 SCNi64
#define SCNoLEAST8 SCNo8
#define SCNoLEAST16 SCNo16
#define SCNoLEAST32 SCNo32
#define SCNoLEAST64 SCNo64
#define SCNuLEAST8 SCNu8
#define SCNuLEAST16 SCNu16
#define SCNuLEAST32 SCNu32
#define SCNuLEAST64 SCNu64
#define SCNxLEAST8 SCNx8
#define SCNxLEAST16 SCNx16
#define SCNxLEAST32 SCNx32
#define SCNxLEAST64 SCNx64
#define SCNbLEAST8 SCNb8
#define SCNbLEAST16 SCNb16
#define SCNbLEAST32 SCNb32
#define SCNbLEAST64 SCNb64


#define SCNdFAST8 SCNd8
#define SCNdFAST16 SCNd64
#define SCNdFAST32 SCNd64
#define SCNdFAST64 SCNd64
#define SCNiFAST8 SCNi8
#define SCNiFAST16 SCNi64
#define SCNiFAST32 SCNi64
#define SCNiFAST64 SCNi64
#define SCNoFAST8 SCNo8
#define SCNoFAST16 SCNo64
#define SCNoFAST32 SCNo64
#define SCNoFAST64 SCNo64
#define SCNuFAST8 SCNu8
#define SCNuFAST16 SCNu64
#define SCNuFAST32 SCNu64
#define SCNuFAST64 SCNu64
#define SCNxFAST8 SCNx8
#define SCNxFAST16 SCNx64
#define SCNxFAST32 SCNx64
#define SCNxFAST64 SCNx64
#define SCNbFAST8 SCNb8
#define SCNbFAST16 SCNb64
#define SCNbFAST32 SCNb64
#define SCNbFAST64 SCNb64


#define SCNdMAX SCNd64
#define SCNiMAX SCNi64
#define SCNoMAX SCNo64
#define SCNuMAX SCNu64
#define SCNxMAX SCNx64
#define SCNbMAX SCNb64
#define SCNdPTR SCNd64
#define SCNiPTR SCNi64
#define SCNoPTR SCNo64
#define SCNuPTR SCNu64
#define SCNxPTR SCNx64
#define SCNbPTR SCNb64

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
