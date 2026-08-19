"""Host services: THE WHOLE CONTRACT between a frontend and a backend.

WHAT THIS IS. One table of named operations with fixed signatures. A frontend
emits calls to these names and knows nothing else about the target; a backend
implements them and is then a complete backend. Nothing else crosses.

That is the destination rather than today's state, and the difference is worth
being exact about. A backend today ALSO owes whatever of the object runtime is
not yet written in the machine subset -- 394 symbols as this is written, and
docs/INERT-RUNTIME.md is the work of removing them. When that finishes, this
table is all that is left. The two halves are the same project seen from
opposite ends: port the runtime so the obligation shrinks, and name the
obligation so it stops growing.

THE FLOOR IS THE MANDATORY GROUP OF THIS TABLE, not a separate thing.
`link/platform.py` holds the contracts and the C for `plat_write`,
`plat_exit` and `plat_heap`, and the whole of stage 2 was an argument for why
that number is three. Nothing here changes it -- `core` below IS that set,
read from there so there is one list. What this file adds is everything a real
program needs that a bare-metal target cannot have: a filesystem, a clock,
entropy, a network, a character database.

WHY THOSE CANNOT SIMPLY JOIN THE FLOOR. Because the floor is what EVERY
backend owes, and stage 2's achievement was getting it from five to three. A
mandatory thirty would undo it and would make a target without a filesystem
impossible to write a backend for. So the rest of this table is OPTIONAL and
DECLARED: a backend says which groups it provides, and a program using one it
does not have is refused at compile time.

WHY NOT `ctypes`, WHICH ALREADY WORKS. `frontends/python/cffi.py` resolves a
native symbol at COMPILE time -- a promise to the linker, not a `dlopen`. That
is exactly right for what it is, and it is why `bundled/pathlib.py` can call
`_open` and `GetFileAttributesA`. But a promise to the linker is a promise
only a LINKING backend can keep: the JVM backend has no linker and no `_open`,
and a bare-metal target has neither. So `pathlib` works on the C backend and
cannot be made to work anywhere else, and the same would be true of every
module that touches a file. The symbol names are the problem -- `_open` is
MSVC's spelling, `open` is POSIX's, and `java.nio` is neither.

So: a NAMED SET OF OPERATIONS with fixed signatures, which each backend
satisfies however it can. The C backend calls libc. The JVM backend calls
`java.nio`. The interpreter calls Python's `os`. A frontend calls one name and
does not know which.

THE ONE RULE, INHERITED FROM THE FLOOR, AND IT IS THE SAME RULE. Nothing here
may know what a Python value is. `plat_write` takes bytes, not a str, and the
floor's own documentation says why: `put_bool` knows that Python spells a true
value `True`, so every backend implementing it owes the LANGUAGE rather than
the machine. Every signature below takes and answers machine words and byte
buffers. A backend author implementing all of them still has not been told
what a `list` is.

NOT AN OPCODE, and the floor is the precedent. `plat_write` is an ordinary
`Op.CALL` of an external symbol; nothing in the instruction set knows it
exists. An opcode would have to be implemented by all five backends plus the
verifier, printer, liveness and interpreter -- eleven places -- to express
what a call with a signature already expresses. What a new opcode buys is a
new SHAPE of instruction, and these are not a new shape; they are calls.

WHAT A GROUP IS. A capability a target either has or does not: a filesystem, a
network stack, a clock. Grouping rather than listing each operation is
deliberate -- a target with files has all of the file operations or none of
them, and a backend author answering "do you have a filesystem" once is a
better question than answering it eleven times.
"""
from __future__ import annotations

from .platform import FLOOR as _FLOOR

#: THE ERROR CODES, and they are NOT `errno`.
#:
#: `errno` is a C concept, it is thread-local, and its numbers differ between
#: platforms -- which is the portability bug this file exists to avoid, in
#: miniature. So an operation that fails answers a NEGATIVE number from this
#: table and nothing else, and a caller that wants to tell "no such file" from
#: "permission denied" gets the same answer on every target.
#:
#: SMALL AND CLOSED. Every code here is one a caller can act on differently.
#: A richer set would be a translation table in every backend, and the ones
#: nobody branches on would drift.
ERRORS: dict[str, int] = {
    "HOST_ERR": -1,          # something failed and nothing more is known
    "HOST_ENOENT": -2,       # no such file or directory
    "HOST_EACCES": -3,       # permission denied
    "HOST_EEXIST": -4,       # already exists
    "HOST_ENOTDIR": -5,      # a path component is not a directory
    "HOST_ENOTEMPTY": -6,    # a directory that is not empty
    "HOST_EAGAIN": -7,       # would block; try again
    "HOST_EPIPE": -8,        # the other end is gone
    "HOST_EINVAL": -9,       # the arguments do not make sense
}

#: HOW A FILE IS OPENED, and NOT libc's `O_*` flags.
#:
#: `O_BINARY` is 0x8000 on MSVC and does not exist on POSIX; `O_CREAT` is 0x100
#: on one platform and 0o100 on another. A frontend that passed those through
#: would be writing platform-specific code in a portable module, which is what
#: `bundled/pathlib.py` had to do and what this replaces.
#:
#: ALWAYS BINARY. Newline translation is a property of TEXT, and text is a
#: language concept -- `str` knows about it and this layer does not. A frontend
#: that wants CRLF writes CRLF.
OPEN_MODES: dict[str, int] = {
    "HOST_OPEN_READ": 0,     # must exist
    "HOST_OPEN_WRITE": 1,    # created if absent, truncated if present
    "HOST_OPEN_APPEND": 2,   # created if absent, position at the end
    "HOST_OPEN_UPDATE": 3,   # read and write, must exist, not truncated
}

#: WHAT A PATH IS, as `host_file_kind` answers it.
#:
#: THREE NUMBERS RATHER THAN A `struct stat`. A struct means a layout every
#: backend agrees on and a frontend can read, which is a second ABI to keep in
#: step; three quarters of what `stat` is asked for is this question, and
#: `bundled/pathlib.py` reached for `GetFileAttributesA` to ask exactly it.
#: Size is a separate call because it is the other quarter.
KINDS: dict[str, int] = {
    "HOST_KIND_MISSING": 0,
    "HOST_KIND_FILE": 1,
    "HOST_KIND_DIR": 2,
    "HOST_KIND_OTHER": 3,    # a device, a socket, a symlink to nothing
}

#: WHERE A SEEK STARTS FROM.
SEEK: dict[str, int] = {
    "HOST_SEEK_SET": 0,
    "HOST_SEEK_CUR": 1,
    "HOST_SEEK_END": 2,
}

#: THE OPERATIONS, grouped by the capability a target either has or has not.
#:
#: name -> (argument IR types, result IR type)
#:
#: A PATH IS A POINTER AND A LENGTH, never a NUL-terminated string. The IR has
#: no C-string convention, `plat_write` already takes a buffer and a count, and
#: a JVM backend handed a bare pointer would have to scan for a terminator it
#: has no reason to believe in. It also means a path may contain a NUL and be
#: rejected honestly rather than silently truncated.
#:
#: DESCRIPTORS SHARE THE FLOOR'S NUMBERING. 0, 1 and 2 are standard input,
#: output and error, so `host_file_write(1, ...)` writes where
#: `plat_write(1, ...)` writes. Two numbering schemes for the same thing is
#: how a program ends up with interleaved output nobody can explain.
GROUPS: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    # ── the floor: emit bytes, stop, get memory ─────────────────────────
    #
    # THE ONE MANDATORY GROUP, and it is `link/platform.py`'s list rather than
    # a copy of it -- a hand-kept second copy of a signature list drifted
    # three times in one afternoon the last time this project kept two.
    # docs/INERT-RUNTIME.md stage 2 is the argument for why it is these three
    # and not the five it used to be, and that argument is unchanged.
    "core": dict(_FLOOR),
    # ── a filesystem ────────────────────────────────────────────────────
    "file": {
        "host_file_open":   (("ptr", "i64", "i64"), "i64"),
        "host_file_read":   (("i64", "ptr", "i64"), "i64"),
        "host_file_write":  (("i64", "ptr", "i64"), "i64"),
        "host_file_close":  (("i64",), "i64"),
        "host_file_seek":   (("i64", "i64", "i64"), "i64"),
        "host_file_kind":   (("ptr", "i64"), "i64"),
        "host_file_size":   (("ptr", "i64"), "i64"),
        "host_file_remove": (("ptr", "i64"), "i64"),
        "host_dir_make":    (("ptr", "i64"), "i64"),
        "host_dir_remove":  (("ptr", "i64"), "i64"),
    },
    # ── a clock ─────────────────────────────────────────────────────────
    #
    # NANOSECONDS AS AN i64, which runs to the year 2262 from the epoch and is
    # what every modern platform's clock answers anyway. A float would lose
    # precision at exactly the scale a profiler cares about, and a pair of
    # words would need a struct.
    #
    # TWO CLOCKS BECAUSE THERE ARE TWO QUESTIONS. `host_time_unix` answers
    # what time it is and may jump backwards when the machine is corrected;
    # `host_time_monotonic` never goes backwards and means nothing absolute.
    # A program that measures a duration with the first has a bug that appears
    # twice a year.
    "time": {
        "host_time_unix":      ((), "i64"),
        "host_time_monotonic": ((), "i64"),
        "host_sleep":          (("i64",), "i64"),
    },
    # ── entropy ─────────────────────────────────────────────────────────
    #
    # THE SYSTEM'S, not a PRNG. A backend fills the buffer from whatever its
    # platform calls cryptographically secure. A seeded generator is a
    # LANGUAGE feature -- `random.Random` is reproducible on purpose -- and
    # belongs above this line, seeded from here.
    "random": {
        "host_random_bytes": (("ptr", "i64"), "i64"),
    },
    # ── the environment the process was started in ──────────────────────
    #
    # COPIED OUT INTO A CALLER'S BUFFER, answering the length it needed. A
    # caller that guessed too small gets the true length and calls again,
    # which is the only shape that works without the layer allocating -- and
    # the layer must not allocate, because who frees it is a question with a
    # different answer in every backend.
    "env": {
        "host_env_get":  (("ptr", "i64", "ptr", "i64"), "i64"),
        "host_arg_count": ((), "i64"),
        "host_arg_get":  (("i64", "ptr", "i64"), "i64"),
    },
    # ── a network ───────────────────────────────────────────────────────
    #
    # STREAMS ONLY, and blocking. Datagrams, non-blocking sockets and TLS are
    # each a larger contract than this whole file, and a backend that has one
    # can offer it as a group of its own rather than by widening this one.
    "net": {
        "host_net_connect": (("ptr", "i64", "i64"), "i64"),
        "host_net_listen":  (("i64", "i64"), "i64"),
        "host_net_accept":  (("i64",), "i64"),
        "host_net_read":    (("i64", "ptr", "i64"), "i64"),
        "host_net_write":   (("i64", "ptr", "i64"), "i64"),
        "host_net_close":   (("i64",), "i64"),
    },
    # ── the character database ──────────────────────────────────────────
    #
    # NOT I/O, AND THAT IS THE POINT OF PUTTING IT HERE. A host operation is
    # anything a backend can do that the IR cannot express and that differs by
    # target -- the JVM has `Character.toUpperCase` and a generated table would
    # be dead weight beside it, while the C backend has the table and no JVM.
    # Same question, two answers, one name.
    #
    # CODE POINTS, NOT BYTES. Case folding is defined on characters, and a
    # layer taking bytes would have to pick an encoding. `str_code.py` already
    # turns UTF-8 into code points in the subset.
    "text": {
        "host_char_upper": (("i64",), "i64"),
        "host_char_lower": (("i64",), "i64"),
        "host_char_class": (("i64",), "i64"),
    },
}

#: The groups a backend owes no matter what. Everything else is declared.
MANDATORY = ("core",)

#: The groups a backend may or may not offer.
OPTIONAL = tuple(g for g in ("file", "time", "random", "env", "net", "text"))

#: Every operation, flattened, for the places that want one dictionary.
ALL: dict[str, tuple[tuple[str, ...], str]] = {
    name: sig for ops in GROUPS.values() for name, sig in ops.items()}

#: Which group an operation belongs to, so a refusal can name the capability
#: rather than only the function.
GROUP_OF: dict[str, str] = {
    name: group for group, ops in GROUPS.items() for name in ops}

NAMES = tuple(ALL)


def group_of(name: str) -> str | None:
    """The capability `name` needs, or None if it is not a host service."""
    return GROUP_OF.get(name)


def signature(name: str):
    """The IR signature of one operation, or None."""
    return ALL.get(name)


#: THE C IMPLEMENTATION, per group. `@STATIC@` and `@PTR@` are substituted
#: exactly as `link/platform.py` substitutes them, because a second convention
#: would be a second thing to keep in step.
#:
#: PER GROUP, and that is not tidiness. A bare-metal C target declares no
#: `file` group, and emitting `fopen` for it would fail to link -- which is
#: the same portability bug in C that `ctypes` had in the frontend. A backend
#: gets exactly the groups it said it has.
C_SOURCE: dict[str, str] = {}

C_SOURCE["file"] = r"""/* --- host services: file ------------------------------------------------ */

/* EVERY PLATFORM FUNCTION THIS NEEDS IS DECLARED HERE, and no header is
   included for them. `<sys/stat.h>` and `<direct.h>` both pull in `<io.h>` on
   MinGW, which declares `_open`, `_read`, `_write` and `_close` -- the very
   names a `ctypes` program declares for itself, and two prototypes for one
   symbol do not compile. Including them would have re-created the obstacle
   `cffi.py` documents, from the other side, and broken every program that
   reaches libc through `ctypes`.

   The prototypes below are the platform's own, written out. That is a real
   cost -- a wrong one is undefined behaviour rather than a compile error --
   which is why there are five of them and not fifty. */
#ifdef _WIN32
int _mkdir(const char *);
int _rmdir(const char *);
#define APY_HOST_MKDIR(p) _mkdir(p)
#else
int mkdir(const char *, unsigned int);
int rmdir(const char *);
#define APY_HOST_MKDIR(p) mkdir((p), 0777)
#define _rmdir rmdir
#endif

/* DIRECTORY DETECTION WITHOUT `stat`, which is what avoids the header. A
   directory is the thing `opendir` accepts and `fopen` does not; `DIR *` is a
   pointer on every platform, so declaring the return as `void *` is
   ABI-identical and needs no `<dirent.h>` either. */
void *opendir(const char *);
int closedir(void *);

/* A PATH ARRIVES AS A POINTER AND A LENGTH and every platform call below
   wants a NUL-terminated string, so it is copied into a bounded buffer. The
   copy is also where an embedded NUL is caught: a path containing one is
   rejected rather than silently truncated at it, which is a real class of
   security bug and costs one comparison to close. */
#define APY_HOST_PATH_MAX 4096
static int apy_host_path(@PTR@ p, int64_t n, char *out)
{
    int64_t i;
    if (n < 0 || n >= APY_HOST_PATH_MAX) return 0;
    for (i = 0; i < n; i++) {
        char c = ((const char *)p)[i];
        if (c == 0) return 0;
        out[i] = c;
    }
    out[n] = 0;
    return 1;
}

/* `errno` TRANSLATED ONCE, HERE. The whole reason this layer exists is that
   `errno` numbers differ between platforms, so a caller must never see one.
   Anything not named becomes the generic failure rather than a number the
   caller would have to look up. */
static int64_t apy_host_err(void)
{
    switch (errno) {
    case ENOENT:  return -2;
    case EACCES:  return -3;
    case EPERM:   return -3;
    case EEXIST:  return -4;
    case ENOTDIR: return -5;
    case ENOTEMPTY: return -6;
    case EAGAIN:  return -7;
    case EPIPE:   return -8;
    case EINVAL:  return -9;
    default:      return -1;
    }
}

@STATIC@int64_t host_file_open(@PTR@ path, int64_t n, int64_t mode)
{
    char buf[APY_HOST_PATH_MAX];
    const char *how;
    FILE *f;
    if (!apy_host_path(path, n, buf)) return -9;
    /* BINARY ALWAYS. Newline translation is a property of TEXT and text is a
       language concept; a frontend that wants CRLF writes CRLF. Without the
       `b` this layer would silently rewrite a program's bytes on Windows. */
    if (mode == 0)      how = "rb";
    else if (mode == 1) how = "wb";
    else if (mode == 2) how = "ab";
    else if (mode == 3) how = "r+b";
    else return -9;
    f = fopen(buf, how);
    if (!f) return apy_host_err();
    /* THE HANDLE IS A `FILE *` WIDENED, not an index into a table this file
       keeps. A table would need a size, a policy for exhausting it, and would
       not survive a backend that wanted its own; a pointer is what the
       platform already gave us and the caller only ever hands it back. */
    return (int64_t)(intptr_t)f;
}

/* THE THREE STANDARD DESCRIPTORS BY NUMBER, so `host_file_write(1, ...)`
   writes where `plat_write(1, ...)` writes. Two numbering schemes for the
   same thing is how a program ends up with interleaved output nobody can
   explain. Every other value is a handle `host_file_open` answered. */
static FILE *apy_host_stream(int64_t fd)
{
    if (fd == 0) return stdin;
    if (fd == 1) return stdout;
    if (fd == 2) return stderr;
    return (FILE *)(intptr_t)fd;
}

@STATIC@int64_t host_file_read(int64_t fd, @PTR@ buf, int64_t n)
{
    FILE *s = apy_host_stream(fd);
    size_t got;
    if (n < 0) return -9;
    got = fread((void *)buf, 1, (size_t)n, s);
    if (got == 0 && ferror(s)) return apy_host_err();
    return (int64_t)got;
}

@STATIC@int64_t host_file_write(int64_t fd, @PTR@ buf, int64_t n)
{
    FILE *s = apy_host_stream(fd);
    size_t put;
    if (n < 0) return -9;
    put = fwrite((const void *)buf, 1, (size_t)n, s);
    if (put != (size_t)n) return apy_host_err();
    /* FLUSHED, for the same reason `plat_write` is: stdout to a pipe is
       block-buffered, so without this the interleaving of stdout and stderr
       depends on where the output is going. */
    if ((fd == 1 || fd == 2) && fflush(s) != 0) return apy_host_err();
    return (int64_t)put;
}

@STATIC@int64_t host_file_close(int64_t fd)
{
    if (fd >= 0 && fd <= 2) return 0;          /* never close the standard three */
    if (fclose((FILE *)(intptr_t)fd) != 0) return apy_host_err();
    return 0;
}

@STATIC@int64_t host_file_seek(int64_t fd, int64_t off, int64_t whence)
{
    FILE *s = apy_host_stream(fd);
    int w = whence == 1 ? SEEK_CUR : whence == 2 ? SEEK_END : SEEK_SET;
    if (whence < 0 || whence > 2) return -9;
    if (fseek(s, (long)off, w) != 0) return apy_host_err();
    return (int64_t)ftell(s);
}

/* THREE NUMBERS RATHER THAN A `struct stat`, which would be a second ABI for
   a frontend to agree with -- and, as it turns out, a header this file cannot
   afford to include. */
@STATIC@int64_t host_file_kind(@PTR@ path, int64_t n)
{
    char buf[APY_HOST_PATH_MAX];
    void *d;
    FILE *f;
    if (!apy_host_path(path, n, buf)) return -9;
    d = opendir(buf);
    if (d) { closedir(d); return 2; }
    f = fopen(buf, "rb");
    if (f) { fclose(f); return 1; }
    /* NEITHER A DIRECTORY NOR READABLE. `ENOENT` is missing; anything else --
       a permission failure, a device -- is something that IS there and is not
       an ordinary file, which is what kind 3 is for. */
    return errno == ENOENT ? 0 : 3;
}

/* SIZE BY SEEKING TO THE END, because `stat` is the header this cannot have.
   Exact for a regular file, which is the only thing a caller asks about. */
@STATIC@int64_t host_file_size(@PTR@ path, int64_t n)
{
    char buf[APY_HOST_PATH_MAX];
    FILE *f;
    long at;
    if (!apy_host_path(path, n, buf)) return -9;
    f = fopen(buf, "rb");
    if (!f) return apy_host_err();
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return -1; }
    at = ftell(f);
    fclose(f);
    return at < 0 ? -1 : (int64_t)at;
}

@STATIC@int64_t host_file_remove(@PTR@ path, int64_t n)
{
    char buf[APY_HOST_PATH_MAX];
    if (!apy_host_path(path, n, buf)) return -9;
    if (remove(buf) != 0) return apy_host_err();
    return 0;
}

@STATIC@int64_t host_dir_make(@PTR@ path, int64_t n)
{
    char buf[APY_HOST_PATH_MAX];
    if (!apy_host_path(path, n, buf)) return -9;
    if (APY_HOST_MKDIR(buf) != 0) return apy_host_err();
    return 0;
}

@STATIC@int64_t host_dir_remove(@PTR@ path, int64_t n)
{
    char buf[APY_HOST_PATH_MAX];
    if (!apy_host_path(path, n, buf)) return -9;
    if (_rmdir(buf) != 0) return apy_host_err();
    return 0;
}
"""

C_SOURCE["time"] = r"""/* --- host services: time ------------------------------------------------ */

/* NANOSECONDS AS AN i64 -- good to the year 2262 from the epoch, and what
   every modern platform's clock answers anyway. A double would lose precision
   at exactly the scale a profiler cares about. */
@STATIC@int64_t host_time_unix(void)
{
    return (int64_t)time(NULL) * 1000000000;
}

@STATIC@int64_t host_time_monotonic(void)
{
    /* NEVER GOES BACKWARDS, which is the whole difference from the one above:
       a duration measured with a wall clock is wrong twice a year. `clock()`
       is the portable fallback and measures CPU rather than elapsed time --
       stated rather than hidden, because a backend with something better
       should use it. */
    return (int64_t)clock() * (1000000000 / CLOCKS_PER_SEC);
}

@STATIC@int64_t host_sleep(int64_t nanos)
{
    if (nanos <= 0) return 0;
    {
        clock_t until = clock() + (clock_t)(nanos / (1000000000 / CLOCKS_PER_SEC));
        while (clock() < until) { }
    }
    return 0;
}
"""

C_SOURCE["random"] = r"""/* --- host services: random ---------------------------------------------- */

/* THE SYSTEM'S ENTROPY, not a PRNG. A seeded generator is a LANGUAGE feature
   -- `random.Random` is reproducible on purpose -- and belongs above this
   line, seeded from here. */
@STATIC@int64_t host_random_bytes(@PTR@ buf, int64_t n)
{
    unsigned char *out = (unsigned char *)buf;
    int64_t i;
    if (n < 0) return -9;
#ifdef _WIN32
    {
        /* `rand_s` is the CRT's cryptographic one and needs no handle.
           DECLARED HERE because <stdlib.h> hides it behind `_CRT_RAND_S`,
           which has to be defined BEFORE that include -- and this C is
           spliced in after it. Declaring the symbol is the smaller of the two
           evils; the alternative is reaching into how the prelude is built
           from a file that should not know. */
        int rand_s(unsigned int *);
        unsigned int v;
        for (i = 0; i < n; i++) {
            if (rand_s(&v) != 0) return -1;
            out[i] = (unsigned char)(v & 0xFF);
        }
        return n;
    }
#else
    {
        FILE *f = fopen("/dev/urandom", "rb");
        size_t got;
        if (!f) return -1;
        got = fread(out, 1, (size_t)n, f);
        fclose(f);
        if (got != (size_t)n) return -1;
        return n;
    }
#endif
}
"""

C_SOURCE["env"] = r"""/* --- host services: env ------------------------------------------------- */

/* COPIED OUT INTO THE CALLER'S BUFFER, answering the length it NEEDED rather
   than the length it wrote. A caller that guessed too small sees a number
   larger than its buffer and calls again -- which is the only shape that
   works without this layer allocating, and it must not allocate, because who
   frees it has a different answer in every backend.
   -2 for a name that is not set, so "absent" and "empty" stay distinct. */
@STATIC@int64_t host_env_get(@PTR@ name, int64_t n, @PTR@ out, int64_t cap)
{
    char key[512];
    const char *got;
    int64_t len, i;
    if (n < 0 || n >= (int64_t)sizeof key) return -9;
    for (i = 0; i < n; i++) {
        char c = ((const char *)name)[i];
        if (c == 0) return -9;
        key[i] = c;
    }
    key[n] = 0;
    got = getenv(key);
    if (!got) return -2;
    len = (int64_t)strlen(got);
    for (i = 0; i < len && i < cap; i++) ((char *)out)[i] = got[i];
    return len;
}

/* THE COMMAND LINE, stashed by the entry wrapper because C only offers it to
   `main`.Zero arguments is a legitimate answer for a backend whose target has
   no command line. */
static int apy_host_argc = 0;
static char **apy_host_argv = 0;

@STATIC@int64_t host_arg_count(void)
{
    return (int64_t)apy_host_argc;
}

@STATIC@int64_t host_arg_get(int64_t i, @PTR@ out, int64_t cap)
{
    int64_t len, k;
    const char *s;
    if (i < 0 || i >= (int64_t)apy_host_argc) return -9;
    s = apy_host_argv[i];
    len = (int64_t)strlen(s);
    for (k = 0; k < len && k < cap; k++) ((char *)out)[k] = s[k];
    return len;
}
"""


def c_source(groups, *, static: bool = False, ptr: str = "void *") -> str:
    """The C for the groups a backend declared, substitutions made.

    UNKNOWN GROUP NAMES ARE IGNORED rather than refused, because a backend may
    declare a capability it implements ITSELF -- the JVM backend will answer
    `file` from `java.nio` and wants no C at all. This function answers "what
    C do you need from me", and for such a backend the answer is none.
    """
    parts = [C_SOURCE[g] for g in groups if g in C_SOURCE]
    text = "\n".join(parts)
    return (text.replace("@PTR@", ptr)
                .replace("@STATIC@", "static " if static else ""))
