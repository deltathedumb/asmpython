/* <errno.h>.

   THE E* NUMBERS ARE LINUX'S, so a program that prints one prints what its
   author expected, and they are the only numbers this library's callers ever
   see. The host services answer a small negative code from a table of their
   own (`objects/hostsvc.py`, and `__uasm_host.h` repeats it) -- the
   whole point of which is that it is the same on every target, where `errno`
   is not. `__host_errno` below is the one place the two meet.

   WHAT SETS IT. Every failing file operation in `<stdio.h>`, and `getenv`
   and `system` in `<stdlib.h>` when the host refuses them. Nothing else can
   fail: there is no locale to be wrong about and no syscall of our own. */
#ifndef _UASM_ERRNO_H
#define _UASM_ERRNO_H
#define __STDC_VERSION_ERRNO_H__ 202311L

static int __errno_storage;
#define errno __errno_storage

#define EPERM 1
#define ENOENT 2
#define ESRCH 3
#define EINTR 4
#define EIO 5
#define ENXIO 6
#define E2BIG 7
#define ENOEXEC 8
#define EBADF 9
#define ECHILD 10
#define EAGAIN 11
#define ENOMEM 12
#define EACCES 13
#define EFAULT 14
#define EBUSY 16
#define EEXIST 17
#define EXDEV 18
#define ENODEV 19
#define ENOTDIR 20
#define EISDIR 21
#define EINVAL 22
#define ENFILE 23
#define EMFILE 24
#define ENOTTY 25
#define EFBIG 27
#define ENOSPC 28
#define ESPIPE 29
#define EROFS 30
#define EMLINK 31
#define EPIPE 32
#define EDOM 33
#define ERANGE 34
#define ENOSYS 38
#define ENOTEMPTY 39
#define EILSEQ 84

/* THE HOST'S CODE, TRANSLATED. `objects/hostsvc.py`'s table is nine small
   negative numbers and is deliberately not `errno`: it is the same on every
   target. This is where it becomes the local spelling, once, so that no
   other header in this directory has to know both sets.

   A NON-NEGATIVE ARGUMENT IS NOT AN ERROR and answers 0 -- callers hand this
   whatever the operation returned rather than testing first. */
static int __host_errno(long __code)
{
    switch (__code) {
    case -2: return ENOENT;
    case -3: return EACCES;
    case -4: return EEXIST;
    case -5: return ENOTDIR;
    case -6: return ENOTEMPTY;
    case -7: return EAGAIN;
    case -8: return EPIPE;
    case -9: return EINVAL;
    case -1: return EIO;
    default: return __code < 0 ? EIO : 0;
    }
}

#endif
