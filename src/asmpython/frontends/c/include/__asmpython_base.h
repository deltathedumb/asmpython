/* Shared by the implementation headers: the platform floor, and nothing else.

   THE FLOOR IS THREE FUNCTIONS -- `plat_write`, `plat_exit`, `plat_heap` --
   and `objects/floor.py` argues at length for why it is three and not more.
   Everything this C library does sits on them, which is what makes a C
   program built here run on every backend AND in the IR interpreter: a
   backend that can run a Python program can already run a C one, with no
   second runtime to implement.

   WHAT IS NOT THERE: a way to READ. The floor writes, exits and asks for
   memory; it has no input, no clock and no filesystem. So `<stdio.h>` here
   has `printf` and no `scanf`, `getchar` answers EOF, and `<time.h>`'s
   `clock` answers -1. Each says so where it is declared rather than
   returning something plausible. */
#ifndef _ASMPYTHON_BASE_H
#define _ASMPYTHON_BASE_H

extern long plat_write(long __fd, const void *__buf, long __n);
extern void plat_exit(long __code);
extern void *plat_heap(long __n);

#endif
