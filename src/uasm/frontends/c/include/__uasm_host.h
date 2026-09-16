/* The optional host services, declared. Private to this directory.

   THE FLOOR IS NOT THIS. `__uasm_base.h` declares `plat_write`,
   `plat_exit` and `plat_heap`, which EVERY backend owes and which therefore
   cost a program nothing to call. The names below are `objects/hostsvc.py`'s
   OPTIONAL GROUPS: a filesystem, a clock, entropy, the environment, another
   program. A backend declares which of them it has, and `Backend.
   check_host_services` refuses a program that calls into a group its target
   has not got -- naming the group, at compile time, rather than leaving an
   undefined symbol for the linker to complain about.

   SO EVERY DECLARATION HERE IS A COST, and the library is arranged so that a
   program pays it only if it asks. `printf` reaches `plat_write` and nothing
   else, so hello world still runs on a target with no filesystem; `fopen` is
   the door to the `file` group, and reading anything -- even `stdin` -- is a
   read and needs it. `lower._prune` drops an unreached declaration before the
   module leaves the frontend, which is what makes "only if it asks" true
   rather than aspirational.

   A POINTER AND A LENGTH, NEVER A NUL-TERMINATED STRING, for every path and
   every name. That is the contract's rule and `objects/hostsvc.py` argues it:
   the IR has no C-string convention and a JVM backend handed a bare pointer
   would have to scan for a terminator it has no reason to believe in.

   THE ERROR CODES ARE NOT `errno` -- they are small negative numbers from one
   table, the same on every target, which is the portability bug this layer
   exists to avoid. `<errno.h>` maps them to C's own numbers in one place, so
   nothing else in this library has to know either set. */
#ifndef _UASM_HOST_H
#define _UASM_HOST_H

/* ── the error codes, from `objects/hostsvc.py`'s ERRORS ───────────────── */
#define __HOST_ERR       (-1)
#define __HOST_ENOENT    (-2)
#define __HOST_EACCES    (-3)
#define __HOST_EEXIST    (-4)
#define __HOST_ENOTDIR   (-5)
#define __HOST_ENOTEMPTY (-6)
#define __HOST_EAGAIN    (-7)
#define __HOST_EPIPE     (-8)
#define __HOST_EINVAL    (-9)

/* ── how a file is opened, from OPEN_MODES. Always binary ─────────────── */
#define __HOST_OPEN_READ   0
#define __HOST_OPEN_WRITE  1
#define __HOST_OPEN_APPEND 2
#define __HOST_OPEN_UPDATE 3

/* ── what a path is, from KINDS ───────────────────────────────────────── */
#define __HOST_KIND_MISSING 0
#define __HOST_KIND_FILE    1
#define __HOST_KIND_DIR     2
#define __HOST_KIND_OTHER   3

/* ── where a seek starts, from SEEK ───────────────────────────────────── */
#define __HOST_SEEK_SET 0
#define __HOST_SEEK_CUR 1
#define __HOST_SEEK_END 2

/* ── the `file` group ─────────────────────────────────────────────────── */
extern long host_file_open(const void *__path, long __n, long __mode);
extern long host_file_read(long __h, void *__buf, long __n);
extern long host_file_write(long __h, const void *__buf, long __n);
extern long host_file_close(long __h);
extern long host_file_seek(long __h, long __off, long __whence);
extern long host_file_kind(const void *__path, long __n);
extern long host_file_size(const void *__path, long __n);
extern long host_file_remove(const void *__path, long __n);
extern long host_dir_make(const void *__path, long __n);
extern long host_dir_remove(const void *__path, long __n);

/* ── the `time` group. Nanoseconds, as an i64 ─────────────────────────── */
extern long host_time_unix(void);
extern long host_time_monotonic(void);
extern long host_sleep(long __nanos);

/* ── the `random` group ───────────────────────────────────────────────── */
extern long host_random_bytes(void *__buf, long __n);

/* ── the `env` group ──────────────────────────────────────────────────── */
extern long host_env_get(const void *__name, long __n, void *__out, long __cap);
extern long host_arg_count(void);
extern long host_arg_get(long __i, void *__out, long __cap);

/* ── the `proc` group ─────────────────────────────────────────────────── */
extern long host_proc_run(const void *__argv, long __count, long __n,
                          void *__out, long __out_cap,
                          void *__err, long __err_cap, void *__status);

#endif
