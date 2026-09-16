/* <time.h>.

   THERE IS NO CLOCK. The platform floor writes, exits and asks for memory;
   nothing in it can ask the host what time it is. So `time` and `clock`
   return the "unavailable" value the standard defines for exactly this case
   -- `(time_t)-1` and `(clock_t)-1` -- rather than a plausible number a
   program would go on to do arithmetic with. */
#ifndef _ASMPYTHON_TIME_H
#define _ASMPYTHON_TIME_H

#include <stddef.h>

typedef long time_t;
typedef long clock_t;

#define CLOCKS_PER_SEC 1000000L

struct tm {
    int tm_sec, tm_min, tm_hour, tm_mday, tm_mon, tm_year;
    int tm_wday, tm_yday, tm_isdst;
};

static time_t time(time_t *__t) { if (__t) *__t = (time_t)-1; return (time_t)-1; }
static clock_t clock(void) { return (clock_t)-1; }
static double difftime(time_t __a, time_t __b) { return (double)(__a - __b); }
static struct tm *localtime(const time_t *__t) { (void)__t; return NULL; }
static struct tm *gmtime(const time_t *__t) { (void)__t; return NULL; }
static time_t mktime(struct tm *__tm) { (void)__tm; return (time_t)-1; }
static size_t strftime(char *__s, size_t __n, const char *__f,
                       const struct tm *__tm)
{ (void)__f; (void)__tm; if (__s && __n) __s[0] = 0; return 0; }
static char *asctime(const struct tm *__tm) { (void)__tm; return NULL; }
static char *ctime(const time_t *__t) { (void)__t; return NULL; }

#endif
