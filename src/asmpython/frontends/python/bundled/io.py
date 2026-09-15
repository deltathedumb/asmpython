"""Text and binary streams: in memory, and over a real file.

COVERAGE: `StringIO` (`write`, `read`, `readline`, `readlines`, `getvalue`,
`seek`/`tell`, `truncate`, iteration, `close`/`closed`, the context manager
protocol) and `BytesIO` (the same, over `bytes`); `SEEK_SET`/`SEEK_CUR`/
`SEEK_END`; and `open(file, mode=...)` as a REAL function for `'r'`, `'w'`,
`'a'`, `'rb'`, `'wb'`, `'ab'` and `'rb+'`/`'r+b'`, returning a stream backed
by `objects/hostsvc.py` that supports `.read([size])`, `.write(data)`,
`.readline()`, `.readlines()`, iteration (line by line), `.seek`/`.tell`,
`.close`/`.closed` and the context manager protocol. `IOBase`, `RawIOBase`,
`BufferedIOBase`, `TextIOBase` and `UnsupportedOperation` exist as shallow
markers -- see below.

NOT COVERED, each refused BY NAME: `BytesIO.getbuffer` (it would need a
working `memoryview`, which -- per `bundled/collections.abc.py` -- "cannot
be named as values in this frontend"); text update mode -- `'r+'`, `'w+'`,
`'a+'` without a `'b'` -- and every OTHER `'+'` combination but `'rb+'`;
`open(newline=...)` for anything but the default; `open(opener=...)`, because
there is no OS-level file descriptor NUMBER this layer can hand one -- a
handle here is `host_file_open`'s own, and honouring a caller's opener
without really calling it would be exactly the plausible-wrong-answer this
project's refusals exist to avoid; and `x` (exclusive-creation) mode. Every
other mode string reaches the same one refusal path as a struct format this
module cannot lay out -- see `struct.py` for that precedent.

THE CLASS HIERARCHY IS DELIBERATELY SHALLOW. Real `io` is `IOBase` at the
root with `RawIOBase`/`BufferedIOBase`/`TextIOBase` under it and three or four
concrete classes layered on `BufferedIOBase` alone (`FileIO` wrapped in a
`BufferedReader`/`BufferedWriter`/`BufferedRandom`, wrapped again in a
`TextIOWrapper` for text mode). Reproducing that gets a program nothing this
module's own tests exercise -- `isinstance(f, io.IOBase)` -- for real weight
in the concrete methods below, so the four names exist and nothing extends
them. `StringIO`, `BytesIO` and what `open()` returns are their own classes.

TEXT MODE GOES THROUGH THE SAME TRANSLATION `pathlib.read_text`/`write_text`
DO, and the logic is REPEATED here rather than imported. `pathlib.py`'s own
docstring says why the translation exists at all -- CPython's text mode
turns `\r\n` and a lone `\r` into `\n` on read and writes `os.linesep` for
every `\n` on write -- and `_LINESEP` below is copied rather than referenced
because the two modules read it as their OWN constant, the same way
`pathlib.py`'s `_OPEN_READ`/`_KIND_FILE`/`_SEEK_SET` are `hostsvc.py`'s
numbers written out rather than imported from it: a bundled module has no
`from objects.hostsvc import OPEN_MODES`, because `hostsvc` is a frontend/
backend contract and not itself bundled. `_LINESEP` is `"\n"` for the same
reason pathlib's is: this is what every target the differential suite runs
against actually uses, and a hardcoded `"\r\n"` regardless of host was the
exact bug `pathlib.write_text` carried and was fixed out of -- see that
module's own comment on the constant and the git history around it. A
GENUINE Windows target needs the host to say so, which is the same open
question `pathlib.py` already states for its path separator.

WHY `open()` DOES NOT CALL `pathlib.Path(file).read_text()`. That method is
real, but it is an ALL-AT-ONCE operation: open, read to EOF, close, one
string back. A real program's `open()` calls `.readline()` in a loop, seeks
midway through a file, or opens for `'a'` and writes once without ever
reading -- none of which `read_text`/`write_text` need to support and none
of which a wrapper around them could give back correctly. So this module
opens its OWN handle through `host_file_open` and keeps it across calls,
exactly the shape `pathlib.Path`'s concrete methods use for one call apiece.
The one thing it DOES share with `pathlib.py` is the read-until-EOF and
write-until-complete LOOPS -- `host_file_read`/`host_file_write` may return
fewer bytes than asked for on a real short read or write, and a single call
whose result is trusted outright silently truncates. See `_read_n` and
`_write_all` below, and `pathlib.Path.read_bytes`'s docstring for the same
loop reasoned out at length.

TEXT-MODE `open()`, READ SIDE, IS A LOADED `StringIO` (see `_TextReader`,
which subclasses it and refuses only `write`). The whole file is read
through `host_file_open`/`host_file_read`/`host_file_close` -- a real,
looped, hostsvc-backed read exactly as `pathlib.Path.read_text` performs one
-- decoded and newline-translated, and then handed to that class for the
in-memory `read`/`readline`/`seek`/`tell`/iteration behaviour a text stream
needs from there on. That is a deliberate reuse of one engine for two
callers rather than a second copy of `StringIO`'s buffer-and-position logic,
and it is why `StringIO`'s methods are written to be correct on their own
merits -- `io.open(path).read()` depends on them being right. The WRITE side
cannot use the same shape, because `'w'`/`'a'` mode has to reach the disk
without waiting for a `.close()` that a crashing program might never call,
so it writes through its own handle immediately, one `.write()` call at a
time.

ONE REAL DIVERGENCE FALLS OUT OF THAT EAGERNESS, and it is worth stating
rather than leaving for someone to trip over: CPython's `TextIOWrapper`
decodes LAZILY, as a program calls `.read()`, so opening a file that
contains bytes which are not valid UTF-8 does not itself fail -- only a
`.read()` that reaches them does, and a program that opens such a file and
never reads it (or reads only past the point where a real one would stop)
sees nothing wrong. This module decodes the WHOLE file at `open()` time, so
the same program sees the `UnicodeDecodeError` immediately instead, or sees
one CPython would not have raised at all. A text file with genuinely
non-UTF-8 content is already a program depending on `errors=` handling this
module does not implement (see above), so the two disagreeing on exactly
WHEN they notice is the smaller of two gaps rather than a new one.
"""


# ── the public surface that is not a class ──────────────────────────────────

#: Where a `seek` starts from. The same three CPython's `io` module exposes,
#: and the same numbers `objects/hostsvc.py`'s `SEEK` table uses -- not a
#: coincidence, see `_SEEK_SET` and friends below.
SEEK_SET = 0
SEEK_CUR = 1
SEEK_END = 2


class UnsupportedOperation(OSError, ValueError):
    """What a stream raises for an operation its mode does not allow.

    BOTH BASES, because that is what CPython's actually is: an `OSError` for
    callers that catch I/O failures broadly, a `ValueError` for callers that
    catch a bad argument -- reading a write-only stream is a bit of both.
    """


class IOBase:
    """The root of CPython's stream hierarchy, here as a marker only.

    NOTHING BELOW EXTENDS THIS. See the module docstring for why: the real
    hierarchy earns its weight from shared mixin methods this module's
    concrete classes do not need a second copy of, and an `isinstance` check
    against it is not part of what this module covers.
    """


class RawIOBase(IOBase):
    """Present so `io.RawIOBase` resolves; nothing extends it."""


class BufferedIOBase(IOBase):
    """Present so `io.BufferedIOBase` resolves; nothing extends it."""


class TextIOBase(IOBase):
    """Present so `io.TextIOBase` resolves; nothing extends it."""


# ── StringIO ──────────────────────────────────────────────────────────────
#
# THE PARTS ARE KEPT AND JOINED ONCE. Appending to a string per `write` is
# quadratic in the number of writes; `getvalue` is where the parts become one
# string, and every other method reads through it.


class StringIO:
    """A text stream over a string held in memory."""

    def __init__(self, initial_value="", newline="\n"):
        # `newline` IS ACCEPTED AND NOT HONOURED past its default. CPython's
        # own default performs no translation at all for `StringIO` -- that
        # is different from `open()`'s default, which does -- so accepting
        # the parameter and doing nothing with it already matches the one
        # value this module's own tests, or any other program's, are likely
        # to pass.
        if initial_value is None:
            initial_value = ""
        self._parts = [initial_value] if initial_value else []
        self._pos = 0
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise ValueError("I/O operation on closed file")

    def getvalue(self):
        self._check_open()
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""

    def write(self, text):
        self._check_open()
        if not isinstance(text, str):
            raise TypeError("string argument expected, got '"
                            + type(text).__name__ + "'")
        held = self.getvalue()
        # A WRITE THAT LANDS ANYWHERE, not only at the end. `before` and
        # `after` are the parts of the existing content the new text does
        # NOT overwrite; `pad` is the gap a `seek` past the current end
        # leaves behind, which CPython fills with NUL rather than raising --
        # `before` is naturally the WHOLE existing string once `_pos` is past
        # it, so `pad`'s length is `_pos - len(before)` and is empty in the
        # ordinary append-at-the-end case.
        before = held[:self._pos]
        pad = "\0" * (self._pos - len(before))
        after = held[self._pos + len(text):]
        self._parts = [before + pad + text + after]
        self._pos = self._pos + len(text)
        return len(text)

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def read(self, size=-1):
        self._check_open()
        held = self.getvalue()
        if size is None or size < 0:
            out = held[self._pos:]
            self._pos = len(held)
            return out
        out = held[self._pos:self._pos + size]
        self._pos = self._pos + len(out)
        return out

    def readline(self, size=-1):
        self._check_open()
        held = self.getvalue()
        at = held.find("\n", self._pos)
        end = len(held) if at < 0 else at + 1
        if size is not None and size >= 0 and self._pos + size < end:
            end = self._pos + size
        out = held[self._pos:end]
        self._pos = end
        return out

    def readlines(self, hint=-1):
        out = []
        while True:
            line = self.readline()
            if not line:
                break
            out.append(line)
        return out

    def seek(self, pos, whence=0):
        self._check_open()
        if whence == 1:
            pos = self._pos + pos
        elif whence == 2:
            pos = len(self.getvalue()) + pos
        if pos < 0:
            raise ValueError("negative seek value " + str(pos))
        self._pos = pos
        return pos

    def tell(self):
        self._check_open()
        return self._pos

    def truncate(self, size=None):
        self._check_open()
        held = self.getvalue()
        at = self._pos if size is None else size
        self._parts = [held[:at]]
        return at

    def flush(self):
        self._check_open()
        return None

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def readable(self):
        return True

    def writable(self):
        return True

    def seekable(self):
        return True

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


class BytesIO:
    """The same stream, over `bytes`."""

    def __init__(self, initial_bytes=b""):
        if initial_bytes is None:
            initial_bytes = b""
        self._parts = [bytes(initial_bytes)] if initial_bytes else []
        self._pos = 0
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise ValueError("I/O operation on closed file")

    def getvalue(self):
        self._check_open()
        if len(self._parts) > 1:
            self._parts = [b"".join(self._parts)]
        return self._parts[0] if self._parts else b""

    def getbuffer(self):
        _refuse("BytesIO.getbuffer")

    def write(self, data):
        self._check_open()
        if isinstance(data, str):
            raise TypeError(
                "a bytes-like object is required, not 'str'")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("a bytes-like object is required, not '"
                            + type(data).__name__ + "'")
        payload = bytes(data)
        held = self.getvalue()
        before = held[:self._pos]
        pad = b"\0" * (self._pos - len(before))
        after = held[self._pos + len(payload):]
        self._parts = [before + pad + payload + after]
        self._pos = self._pos + len(payload)
        return len(payload)

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def read(self, size=-1):
        self._check_open()
        held = self.getvalue()
        if size is None or size < 0:
            out = held[self._pos:]
            self._pos = len(held)
            return out
        out = held[self._pos:self._pos + size]
        self._pos = self._pos + len(out)
        return out

    def readline(self, size=-1):
        self._check_open()
        held = self.getvalue()
        at = held.find(b"\n", self._pos)
        end = len(held) if at < 0 else at + 1
        if size is not None and size >= 0 and self._pos + size < end:
            end = self._pos + size
        out = held[self._pos:end]
        self._pos = end
        return out

    def readlines(self, hint=-1):
        out = []
        while True:
            line = self.readline()
            if not line:
                break
            out.append(line)
        return out

    def seek(self, pos, whence=0):
        self._check_open()
        if whence == 1:
            pos = self._pos + pos
        elif whence == 2:
            pos = len(self.getvalue()) + pos
        if pos < 0:
            raise ValueError("negative seek value " + str(pos))
        self._pos = pos
        return pos

    def tell(self):
        self._check_open()
        return self._pos

    def truncate(self, size=None):
        self._check_open()
        held = self.getvalue()
        at = self._pos if size is None else size
        self._parts = [held[:at]]
        return at

    def flush(self):
        self._check_open()
        return None

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def readable(self):
        return True

    def writable(self):
        return True

    def seekable(self):
        return True

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


# ── `open()`, and the real file it returns ──────────────────────────────────
#
# NOTHING HERE NAMES A PLATFORM, for the same reason `pathlib.py`'s concrete
# half does not: every operation below is `objects/hostsvc.py`'s, which is
# the contract every backend implements instead of this module knowing which
# operating system it is on.

#: How `host_file_open` is asked, from `hostsvc.OPEN_MODES`. `pathlib.py`
#: keeps its own copy of these same four numbers for the same reason this
#: module does: a bundled module has no import that would give them a name.
_OPEN_READ = 0
_OPEN_WRITE = 1
_OPEN_APPEND = 2
_OPEN_UPDATE = 3

#: Where a seek starts, from `hostsvc.SEEK` -- the same numbers as the public
#: `SEEK_SET`/`SEEK_CUR`/`SEEK_END` above, kept as a separate private copy so
#: a rename of the public ones cannot silently change what this module asks
#: the host for.
_SEEK_SET = 0
_SEEK_CUR = 1
_SEEK_END = 2

#: What text mode writes for `\n`. See the module docstring for why this is
#: `"\n"` and not a hardcoded `"\r\n"`, and why it is `pathlib.py`'s constant
#: written out again rather than imported.
_LINESEP = "\n"


def _refuse(what):
    raise NotImplementedError(
        "io." + what + " is not implemented by this compiler's bundled "
        "module; see the module docstring for what is covered")


def _fsencode(path):
    """The bytes a host call takes. UTF-8, which is what the cell holds."""
    return str(path).encode("utf-8")


class _TextReader(StringIO):
    """Text `'r'` mode `open()` -- a `StringIO` preloaded from a real file,
    with `write` REFUSED.

    A PLAIN `StringIO` WOULD BE WRONG HERE, not merely incomplete: CPython's
    `open(path, 'r').write(...)` raises `UnsupportedOperation`, and handing
    back an ordinary writable `StringIO` would silently let a caller mutate
    it instead -- a plausible answer for the wrong question, which is what
    every refusal in this module exists to avoid. Subclassing rather than
    reimplementing `read`/`readline`/`seek`/`tell`/iteration is the reuse the
    module docstring describes.
    """

    def write(self, text):
        raise UnsupportedOperation("not writable")

    def writelines(self, lines):
        raise UnsupportedOperation("not writable")

    def writable(self):
        return False


class _RawFile:
    """A real, binary, seekable file, backed by `objects/hostsvc.py`.

    THIS IS WHAT `open()` RETURNS FOR EVERY BINARY MODE, and it is also what
    text `'w'`/`'a'` mode writes THROUGH -- see `_TextWriter` below, which
    wraps one of these rather than repeating the read/write/seek/close loops
    a second time for text.
    """

    def __init__(self, path, mode_code, readable, writable):
        self.name = path
        raw = _fsencode(path)
        fd = host_file_open(raw, len(raw), mode_code)
        if fd < 0:
            if mode_code == _OPEN_READ or mode_code == _OPEN_UPDATE:
                raise FileNotFoundError(
                    "[Errno 2] No such file or directory: " + repr(path))
            raise OSError("[Errno 13] Permission denied: " + repr(path))
        self._fd = fd
        self._readable = readable
        self._writable = writable
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise ValueError("I/O operation on closed file")

    def readable(self):
        return self._readable

    def writable(self):
        return self._writable

    def seekable(self):
        return True

    def seek(self, pos, whence=0):
        self._check_open()
        got = host_file_seek(self._fd, pos, whence)
        if got < 0:
            raise OSError("[Errno 9] Bad file descriptor: " + repr(self.name))
        return got

    def tell(self):
        return self.seek(0, _SEEK_CUR)

    def _read_n(self, n):
        """Exactly `n` bytes, or fewer only at EOF -- READ IN A LOOP.

        THE SAME HAZARD `pathlib.Path.read_bytes` DOCUMENTS: a short read is
        real and a single call that trusts its result truncates silently. A
        fresh buffer each round is what the fd's own position makes correct
        -- see that method's docstring for the reasoning in full.
        """
        out = b""
        remaining = n
        while remaining > 0:
            chunk = bytearray(remaining)
            got = host_file_read(self._fd, chunk, remaining)
            if got < 0:
                raise OSError(
                    "[Errno 5] Input/output error: " + repr(self.name))
            if got == 0:
                break
            out = out + bytes(chunk[:got])
            remaining = remaining - got
        return out

    def _read_to_end(self):
        """Everything from the current position to EOF.

        SEEK TO THE END AND BACK TO LEARN THE REMAINING SIZE, the same trick
        `pathlib.Path.read_bytes` uses from the front of the file -- it works
        from any starting position, not only zero, because `host_file_seek`
        answers the position it landed at rather than a count relative to
        where it started.
        """
        start = self.tell()
        end = self.seek(0, _SEEK_END)
        self.seek(start, _SEEK_SET)
        remaining = end - start
        if remaining <= 0:
            return b""
        return self._read_n(remaining)

    def read(self, size=-1):
        self._check_open()
        if not self._readable:
            raise UnsupportedOperation("not readable")
        if size is None or size < 0:
            return self._read_to_end()
        return self._read_n(size)

    def readline(self, size=-1):
        self._check_open()
        if not self._readable:
            raise UnsupportedOperation("not readable")
        # READ THE REST, THEN GIVE BACK THE UNUSED PART. Simple and correct
        # rather than fast -- exactly the trade `pathlib.py`'s own docstring
        # names for `collections.deque`: "the answers match and the costs do
        # not," which is the right side of that trade for a file this
        # module's own tests size.
        start = self.tell()
        rest = self._read_to_end()
        at = rest.find(b"\n")
        cut = len(rest) if at < 0 else at + 1
        if size is not None and size >= 0 and size < cut:
            cut = size
        line = rest[:cut]
        self.seek(start + len(line), _SEEK_SET)
        return line

    def readlines(self, hint=-1):
        out = []
        while True:
            line = self.readline()
            if not line:
                break
            out.append(line)
        return out

    def write(self, data):
        self._check_open()
        if not self._writable:
            raise UnsupportedOperation("not writable")
        if isinstance(data, str):
            raise TypeError(
                "a bytes-like object is required, not 'str'")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("a bytes-like object is required, not '"
                            + type(data).__name__ + "'")
        payload = bytes(data)
        total = 0
        rest = payload
        while len(rest) > 0:
            wrote = host_file_write(self._fd, rest, len(rest))
            if wrote <= 0:
                raise OSError(
                    "[Errno 5] Input/output error: " + repr(self.name))
            total = total + wrote
            rest = rest[wrote:]
        return total

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def flush(self):
        self._check_open()
        return None

    def close(self):
        if not self._closed:
            host_file_close(self._fd)
            self._closed = True

    @property
    def closed(self):
        return self._closed

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


class _TextWriter:
    """Text `'w'`/`'a'` mode -- a `_RawFile` with `str` translated at the
    edge, rather than a second copy of the open/write/seek/close handle.

    NO TRANSLATION ACTUALLY HAPPENS ON THIS HOST, because `_LINESEP` is
    `"\n"` -- see the module docstring. The `\n` -> `_LINESEP` step is still
    written out rather than skipped, because it is the one line a genuine
    Windows host would need to change, exactly as `pathlib.write_text` states
    for the same reason.
    """

    def __init__(self, raw):
        self._raw = raw
        self.name = raw.name

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError("write() argument must be str, not "
                            + type(text).__name__)
        translated = text.replace("\n", _LINESEP) if _LINESEP != "\n" \
            else text
        self._raw.write(translated.encode("utf-8"))
        return len(text)

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def read(self, size=-1):
        raise UnsupportedOperation("not readable")

    def readline(self, size=-1):
        raise UnsupportedOperation("not readable")

    def readlines(self, hint=-1):
        raise UnsupportedOperation("not readable")

    def seek(self, pos, whence=0):
        return self._raw.seek(pos, whence)

    def tell(self):
        return self._raw.tell()

    def flush(self):
        return self._raw.flush()

    def close(self):
        self._raw.close()

    @property
    def closed(self):
        return self._raw.closed

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return True

    def __iter__(self):
        return self

    def __next__(self):
        raise UnsupportedOperation("not readable")

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


def open(file, mode="r", buffering=-1, encoding=None, errors=None,
         newline=None, closefd=True, opener=None):
    """A real stream over a real file, through `objects/hostsvc.py`.

    `buffering` IS ACCEPTED AND IGNORED, honestly: every write below already
    goes straight to `host_file_write` with nothing held back, so there is no
    buffering POLICY here for the argument to change -- accepting it costs
    nothing and lets an ordinary call site that passes it keep working.
    """
    if opener is not None:
        _refuse("open(opener=...)")
    if closefd is False:
        raise ValueError("Cannot use closefd=False with file name")
    if newline is not None:
        _refuse("open(newline=" + repr(newline) + ")")

    # AN UNKNOWN MODE LETTER IS INVALID, not unimplemented, and CPython says
    # so before it looks at anything else: `open(p, "q")` is
    # `ValueError: invalid mode: 'q'`. Refusing it as unimplemented claimed
    # this module might grow a mode Python does not have.
    for letter in mode:
        if letter not in "rwxabt+":
            raise ValueError("invalid mode: " + repr(mode))

    binary = "b" in mode
    plus = "+" in mode
    base = mode.replace("b", "").replace("t", "").replace("+", "")
    if base != "r" and base != "w" and base != "a":
        _refuse("open(mode=" + repr(mode) + ")")
    if plus and not binary:
        _refuse("open(mode=" + repr(mode) + ") -- text update mode")
    if plus and base != "r":
        _refuse("open(mode=" + repr(mode) + ") -- only 'rb+' is covered")

    path = file if isinstance(file, str) else str(file)

    if binary:
        if base == "r":
            code = _OPEN_UPDATE if plus else _OPEN_READ
            return _RawFile(path, code, True, plus)
        if base == "w":
            return _RawFile(path, _OPEN_WRITE, False, True)
        return _RawFile(path, _OPEN_APPEND, False, True)

    if base == "r":
        # THE WHOLE FILE, READ FOR REAL through the same hostsvc calls
        # `pathlib.Path.read_text` uses -- see the module docstring for why
        # the result is then handed to `StringIO` rather than this being a
        # call to that method.
        raw = _RawFile(path, _OPEN_READ, True, False)
        data = raw._read_to_end()
        raw.close()
        text = data.decode(encoding or "utf-8")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return _TextReader(text)
    if base == "w":
        return _TextWriter(_RawFile(path, _OPEN_WRITE, False, True))
    return _TextWriter(_RawFile(path, _OPEN_APPEND, False, True))
