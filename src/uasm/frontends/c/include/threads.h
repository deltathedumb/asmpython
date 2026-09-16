/* <threads.h>.

   THREADS ARE A CAPABILITY OF THE TARGET, and this is the header that asks
   for one. `objects/hostsvc.py`'s `thread` group is seventeen operations --
   start, join, detach, yield, a mutex, a condition variable -- and a backend
   declares whether its target has them. The C backend does, over pthreads; a
   backend whose target does not says so, and a program that calls
   `thrd_create` is refused AT COMPILE TIME naming the group rather than
   failing to link or, worse, running its work nowhere.

   THE FLOOR STILL CANNOT CREATE ONE, which is what this header used to say
   and is still true: `plat_write`, `plat_exit` and `plat_heap` have no
   thread in them. The mistake was concluding that nothing could -- the same
   mistake `<stdio.h>` made about reading a file. A capability a target
   either has or has not is exactly what the optional groups are for.

   WHAT A PROGRAM MUST STILL EXPECT: `thrd_create` can FAIL, and a portable
   program checks. It fails on Windows (see `objects/hostsvc.py`'s C, which
   says why), and in the reference interpreter, which runs one thread.

   `<stdatomic.h>` AND THIS HEADER TOGETHER are not quite what they look
   like: that header's operations are ordinary loads and stores with the
   right names, which is correct for one thread and is a promise this
   library cannot keep once there are two. A program with real sharing wants
   a mutex from here, not an `_Atomic` from there. Said plainly rather than
   discovered.

   `thread_local` IS NOT THERE EITHER, and C23 makes it a keyword rather
   than a library name: `_Thread_local` parses and is accepted, and with one
   address space and no per-thread storage in the group it means `static`.
   A program that needs per-thread state passes it through the argument
   `thrd_create` already takes. */
#ifndef _UASM_THREADS_H
#define _UASM_THREADS_H

#include <stddef.h>
#include <time.h>
#include <__uasm_host.h>

/* THE RETURN CODES C DEFINES, and the only five there are. */
#define thrd_success  0
#define thrd_busy     1
#define thrd_error    2
#define thrd_nomem    3
#define thrd_timedout 4

#define mtx_plain     0
#define mtx_recursive 1
#define mtx_timed     2

/* A HANDLE IS A NUMBER the host gave us, in a struct of its own so that the
   types are distinct -- C requires `thrd_t`, `mtx_t` and `cnd_t` to be
   complete object types and a program may keep one in a struct. */
typedef struct { long __h; } thrd_t;
typedef struct { long __h; } mtx_t;
typedef struct { long __h; } cnd_t;
typedef struct { long __h; } once_flag;

#define ONCE_FLAG_INIT { 0 }
#define TSS_DTOR_ITERATIONS 1

typedef int (*thrd_start_t)(void *);

/* THE TRAMPOLINE. The contract's start routine answers a machine word and
   C's answers an `int`; going through a `long` keeps the sign and costs
   nothing. The pair is passed as one block so that both survive the call. */
struct __thrd_call { thrd_start_t __fn; void *__arg; };

static long __thrd_enter(void *__p)
{
    struct __thrd_call *__c = (struct __thrd_call *)__p;
    thrd_start_t __fn = __c->__fn;
    void *__arg = __c->__arg;
    int __r;
    __r = __fn(__arg);
    return (long)__r;
}

/* ONE BLOCK PER THREAD, from the platform heap: it has to outlive this call
   and the thread reads it after `thrd_create` has returned. Nothing frees
   it, which is one block per thread started and is the same trade
   `host_thread_detach` documents. */
extern void *plat_heap(long __n);

static int thrd_create(thrd_t *__t, thrd_start_t __fn, void *__arg)
{
    struct __thrd_call *__c;
    long __h;
    if (__t == NULL || __fn == NULL) return thrd_error;
    __c = (struct __thrd_call *)plat_heap((long)sizeof *__c);
    if (__c == NULL) return thrd_nomem;
    __c->__fn = __fn;
    __c->__arg = __arg;
    __h = host_thread_start(__thrd_enter, __c);
    if (__h < 0) return thrd_error;
    __t->__h = __h;
    return thrd_success;
}

static int thrd_join(thrd_t __t, int *__res)
{
    long __got = 0;
    if (host_thread_join(__t.__h, &__got) < 0) return thrd_error;
    if (__res) *__res = (int)__got;
    return thrd_success;
}

static int thrd_detach(thrd_t __t)
{ return host_thread_detach(__t.__h) < 0 ? thrd_error : thrd_success; }

static thrd_t thrd_current(void)
{
    thrd_t __t;
    __t.__h = host_thread_self();
    return __t;
}

/* COMPARING THE NUMBERS, which is all `thrd_equal` is for. A thread's own
   handle and the one `thrd_create` answered are different numbers for the
   same thread -- the host services say so -- so this compares what a
   program can actually compare: two answers from `thrd_current`. */
static int thrd_equal(thrd_t __a, thrd_t __b) { return __a.__h == __b.__h; }

static void thrd_yield(void) { host_thread_yield(); }

static _Noreturn void thrd_exit(int __res)
{
    host_thread_exit((long)__res);
    /* THE HOST DOES NOT RETURN FROM THAT, and a target whose threads are
       not implemented has no thread to leave -- so this loops rather than
       falling out of a `_Noreturn` function. */
    for (;;) { }
}

static int thrd_sleep(const struct timespec *__wait, struct timespec *__left)
{
    long __ns;
    if (__wait == NULL) return -1;
    __ns = (long)__wait->tv_sec * 1000000000L + __wait->tv_nsec;
    if (host_sleep(__ns) < 0) return -1;
    if (__left) { __left->tv_sec = 0; __left->tv_nsec = 0; }
    return 0;
}

/* ── mutexes ──────────────────────────────────────────────────────────── */
static int mtx_init(mtx_t *__m, int __type)
{
    long __h;
    if (__m == NULL) return thrd_error;
    /* `mtx_timed` IS NOT A KIND OF MUTEX HERE: the timed wait is a property
       of the WAIT (`host_mutex_timedlock` takes the timeout), so a mutex
       asked for as timed is an ordinary one and `mtx_timedlock` works on
       any of them. C allows that -- the type says what a program intends. */
    __h = host_mutex_new((__type & mtx_recursive) ? 1 : 0);
    if (__h < 0) return thrd_error;
    __m->__h = __h;
    return thrd_success;
}

static int mtx_lock(mtx_t *__m)
{
    if (__m == NULL) return thrd_error;
    return host_mutex_lock(__m->__h) < 0 ? thrd_error : thrd_success;
}

static int mtx_trylock(mtx_t *__m)
{
    long __r;
    if (__m == NULL) return thrd_error;
    __r = host_mutex_trylock(__m->__h);
    if (__r < 0) return thrd_error;
    return __r == 0 ? thrd_success : thrd_busy;
}

static int mtx_timedlock(mtx_t *__m, const struct timespec *__until)
{
    long __ns;
    long __r;
    if (__m == NULL || __until == NULL) return thrd_error;
    /* C'S DEADLINE IS ABSOLUTE and the host service's is a DURATION, so the
       current time is subtracted here -- the one place that conversion
       happens. */
    {
        long __now = host_time_unix();
        __ns = (long)__until->tv_sec * 1000000000L + __until->tv_nsec - __now;
        if (__ns < 0) __ns = 0;
    }
    __r = host_mutex_timedlock(__m->__h, __ns);
    if (__r < 0) return thrd_error;
    return __r == 0 ? thrd_success : thrd_timedout;
}

static int mtx_unlock(mtx_t *__m)
{
    if (__m == NULL) return thrd_error;
    return host_mutex_unlock(__m->__h) < 0 ? thrd_error : thrd_success;
}

static void mtx_destroy(mtx_t *__m) { if (__m) host_mutex_free(__m->__h); }

/* ── condition variables ──────────────────────────────────────────────── */
static int cnd_init(cnd_t *__c)
{
    long __h;
    if (__c == NULL) return thrd_error;
    __h = host_cond_new();
    if (__h < 0) return thrd_error;
    __c->__h = __h;
    return thrd_success;
}

static int cnd_wait(cnd_t *__c, mtx_t *__m)
{
    if (__c == NULL || __m == NULL) return thrd_error;
    return host_cond_wait(__c->__h, __m->__h, -1) < 0 ? thrd_error
                                                      : thrd_success;
}

static int cnd_timedwait(cnd_t *__c, mtx_t *__m, const struct timespec *__until)
{
    long __ns, __r;
    if (__c == NULL || __m == NULL || __until == NULL) return thrd_error;
    {
        long __now = host_time_unix();
        __ns = (long)__until->tv_sec * 1000000000L + __until->tv_nsec - __now;
        if (__ns < 0) __ns = 0;
    }
    __r = host_cond_wait(__c->__h, __m->__h, __ns);
    if (__r < 0) return thrd_error;
    return __r == 0 ? thrd_success : thrd_timedout;
}

static int cnd_signal(cnd_t *__c)
{
    if (__c == NULL) return thrd_error;
    return host_cond_signal(__c->__h) < 0 ? thrd_error : thrd_success;
}

static int cnd_broadcast(cnd_t *__c)
{
    if (__c == NULL) return thrd_error;
    return host_cond_broadcast(__c->__h) < 0 ? thrd_error : thrd_success;
}

static void cnd_destroy(cnd_t *__c) { if (__c) host_cond_free(__c->__h); }

/* ── call_once ────────────────────────────────────────────────────────── */
/* ONE MUTEX FOR ALL OF THEM, made the first time anybody calls. That is a
   race in itself -- two threads reaching `call_once` for the FIRST time at
   once could each make one -- and it is the one the host services cannot
   close without an atomic exchange the IR does not have. A program that
   needs `call_once` to be safe from the very first instruction calls it
   once from the main thread before starting any others, which is what the
   function is for. */
static long __once_mutex;

static void call_once(once_flag *__flag, void (*__fn)(void))
{
    if (__flag == NULL || __fn == NULL) return;
    if (__once_mutex == 0) __once_mutex = host_mutex_new(0);
    if (__once_mutex < 0) { if (!__flag->__h) { __flag->__h = 1; __fn(); } return; }
    host_mutex_lock(__once_mutex);
    if (__flag->__h == 0) {
        __flag->__h = 1;
        host_mutex_unlock(__once_mutex);
        __fn();
        return;
    }
    host_mutex_unlock(__once_mutex);
}

/* ── thread-local storage ─────────────────────────────────────────────── */
/* A KEY AND A POINTER PER THREAD UNDER IT, which is the `thread` group's
   last four operations. The destructor runs when a thread ENDS and the
   value is not null, which is what C promises and what pthreads does; the
   reference interpreter does it in the same place. */
typedef long tss_t;
typedef void (*tss_dtor_t)(void *);

static int tss_create(tss_t *__key, tss_dtor_t __dtor)
{
    long __k;
    if (__key == NULL) return thrd_error;
    __k = host_tss_new(__dtor);
    if (__k < 0) return thrd_error;
    *__key = __k;
    return thrd_success;
}

static void *tss_get(tss_t __key) { return host_tss_get(__key); }

static int tss_set(tss_t __key, void *__v)
{ return host_tss_set(__key, __v) < 0 ? thrd_error : thrd_success; }

static void tss_delete(tss_t __key) { host_tss_free(__key); }

#endif
