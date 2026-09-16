"""Operating system interfaces -- as far as `objects/hostsvc.py` reaches.

COVERAGE: `os.path` -- `join`, `basename`, `dirname`, `splitext`, `normpath`,
`isabs`, `exists`, `isfile`, `isdir`, and `abspath` for an ALREADY-ABSOLUTE
path only (see below). `os.remove`/`unlink`, `os.rmdir`, `os.mkdir`,
`os.makedirs`, `os.getenv`, `os.environ` (read-only: `[]`, `.get`, `in` --
NOT iteration, NOT assignment), `os.linesep`, `os.sep`, `os.pathsep`.
PEP 519's `os.fspath` and `os.PathLike` -- the second a real ABC, so a class
with `__fspath__` is path-like structurally and `register` works. Each
of the five mutating calls raises the same EXCEPTION CLASS CPython does for
"missing", "already exists" and "not empty" -- `FileNotFoundError`,
`FileExistsError`, `OSError` -- because they go straight to
`objects/hostsvc.py`'s error CODE rather than through `pathlib.Path`'s
mutators; see the module docstring below for why.

NOT COVERED, each REFUSED BY NAME rather than answered wrongly: `os.getcwd`,
`os.listdir`, `os.walk`, `os.stat`, `os.chdir`, `os.rename`, `os.replace`.
Every one is blocked on something `pathlib.py` is already blocked on, or on
a host primitive that does not exist -- see each refusal below for which.

WHY THIS IS NARROWER THAN CPYTHON'S `os`, on purpose. `objects/hostsvc.py` is
the whole ceiling: a filesystem that opens, reads, writes, closes, seeks,
answers a path's KIND (missing/file/dir), makes and removes one file or one
empty directory, and an environment that answers ONE NAME at a time. There is
no `struct stat`, no directory enumeration, no working directory, and no
rename -- so `os.stat`, `os.listdir`, `os.walk`, `os.getcwd`, `os.chdir` and
`os.rename` are not a smaller version of themselves here, they are ABSENT,
and each says so rather than returning something plausible and wrong.

`os.path` REUSES `pathlib.Path` FOR THE THREE QUESTIONS THAT TOUCH THE DISK
(`exists`, `isfile`, `isdir`) and NOT for the STRING operations (`join`,
`basename`, `dirname`, `splitext`, `normpath`). That split was measured
rather than assumed: `pathlib.PurePosixPath` drops `.` components, collapses
repeated `/`, and rebuilds its string from parsed parts rather than keeping
the original text -- so `PurePosixPath("/a/b/").name` is `"b"` where
CPython's `os.path.basename("/a/b/")` is `""`, and `str(PurePosixPath("/a",
""))` is `"/a"` where `os.path.join("/a", "")` is `"/a/"`. Both are CORRECT
for what `pathlib` is for -- a parsed, normalized path -- and WRONG for what
`os.path` is for, which is CPython's own `posixpath`, an unparsed STRING
algorithm that preserves exactly what a program handed it. So the string
functions below are `posixpath`'s own algorithms (`join`, `basename`,
`dirname`, `splitext`, `normpath`, `splitroot`) written out directly, and
`exists`/`isfile`/`isdir` -- which only ever ask the host "what is here", and
never re-render a path -- go through `pathlib.Path`, which already asks that
question correctly and is already proved against CPython by its own test.

`os.path.abspath` IS PARTIAL, and the reason is the same one `pathlib.py`
gives for `absolute`: making a relative path absolute needs the working
directory, and `os.getcwd` has nowhere to get one from -- see below. An
ALREADY-ABSOLUTE path needs no working directory at all, and CPython's own
`abspath` is `normpath` past that point, so `abspath` here does exactly that
much and refuses a relative path by name rather than guessing at one.

`os.environ` HAS NO ENUMERATION, for the same reason `pathlib.Path.iterdir`
does not exist: `objects/hostsvc.py`'s `env` group is `host_env_get`, which
answers ONE NAME at a time, and there is no host service that lists the
environment table at all. So `os.environ` cannot be the populated `dict`
CPython builds at start -- it is a small object that asks the host by name
on `[]`, `.get` and `in`, and has no `__iter__`, `__len__` or `keys`, because
defining one that answered an empty result would look like an empty
environment rather than a table nobody can read. It has no `__setitem__`
either: there is no host service to change the real process environment,
and a fake one that only remembered its own writes would let a program
believe it had set something a real child process would never see -- there
is no `subprocess` here for that to matter to yet, but the shape would be
wrong the day there is.

`os.linesep` IS `"\\n"`, reusing `pathlib.py`'s own `_LINESEP` and its own
reasoning: this is what every target this suite actually runs on writes for
a newline (Linux, macOS, bare metal, the JVM), and `"\\r\\n"` was pathlib's
PREVIOUS answer, hardcoded for a Windows host regardless of what actually ran
the program -- wrong everywhere it was checked. `os.sep` and `os.pathsep`
are POSIX values (`"/"`, `":"`) for the reason `pathlib.Path` is POSIX-
flavoured: the FLAVOUR -- what a path separator a program should render --
is a question about the target this module has not answered yet, same as
there. See `pathlib.py`'s module docstring for the open half of that.
"""

import abc as _abc
import pathlib as _pathlib


#: `objects/hostsvc.py`'s error table -- NOT `errno`, see `pathlib.py`'s own
#: copy of this note for why. A bundled module has no import to get the
#: real table from, so the numbers are written out, exactly as `pathlib.py`
#: writes out its own `_ENOENT` and the rest.
_HOST_ENOENT = -2
_HOST_EACCES = -3
_HOST_EEXIST = -4
_HOST_ENOTDIR = -5
_HOST_ENOTEMPTY = -6

#: What `write_text` writes for `\n` -- CPython's `os.linesep`. The SAME
#: value and the SAME reasoning as `pathlib._LINESEP`; see the module
#: docstring above and `pathlib.py`'s own note on why `"\n"` is right for
#: every target this suite is actually checked against today.
linesep = "\n"

#: POSIX values. See the module docstring: the separator is a FLAVOUR
#: question `pathlib.Path` has not answered either.
sep = "/"
pathsep = ":"


def _refuse(what):
    raise NotImplementedError(
        "os." + what + " is not implemented by this compiler's bundled "
        "module; see the module docstring for what is covered")


# ── os.path ──────────────────────────────────────────────────────────────
#
# THE STRING HALF IS `posixpath`'s OWN ALGORITHMS, not `pathlib`'s -- see the
# module docstring for why reusing `PurePosixPath` here would be a plausible
# WRONG answer rather than a reuse.

class PathLike(_abc.ABC):
    """`os.PathLike` -- PEP 519's ABC for anything that names a file.

    STRUCTURAL, like CPython's: `__subclasshook__` asks whether the class has
    `__fspath__` at all, so `pathlib.PurePath` answers True for it without
    ever having been registered, and so does a class a program wrote five
    minutes ago. Inheriting from it works too, and so does `register`, which
    is the reason this is a real ABC rather than a class with a hand-written
    metaclass -- both are part of what a program may do with it.
    """

    __slots__ = ()

    @_abc.abstractmethod
    def __fspath__(self):
        """The file system path this object stands for, as a `str`."""
        raise NotImplementedError

    @classmethod
    def __subclasshook__(cls, subclass):
        # NotImplemented FOR A SUBCLASS OF THIS ONE, so the ordinary MRO
        # answer stands rather than this hook deciding for a class that
        # inherited the abstract method and has not overridden it.
        if cls is PathLike:
            return hasattr(subclass, "__fspath__")
        return NotImplemented


def fspath(path):
    """`os.fspath(path)` -- the `str` (or `bytes`) a path-like object names.

    A `str` OR `bytes` PASSES STRAIGHT THROUGH, unchanged and un-copied,
    which is what makes this safe to call on an argument that may already be
    either. Everything else is asked for `__fspath__`, and what that answers
    is checked: a `__fspath__` returning an int is the class's error and
    CPython names it rather than handing the number on.
    """
    if isinstance(path, str) or isinstance(path, bytes):
        return path
    hook = getattr(path, "__fspath__", None)
    if hook is None:
        raise TypeError("expected str, bytes or os.PathLike object, not "
                        + type(path).__name__)
    got = hook()
    if isinstance(got, str) or isinstance(got, bytes):
        return got
    raise TypeError("expected " + type(path).__name__
                    + ".__fspath__() to return str or bytes, not "
                    + type(got).__name__)


def _splitroot(p):
    """`("", root, tail)`: `posixpath.splitroot`, POSIX branch only.

    THE TWO-SLASH CASE IS REAL, not an oversight: POSIX leaves `//foo`
    implementation-defined and CPython preserves it rather than collapsing
    it to one slash, so `normpath("//foo")` stays `"//foo"` while
    `normpath("///foo")` becomes `"/foo"`. Getting this wrong is exactly the
    kind of thing a differential test catches and a hand-wave does not.
    """
    if p[:1] != "/":
        return "", "", p
    if p[1:2] != "/" or p[2:3] == "/":
        return "", "/", p[1:]
    return "", p[:2], p[2:]


class _OsPath:
    """`os.path`. An OBJECT and not a second bundled module, because a
    program reaches it as `os.path.join(...)` -- an attribute of `os` -- and
    that is what a plain attribute access on the module object gives it,
    with no splice machinery involved past `os` itself."""

    def join(self, a, *parts):
        path = fspath(a)
        for b in parts:
            b = fspath(b)
            if b.startswith("/") or not path:
                path = b
            elif path.endswith("/"):
                path = path + b
            else:
                path = path + "/" + b
        return path

    def basename(self, p):
        p = fspath(p)
        i = p.rfind("/") + 1
        return p[i:]

    def dirname(self, p):
        p = fspath(p)
        i = p.rfind("/") + 1
        head = p[:i]
        if head and head != "/" * len(head):
            head = head.rstrip("/")
        return head

    def splitext(self, p):
        """`(root, ext)`: `genericpath._splitext`, this module's separators.

        LEADING DOTS ARE NOT AN EXTENSION. `.bashrc` has none and neither
        does `...bashrc` -- the scan has to pass every leading dot in the
        FILENAME (past the last `/`) before an internal one counts, which is
        the same rule `pathlib.suffix` states for a single leading dot and
        generalises here to a run of them, matching CPython's own loop.
        """
        p = fspath(p)
        sep_index = p.rfind("/")
        dot_index = p.rfind(".")
        if dot_index > sep_index:
            filename_index = sep_index + 1
            while filename_index < dot_index:
                if p[filename_index:filename_index + 1] != ".":
                    return p[:dot_index], p[dot_index:]
                filename_index += 1
        return p, ""

    def normpath(self, path):
        path = fspath(path)
        if not path:
            return "."
        _, initial_slashes, rest = _splitroot(path)
        comps = rest.split("/")
        new_comps = []
        for comp in comps:
            if not comp or comp == ".":
                continue
            if comp != ".." or (not initial_slashes and not new_comps) or (
                    new_comps and new_comps[-1] == ".."):
                new_comps.append(comp)
            elif new_comps:
                new_comps.pop()
        out = initial_slashes + "/".join(new_comps)
        return out if out else "."

    def isabs(self, p):
        return fspath(p).startswith("/")

    def abspath(self, path):
        """`normpath(path)` for an ALREADY-ABSOLUTE path; a relative one is
        refused. See the module docstring: making a relative path absolute
        needs `os.getcwd`, which has nowhere to get a working directory
        from."""
        path = fspath(path)
        if path.startswith("/"):
            return self.normpath(path)
        _refuse("path.abspath given a relative path -- there is no "
                "os.getcwd to resolve it against")

    def exists(self, p):
        """THROUGH `pathlib.Path`, which already asks the host this exact
        question and is already proved against CPython -- see the module
        docstring for why this half reuses it and the string half does
        not."""
        return _pathlib.Path(fspath(p)).exists()

    def isfile(self, p):
        return _pathlib.Path(fspath(p)).is_file()

    def isdir(self, p):
        return _pathlib.Path(fspath(p)).is_dir()


path = _OsPath()


# ── files and directories ───────────────────────────────────────────────
#
# DIRECTLY AGAINST `objects/hostsvc.py`, and NOT through `pathlib.Path` --
# which was the first attempt, and measured rather than assumed to be
# wrong. `pathlib.Path.rmdir` raises the SAME `OSError("[Errno 41]
# Directory not empty: ...")` for every failure `host_dir_remove` can
# report, a MISSING directory included -- it never looks at which code
# came back. That is invisible in `tests/stdlib/pathlib.py`, which never
# calls `rmdir` on a path that is not there; `os.rmdir` on one is exactly
# what CPython's own `FileNotFoundError` is for, and reusing `Path.rmdir`
# for it printed `OSError` where CPython raises `FileNotFoundError` --
# caught running THIS module's own differential test, not asserted from
# reading the source. `remove` and `mkdir` reuse `Path.unlink`/`Path.mkdir`
# was SAFE for the one error each is tested against here (a missing file, an
# existing directory) because those happen to be the one code each of
# THOSE two hardcodes -- but "safe by coincidence" is not a foundation, so
# all four go through the host codes directly and uniformly instead.

def _raise_host_error(code, path):
    """The `OSError` CPython raises for one of `objects/hostsvc.py`'s error
    codes, with the real errno number and `strerror` text for the platform
    this suite is actually checked against (Linux) -- the same thing
    `pathlib.py` does for the codes it handles, extended to cover
    `ENOTEMPTY` and `ENOTDIR` too."""
    if code == _HOST_ENOENT:
        raise FileNotFoundError(
            "[Errno 2] No such file or directory: " + repr(path))
    if code == _HOST_EEXIST:
        raise FileExistsError("[Errno 17] File exists: " + repr(path))
    if code == _HOST_EACCES:
        raise PermissionError("[Errno 13] Permission denied: " + repr(path))
    if code == _HOST_ENOTDIR:
        raise NotADirectoryError("[Errno 20] Not a directory: " + repr(path))
    if code == _HOST_ENOTEMPTY:
        raise OSError("[Errno 39] Directory not empty: " + repr(path))
    raise OSError("[Errno 1] Operation not permitted: " + repr(path))


def remove(path):
    p = fspath(path)
    raw = p.encode("utf-8")
    code = host_file_remove(raw, len(raw))
    if code != 0:
        _raise_host_error(code, p)


#: `os.unlink` IS `os.remove`, the same function under two names -- CPython
#: gives them the same C implementation; this gives them the same Python one.
unlink = remove


def rmdir(path):
    p = fspath(path)
    raw = p.encode("utf-8")
    code = host_dir_remove(raw, len(raw))
    if code != 0:
        _raise_host_error(code, p)


def mkdir(path, mode=511):
    p = fspath(path)
    raw = p.encode("utf-8")
    code = host_dir_make(raw, len(raw))
    if code != 0:
        _raise_host_error(code, p)


def makedirs(name, mode=511, exist_ok=False):
    """`mkdir`, and every missing ancestor along the way.

    THE ANCESTOR STACK IS BUILT WITH `path.dirname`, repeatedly, rather than
    `pathlib.Path.parents` -- this module's own string half, reused for the
    same reason `exists`/`isfile`/`isdir` reuse `pathlib.Path`: it is
    already the right tool, proved above against CPython's own `posixpath`.

    ONLY THE FINAL, DEEPEST ENTRY'S "ALREADY EXISTS" IS AN ERROR (unless
    `exist_ok`) -- every ancestor's is silently fine, which is what
    `makedirs` means by "and every missing ancestor": the ones that were
    not missing are not a problem.
    """
    p = fspath(name)
    stack = []
    cur = p
    while cur and cur != "/" and cur != ".":
        stack.append(cur)
        parent = path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    stack.reverse()
    last = len(stack) - 1
    for i, one in enumerate(stack):
        raw = one.encode("utf-8")
        code = host_dir_make(raw, len(raw))
        if code == 0:
            continue
        if code == _HOST_EEXIST:
            if i == last and not exist_ok:
                raise FileExistsError("[Errno 17] File exists: " + repr(one))
            continue
        _raise_host_error(code, one)


# ── the environment ──────────────────────────────────────────────────────
#
# ONE NAME AT A TIME, because that is what `host_env_get` answers -- see the
# module docstring for why there is no enumeration and no assignment.

def _getenv_raw(name):
    """The value of `name` from the process environment, or `None`.

    A GROWING BUFFER, the same shape `pathlib.Path.read_bytes` uses for the
    same reason: `host_env_get` answers the length it NEEDED rather than the
    length it wrote, so a caller whose first guess was too small asks again
    with a buffer that big instead of silently keeping a truncated answer.
    """
    raw = name.encode("utf-8")
    cap = 512
    buf = bytearray(cap)
    got = host_env_get(raw, len(raw), buf, cap)
    if got == _HOST_ENOENT:
        return None
    if got < 0:
        return None
    if got > cap:
        cap = got
        buf = bytearray(cap)
        got = host_env_get(raw, len(raw), buf, cap)
        if got < 0:
            return None
    return bytes(buf[:got]).decode("utf-8")


def getenv(key, default=None):
    got = _getenv_raw(key)
    return default if got is None else got


class _Environ:
    """`os.environ`, READ-ONLY. `[]`, `.get` and `in` each ask the host by
    NAME; there is nothing here that lists, counts or assigns -- see the
    module docstring for why each of those would have to lie."""

    def __getitem__(self, key):
        got = _getenv_raw(key)
        if got is None:
            raise KeyError(key)
        return got

    def __contains__(self, key):
        return _getenv_raw(key) is not None

    def get(self, key, default=None):
        got = _getenv_raw(key)
        return default if got is None else got


environ = _Environ()


# ── refused by name ─────────────────────────────────────────────────────
#
# A STUB IS WORSE THAN A REFUSAL -- `pathlib.py`'s own words, and the same
# reasoning: `listdir` answering `[]`, `getcwd` answering `"."`, `stat`
# answering a plausible-looking object with the wrong fields -- each is a
# value a caller cannot tell from the truth. Naming the function is the
# whole point.
#
# THREE DIFFERENT REASONS, worth keeping separate:
#
# NO WORKING DIRECTORY. `getcwd` and `chdir` need one; `objects/hostsvc.py`
# has no service that answers or changes it, so there is nothing to build
# these on top of at all -- not even the "answer is unreachable, not
# unparseable" situation `pathlib.py`'s `cwd`/`absolute` are in, because
# THIS module never had a native call to try in the first place.
#
# NO STRUCTURE THIS FRONTEND CAN RECEIVE. `stat` wants a `struct stat` and
# `listdir`/`walk` want directory enumeration -- `FindFirstFileA` and a
# `WIN32_FIND_DATA` on the platform pathlib.py used to reach for. A native
# call here returns one machine word with no layout declared anywhere, and
# closing this needs `ctypes.Structure` in `cffi.py`, not more of this
# module -- the exact limit `pathlib.py`'s `iterdir`/`glob`/`stat` are
# refused on, inherited rather than rediscovered.
#
# NO PRIMITIVE AT ALL. `objects/hostsvc.py`'s `file` group has `open`,
# `read`, `write`, `close`, `seek`, `kind`, `size`, `remove`, `make` and
# `remove` for a directory -- ten operations, and none of them is "rename".
# There is nothing to call `rename`/`replace` on top of, the same way there
# is nothing to call `getcwd` on top of.

def getcwd():
    _refuse("getcwd")


def listdir(path="."):
    _refuse("listdir")


def walk(top, topdown=True, onerror=None, followlinks=False):
    _refuse("walk")


def stat(path, dir_fd=None, follow_symlinks=True):
    _refuse("stat")


def chdir(path):
    _refuse("chdir")


def rename(src, dst):
    _refuse("rename")


def replace(src, dst):
    _refuse("replace")
