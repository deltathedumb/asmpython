/* <setjmp.h>.

   IT WORKS, AND NOT BY SAVING A FRAME. There is nothing here to save one
   with: the UIR has virtual registers, blocks and branches within a
   function, and deliberately no way to name a stack -- which is what lets
   one module run in an interpreter, as JVM bytecode and as machine code.
   So a `longjmp` does not throw frames away; it asks them to leave. It sets
   a flag, every call site in the program checks that flag the moment its
   call returns and returns too if it is set, and the frame that recognises
   the jump's token branches back to its own `setjmp`. `longjmp.py` is that
   rewriting and says at length why it is shaped this way.

   WHAT IT COSTS is a load and a branch after every call -- in a program that
   uses `setjmp`, and in no other: the rewriting runs only when one of these
   two names is called.

   WHAT YOU GET, COMPARED TO A HOSTED IMPLEMENTATION.

     * `setjmp` answers 0 the first time and `longjmp`'s value, or 1, after.
     * A local that is not `volatile` and changed in between has an
       INDETERMINATE value, says C. Here it keeps the value it had: the frame
       never went away. Stricter than the standard, which is the safe way to
       differ -- code written for a real implementation still works.
     * `longjmp` to a frame that has returned is undefined in C and undefined
       here: the token belongs to that ACTIVATION, so nothing matches it and
       the unwinding runs out of the program.
     * The address of `setjmp` cannot be taken (E1603). It is a branch inside
       its caller, so there is no function to point at.

   `jmp_buf` IS ONE WORD, because one word is what is in it: the token of the
   activation that saved it. A hosted `jmp_buf` is a register file and a
   sigmask; this one is a number, and `sizeof (jmp_buf)` says 8. */
#ifndef _UASM_SETJMP_H
#define _UASM_SETJMP_H

/* AN ARRAY, as C requires -- which is what makes `jmp_buf env;` and then
   `setjmp(env)` pass the address without an `&`, and what stops a program
   copying one by assignment. */
typedef long jmp_buf[1];

/* THE COMPILER RECOGNISES THESE TWO NAMES, which is why they are not spelled
   `setjmp` and `longjmp`: C says `setjmp` is a macro, and a program that
   `#undef`s it is entitled to a function -- which cannot exist here. Calling
   these directly is the same as using the macros. */
extern int __c_setjmp(void *__env);
extern _Noreturn void __c_longjmp(void *__env, int __val);

/* AND `longjmp` IS ALSO A FUNCTION, because C says a program may write
   `(longjmp)(env, 1)` or take its address when the header declares it --
   7.1.4p1, the rule that makes every library macro parenthesisable. The
   rewriting works on calls to `__c_longjmp` in the IR, so a wrapper that
   makes one is unwound exactly as a direct call would be. `setjmp` cannot
   have the same treatment and the note above says why: it is a branch
   inside its caller, so there is no function for it to be. */
static _Noreturn void longjmp(long *__env, int __val)
{
    __c_longjmp(__env, __val);
}

#define setjmp(env) __c_setjmp(env)
#define longjmp(env, val) __c_longjmp((env), (val))

/* POSIX'S SPELLINGS, for code that uses them. `_setjmp` and `_longjmp`
   differ from the plain ones only in not touching the signal mask, and
   `sigsetjmp`'s `savemask` says whether to save it -- there is no mask here
   (see `<signal.h>`), so all four are the same jump and the flag is ignored
   rather than pretended about. */
typedef long sigjmp_buf[1];
#define _setjmp(env) __c_setjmp(env)
#define _longjmp(env, val) __c_longjmp((env), (val))
#define sigsetjmp(env, savemask) ((void)(savemask), __c_setjmp(env))
#define siglongjmp(env, val) __c_longjmp((env), (val))

#endif
