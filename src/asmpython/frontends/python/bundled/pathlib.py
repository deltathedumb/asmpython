"""Filesystem paths as objects.

COVERAGE: the whole PURE half -- `PurePath`, `PurePosixPath`, `parts`, `name`,
`suffix`, `suffixes`, `stem`, `parent`, `parents`, `root`, `anchor`,
`is_absolute`, `joinpath`, `with_name`, `with_suffix`, `with_stem`,
`relative_to`, `as_posix`, `/`, comparison, `__fspath__` -- and a CONCRETE
`Path` with `exists`, `is_file`, `is_dir`, `read_bytes`, `read_text`,
`write_bytes`, `write_text`, `mkdir`, `touch`, `unlink`, `rmdir`.

NOT COVERED, each REFUSED BY NAME rather than answered wrongly: `iterdir`,
`glob`, `rglob`, `stat`, `resolve`, `absolute`, `open` and symlinks.

THE ONE DIVERGENCE THAT IS NOT A REFUSAL is the separator. `Path` here is
POSIX-flavoured, so `str(Path("a") / "b")` is `a/b` where CPython on Windows
answers `a\\b`; `as_posix()` agrees on both. Closing it means a real Windows
flavour -- drive letters, UNC names, case-insensitive comparison, `\\` and `/`
both accepted -- and doing the SEPARATOR alone would be worse than not doing
it: `Path("C:/x").is_absolute()` would still answer False while the class
looked native, which is the plausible-wrong-answer this file exists to avoid.
It is one thing to add, not four, and it is declared here until then.

THE CONCRETE HALF GOES THROUGH `link/hostsvc.py`, which is the contract every
backend implements: `host_file_open`, `host_file_read`, `host_file_kind` and
six more. Nothing is imported and nothing is declared -- those names are part
of the frontend/backend contract exactly as `plat_write` is, so they are
reachable the way the platform floor is.

IT USED TO BE `ctypes`, AND THAT IS WORTH RECORDING because the reason it
changed is the reason the layer exists. A `ctypes` declaration is resolved by
the LINKER at compile time -- a promise, not a `dlopen` -- so the name has to
be a literal and the literal has to be a platform spelling. This module named
eight: `_open`, `_read`, `_write`, `_close` and `_lseek` from the MSVC C
runtime, and `GetFileAttributesA`, `CreateDirectoryA`, `DeleteFileA` and
`RemoveDirectoryA` from kernel32. It worked, and it worked only where a linker
could find those symbols: the C backend, on Windows. The JVM has no linker and
no `_open`; a bare-metal target has neither.

WHAT WENT WITH THEM was every platform constant. `_O_BINARY` was 32768 because
that is MSVC's number, `_S_IWRITE` was 128 for the same reason, and `_INVALID`
was `GetFileAttributesA`'s sentinel. A module with no business knowing which
operating system it is on no longer does. The open modes, the kinds and the
error codes below are the LAYER's, defined once in `hostsvc.py` and the same
on every target.

BINARY IS NOT A FLAG ANY MORE. `host_file_open` is always binary by contract,
because newline translation is a property of TEXT and text is a language
concept -- so `read_text` and `write_text` are the only things in this module
that know what a line ending is, which is where that knowledge belongs.

WHAT IS STILL PLATFORM-SPECIFIC IS THE FLAVOUR AND NOT THE HOST. `Path` is
POSIX-flavoured, so `str()` renders `/`; see above. That is a question about
what a path MEANS, which no host service can answer.

THE INTERPRETER RUNS THIS TOO, which is not free and is the reason it can be
trusted. `ir/hostsvc_host.py` answers the same names through Python's `os`,
marshalling each buffer across the boundary between a host object and
interpreter memory -- including the WRITE-BACK, without which a read fills a
copy and `read_bytes` answers the right number of zero bytes. Without the
interpreter the oracle could not execute this module and the compiled
behaviour would be measured against nothing.

POSIX SEMANTICS, spelled out: `/` separates, a leading `/` means absolute, `.`
segments are dropped and `..` is KEPT -- resolving it needs to know what the
names refer to, which a pure path deliberately does not.
"""


class PurePosixPath:
    def __init__(self, *segments):
        text = ""
        for segment in segments:
            piece = str(segment)
            if piece.startswith("/") or not text:
                # AN ABSOLUTE SEGMENT RESTARTS THE PATH. `PurePath("/a") /
                # "/b"` is `/b`, which is what joining an absolute path means.
                text = piece
            else:
                text = text.rstrip("/") + "/" + piece
        self._raw = text if text else "."
        self._absolute = self._raw.startswith("/")
        parts = []
        for piece in self._raw.split("/"):
            if piece == "" or piece == ".":
                continue
            parts.append(piece)
        self._names = parts

    @property
    def parts(self):
        # THE ROOT IS A PART. `PurePosixPath("/a").parts` is `('/', 'a')` --
        # the leading slash is a component, not punctuation between them.
        if self._absolute:
            return tuple(["/"] + self._names)
        return tuple(self._names)

    @property
    def name(self):
        return self._names[-1] if self._names else ""

    @property
    def suffix(self):
        base = self.name
        at = base.rfind(".")
        # A LEADING DOT IS NOT A SUFFIX: `.bashrc` has none, which is why the
        # dot must be past the first character.
        return base[at:] if at > 0 else ""

    @property
    def suffixes(self):
        base = self.name
        if base.startswith("."):
            base = base[1:]
        out = []
        for piece in base.split(".")[1:]:
            out.append("." + piece)
        return out

    @property
    def stem(self):
        base = self.name
        got = self.suffix
        return base[:len(base) - len(got)] if got else base

    def _derive(self, *parts):
        """A new path of THIS class from `parts`.

        THE CLASS HAS TO SURVIVE THE OPERATION. `Path("d") / "f"` is the way
        every program names a file inside a directory, and building the result
        with a hard-coded `PurePosixPath` made it a PURE path -- one with no
        `exists`, no `read_text` and no way to reach the disk. The failure was
        an AttributeError at the far end, naming the method rather than the
        `/` that lost the class.

        `type(self)` AND NOT AN OVERRIDE PER SUBCLASS, because every subclass
        here takes the same constructor arguments; a flavour that needed to
        parse differently would be a different `__init__`, not a different
        derivation. CPython calls this `with_segments` and means the same
        thing by it.
        """
        return type(self)(*parts)

    @property
    def parent(self):
        if not self._names:
            return self
        if len(self._names) == 1:
            return self._derive("/" if self._absolute else ".")
        head = "/".join(self._names[:-1])
        return self._derive(("/" + head) if self._absolute else head)

    @property
    def parents(self):
        out = []
        here = self
        while True:
            up = here.parent
            if str(up) == str(here):
                break
            out.append(up)
            here = up
        return tuple(out)

    @property
    def root(self):
        return "/" if self._absolute else ""

    @property
    def anchor(self):
        return self.root

    def is_absolute(self):
        return self._absolute

    def joinpath(self, *others):
        return self._derive(str(self), *others)

    def with_name(self, name):
        if not self._names:
            raise ValueError(repr(str(self)) + " has an empty name")
        return self.parent / name

    def with_suffix(self, suffix):
        return self.with_name(self.stem + suffix)

    def with_stem(self, stem):
        return self.with_name(stem + self.suffix)

    def relative_to(self, other):
        base = other if isinstance(other, PurePosixPath) \
            else PurePosixPath(other)
        mine, theirs = self.parts, base.parts
        if mine[:len(theirs)] != theirs:
            raise ValueError(repr(str(self)) + " is not in the subpath of "
                             + repr(str(base)))
        return self._derive(*mine[len(theirs):]) if mine[len(theirs):] \
            else self._derive(".")

    def match(self, path_pattern):
        """Whether this path matches a glob pattern, from the RIGHT.

        NOT a match of the whole path unless the pattern says so. `match`
        answers "does this path END this way", so `*.txt` matches every path
        whose last component is a `.txt` file at any depth -- and a pattern
        that starts with `/` is anchored and must account for every component.
        That asymmetry is the whole method; a version that matched left to
        right would answer False for the case people actually use it for.

        NO `fnmatch` AND NO `re`, deliberately. CPython builds a regular
        expression per component and caches it, which is right for a module
        that also has to serve `glob` over thousands of names. This module
        refuses `glob`, so the only caller is a single path with a handful of
        components, and `_glob_match` below is the whole of what that needs --
        against making every program that touches a Path drag `re` in with it.
        """
        pattern = path_pattern if isinstance(path_pattern, PurePosixPath) \
            else PurePosixPath(path_pattern)
        wanted = pattern.parts
        if not wanted:
            raise ValueError("empty pattern")
        mine = self.parts
        if pattern.is_absolute():
            if len(wanted) != len(mine):
                return False
        elif len(wanted) > len(mine):
            return False
        i, j = len(mine) - 1, len(wanted) - 1
        while j >= 0:
            if not _glob_match(mine[i], wanted[j]):
                return False
            i = i - 1
            j = j - 1
        return True

    def as_posix(self):
        return str(self)

    def __truediv__(self, other):
        return self._derive(str(self), str(other))

    def __rtruediv__(self, other):
        return self._derive(str(other), str(self))

    def __str__(self):
        if self._absolute:
            return "/" + "/".join(self._names)
        return "/".join(self._names) if self._names else "."

    def __repr__(self):
        return type(self).__name__ + "(" + repr(str(self)) + ")"

    def __eq__(self, other):
        if isinstance(other, PurePosixPath):
            return str(self) == str(other)
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash(str(self))

    def __lt__(self, other):
        return self.parts < other.parts

    def __fspath__(self):
        return str(self)


class PurePath(PurePosixPath):
    """The system's flavour. POSIX here -- see the module docstring."""


class PureWindowsPath(PurePosixPath):
    """Present so an import of it resolves; the separator handling a real one
    needs is not written, so it behaves as a POSIX path."""


# ── the concrete half ───────────────────────────────────────────────────────
#
# NOTHING HERE NAMES A PLATFORM. It did: the flags were MSVC's numbers, the
# file calls were MSVC's spellings, and the metadata call was kernel32's. All
# of that is now `link/hostsvc.py`'s problem, which is what makes this half of
# the module portable rather than Windows-and-C-only.
#
# BINARY IS NOT A FLAG ANY MORE either. `host_file_open` is always binary by
# contract, because newline translation is a property of TEXT and text is a
# language concept -- so the CRLF handling in `read_text`/`write_text` below
# is the only place in this module that knows what a line ending is, which is
# where it belongs.

#: THE HOST SERVICE LAYER, and not `ctypes`. This module used to declare
#: `_open`, `_read`, `GetFileAttributesA` and five more through `ctypes`,
#: which `frontends/python/cffi.py` resolves at COMPILE time -- a promise to
#: the linker rather than a `dlopen`. That works, and it is why this module
#: worked; it is also why it worked ONLY on the C backend, because the JVM has
#: no linker and no `_open`, and because the names themselves are platform
#: spellings: `_open` is MSVC's, `open` is POSIX's, `java.nio` is neither.
#:
#: `link/hostsvc.py` names the same operations once and lets each backend
#: satisfy them however it can. Nothing is imported and nothing is declared:
#: the names below are part of the contract every backend implements, exactly
#: as `plat_write` is, so they are reachable without an import.
#:
#: WHAT THE MIGRATION REMOVED, and it is the point of the exercise: every
#: platform constant. `_O_BINARY` was 32768 because that is MSVC's number,
#: `_S_IWRITE` was 128 for the same reason, and `_INVALID` was
#: `GetFileAttributesA`'s sentinel. All three were this module knowing which
#: operating system it was on, in a file that has no business knowing.

#: How `host_file_open` is asked, from `hostsvc.OPEN_MODES`. Numbers rather
#: than a name because a bundled module has no import to get them from -- the
#: same reason the C's `apy_str_kind()` is a function returning a literal.
_OPEN_READ = 0
_OPEN_WRITE = 1
_OPEN_APPEND = 2

#: What `host_file_kind` answers, from `hostsvc.KINDS`.
_KIND_MISSING = 0
_KIND_FILE = 1
_KIND_DIR = 2

#: Where a seek starts, from `hostsvc.SEEK`.
_SEEK_SET = 0
_SEEK_END = 2

#: The failures this module tells apart, from `hostsvc.ERRORS`. NOT `errno`:
#: those numbers differ between platforms, which is the bug the table exists
#: to close, and a module that branched on them would be platform-specific
#: again in the one place that matters most.
_ENOENT = -2
_EACCES = -3
_EEXIST = -4
_ENOTEMPTY = -6

#: What text mode writes for `\n`. This module is POSIX-flavoured
#: and Windows-hosted, which the docstring records; it is a named constant
#: so the POSIX variant is a one-line change rather than a search.
_LINESEP = "\r\n"


def _glob_match(name, pattern):
    """One component against one glob pattern: `*`, `?` and `[...]`.

    BACKTRACKING WITHOUT RECURSION, because `*` is the only construct that can
    need it and there is a standard way to do it in a loop: remember where the
    last `*` was and where in the name it was last tried, and on a mismatch
    resume from there having given the star one more character. The recursive
    form is shorter and goes quadratic on a name full of stars, which is a
    property a path component should not have.

    `[...]` IS A SET, with `!` or `^` to negate and `a-z` for a range -- the
    three things a shell offers. A `]` immediately after the bracket is a
    literal `]` and not the end, which is the one rule that is easy to miss
    and the only way to put a `]` in a set at all. An unterminated `[` is a
    literal `[`, which is what fnmatch does rather than raising.
    """
    n, m = len(name), len(pattern)
    i = j = 0
    star = -1
    resume = 0
    while i < n:
        if j < m and pattern[j] == "[":
            end = j + 1
            if end < m and pattern[end] in ("!", "^"):
                end = end + 1
            if end < m and pattern[end] == "]":
                end = end + 1
            while end < m and pattern[end] != "]":
                end = end + 1
            if end < m:
                if _set_match(name[i], pattern[j + 1:end]):
                    i = i + 1
                    j = end + 1
                    continue
            elif pattern[j] == name[i]:
                # An unterminated `[`, matched as the literal character.
                i = i + 1
                j = j + 1
                continue
        elif j < m and (pattern[j] == "?" or pattern[j] == name[i]):
            i = i + 1
            j = j + 1
            continue
        elif j < m and pattern[j] == "*":
            star = j
            resume = i
            j = j + 1
            continue
        if star >= 0:
            j = star + 1
            resume = resume + 1
            i = resume
            continue
        return False
    while j < m and pattern[j] == "*":
        j = j + 1
    return j == m


def _set_match(ch, body):
    """`ch` against the inside of a `[...]`, ranges and negation included."""
    negate = bool(body) and (body[0] == "!" or body[0] == "^")
    if negate:
        body = body[1:]
    found = False
    k = 0
    while k < len(body):
        if k + 2 < len(body) and body[k + 1] == "-":
            if body[k] <= ch and ch <= body[k + 2]:
                found = True
            k = k + 3
        else:
            if body[k] == ch:
                found = True
            k = k + 1
    return found != negate


def _fsencode(path):
    """The bytes a native call takes. UTF-8, which is what the cell holds."""
    return str(path).encode("utf-8")


def _refuse(what):
    raise NotImplementedError(
        "pathlib." + what + " is not implemented by this compiler's bundled "
        "module; see the module docstring for what is covered")


class Path(PurePosixPath):
    """A path that touches the filesystem.

    FAILURE IS REPORTED THE WAY PYTHON DOES IT: a missing file is a
    `FileNotFoundError` from a read and `False` from `exists`, never a
    negative file descriptor leaking into the program.
    """

    def _fd(self, how):
        raw = _fsencode(self)
        return host_file_open(raw, len(raw), how)

    def _kind(self):
        """What the path is: missing, a file, a directory, or something else.

        ONE CALL FOR THE THREE QUESTIONS, which is what `host_file_kind`
        answers and why it answers a small number rather than a `struct
        stat`. This used to be `GetFileAttributesA` and a bit test."""
        raw = _fsencode(self)
        return host_file_kind(raw, len(raw))

    def exists(self):
        return self._kind() != _KIND_MISSING

    def is_dir(self):
        return self._kind() == _KIND_DIR

    def is_file(self):
        return self._kind() == _KIND_FILE

    def read_bytes(self):
        """The whole file, READ UNTIL IT ENDS rather than once.

        A `bytearray` IS THE WRITABLE BUFFER, and that is the whole reason
        reading is possible here. Its cell is a string's cell with the mutable
        flag set, so its bytes are addressable and the native call writes into
        them directly -- no copy, and no buffer type this frontend has to grow.

        THE LOOP IS NOT DEFENSIVE PADDING. `read(2)` is allowed to return
        fewer bytes than asked for and does, and a single call whose result is
        sliced to what came back TRUNCATES THE FILE SILENTLY -- no error, no
        short-read flag, just a shorter answer than CPython gives. It is rare
        on a regular file, which is what makes it the kind of bug that ships.

        A FRESH BUFFER PER ROUND, because the buffer's ADDRESS is what crosses
        to the callee and this frontend cannot offset one -- there is no way to
        say "fill from byte 400 on". The descriptor already carries the file
        position, so starting each read at the front of a new buffer and
        joining the pieces reads the same bytes in the same order. The first
        round asks for the whole file, so the second normally finds EOF.

        THE SIZE IS A HINT AND NOT A CONTRACT, which is what makes `_lseek`'s
        32-bit `long` return survivable: past 2GB it wraps or answers -1, and
        the loop reads to EOF regardless -- in chunks of whatever it was told,
        or of 4096 if it was told nothing usable.
        """
        fd = self._fd(_OPEN_READ)
        if fd < 0:
            raise FileNotFoundError(
                "[Errno 2] No such file or directory: " + repr(str(self)))
        size = host_file_seek(fd, 0, _SEEK_END)
        host_file_seek(fd, 0, _SEEK_SET)
        room = size if size > 0 else 4096
        out = b""
        while True:
            chunk = bytearray(room)
            got = host_file_read(fd, chunk, room)
            if got < 0:
                host_file_close(fd)
                raise OSError(
                    "[Errno 5] Input/output error: " + repr(str(self)))
            if got == 0:
                break
            out = out + bytes(chunk[:got])
        host_file_close(fd)
        return out

    def read_text(self, encoding=None, errors=None):
        """The file as text, with UNIVERSAL NEWLINES applied.

        `read_text` IS TEXT MODE, and text mode translates. CPython
        reaches this through `open(mode="r")`, whose default newline
        handling turns `\r\n` and a lone `\r` into `\n` on every
        platform -- so a file written by anything else reads back the way
        CPython reads it. Skipping the translation and calling that
        "raw is more honest" would make this module disagree with the
        oracle on the one operation people use it for. `read_bytes` stays
        raw, which is the actual place for untranslated content.
        """
        text = self.read_bytes().decode(encoding or "utf-8")
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def write_bytes(self, data):
        """`data` to the file, WRITTEN UNTIL IT IS ALL THERE.

        The other half of `read_bytes`'s loop and the same hazard: `write(2)`
        may take fewer bytes than offered, and returning its count as though
        the job were done leaves a file that is the right NAME and the wrong
        LENGTH. Here the remainder can be a slice rather than an offset --
        slicing gives a new object with its own contiguous bytes -- so the
        loop does not need the pointer arithmetic the read side lacks.
        """
        fd = self._fd(_OPEN_WRITE)
        if fd < 0:
            raise OSError("[Errno 13] Permission denied: " + repr(str(self)))
        total = 0
        rest = data
        while len(rest) > 0:
            wrote = host_file_write(fd, rest, len(rest))
            if wrote <= 0:
                host_file_close(fd)
                raise OSError(
                    "[Errno 5] Input/output error: " + repr(str(self)))
            total = total + wrote
            rest = rest[wrote:]
        host_file_close(fd)
        return total

    def write_text(self, data, encoding=None, errors=None):
        """`data` written as text, with the platform's line ending.

        THE OTHER HALF OF `read_text`'s TRANSLATION, and the reason it is
        here rather than left out: CPython's `open(mode="w")`
        writes `os.linesep` for every `\n` it is given, so on
        Windows a text file it produced has `\r\n` in it and
        `read_bytes` can see them. A round trip through `write_text` and
        `read_text` is unaffected either way -- it is the program that
        writes text and reads BYTES that can tell, and that program should
        get the same answer here as it does from CPython.

        THE COUNT RETURNED IS CPython's, which is the length of the string
        in CHARACTERS and not the number of bytes the translation made.
        """
        text = data.replace("\n", _LINESEP) if _LINESEP != "\n" else data
        self.write_bytes(text.encode(encoding or "utf-8"))
        return len(data)

    def mkdir(self, mode=511, parents=False, exist_ok=False):
        if parents:
            ups = list(self.parents)
            ups.reverse()
            for up in ups:
                if str(up) != "." and str(up) != "/":
                    raw = _fsencode(up)
                    host_dir_make(raw, len(raw))
        raw = _fsencode(self)
        if host_dir_make(raw, len(raw)) != 0:
            if exist_ok and self.is_dir():
                return None
            raise FileExistsError("[Errno 17] File exists: " + repr(str(self)))
        return None

    def touch(self, mode=438, exist_ok=True):
        if self.is_file():
            if not exist_ok:
                raise FileExistsError(
                    "[Errno 17] File exists: " + repr(str(self)))
            return None
        self.write_bytes(b"")
        return None

    def unlink(self, missing_ok=False):
        raw = _fsencode(self)
        if host_file_remove(raw, len(raw)) != 0:
            if missing_ok:
                return None
            raise FileNotFoundError(
                "[Errno 2] No such file or directory: " + repr(str(self)))
        return None

    def rmdir(self):
        raw = _fsencode(self)
        if host_dir_remove(raw, len(raw)) != 0:
            raise OSError("[Errno 41] Directory not empty: " + repr(str(self)))
        return None

    # ── refused by name ─────────────────────────────────────────────────
    #
    # A STUB IS WORSE THAN A REFUSAL. `iterdir` answering `[]`, `resolve`
    # answering `self`, `is_symlink` answering False -- each is a plausible
    # value that a caller cannot tell from the truth, and a program built on
    # one is wrong somewhere else entirely. Naming the method is the whole
    # point: the failure says which feature is missing, at the call.
    #
    # THEY ARE BLOCKED ON THREE DIFFERENT THINGS and it is worth separating
    # them, because they do not become available together.
    #
    # A STRUCT THIS FRONTEND CANNOT RECEIVE. `stat` wants a `struct stat`,
    # `iterdir`/`glob`/`rglob`/`walk` want `FindFirstFileA` and a
    # `WIN32_FIND_DATA`. A native call here returns one machine word, and
    # nothing declares a layout -- so these need `ctypes.Structure`, which is
    # a feature of `cffi.py` and not of this module.
    #
    # THE WINDOWS FLAVOUR. `cwd`, `home`, `absolute` and `resolve` all need
    # the working directory, and `GetCurrentDirectoryA` would give it -- it
    # takes a buffer, and a `bytearray` is one, so the CALL is reachable
    # today. What is not is the ANSWER: on Windows it is `C:\Users\...`, and
    # `Path` here is POSIX-flavoured, so the parser would read `C:` as an
    # ordinary name and `is_absolute()` would say False. These unblock when
    # the flavour does, together, and not before -- see the module docstring.
    # They are the compiler's own most-wanted (`resolve` 14 uses, `absolute`
    # 7), which is what makes the flavour the next thing to write.
    #
    # A FILE OBJECT. `open` needs one, and there is no `io` module yet.

    def iterdir(self):
        _refuse("Path.iterdir")

    def glob(self, pattern):
        _refuse("Path.glob")

    def rglob(self, pattern):
        _refuse("Path.rglob")

    def walk(self, top_down=True, on_error=None, follow_symlinks=False):
        _refuse("Path.walk")

    def stat(self, follow_symlinks=True):
        _refuse("Path.stat")

    def lstat(self):
        _refuse("Path.lstat")

    def is_symlink(self):
        _refuse("Path.is_symlink")

    def samefile(self, other):
        _refuse("Path.samefile")

    def rename(self, target):
        _refuse("Path.rename")

    def replace(self, target):
        _refuse("Path.replace")

    def resolve(self, strict=False):
        _refuse("Path.resolve")

    def absolute(self):
        _refuse("Path.absolute")

    def open(self, mode="r"):
        _refuse("Path.open")

    @classmethod
    def cwd(cls):
        _refuse("Path.cwd")

    @classmethod
    def home(cls):
        _refuse("Path.home")


class PosixPath(Path):
    """The name CPython gives the concrete flavour on POSIX."""


class WindowsPath(Path):
    """The name CPython gives the concrete flavour on Windows."""
