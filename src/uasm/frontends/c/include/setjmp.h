/* <setjmp.h> -- refused, and here is why.

   `longjmp` restores a saved MACHINE FRAME: a stack pointer, a program
   counter, and whichever callee-saved registers the ABI says survive a call.
   The UIR has none of those. It has virtual registers, basic blocks and
   branches within one function, and deliberately no way to name a frame at
   all -- that absence is what lets the same IR run in an interpreter, as
   JVM bytecode, as a `.pyc` and as x86-64 machine code without any of them
   agreeing about what a stack is.

   So this is not a gap waiting for someone with an afternoon. A `setjmp`
   that compiled and then did not unwind would be far worse than one that
   does not compile: the program would run, and be wrong somewhere else.

   WHAT TO DO INSTEAD, if the code is yours: return an error code, or use a
   flag and a `goto` within the function. Non-local exit BETWEEN functions is
   the one thing this compiler cannot express.

   This file exists rather than being absent so that the error names the
   problem instead of saying `cannot find include file 'setjmp.h'`. */
#error <setjmp.h> is not supported: longjmp restores a machine frame, and the UIR has no way to name one. Return an error code, or use a flag and a goto within the function.

/* Declared anyway, so that a program guarded on `__has_include` or compiled
   with the error suppressed gets one complaint rather than a hundred. */
typedef long jmp_buf[8];
typedef long sigjmp_buf[8];
