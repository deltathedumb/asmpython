# COVERAGE: StringIO (write, read, readline, readlines, getvalue, seek/tell,
# truncate, iteration, close/closed, context manager) and BytesIO (the same,
# byte-exact); io.SEEK_SET/SEEK_CUR/SEEK_END; and io.open() on a REAL file for
# 'r', 'w', 'a', 'rb', 'wb', 'ab' and 'rb+', exercising read([size]), write,
# readline, readlines, line iteration, seek/tell mid-stream, close/closed, the
# context manager protocol, and the errors a wrong-mode operation raises. NOT
# covered: BytesIO.getbuffer, text update mode ('r+'/'w+'/'a+'),
# open(newline=...) past the default, open(opener=...) -- each refused BY
# NAME rather than answered wrongly (see the module docstring).
#
# THE FILE IS MADE AND REMOVED BY THIS PROGRAM, in the repository root under
# both interpreters, with a name unique enough not to collide with a real one
# -- the same arrangement `tests/stdlib/pathlib.py` uses, and for the same
# reason: a leftover from a run that died halfway makes the NEXT run disagree
# with the first, which looks exactly like a miscompile and is not one.
import io
import pathlib

_PATH = "apy-io-case.txt"


def _cleanup():
    pathlib.Path(_PATH).unlink(missing_ok=True)


_cleanup()

# ---- StringIO ---------------------------------------------------------------
s = io.StringIO()
print(s.write("hello "), s.write("world"))
print(s.getvalue())
s.seek(0)
print(s.read(5))
print(s.read())
print(s.read())

s2 = io.StringIO("one\ntwo\nthree")
print(s2.readline())
print(s2.readline())
print(s2.readline())
print(s2.readline())
print(s2.tell())
s2.seek(0)
print(s2.readlines())

s3 = io.StringIO("a\nb\nc\n")
for line in s3:
    print(repr(line))
print(list(io.StringIO("x\ny\n")))

s4 = io.StringIO("abcdef")
s4.seek(2)
print(s4.write("XY"))
print(s4.getvalue())
# TEXT-STREAM `seek` ONLY TAKES AN ARBITRARY POSITION WITH `whence=0`; a
# nonzero relative or from-the-end seek is refused by real `StringIO` itself
# ("Can't do nonzero cur-relative seeks"), so mid-stream navigation here goes
# through `tell()`-derived absolute positions -- which is what `io.open()`'s
# own text-mode seek/tell test below does too, for the same reason.
end = s4.seek(0, io.SEEK_END)
print(end)
s4.seek(end - 3)
print(s4.read())

with io.StringIO("ctx") as s5:
    print(s5.read())
print(s5.closed)
try:
    s5.read()
except ValueError:
    print("StringIO closed: ValueError")

try:
    io.StringIO().write(3)
except TypeError as e:
    print("StringIO.write(int):", e)

# ---- BytesIO ------------------------------------------------------------------
b = io.BytesIO()
print(b.write(b"hello "), b.write(b"world"))
print(b.getvalue())
b.seek(0)
print(b.read(5))
print(b.read())

b2 = io.BytesIO(b"one\ntwo\nthree")
print(b2.readline())
print(b2.readline())
print(b2.readline())
print(b2.tell())
b2.seek(0)
print(b2.readlines())

for chunk in io.BytesIO(b"x\ny\n"):
    print(repr(chunk))

b3 = io.BytesIO(b"\x00\x01\xff\xfe")
print(b3.read())

with io.BytesIO(b"ctx") as b4:
    print(b4.read())
print(b4.closed)

try:
    io.BytesIO().write("x")
except TypeError as e:
    print("BytesIO.write(str):", e)

# `getbuffer` IS NOT EXERCISED HERE: this module refuses it by name (see the
# module docstring) and real CPython does not, so a differential test of it
# could only ever disagree with the oracle -- the same reasoning `re.py`'s
# own refusals are held to (docs/STDLIB.md, "`re`, the keystone").

# ---- io.open() on a real file ------------------------------------------------
f = io.open(_PATH, "w")
print(f.write("line one\n"))
print(f.write("line two\n"))
f.close()
print(f.closed)

f = io.open(_PATH, "r")
print(f.read())
f.close()

f = io.open(_PATH, "r")
print(f.readline())
print(f.readline())
print(f.readline())
f.close()

f = io.open(_PATH, "r")
for line in f:
    print(repr(line))
f.close()

with io.open(_PATH, "a") as f:
    f.write("line three\n")
with io.open(_PATH, "r") as f:
    print(f.readlines())

# tell()/seek() mid-stream, in text mode.
f = io.open(_PATH, "r")
print(f.read(4))
at = f.tell()
print(at)
print(f.read(4))
f.seek(at)
print(f.read(4))
f.seek(0)
print(f.tell())
f.close()

# Binary round trip, byte-exact, including a NUL and a high byte.
raw = b"\x00binary\xffdata\x01"
f = io.open(_PATH, "wb")
print(f.write(raw))
f.close()
f = io.open(_PATH, "rb")
got = f.read()
print(got == raw, len(got))
f.close()

# Binary seek/tell mid-stream.
f = io.open(_PATH, "rb")
print(f.read(3))
print(f.tell())
f.seek(0, io.SEEK_END)
end = f.tell()
print(end)
f.seek(-4, io.SEEK_END)
print(f.read())
f.seek(1)
print(f.read(1))
f.close()

# Binary append.
f = io.open(_PATH, "ab")
f.write(b"MORE")
f.close()
f = io.open(_PATH, "rb")
print(f.read() == raw + b"MORE")
f.close()

# rb+: update in place without truncating.
f = io.open(_PATH, "rb+")
print(f.read(1))
f.seek(1)
f.write(b"XY")
f.close()
f = io.open(_PATH, "rb")
print(f.read())
f.close()

# Wrong-mode operations raise, not silently no-op. Plain text is put back
# first: the file still holds the rb+ test's binary content, and THIS
# module's text-mode `open()` decodes the whole file eagerly, at `open()`
# rather than at the first `read()` -- unlike CPython's lazy TextIOWrapper,
# which would not have failed on the untouched binary content EITHER,
# because it never reaches a `read()` before the write it is actually
# testing fails first. See the module docstring for that divergence; it is
# not what this section exists to exercise.
with io.open(_PATH, "w") as _reset:
    _reset.write("plain text\n")

f = io.open(_PATH, "r")
try:
    f.write("x")
except io.UnsupportedOperation:
    print("read-only file: write refused")
f.close()

f = io.open(_PATH, "w")
try:
    f.read()
except io.UnsupportedOperation:
    print("write-only file: read refused")
f.close()

try:
    f.write("after close")
except ValueError:
    print("write after close: ValueError")

# A missing file behaves like pathlib's own missing-file case.
missing = "apy-io-case-missing.txt"
try:
    io.open(missing, "r")
except FileNotFoundError:
    print("open missing: FileNotFoundError")
try:
    io.open(missing, "rb+")
except FileNotFoundError:
    print("open missing rb+: FileNotFoundError")

# ---- the BUILTIN `open` ------------------------------------------------------
# It is `io.open`, and it was unbound: `open(p)` was `call to unknown function
# 'open'` while every line above worked. The file objects existed the whole
# time; only the name pointing at them was missing.

with open(_PATH, "w") as f:
    f.write("builtin one\nbuiltin two\n")
with open(_PATH) as f:
    print("builtin read:", repr(f.read()))
with open(_PATH) as f:
    print("builtin lines:", [line.rstrip("\n") for line in f])
with open(_PATH) as f:
    print("builtin readlines:", f.readlines())
with open(_PATH, "rb") as f:
    print("builtin binary:", f.read())
with open(_PATH, "a") as f:
    f.write("builtin three\n")
with open(_PATH) as f:
    print("builtin appended:", len(f.readlines()))

# `open` IS `io.open`, not a second implementation beside it.
print("same function:", open is io.open)

# AN UNKNOWN MODE LETTER IS INVALID, not unimplemented -- CPython checks the
# letters before anything else.
try:
    open(_PATH, "q")
except ValueError as exc:
    print("bad mode:", exc)

try:
    open("apy-io-case-missing.txt")
except FileNotFoundError:
    print("builtin missing: FileNotFoundError")

_cleanup()
print("done")
