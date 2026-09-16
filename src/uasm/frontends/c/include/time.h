/* <time.h>.

   THERE IS A CLOCK, and it is `objects/hostsvc.py`'s `time` group: two
   clocks, in nanoseconds, plus a sleep. A backend whose target has none says
   so and a program that calls `time` or `clock` is refused at compile time
   naming the group -- rather than being handed a plausible number it would go
   on to do arithmetic with. A program that never asks pays nothing:
   `lower._prune` drops the declaration with the function.

   TWO CLOCKS BECAUSE THERE ARE TWO QUESTIONS. `time` answers what time it is
   and may jump when the machine is corrected; `clock` answers how long this
   program has been running and never goes backwards. Measuring a duration
   with the first is a bug that appears twice a year, which is why the host
   services have both and why these two do not share one.

   THE CALENDAR IS UTC, AND SAYS SO. `localtime` is `gmtime`: the host
   services can say what time it is and cannot say what the local offset from
   UTC is -- there is no `TZ` to read that would mean anything on a target
   without an environment, and a guessed offset is worse than a stated one.
   `tm_isdst` is therefore 0 rather than -1: it is not unknown, it is not in
   effect. A program that wants local time has the offset from somewhere else
   and adds it.

   THE CONVERSION IS EXACT AND HAS NO TABLE. Days to a civil date and back
   are the shift-the-year-to-March algorithm, which makes the leap day the
   last day of the year and turns the whole thing into three divisions. It is
   correct for every year in a 64-bit time_t, not only for 1970-2038. */
#ifndef _UASM_TIME_H
#define _UASM_TIME_H

#include <stddef.h>
#include <__uasm_host.h>

typedef long time_t;
typedef long clock_t;

/* NANOSECONDS, because that is what the host answers and a divisor that
   loses precision should be the program's choice rather than this header's.
   C only requires that `clock()/CLOCKS_PER_SEC` be a number of seconds. */
#define CLOCKS_PER_SEC 1000000000L
#define TIME_UTC 1

struct tm {
    int tm_sec, tm_min, tm_hour, tm_mday, tm_mon, tm_year;
    int tm_wday, tm_yday, tm_isdst;
};

struct timespec { time_t tv_sec; long tv_nsec; };

static time_t time(time_t *__t)
{
    long now = host_time_unix();
    time_t secs = (time_t)(now / 1000000000L);
    if (__t) *__t = secs;
    return secs;
}

static clock_t clock(void) { return (clock_t)host_time_monotonic(); }

static int timespec_get(struct timespec *__ts, int __base)
{
    long now;
    if (__ts == NULL || __base != TIME_UTC) return 0;
    now = host_time_unix();
    __ts->tv_sec = (time_t)(now / 1000000000L);
    __ts->tv_nsec = now % 1000000000L;
    if (__ts->tv_nsec < 0) { __ts->tv_nsec += 1000000000L; __ts->tv_sec--; }
    return __base;
}

static double difftime(time_t __a, time_t __b) { return (double)(__a - __b); }

/* ── the civil calendar ───────────────────────────────────────────────── */
/* DAYS SINCE 1970-01-01 TO A DATE, and back, by shifting the year to start
   in March. The leap day then falls at the END of the year, so the length of
   a year is a constant and the month lengths follow one formula -- which is
   why there is no table here and no special case for February. The era is
   400 years, which is where the Gregorian rule repeats exactly. */
static void __civil_from_days(long __z, int *__y, int *__m, int *__d)
{
    long era, doe, yoe, doy, mp, y;
    unsigned int  uoe;
    __z += 719468;                       /* shift the epoch to 0000-03-01 */
    era = (__z >= 0 ? __z : __z - 146096) / 146097;
    doe = __z - era * 146097;            /* day of era, 0..146096        */
    uoe = (unsigned int)doe;
    yoe = (long)((uoe - uoe / 1460u + uoe / 36524u - uoe / 146096u) / 365u);
    y = yoe + era * 400;
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    mp = (5 * doy + 2) / 153;            /* month, counting March as 0   */
    *__d = (int)(doy - (153 * mp + 2) / 5 + 1);
    *__m = (int)(mp < 10 ? mp + 3 : mp - 9);
    *__y = (int)(y + (mp < 10 ? 0 : 1));
}

static long __days_from_civil(long __y, long __m, long __d)
{
    long era, yoe, doy, doe;
    __y -= __m <= 2;
    era = (__y >= 0 ? __y : __y - 399) / 400;
    yoe = __y - era * 400;
    doy = (153 * (__m + (__m > 2 ? -3 : 9)) + 2) / 5 + __d - 1;
    doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

static int __leap_year(int __y)
{
    return (__y % 4 == 0 && __y % 100 != 0) || __y % 400 == 0;
}

static struct tm __tm_storage;

static struct tm *gmtime(const time_t *__t)
{
    long secs, days, rem;
    int y, m, d;
    if (__t == NULL) return NULL;
    secs = (long)*__t;
    days = secs / 86400;
    rem = secs % 86400;
    /* FLOOR DIVISION, not truncation: a time before the epoch has a negative
       remainder, and 23:00 on the 31st of December 1969 is not hour -1. */
    if (rem < 0) { rem += 86400; days--; }
    __civil_from_days(days, &y, &m, &d);
    __tm_storage.tm_sec = (int)(rem % 60);
    __tm_storage.tm_min = (int)((rem / 60) % 60);
    __tm_storage.tm_hour = (int)(rem / 3600);
    __tm_storage.tm_mday = d;
    __tm_storage.tm_mon = m - 1;
    __tm_storage.tm_year = y - 1900;
    /* 1970-01-01 WAS A THURSDAY, which is where the 4 comes from. */
    __tm_storage.tm_wday = (int)(((days % 7) + 11) % 7);
    __tm_storage.tm_yday = (int)(days - __days_from_civil(y, 1, 1));
    __tm_storage.tm_isdst = 0;
    return &__tm_storage;
}

static struct tm *localtime(const time_t *__t) { return gmtime(__t); }

/* NORMALISING, WHICH IS MOST OF WHAT `mktime` IS FOR. A caller may hand it
   the 32nd of January or an hour of 25 and expects the answer for the day
   that really is; the fields are put back in range here and the struct is
   updated, which C requires. */
static time_t mktime(struct tm *__tm)
{
    long secs, days, extra;
    int y, m, d;
    if (__tm == NULL) return (time_t)-1;
    m = __tm->tm_mon;
    y = __tm->tm_year + 1900;
    /* MONTHS FIRST, because a month out of range changes the year and the
       day count depends on both. */
    extra = m >= 0 ? m / 12 : -((-m + 11) / 12);
    y += (int)extra;
    m -= (int)extra * 12;
    days = __days_from_civil(y, m + 1, __tm->tm_mday);
    secs = days * 86400 + (long)__tm->tm_hour * 3600
         + (long)__tm->tm_min * 60 + (long)__tm->tm_sec;
    {
        time_t t = (time_t)secs;
        struct tm *back = gmtime(&t);
        __tm->tm_sec = back->tm_sec;
        __tm->tm_min = back->tm_min;
        __tm->tm_hour = back->tm_hour;
        __tm->tm_mday = back->tm_mday;
        __tm->tm_mon = back->tm_mon;
        __tm->tm_year = back->tm_year;
        __tm->tm_wday = back->tm_wday;
        __tm->tm_yday = back->tm_yday;
        __tm->tm_isdst = 0;
        return t;
    }
}

/* ── printing a time ──────────────────────────────────────────────────── */
static const char *__wday_name(int __w)
{
    switch (__w) {
    case 0: return "Sunday";
    case 1: return "Monday";
    case 2: return "Tuesday";
    case 3: return "Wednesday";
    case 4: return "Thursday";
    case 5: return "Friday";
    default: return "Saturday";
    }
}

static const char *__mon_name(int __m)
{
    switch (__m) {
    case 0: return "January";
    case 1: return "February";
    case 2: return "March";
    case 3: return "April";
    case 4: return "May";
    case 5: return "June";
    case 6: return "July";
    case 7: return "August";
    case 8: return "September";
    case 9: return "October";
    case 10: return "November";
    default: return "December";
    }
}

static size_t __tm_number(char *__out, size_t __at, size_t __cap,
                          long __v, int __width, char __pad)
{
    char tmp[24];
    int n = 0, neg = 0, i;
    if (__v < 0) { neg = 1; __v = -__v; }
    do { tmp[n++] = (char)('0' + (int)(__v % 10)); __v /= 10; } while (__v);
    if (neg) tmp[n++] = '-';
    while (n < __width) tmp[n++] = __pad;
    for (i = n - 1; i >= 0; i--) {
        if (__at < __cap) __out[__at] = tmp[i];
        __at++;
    }
    return __at;
}

static size_t __tm_text(char *__out, size_t __at, size_t __cap,
                        const char *__s, int __abbrev)
{
    int i;
    for (i = 0; __s[i] && (!__abbrev || i < 3); i++) {
        if (__at < __cap) __out[__at] = __s[i];
        __at++;
    }
    return __at;
}

/* EVERY CONVERSION C23 DEFINES, and nothing locale-dependent means anything
   else here: the C locale is the only one there is (`<locale.h>` says why),
   so `%c`, `%x` and `%X` are its formats rather than a table's. */
static size_t strftime(char *__s, size_t __n, const char *__f,
                       const struct tm *__tm)
{
    size_t at = 0;
    const char *f = __f;
    if (__s == NULL || __f == NULL || __tm == NULL) return 0;
    while (*f) {
        if (*f != '%') {
            if (at < __n) __s[at] = *f;
            at++;
            f++;
            continue;
        }
        f++;
        if (*f == 'E' || *f == 'O') f++;   /* the alternatives are the same */
        switch (*f) {
        case 'a': at = __tm_text(__s, at, __n, __wday_name(__tm->tm_wday), 1); break;
        case 'A': at = __tm_text(__s, at, __n, __wday_name(__tm->tm_wday), 0); break;
        case 'b': case 'h':
            at = __tm_text(__s, at, __n, __mon_name(__tm->tm_mon), 1); break;
        case 'B': at = __tm_text(__s, at, __n, __mon_name(__tm->tm_mon), 0); break;
        case 'c':
            at = __tm_text(__s, at, __n, __wday_name(__tm->tm_wday), 1);
            at = __tm_text(__s, at, __n, " ", 0);
            at = __tm_text(__s, at, __n, __mon_name(__tm->tm_mon), 1);
            at = __tm_text(__s, at, __n, " ", 0);
            at = __tm_number(__s, at, __n, __tm->tm_mday, 2, ' ');
            at = __tm_text(__s, at, __n, " ", 0);
            at = __tm_number(__s, at, __n, __tm->tm_hour, 2, '0');
            at = __tm_text(__s, at, __n, ":", 0);
            at = __tm_number(__s, at, __n, __tm->tm_min, 2, '0');
            at = __tm_text(__s, at, __n, ":", 0);
            at = __tm_number(__s, at, __n, __tm->tm_sec, 2, '0');
            at = __tm_text(__s, at, __n, " ", 0);
            at = __tm_number(__s, at, __n, __tm->tm_year + 1900, 4, '0');
            break;
        case 'C': at = __tm_number(__s, at, __n, (__tm->tm_year + 1900) / 100, 2, '0'); break;
        case 'd': at = __tm_number(__s, at, __n, __tm->tm_mday, 2, '0'); break;
        case 'D':
            at = __tm_number(__s, at, __n, __tm->tm_mon + 1, 2, '0');
            at = __tm_text(__s, at, __n, "/", 0);
            at = __tm_number(__s, at, __n, __tm->tm_mday, 2, '0');
            at = __tm_text(__s, at, __n, "/", 0);
            at = __tm_number(__s, at, __n, (__tm->tm_year + 1900) % 100, 2, '0');
            break;
        case 'e': at = __tm_number(__s, at, __n, __tm->tm_mday, 2, ' '); break;
        case 'F':
            at = __tm_number(__s, at, __n, __tm->tm_year + 1900, 4, '0');
            at = __tm_text(__s, at, __n, "-", 0);
            at = __tm_number(__s, at, __n, __tm->tm_mon + 1, 2, '0');
            at = __tm_text(__s, at, __n, "-", 0);
            at = __tm_number(__s, at, __n, __tm->tm_mday, 2, '0');
            break;
        case 'H': at = __tm_number(__s, at, __n, __tm->tm_hour, 2, '0'); break;
        case 'I': {
            int h = __tm->tm_hour % 12;
            at = __tm_number(__s, at, __n, h == 0 ? 12 : h, 2, '0');
            break;
        }
        case 'j': at = __tm_number(__s, at, __n, __tm->tm_yday + 1, 3, '0'); break;
        case 'm': at = __tm_number(__s, at, __n, __tm->tm_mon + 1, 2, '0'); break;
        case 'M': at = __tm_number(__s, at, __n, __tm->tm_min, 2, '0'); break;
        case 'n': at = __tm_text(__s, at, __n, "\n", 0); break;
        case 'p': at = __tm_text(__s, at, __n, __tm->tm_hour < 12 ? "AM" : "PM", 0); break;
        case 'r':
            {
                int h = __tm->tm_hour % 12;
                at = __tm_number(__s, at, __n, h == 0 ? 12 : h, 2, '0');
                at = __tm_text(__s, at, __n, ":", 0);
                at = __tm_number(__s, at, __n, __tm->tm_min, 2, '0');
                at = __tm_text(__s, at, __n, ":", 0);
                at = __tm_number(__s, at, __n, __tm->tm_sec, 2, '0');
                at = __tm_text(__s, at, __n, __tm->tm_hour < 12 ? " AM" : " PM", 0);
            }
            break;
        case 'R':
            at = __tm_number(__s, at, __n, __tm->tm_hour, 2, '0');
            at = __tm_text(__s, at, __n, ":", 0);
            at = __tm_number(__s, at, __n, __tm->tm_min, 2, '0');
            break;
        case 'S': at = __tm_number(__s, at, __n, __tm->tm_sec, 2, '0'); break;
        case 't': at = __tm_text(__s, at, __n, "\t", 0); break;
        case 'T': case 'X':
            at = __tm_number(__s, at, __n, __tm->tm_hour, 2, '0');
            at = __tm_text(__s, at, __n, ":", 0);
            at = __tm_number(__s, at, __n, __tm->tm_min, 2, '0');
            at = __tm_text(__s, at, __n, ":", 0);
            at = __tm_number(__s, at, __n, __tm->tm_sec, 2, '0');
            break;
        case 'u': at = __tm_number(__s, at, __n,
                                   __tm->tm_wday == 0 ? 7 : __tm->tm_wday, 1, '0'); break;
        case 'U': at = __tm_number(__s, at, __n,
                                   (__tm->tm_yday + 7 - __tm->tm_wday) / 7, 2, '0'); break;
        case 'w': at = __tm_number(__s, at, __n, __tm->tm_wday, 1, '0'); break;
        case 'W': at = __tm_number(__s, at, __n,
                                   (__tm->tm_yday + 7 - (__tm->tm_wday + 6) % 7) / 7,
                                   2, '0'); break;
        case 'x':
            at = __tm_number(__s, at, __n, __tm->tm_mon + 1, 2, '0');
            at = __tm_text(__s, at, __n, "/", 0);
            at = __tm_number(__s, at, __n, __tm->tm_mday, 2, '0');
            at = __tm_text(__s, at, __n, "/", 0);
            at = __tm_number(__s, at, __n, (__tm->tm_year + 1900) % 100, 2, '0');
            break;
        case 'y': at = __tm_number(__s, at, __n, (__tm->tm_year + 1900) % 100, 2, '0'); break;
        case 'Y': at = __tm_number(__s, at, __n, __tm->tm_year + 1900, 4, '0'); break;
        case 'G': {
            /* THE ISO WEEK-BASED YEAR, which is not the calendar year in the
               few days either side of New Year: a week belongs to the year
               that owns its Thursday. */
            int wday = (__tm->tm_wday + 6) % 7;      /* Monday is 0 */
            int year = __tm->tm_year + 1900;
            int yday = __tm->tm_yday;
            if (yday - wday < -3) year--;
            else if (365 + (__leap_year(year) ? 1 : 0) - yday < 4 - wday) year++;
            at = __tm_number(__s, at, __n, year, 4, '0');
            break;
        }
        case 'g': {
            int wday = (__tm->tm_wday + 6) % 7;
            int year = __tm->tm_year + 1900;
            int yday = __tm->tm_yday;
            if (yday - wday < -3) year--;
            else if (365 + (__leap_year(year) ? 1 : 0) - yday < 4 - wday) year++;
            at = __tm_number(__s, at, __n, year % 100, 2, '0');
            break;
        }
        case 'V': {
            int wday = (__tm->tm_wday + 6) % 7;
            int week = (__tm->tm_yday - wday + 10) / 7;
            if (week < 1) {
                int py = __tm->tm_year + 1899;
                int pdays = 365 + (__leap_year(py) ? 1 : 0);
                week = (__tm->tm_yday + pdays - wday + 10) / 7;
            } else if (week > 52) {
                int days = 365 + (__leap_year(__tm->tm_year + 1900) ? 1 : 0);
                if (days - __tm->tm_yday < 4 - wday) week = 1;
            }
            at = __tm_number(__s, at, __n, week, 2, '0');
            break;
        }
        case 'z': at = __tm_text(__s, at, __n, "+0000", 0); break;
        case 'Z': at = __tm_text(__s, at, __n, "UTC", 0); break;
        case 's': {
            struct tm copy = *__tm;
            at = __tm_number(__s, at, __n, (long)mktime(&copy), 1, '0');
            break;
        }
        case '%': at = __tm_text(__s, at, __n, "%", 0); break;
        case 0:   f--; break;               /* a trailing % is kept as one */
        default:
            /* AN UNKNOWN CONVERSION IS UNDEFINED and this one keeps the two
               characters, which is what makes a format with a typo in it
               visible rather than silently shorter. */
            at = __tm_text(__s, at, __n, "%", 0);
            if (at < __n) __s[at] = *f;
            at++;
            break;
        }
        f++;
    }
    if (at >= __n) {
        /* C SAYS THE ARRAY IS INDETERMINATE when it did not fit, and answers
           0. Terminating it anyway costs one store and is what a caller that
           forgot to check will read. */
        if (__n > 0) __s[__n - 1] = 0;
        return 0;
    }
    __s[at] = 0;
    return at;
}

static char __asctime_buf[32];

static char *asctime(const struct tm *__tm)
{
    if (__tm == NULL) return NULL;
    /* THE EXACT 26-CHARACTER FORM C SPECIFIES, which is `strftime`'s `%c`
       with a different day-of-month padding and a newline. Written through
       the same two helpers so the two cannot disagree. */
    {
        size_t at = 0;
        at = __tm_text(__asctime_buf, at, 32, __wday_name(__tm->tm_wday), 1);
        at = __tm_text(__asctime_buf, at, 32, " ", 0);
        at = __tm_text(__asctime_buf, at, 32, __mon_name(__tm->tm_mon), 1);
        at = __tm_text(__asctime_buf, at, 32, " ", 0);
        at = __tm_number(__asctime_buf, at, 32, __tm->tm_mday, 2, ' ');
        at = __tm_text(__asctime_buf, at, 32, " ", 0);
        at = __tm_number(__asctime_buf, at, 32, __tm->tm_hour, 2, '0');
        at = __tm_text(__asctime_buf, at, 32, ":", 0);
        at = __tm_number(__asctime_buf, at, 32, __tm->tm_min, 2, '0');
        at = __tm_text(__asctime_buf, at, 32, ":", 0);
        at = __tm_number(__asctime_buf, at, 32, __tm->tm_sec, 2, '0');
        at = __tm_text(__asctime_buf, at, 32, " ", 0);
        at = __tm_number(__asctime_buf, at, 32, __tm->tm_year + 1900, 4, '0');
        at = __tm_text(__asctime_buf, at, 32, "\n", 0);
        __asctime_buf[at < 31 ? at : 31] = 0;
    }
    return __asctime_buf;
}

static char *ctime(const time_t *__t) { return asctime(gmtime(__t)); }

#endif
