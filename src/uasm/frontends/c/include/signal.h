/* <signal.h> -- the half of it that needs no operating system.

   NOTHING DELIVERS AN ASYNCHRONOUS SIGNAL here: the platform floor writes,
   exits and asks for memory, and no interrupt from outside the program can
   reach it. A ctrl-C does not become a `SIGINT`.

   `raise` IS THE OTHER HALF AND IT IS ENTIRELY IMPLEMENTABLE, because it is
   synchronous: the program raises a signal on itself, and what C asks for
   is that the installed handler be called. That is a table and a call, and
   it needs nothing underneath. So `signal` records a handler and answers
   the previous one, `raise` calls it, and `abort` raises `SIGABRT` first --
   which is the wording in 7.24.4.1, "unless the signal SIGABRT is being
   caught and the signal handler does not return".

   A PROGRAM THAT NEVER RAISES ONE PAYS NOTHING. The table is sixteen
   pointers and `lower.prune` drops it along with these two functions. */
#ifndef _UASM_SIGNAL_H
#define _UASM_SIGNAL_H
#define __STDC_VERSION_SIGNAL_H__ 202311L

#include <__uasm_base.h>

typedef int sig_atomic_t;

#define SIG_DFL ((void (*)(int))0)
#define SIG_IGN ((void (*)(int))1)
#define SIG_ERR ((void (*)(int))-1)

#define SIGABRT 6
#define SIGFPE 8
#define SIGILL 4
#define SIGINT 2
#define SIGSEGV 11
#define SIGTERM 15

/* THE SIGNALS C NAMES and no others; `SIGTERM` is the largest. A number
   outside the range is refused, which is what C means by the set of valid
   signals being implementation-defined. */
#define __SIG_LAST 15

/* ONE SLOT EACH, ZERO-FILLED -- and `SIG_DFL` is the null pointer, so an
   untouched slot already says "the default action". */
static void (*__sig_handler[__SIG_LAST + 1])(int);

static void (*signal(int __sig, void (*__handler)(int)))(int)
{
    void (*__old)(int);
    if (__sig <= 0 || __sig > __SIG_LAST) return SIG_ERR;
    __old = __sig_handler[__sig];
    __sig_handler[__sig] = __handler;
    return __old;
}

static int raise(int __sig)
{
    void (*__h)(int);
    if (__sig <= 0 || __sig > __SIG_LAST) return -1;
    __h = __sig_handler[__sig];
    if (__h == SIG_IGN) return 0;
    if (__h == SIG_DFL) {
        /* THE DEFAULT ACTION IS TERMINATION for every signal C names, and
           the status is the one a shell reports for a death by that
           signal: 128 plus the number. Nothing is flushed, which is what
           abnormal termination means. */
        plat_exit(128 + __sig);
        for (;;) { }
    }
    /* THE HANDLER IS NOT RESET TO `SIG_DFL` FIRST. C leaves that
       implementation-defined and this follows glibc: a handler stays
       installed, so one that raises its own signal again does not
       terminate the program on the second time round. */
    __h(__sig);
    return 0;
}

#endif
