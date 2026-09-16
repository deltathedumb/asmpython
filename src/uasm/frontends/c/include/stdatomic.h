/* <stdatomic.h> -- one thread, so every operation already is atomic.

   This is not a pretence. `<threads.h>` refuses because the platform floor
   cannot create a thread, so there is exactly one; on one thread a plain load
   is indivisible with respect to every other operation in the program, which
   is the whole of what `atomic_load` promises. The memory orders are accepted
   and ignored for the same reason: there is no second observer for them to
   order anything against.

   IF THREADS EVER ARRIVE, this file becomes wrong and has to be rewritten
   against whatever the IR grows to express them. Said here so that the day it
   matters, the reason it was ever right is on the page. */
#ifndef _UASM_STDATOMIC_H
#define _UASM_STDATOMIC_H

#include <stdint.h>
#include <stddef.h>

#define ATOMIC_BOOL_LOCK_FREE 2
#define ATOMIC_CHAR_LOCK_FREE 2
#define ATOMIC_SHORT_LOCK_FREE 2
#define ATOMIC_INT_LOCK_FREE 2
#define ATOMIC_LONG_LOCK_FREE 2
#define ATOMIC_LLONG_LOCK_FREE 2
#define ATOMIC_POINTER_LOCK_FREE 2

typedef enum {
    memory_order_relaxed, memory_order_consume, memory_order_acquire,
    memory_order_release, memory_order_acq_rel, memory_order_seq_cst
} memory_order;

typedef _Bool atomic_bool;
typedef char atomic_char;
typedef signed char atomic_schar;
typedef unsigned char atomic_uchar;
typedef short atomic_short;
typedef unsigned short atomic_ushort;
typedef int atomic_int;
typedef unsigned int atomic_uint;
typedef long atomic_long;
typedef unsigned long atomic_ulong;
typedef long long atomic_llong;
typedef unsigned long long atomic_ullong;
typedef size_t atomic_size_t;
typedef ptrdiff_t atomic_ptrdiff_t;
typedef intptr_t atomic_intptr_t;
typedef uintptr_t atomic_uintptr_t;
typedef intmax_t atomic_intmax_t;
typedef uintmax_t atomic_uintmax_t;

typedef struct { _Bool __v; } atomic_flag;
#define ATOMIC_FLAG_INIT { 0 }
#define ATOMIC_VAR_INIT(v) (v)

#define atomic_init(p, v) ((void)(*(p) = (v)))
#define atomic_is_lock_free(p) ((void)(p), 1)
#define atomic_store(p, v) ((void)(*(p) = (v)))
#define atomic_store_explicit(p, v, o) ((void)(o), atomic_store(p, v))
#define atomic_load(p) (*(p))
#define atomic_load_explicit(p, o) ((void)(o), atomic_load(p))
#define atomic_exchange(p, v) (__c_atomic_swap_helper(p, v))
#define atomic_exchange_explicit(p, v, o) ((void)(o), atomic_exchange(p, v))
#define atomic_fetch_add(p, v) (__c_atomic_add_helper(p, v))
#define atomic_fetch_add_explicit(p, v, o) ((void)(o), atomic_fetch_add(p, v))
#define atomic_fetch_sub(p, v) (__c_atomic_add_helper(p, -(v)))
#define atomic_fetch_sub_explicit(p, v, o) ((void)(o), atomic_fetch_sub(p, v))
#define atomic_fetch_or(p, v) (__c_atomic_or_helper(p, v))
#define atomic_fetch_and(p, v) (__c_atomic_and_helper(p, v))
#define atomic_fetch_xor(p, v) (__c_atomic_xor_helper(p, v))
#define atomic_thread_fence(o) ((void)(o))
#define atomic_signal_fence(o) ((void)(o))

/* A STATEMENT EXPRESSION, because each of these has to yield the OLD value
   and evaluate its pointer once. A function per type would need one per type;
   this is the same text for every one of them. */
#define __c_atomic_swap_helper(p, v) ({ __typeof__(*(p)) *__q = (p); \
    __typeof__(*(p)) __old = *__q; *__q = (v); __old; })
#define __c_atomic_add_helper(p, v) ({ __typeof__(*(p)) *__q = (p); \
    __typeof__(*(p)) __old = *__q; *__q = (__typeof__(*(p)))(__old + (v)); __old; })
#define __c_atomic_or_helper(p, v) ({ __typeof__(*(p)) *__q = (p); \
    __typeof__(*(p)) __old = *__q; *__q = (__typeof__(*(p)))(__old | (v)); __old; })
#define __c_atomic_and_helper(p, v) ({ __typeof__(*(p)) *__q = (p); \
    __typeof__(*(p)) __old = *__q; *__q = (__typeof__(*(p)))(__old & (v)); __old; })
#define __c_atomic_xor_helper(p, v) ({ __typeof__(*(p)) *__q = (p); \
    __typeof__(*(p)) __old = *__q; *__q = (__typeof__(*(p)))(__old ^ (v)); __old; })

#define atomic_compare_exchange_strong(p, e, d) ({ \
    __typeof__(*(p)) *__q = (p); __typeof__(*(p)) *__ex = (e); \
    _Bool __ok = (*__q == *__ex); if (__ok) *__q = (d); else *__ex = *__q; __ok; })
#define atomic_compare_exchange_weak(p, e, d) \
    atomic_compare_exchange_strong(p, e, d)
#define atomic_compare_exchange_strong_explicit(p, e, d, s, f) \
    ((void)(s), (void)(f), atomic_compare_exchange_strong(p, e, d))
#define atomic_compare_exchange_weak_explicit(p, e, d, s, f) \
    ((void)(s), (void)(f), atomic_compare_exchange_strong(p, e, d))

static _Bool atomic_flag_test_and_set(atomic_flag *__f)
{ _Bool old = __f->__v; __f->__v = 1; return old; }
static void atomic_flag_clear(atomic_flag *__f) { __f->__v = 0; }
#define atomic_flag_test_and_set_explicit(f, o) \
    ((void)(o), atomic_flag_test_and_set(f))
#define atomic_flag_clear_explicit(f, o) ((void)(o), atomic_flag_clear(f))

#endif
