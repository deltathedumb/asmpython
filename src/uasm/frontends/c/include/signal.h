/* <signal.h> -- there are no signals.

   The platform floor writes, exits and asks for memory. Nothing in it can
   install a handler, and nothing can deliver one. `signal` answers `SIG_ERR`,
   which is what a hosted implementation answers for a signal it refuses to
   handle, so a program that checks is told the truth; `raise` answers
   non-zero, which means it failed. */
#ifndef _UASM_SIGNAL_H
#define _UASM_SIGNAL_H

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

static void (*signal(int __sig, void (*__handler)(int)))(int)
{ (void)__sig; (void)__handler; return SIG_ERR; }

static int raise(int __sig) { (void)__sig; return -1; }

#endif
